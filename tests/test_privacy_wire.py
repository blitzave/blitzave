"""What leaves the laptop when Goose answers, checked on the wire.

test_goose.py's G1 checks what reaches a model's chat(). This checks the
bytes each real adapter puts on the network: every HTTP request httpx sends
from anywhere in the process, with its URL, headers and body. A tap on
httpx's transport records each one and answers like the model's server
would, so no request ever leaves the machine.

The household is made up and every sensitive field holds a canary: CANARY in
every description, merchant, bank, account name and id, account numbers 9137
and 8264, amounts nothing else would contain, a SimpleFIN access URL and an
API key. What may leave, and does: the question as typed, category and tag
names, accounts as aliases ("A2 (credit card)"), and today's date.

The last test is the positive control: an adapter that slips one description
into its prompt must be caught, or a clean pass here would prove nothing.
"""

import json
import re
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from blitzave import assistant
from blitzave.__main__ import URL, create_app
from blitzave.sources import models, simplefin
from blitzave.sources.base import AccountRow, BalanceRow, TxRow
from blitzave.store import db, ingest, transactions, transfers, users

TODAY = date(2026, 11, 14)
API_KEY = "sk-CANARY-not-a-real-key-0000"
ACCESS_URL = "https://CANARY-USER:CANARY-PASS@canary-bridge.example.test/simplefin"
ACCOUNT_NUMBERS = ["9137", "8264"]
CANARY = re.compile(r"canary[\w-]*", re.IGNORECASE)  # lowercased or not, it's a leak
FIGURE = re.compile(r"\$([\d,]+)\.(\d\d)")
AUTH_HEADERS = {"x-api-key", "authorization"}  # where a provider's key belongs, and nowhere else


class FixedDate(date):
    @classmethod
    def today(cls):
        return TODAY


# --- the tap ---------------------------------------------------------------------------


def _ollama(path: str, text: str) -> dict:
    if path == "/api/show":  # asked once per model: can it think?
        return {"capabilities": ["completion"]}
    return {"message": {"role": "assistant", "content": text}, "done": True}


def _claude(path: str, text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "model": "canned"}


# How each provider's server answers, keyed like models.ADAPTERS. A new
# adapter fails every test below until it has an entry here.
REPLIES = {"ollama": _ollama, "anthropic": _claude}
NOT_A_MODEL_CALL = {"/api/show"}  # answered without using up a scripted reply


class Tap:
    """Every request httpx sends, as it went out, answered with the next
    scripted reply in the provider's own shape."""

    def __init__(self, provider: str):
        self.reply = REPLIES[provider]
        self.script: list[str] = []
        self.requests: list[httpx.Request] = []

    def say(self, *replies) -> None:
        self.script += [r if isinstance(r, str) else json.dumps(r) for r in replies]

    def answer(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        path = request.url.path
        text = "" if path in NOT_A_MODEL_CALL or not self.script else self.script.pop(0)
        return httpx.Response(200, json=self.reply(path, text))


@pytest.fixture
def tap(request, monkeypatch):
    """Installed before the app starts, so its startup is watched too.
    TestClient talks to the app through its own transport, not these two."""
    tap = Tap(request.param)

    def handle(transport, req):
        return tap.answer(req)

    async def handle_async(transport, req):
        return tap.answer(req)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle_async)
    return tap


# --- the canary household ---------------------------------------------------------------

GROCERIES = [  # one a month, May through November
    ("2026-05-04", -481729), ("2026-06-03", -362154), ("2026-07-06", -173986), ("2026-08-04", -245817),
    ("2026-09-02", -156392), ("2026-10-05", -309471), ("2026-11-03", -128763),
]
TRIP = [("2026-05-14", -534618), ("2026-05-20", -617239)]
PAYMENT = 893527  # checking to the card, in the middle of the trip
BALANCES = {"CANARY-ACCT-1": 735146, "CANARY-ACCT-2": -296853}
SEEDED = [cents for _, cents in GROCERIES + TRIP] + [PAYMENT, *BALANCES.values()]


@pytest.fixture
def household(tap, request, tmp_path, monkeypatch, fake_keyring):
    """The app with a canary household and the provider being tested set up
    as Goose's model, its key and the SimpleFIN access URL in the (fake)
    keychain. Yields the client; the tap holds what went out."""
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    monkeypatch.delenv("BLITZAVE_DEV", raising=False)
    monkeypatch.setattr(assistant, "date", FixedDate)
    with TestClient(create_app(), base_url=URL) as client:
        # Saved after startup, so the startup sync doesn't run against it.
        simplefin.store_access_url(ACCESS_URL)
        provider = request.node.callspec.params["tap"]
        settings = {"provider": provider, "model": "test-model", "api_key": API_KEY, "remote_ok": True}
        assert client.put("/api/goose/settings", json=settings).status_code == 200
        groceries = client.post("/api/categories", json={"name": "Groceries", "kind": "expense"}).json()
        client.post("/api/categories", json={"name": "Dining", "kind": "expense"})
        client.post("/api/tags", json={"name": "Trip"})
        _seed(groceries["id"])
        yield client


