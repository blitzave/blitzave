"""Goose: a question about the user's own money becomes a plan, and the engine
runs it. ARCHITECTURE.md ("Goose") has the picture; the rule is one-way.

- A model only plans. It gets the question, today's date and the user's own
  labels (category and tag names, accounts as aliases like "A1 (checking)"),
  and answers with a Plan from the grammar below. It never sees a
  transaction, a count, or whether anything was found or changed, and it
  keeps no memory between questions.
- The engine runs each intent in its lane. Show lists rows. Change previews a
  tag or a category, and writes only the rows the user leaves checked, on
  Apply, with Undo. Summarize works out one scope's monthly facts.
- A model may word that summary, but it never sees or types a number: it
  gets the slot names only, writes {slots}, and fill() swaps in the engine's
  display strings or throws the sentence away.

Like jobs.py, this module orchestrates sources/, store/ and refine/. It holds
no SQL and makes no network call of its own.
"""

import re
import secrets
import time
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from blitzave import jobs, traces
from blitzave.api.money import format_cents
from blitzave.refine import spending
from blitzave.refine.savings import month_key
from blitzave.sources import models
from blitzave.sources.models import ModelError
from blitzave.store import accounts, categories, db, tags, transactions, users
from blitzave.store import settings as shared_settings
from blitzave.words import goose as words
from blitzave.words import month_name

SHOW_LIMIT = 50  # rows a Show card lists; the Transactions page has the rest
PREVIEW_LIMIT = 500  # rows one preview can hold; past this, ask for less
PROPOSAL_SECONDS = 15 * 60

ACCOUNT_KINDS = {
    "checking": "checking",
    "savings": "savings",
    "targeted": "goal account",
    "credit_card": "credit card",
    "loan": "loan",
}


class NotSetUpError(Exception):
    """No model is chosen yet, or a remote one has no consent saved."""


class SettingsError(Exception):
    """The model settings can't be saved as sent."""


class ProposalGoneError(Exception):
    """The preview was used, expired, or never existed."""


class NotInPreviewError(Exception):
    """Apply named a row the preview didn't show."""


# --- the grammar: everything a model can say -------------------------------------


def _label(field: str) -> AfterValidator:
    """A value that must be one of the user's own labels, which arrive as
    the validation context."""

    def check(value: str, info: ValidationInfo) -> str:
        names = info.context[field]
        if value not in names:
            raise ValueError(f"must be one of: {', '.join(names) or 'none yet'}")
        return value

    return AfterValidator(check)


Category = Annotated[str, _label("category")]
Tag = Annotated[str, _label("tag")]
Account = Annotated[str, _label("account")]


class Filter(BaseModel):
    """Which transactions: whatever list_transactions() can narrow by."""

    start: date | None = None
    end: date | None = None
    category: Category | None = None
    tag: Tag | None = None
    account: Account | None = None
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None


class ShowTransactions(BaseModel):
    """List the transactions the filter matches."""

    intent: Literal["show_transactions"]
    filter: Filter


class ProposeTag(BaseModel):
    """Preview putting a tag, existing or new, on what the filter matches."""

    intent: Literal["propose_tag"]
    tag_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    filter: Filter


class ProposeCategory(BaseModel):
    """Preview filing what the filter matches under one category."""

    intent: Literal["propose_category"]
    category: Category
    filter: Filter


class SpendingSummary(BaseModel):
    """Money in one category or one tag, or all spending when both are null,
    over the last `months` whole months."""

    intent: Literal["spending_summary"]
    category: Category | None = None
    tag: Tag | None = None
    months: int = Field(6, ge=1, le=24)

    @model_validator(mode="after")
    def _one_scope(self) -> "SpendingSummary":
        if self.category and self.tag:
            raise ValueError("summarize a category or a tag, not both")
        return self


class Clarify(BaseModel):
    """Two or more labels fit the question equally well."""

    intent: Literal["clarify"]
    field: Literal["category", "tag"]
    options: list[str] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def _options_are_labels(self, info: ValidationInfo) -> "Clarify":
        unknown = [o for o in self.options if o not in info.context[self.field]]
        if unknown:
            raise ValueError(f"options must be {self.field} labels, and {unknown} aren't")
        return self