def _seed(groceries_id: int) -> None:
    conn = db.connect()
    user_id = users.get_or_create_user(conn)
    checking = ingest.upsert_account(conn, user_id, AccountRow(
        "CANARY-ACCT-1", "asset", "9137", bank_name="CANARY-BANK", asset_type="checking",
        meta={"name": "CANARY-BANK Checking (9137)"},
    ))
    card = ingest.upsert_account(conn, user_id, AccountRow(
        "CANARY-ACCT-2", "liability", "8264", issuer_name="CANARY-BANK", liability_type="credit_card",
        meta={"name": "CANARY-BANK Card (8264)"},
    ))
    rows = [(card, "CANARY-ACCT-2", posted, cents) for posted, cents in GROCERIES + TRIP]
    rows += [(checking, "CANARY-ACCT-1", "2026-05-18", -PAYMENT), (card, "CANARY-ACCT-2", "2026-05-18", PAYMENT)]
    for n, (account_id, account, posted, cents) in enumerate(rows, start=1):
        ingest.upsert_transaction(conn, user_id, account_id, TxRow(
            account, f"CANARY-TX-{n}", cents, "USD", "debit" if cents < 0 else "credit", posted,
            f"CANARY-D-{n}", f"CANARY-M-{n}",
        ))
    for account_id, (account, cents) in zip([checking, card], BALANCES.items()):
        ingest.upsert_balance(conn, account_id, BalanceRow(account, TODAY.isoformat(), cents))
    conn.commit()
    for n in range(1, len(GROCERIES) + 1):
        transactions.update_metadata(conn, f"CANARY-TX-{n}", categories={groceries_id: 1.0})
    transfers.create_transfer(conn, user_id, f"CANARY-TX-{len(rows) - 1}", f"CANARY-TX-{len(rows)}")
    conn.close()


# --- the check ---------------------------------------------------------------------------


def amount_forms(cents: int) -> set[str]:
    """Every way an amount could be written: 4,817.29, 4817.29, 481729, 4817."""
    dollars, rest = divmod(abs(cents), 100)
    return {f"{dollars:,}.{rest:02}", f"{dollars}.{rest:02}", f"{dollars}{rest:02}", str(dollars)}


def shown_figures(cards: list) -> set[int]:
    """Every amount the engine showed the user, in cents. The ones under $100
    are left out: "0" or "42" turn up in dates and settings. Every seeded
    amount is over $1,000, so in practice that's only an empty month."""
    found = {int(d.replace(",", "")) * 100 + int(c) for d, c in FIGURE.findall(json.dumps(cards))}
    return {cents for cents in found if cents >= 10000}


def leaks(requests: list[httpx.Request], base_url: str, figures: set[int]) -> list[str]:
    """Everything wrong with what went out, one line each; empty when nothing is.
    A request may go only to the configured host. The API key may appear only
    in an auth header of such a request. No canary, account number or amount
    may appear anywhere else: the URL, any header, or the body."""
    host = httpx.URL(base_url)
    numbers = {form for cents in figures for form in amount_forms(cents)} | set(ACCOUNT_NUMBERS)
    found = []
    for r in requests:
        where = f"{r.method} {r.url}"
        if (r.url.scheme, r.url.host, r.url.port) != (host.scheme, host.host, host.port):
            found.append(f"{where}: not the configured host {base_url}")
        parts = {"url": str(r.url), "body": r.content.decode("utf-8", "replace")}
        for name, value in r.headers.items():
            if name.lower() in AUTH_HEADERS and value.removeprefix("Bearer ") == API_KEY:
                continue  # the key, in its place; the host check above covers where it went
            if name.lower() == "content-length":  # the body's size, which could match an amount by chance
                continue
            parts[f"header {name}"] = value
        for part, text in parts.items():
            found += [f"{where}: {part} holds {m.group(0)!r}" for m in CANARY.finditer(text)]
            found += [f"{where}: {part} holds {n!r}" for n in sorted(numbers) if n in text]
    return found


def configured_base_url(client) -> str:
    return client.get("/api/goose/settings").json()["base_url"]