class Decline(BaseModel):
    """Out of scope. The reason picks the engine's words."""

    intent: Literal["decline"]
    reason: Literal["investment_advice", "not_your_data", "cannot_change_that", "not_supported"]


Intent = Annotated[
    ShowTransactions | ProposeTag | ProposeCategory | SpendingSummary | Clarify | Decline,
    Field(discriminator="intent"),
]


class Plan(BaseModel):
    """One planner reply: one to three intents, run in order."""

    intents: list[Intent] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _clarify_and_decline_stand_alone(self) -> "Plan":
        if len(self.intents) > 1 and any(i.intent in ("clarify", "decline") for i in self.intents):
            raise ValueError("clarify and decline must be the only intent")
        return self


# Keywords Ollama's grammar or Claude's structured outputs can't take.
# Pydantic still checks every one of them on the reply.
_UNPORTABLE = {
    "title", "default", "discriminator",
    "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum",
}
LABELLED = ("category", "tag", "account")


def plan_schema(names: dict[str, list[str]]) -> dict:
    """The Plan's JSON schema in the subset both providers accept (every
    object closed and fully required, unions as anyOf), with the user's
    labels as the only values a category, tag or account can take."""

    def portable(node):
        if isinstance(node, list):
            return [portable(item) for item in node]
        if not isinstance(node, dict):
            return node
        node = {k: portable(v) for k, v in node.items() if k not in _UNPORTABLE}
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")
        if node.get("type") == "object":
            properties = node.get("properties", {})
            for name in LABELLED:
                if name in properties:
                    labels = names[name]
                    properties[name] = (
                        {"anyOf": [{"type": "string", "enum": labels}, {"type": "null"}]}
                        if labels
                        else {"type": "null"}
                    )
            node["required"] = list(properties)
            node["additionalProperties"] = False
        return node

    return portable(Plan.model_json_schema())


PLANNER = """\
You plan answers to questions about the user's own money in BlitZave, a personal \
finance app on their laptop. Reply with one JSON plan that fits the schema. You never \
see the user's data and nothing you write is shown to them, so never try to answer \
the question yourself.

A plan holds one to three intents:
- show_transactions: list the transactions a filter matches.
- propose_tag: put a tag on the transactions a filter matches. tag_name can be an \
existing tag or a new one. The user checks a preview before anything changes.
- propose_category: file the transactions a filter matches under one category, also \
after a preview.
- spending_summary: money in one category or one tag, or all spending when both are \
null, over the last `months` whole months. It can only use a tag that exists already.
- clarify: two or more labels fit the question equally well. List them as options.
- decline: investment advice (investment_advice), other people's money \
(not_your_data), changing amounts, dates or balances (cannot_change_that), or \
anything no intent covers (not_supported). clarify and decline stand alone.

A filter narrows transactions. start and end are dates, YYYY-MM-DD, both inclusive. \
category, tag and account must be labels listed below. text matches words in the \
description or merchant. Leave null what the question doesn't say. A word that \
matches no label goes in text. A date with no year is the most recent one that has \
passed. "Last month" is the previous calendar month.

The user's labels:
Categories: {categories}
Tags: {tags}
Accounts: {accounts}"""

WRITER = """\
You write one or two short, calm sentences that answer the user's question about their \
own money in BlitZave, an app that has their back. You get the names of the facts, \
not their values. Write each fact as its {name} in curly braces, exactly as listed: \
never type a number, an amount or a month yourself, and never compare amounts \
yourself; {trend} says how the latest month compares. Say what happened without \
judging it: no "should", no "too much", no warnings. Reply with the sentences only."""


@dataclass(frozen=True)
class Labels:
    """What a model may name. Real account names and numbers stay here."""

    categories: dict[str, dict]  # name -> category row
    tags: dict[str, int]  # name -> tag id
    accounts: dict[str, dict]  # alias ("A1") -> account row

    @classmethod
    def load(cls, conn) -> "Labels":
        return cls(
            categories={c["name"]: c for c in categories.list_categories(conn)},
            tags={t["name"]: t["id"] for t in tags.list_tags(conn)},
            accounts={f"A{n}": a for n, a in enumerate(accounts.list_accounts(conn), start=1)},
        )

    def names(self) -> dict[str, list[str]]:
        return {"category": [*self.categories], "tag": [*self.tags], "account": [*self.accounts]}

    def prompt(self) -> str:
        kinds = {
            alias: ACCOUNT_KINDS.get(a["asset_type"] or a["liability_type"], "account")
            for alias, a in self.accounts.items()
        }
        return PLANNER.format(
            categories=", ".join(self.categories) or "none yet",
            tags=", ".join(self.tags) or "none yet",
            accounts=", ".join(f"{alias} ({kind})" for alias, kind in kinds.items()) or "none yet",
        )


# --- a question, end to end ----------------------------------------------------------


def ask(question: str) -> list[dict]:
    """The cards for one question, in plan order. Every model call goes
    through one Turn, which dev mode writes down (traces.py)."""
    today = date.today()
    conn = db.connect()
    try:
        turn = traces.Turn(_model(), question)
        try:
            labels = Labels.load(conn)
            plan = _plan(turn, question, labels, today)
            if plan is None:
                return [{"type": "message", "text": words.NO_PLAN}]
            return [_run(conn, turn, intent, labels, question, today) for intent in plan.intents]
        finally:
            traces.write(turn)
    finally:
        conn.close()


def _plan(turn: traces.Turn, question: str, labels: Labels, today: date) -> Plan | None:
    """One call, and one repair if the reply doesn't validate. The repair
    quotes the model's own reply and what was wrong with it, and nothing
    from the database. None after two misses."""
    names = labels.names()
    system = labels.prompt()
    schema = plan_schema(names)
    messages = [{"role": "user", "content": f"Today is {today:%A}, {today.isoformat()}.\n\n{question}"}]
    for step in ("plan", "repair"):
        reply = turn.chat(step, system, messages, schema)
        try:
            plan = Plan.model_validate_json(reply, context=names)
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'reply'}: {err['msg']}" for err in e.errors()[:3]
            )
            turn.verdict(f"doesn't fit: {problems}")
            messages = [
                *messages,
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"That plan doesn't fit: {problems}. Reply with a corrected plan."},
            ]
        else:
            turn.verdict("fits")
            return plan
    return None


def _run(conn, turn: traces.Turn, intent, labels: Labels, question: str, today: date) -> dict:
    if intent.intent == "show_transactions":
        return _show(conn, intent.filter, labels)
    if intent.intent in ("propose_tag", "propose_category"):
        return _propose(conn, intent, labels)
    if intent.intent == "spending_summary":
        return _summarize(conn, turn, intent, labels, question, today)
    if intent.intent == "clarify":
        return {"type": "clarify", "text": words.clarify(intent.field), "options": intent.options}
    return {"type": "message", "text": words.DECLINE[intent.reason]}


def _query(f: Filter, labels: Labels) -> dict:
    """The filter as list_transactions() arguments, labels turned back into ids."""
    return {
        "start": f.start.isoformat() if f.start else None,
        "end": f.end.isoformat() if f.end else None,
        "q": f.text,
        "category_id": labels.categories[f.category]["id"] if f.category else None,
        "tag_id": labels.tags[f.tag] if f.tag else None,
        "account_external_id": labels.accounts[f.account]["id"] if f.account else None,
    }


def _chips(f: Filter, labels: Labels) -> list[str]:
    account = labels.accounts[f.account]["name"] if f.account else None
    return words.filter_chips(f.start, f.end, f.category, f.tag, account, f.text)


def _row(row: dict) -> dict:
    return {
        "posted_on": row["posted_on"],
        "description": row["description"],
        "amount_display": format_cents(row["amount_cents"]),
    }