# --- every intent, through every adapter ---------------------------------------------------

MAY_TRIP = {"start": "2026-05-13", "end": "2026-05-27"}
NO_FILTER = {"start": None, "end": None, "category": None, "tag": None, "account": None, "text": None}


def _plan(*intents) -> dict:
    return {"intents": list(intents)}


# (question, what the model says, in order, and the card the engine makes)
QUESTIONS = [
    ("show what I spent on the card on my May trip",
     [_plan({"intent": "show_transactions", "filter": {**NO_FILTER, **MAY_TRIP, "account": "A2"}})],
     "transactions"),
    ("tag the May trip as Trip",
     [_plan({"intent": "propose_tag", "tag_name": "Trip", "filter": {**NO_FILTER, **MAY_TRIP}})],
     "proposal"),
    ("the May trip was really dining",
     [_plan({"intent": "propose_category", "category": "Dining", "filter": {**NO_FILTER, **MAY_TRIP}})],
     "proposal"),
    ("how's my grocery spending going",
     [_plan({"intent": "spending_summary", "category": "Groceries", "tag": None, "months": 6}),
      "{label} averaged {average}."],
     "summary"),
    ("what did the trip cost",  # the writer types a figure, and fill() throws it away
     [_plan({"intent": "spending_summary", "category": None, "tag": "Trip", "months": 6}),
      "{label} came to $100."],
     "summary"),
    ("grocery last month",  # a label the user doesn't have: one repair, quoting the model's own reply
     [_plan({"intent": "spending_summary", "category": "Grocery", "tag": None, "months": 1}),
      _plan({"intent": "spending_summary", "category": "Groceries", "tag": None, "months": 1}),
      "{label}: {latest}."],
     "summary"),
    ("how much did I spend on food",
     [_plan({"intent": "clarify", "field": "category", "options": ["Groceries", "Dining"]})],
     "clarify"),
    ("should I put my savings in index funds",
     [_plan({"intent": "decline", "reason": "investment_advice"})],
     "message"),
]


@pytest.mark.parametrize("tap", sorted(models.ADAPTERS), indirect=True)
def test_nothing_but_the_question_and_labels_reaches_the_wire(household, tap):
    cards = []
    for question, replies, card_type in QUESTIONS:
        tap.say(*replies)
        response = household.post("/api/goose", json={"question": question})
        assert response.status_code == 200, response.text
        [card] = response.json()["cards"]
        assert card["type"] == card_type, (question, card)
        cards.append(card)
    assert not tap.script  # every scripted reply was asked for: each step really reached the wire

    figures = shown_figures(cards)
    assert figures >= {2495_64}  # the grocery average the user saw (June to October), checked too
    assert leaks(tap.requests, configured_base_url(household), figures | set(SEEDED)) == []
    assert any("A2 (credit card)" in r.content.decode() for r in tap.requests)  # the alias is all that went


def leaky(adapter: type) -> type:
    """A broken adapter that reads one description from the database and
    slips it into the system prompt."""

    class Leaky(adapter):
        def chat(self, system, messages, schema=None):
            conn = db.connect()
            try:
                _, [row, *_] = transactions.list_transactions(conn, limit=1)
            finally:
                conn.close()
            return super().chat(f"{system}\n\nFor context: {row['description']}", messages, schema)

    return Leaky


@pytest.mark.parametrize("tap", sorted(models.ADAPTERS), indirect=True)
def test_positive_control_a_leaky_adapter_is_caught(household, tap, monkeypatch):
    provider = household.get("/api/goose/settings").json()["provider"]
    monkeypatch.setitem(models.ADAPTERS, provider, leaky(models.ADAPTERS[provider]))
    tap.say(_plan({"intent": "decline", "reason": "investment_advice"}))
    assert household.post("/api/goose", json={"question": "should I buy bonds"}).status_code == 200

    found = leaks(tap.requests, configured_base_url(household), set(SEEDED))
    assert found and all(re.search(r"body holds 'CANARY-D-\d+'", line) for line in found), found


@pytest.mark.parametrize("tap", sorted(models.ADAPTERS), indirect=True)
def test_positive_control_a_stray_request_is_caught(household, tap):
    """A key sent anywhere but its auth header, and a call to another host."""
    household.get("/api/goose/settings")  # the app is up
    httpx.post("https://elsewhere.example.test/collect", json={"k": API_KEY}, headers={"x-api-key": API_KEY})
    found = leaks(tap.requests, configured_base_url(household), set())
    assert any("not the configured host" in line for line in found)
    assert any("body holds 'CANARY-not-a-real-key-0000'" in line for line in found)