def _show(conn, f: Filter, labels: Labels) -> dict:
    total, rows = transactions.list_transactions(conn, **_query(f, labels), limit=SHOW_LIMIT)
    return {
        "type": "transactions",
        "chips": _chips(f, labels),
        "text": words.found(total, len(rows)),
        "rows": [_row(r) for r in rows],
    }


def _propose(conn, intent: ProposeTag | ProposeCategory, labels: Labels) -> dict:
    """A preview, and nothing written. Pair legs are left out, so tagging a
    trip never catches the card payment."""
    total, rows = transactions.list_transactions(
        conn, **_query(intent.filter, labels), kind="transaction", limit=PREVIEW_LIMIT
    )
    chips = [*_chips(intent.filter, labels), words.PAIRS_LEFT_OUT]
    if total == 0 or total > PREVIEW_LIMIT:
        text = words.NOTHING_TO_CHANGE if total == 0 else words.too_many(PREVIEW_LIMIT)
        return {"type": "message", "chips": chips, "text": text}
    if intent.intent == "propose_tag":
        # An existing tag in any capitalization is that tag, not a new one.
        name = next((t for t in labels.tags if t.casefold() == intent.tag_name.casefold()), intent.tag_name)
        change = {"tag": name}
        text = words.tag_preview(len(rows), name, is_new=name not in labels.tags)
    else:
        change = {"category_id": labels.categories[intent.category]["id"], "category": intent.category}
        text = words.category_preview(len(rows), intent.category)
    return {
        "type": "proposal",
        "proposal_id": _hold({**change, "keys": [(r["account_id"], r["id"]) for r in rows]}),
        "chips": chips,
        "text": text,
        "rows": [_row(r) for r in rows],  # Apply sends back positions in this list
    }


def _summarize(conn, turn: traces.Turn, intent: SpendingSummary, labels: Labels, question: str, today: date) -> dict:
    first = transactions.first_posted_on(conn)
    months = spending.whole_months(today, date.fromisoformat(first) if first else None, intent.months)
    start = f"{months[0]}-01" if months else today.replace(day=1).isoformat()
    end = today.isoformat()
    if intent.category:
        category = labels.categories[intent.category]
        net = categories.monthly_totals(conn, start, end).get(category["id"], {})
        # Positive means money out, except for income, where it means money in.
        sign = 1 if category["kind"] == "income" else -1
        amounts = {key: sign * cents for key, cents in net.items()}
        label, scope = intent.category, f"Category: {intent.category}"
    elif intent.tag:
        net = tags.monthly_totals(conn, labels.tags[intent.tag], start, end)
        amounts = {key: -cents for key, cents in net.items()}
        label, scope = intent.tag, f"Tag: {intent.tag}"
    else:
        amounts = spending.by_month(transactions.spending_rows(conn, start))
        label, scope = "your spending", "All spending"
    facts = spending.summary(amounts, months, month_key(today))
    shown = words.summary_facts(facts, label, today.year)
    return {
        "type": "summary",
        "chips": words.summary_chips(scope, months, today.year),
        "text": _phrase(turn, question, shown) or words.summary_sentence(shown),
        "figure": shown.get("average", shown["so_far"]),
        "figure_label": "a month on average" if months else "so far this month",
        "months": [
            {"label": month_name(key, today.year), "amount_display": format_cents(cents)}
            for key, cents in zip(months, facts["amounts"])
        ],
    }


def _phrase(turn: traces.Turn, question: str, shown: dict[str, str]) -> str | None:
    """The model's wording of a summary, if it passes fill(). The model gets
    every slot's name and meaning, never a value, so the request is the same
    whatever the figures are; only fill() sees them."""
    facts = "\n".join(f"{{{key}}}: {meaning}" for key, meaning in words.SLOTS.items())
    try:
        reply = turn.chat("write", WRITER, [{"role": "user", "content": f"Question: {question}\n\nFacts:\n{facts}"}])
    except ModelError:
        return None
    turn.verdict(refusal(reply, shown) or "kept")
    return fill(reply, shown)


# --- numbers by reference -----------------------------------------------------------------

SLOT = re.compile(r"\{(\w+)\}")
# Outside a slot, a reply may not type a digit, a $ or % sign, a month, or a
# number in words: those come only from the engine's facts.
TYPED_FIGURE = re.compile(
    r"[\d$%]"
    r"|\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
    r"|(?i:\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty"
    r"|hundred|thousand|million|half|twice|double|triple)\b)"
)
# The writer never sees a value, so a comparison it types is a guess, and
# it's refused even when it happens to be true. How amounts compare comes
# only from the slots: {trend}, {highest_month}, {lowest_month}.
TYPED_COMPARISON = re.compile(
    r"\b(more|less|fewer|higher|lower|highest|lowest|most|least|bigger|smaller|above|below"
    r"|than|usual|normal|typical|rose|rising|fell|falling|dropped|jump\w*|spike\w*"
    r"|increase\w*|decrease\w*)\b",
    re.IGNORECASE,
)
NOT_ALLY = re.compile(
    r"\b(overspen\w*|fail\w*|miss(ed)?|blew|wasted|too much|should|must|streak\w*|behind|bad)\b",
    re.IGNORECASE,
)


def fill(sentence: str, facts: dict[str, str]) -> str | None:
    """The writer's sentence with each {slot} replaced by the engine's own
    display string, or None, meaning: show words/'s sentence instead."""
    if refusal(sentence, facts):
        return None
    filled = SLOT.sub(lambda m: facts[m.group(1)], sentence.strip())
    return filled[:1].upper() + filled[1:]


def refusal(sentence: str, facts: dict[str, str]) -> str | None:
    """The first rule a writer's sentence breaks, or None when fill() would
    show it. The reason quotes only the model's own words, never a figure."""
    sentence = sentence.strip()
    if not sentence:
        return "empty"
    if len(sentence) > 280:
        return "longer than 280 characters"
    if len(re.findall(r"[.!?](?:\s|$)", sentence)) > 2:
        return "more than two sentences"
    unknown = [key for key in SLOT.findall(sentence) if key not in facts]
    if unknown:
        return f"a slot this summary doesn't have: {{{unknown[0]}}}"
    typed = SLOT.sub("", sentence)  # the words the model wrote itself
    for rule, pattern in (("typed a figure", TYPED_FIGURE), ("typed a comparison", TYPED_COMPARISON)):
        if found := pattern.search(typed):
            return f"{rule}: {found.group(0)!r}"
    if found := NOT_ALLY.search(sentence):
        return f"a scorekeeper word: {found.group(0)!r}"
    return None


# --- Apply and Undo ------------------------------------------------------------------------
# In memory only. A proposal id is a one-time capability, gone after Apply,
# after PROPOSAL_SECONDS, or on a restart. _applied keeps what Undo needs for
# as long.

_held: dict[str, dict] = {}
_applied: dict[str, dict] = {}


def _hold(proposal: dict) -> str:
    now = time.monotonic()
    for store in (_held, _applied):
        for key in [k for k, v in store.items() if v["expires"] < now]:
            del store[key]
    proposal_id = secrets.token_urlsafe(16)
    _held[proposal_id] = {**proposal, "expires": now + PROPOSAL_SECONDS}
    return proposal_id


def apply(proposal_id: str, rows: list[int]) -> dict:
    """Writes the change to the rows the user left checked, sent as their
    positions in the preview, in one commit. The filter is never run again,
    so a sync since the preview can't slip rows in."""
    proposal = _held.get(proposal_id)
    if proposal is None or proposal["expires"] < time.monotonic():
        raise ProposalGoneError(words.GONE)
    if not set(rows) <= set(range(len(proposal["keys"]))):
        raise NotInPreviewError("apply only to rows the preview showed")
    if _held.pop(proposal_id, None) is None:  # one Apply per preview, even two at once
        raise ProposalGoneError(words.GONE)
    conn = db.connect()
    try:
        ids = transactions.row_ids(conn, [proposal["keys"][i] for i in sorted(set(rows))])
        if "tag" in proposal:
            user_id = users.get_or_create_user(conn)
            tag_id, created, added = tags.tag_transactions(conn, user_id, proposal["tag"], ids)
            receipt = {"tag_id": tag_id, "drop_tag": created, "ids": added}
            text = words.tagged(len(ids), proposal["tag"])
        else:
            before = transactions.set_category(conn, ids, proposal["category_id"])
            receipt = {"category_id": proposal["category_id"], "before": before}
            text = words.filed(len(before), proposal["category"])
    finally:
        conn.close()
    _applied[proposal_id] = {**receipt, "expires": time.monotonic() + PROPOSAL_SECONDS}
    return {"text": text, "undo": True}


def undo(proposal_id: str) -> dict:
    """Takes back exactly what Apply wrote: the tag pairs it added (and the
    tag, if Apply made it and nothing else uses it), or each row's categories
    as they were, unless the row has changed since."""
    receipt = _applied.pop(proposal_id, None)
    if receipt is None or receipt["expires"] < time.monotonic():
        raise ProposalGoneError(words.GONE)
    conn = db.connect()
    try:
        if "tag_id" in receipt:
            tags.untag_transactions(conn, receipt["tag_id"], receipt["ids"], receipt["drop_tag"])
        else:
            transactions.restore_categories(conn, receipt["category_id"], receipt["before"])
    finally:
        conn.close()
    return {"text": words.UNDONE}


# --- which model ------------------------------------------------------------------------------
# Saved in the settings file Live and Demo share (store/settings.py), so it's
# set once for both. A Claude API key lives only in the keychain.


def _saved() -> dict:
    return shared_settings.read(jobs.shared_folder()).get("goose") or {}


def _model() -> models.ChatModel:
    saved = _saved()
    if not saved:
        raise NotSetUpError("Choose a model for Goose first.")
    if models.is_remote(saved["base_url"]) and not saved.get("remote_ok"):
        raise NotSetUpError("A model that isn't on this laptop needs your OK first.")
    return models.make_model(saved["provider"], saved["model"], saved["base_url"])


def settings() -> dict:
    """What the settings screen shows. The API key never comes back."""
    saved = _saved()
    return {
        "provider": saved.get("provider"),
        "model": saved.get("model"),
        "base_url": saved.get("base_url"),
        "remote": bool(saved) and models.is_remote(saved["base_url"]),
        "key_saved": models.has_api_key(),
        # Only `blitzave --demo` keeps the key in memory; Demo shares the keychain.
        "disclosure": words.DISCLOSURE if jobs.LIVE is not None else words.DEMO_DISCLOSURE,
    }


def model_names(provider: str, base_url: str | None) -> list[str]:
    """The model names the settings screen offers. Claude's are a short
    fixed list; Ollama's are what it has, asked only on this laptop. Empty
    when Ollama can't be asked, and the screen takes a typed name instead."""
    if provider == "anthropic":
        return list(models.CLAUDE_MODELS)
    try:
        return models.ollama_models(base_url or models.OLLAMA_URL)
    except ModelError:
        return []


def save_settings(provider: str, model: str, base_url: str | None, api_key: str | None, remote_ok: bool) -> dict:
    """A model whose host isn't this laptop needs remote_ok, which the
    settings screen sends only after showing the disclosure. The consent is
    saved with the settings it was given for, so a new address asks again."""
    if provider == "anthropic":
        base_url = models.ANTHROPIC_URL
    base_url = (base_url or models.OLLAMA_URL).strip().rstrip("/")
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SettingsError("The address should look like http://127.0.0.1:11434.")
    remote = models.is_remote(base_url)
    if remote and not remote_ok:
        raise SettingsError("A model that isn't on this laptop needs your OK: check the box under what it would be sent.")
    if api_key and api_key.strip():
        models.save_api_key(api_key.strip())
    if provider == "anthropic" and not models.has_api_key():
        raise SettingsError("Claude needs an API key.")
    shared_settings.update(
        jobs.shared_folder(),
        {"goose": {"provider": provider, "model": model.strip(), "base_url": base_url, "remote_ok": remote}},
    )
    return settings()
