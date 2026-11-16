"""Goose's fixed words: everything a card says, so no model has to.

A model's only words that ever reach the screen are a summary sentence that
passed assistant.fill(). Every sentence here is held to the Prime Directive: a
higher month is reported, never judged. Amounts arrive as format_cents()
strings.
"""

from datetime import date

from blitzave.api.money import format_cents
from blitzave.words import MONTHS, month_name

NO_PLAN = "That one didn't come through clearly. Try naming a category, a tag or some dates."
GONE = "This preview isn't open anymore. Ask again for a fresh one."
UNDONE = "Undone. Those transactions are back the way they were."
PAIRS_LEFT_OUT = "Card payments and transfers left out"
NOTHING_TO_CHANGE = "Nothing matched, so there's nothing to change."
MONTH_LEFT_OUT = "This month left out of the average"

DECLINE = {
    "investment_advice": (
        "BlitZave keeps track of your money, but it doesn't give investment advice."
        " A licensed advisor can help with that."
    ),
    "not_your_data": "Goose can only see the accounts you've connected here.",
    "cannot_change_that": (
        "Amounts, dates and balances come from your bank, so they stay as they are."
        " Goose can tag transactions or file them under a category."
    ),
    "not_supported": (
        "That's not something Goose can do yet. It can find transactions, tag them,"
        " file them under a category, and add up a category or tag by month."
    ),
}

# Shown before any model that isn't on this laptop is used. It lists exactly
# what assistant.py puts in a request; keep the two in step.
DISCLOSURE = (
    "This model isn't on your laptop, so each question sends it: your question and"
    " today's date; your category and tag names, goals included; and your accounts"
    " as types only, like \"A1 (checking)\". Never sent: a transaction, a"
    " description or merchant, an amount or total, a balance, an account number,"
    " or your bank's name. A Claude API key stays in your system's keychain and"
    " goes only to Anthropic."
)

TREND = {"higher": "higher than usual", "lower": "lower than usual", "usual": "close to usual"}

# Every {slot} a summary sentence may use, and what it stands for. A model
# that words a summary gets this list, never the values, so what it's sent
# is the same whatever the figures are. summary_facts() fills only some of
# them; a sentence that uses one it left out isn't shown.
SLOTS = {
    "label": "what the question was about, like a category's name",
    "so_far": "the amount so far this month",
    "months": "how many whole months the summary covers",
    "first_month": "the first of those months",
    "latest_month": "the latest whole month",
    "latest": "the latest whole month's amount",
    "total": "all the whole months together",
    "average": "the average whole month",
    "highest_month": "the whole month with the most",
    "highest": "that month's amount",
    "lowest_month": "the whole month with the least",
    "lowest": "that month's amount",
    "trend": "how the latest whole month compares with the ones before it, like \"higher than usual\"",
}


def clarify(field: str) -> str:
    return f"Which {field} did you mean?"


def found(total: int, shown: int) -> str:
    if total == 0:
        return "Nothing matched."
    if shown < total:
        return f"The latest {shown} of {total} transactions."
    return f"{_count(total)}."


def too_many(limit: int) -> str:
    return (
        f"That's more than {limit} transactions to change at once."
        " Try a shorter date range, or name a category or an account."
    )


def tag_preview(count: int, name: str, is_new: bool) -> str:
    new = " (a new tag)" if is_new else ""
    return f"Tag {_these(count)} “{name}”{new}?"


def category_preview(count: int, name: str) -> str:
    return f"File {_these(count)} under {name}?"


def tagged(count: int, name: str) -> str:
    return f"Tagged {_count(count)} “{name}”."


def filed(count: int, name: str) -> str:
    return f"Filed {_count(count)} under {name}."


def filter_chips(
    start: date | None,
    end: date | None,
    category: str | None,
    tag: str | None,
    account: str | None,
    text: str | None,
) -> list[str]:
    """How the question was read, in the user's own names. The planner is the
    one part that can misread a question, so every card shows its reading."""
    chips = [_date_range(start, end)]
    if category:
        chips.append(f"Category: {category}")
    if tag:
        chips.append(f"Tag: {tag}")
    if account:
        chips.append(f"Account: {account}")
    if text:
        chips.append(f"Text: “{text}”")
    return chips


def summary_chips(scope: str, months: list[str], this_year: int) -> list[str]:
    """scope is "Category: Groceries", "Tag: PNW" or "All spending"."""
    if not months:
        return [scope]
    span = month_name(months[0], this_year)
    if len(months) > 1:
        span += f" – {month_name(months[-1], this_year)}"
    return [scope, span, MONTH_LEFT_OUT]


def summary_facts(facts: dict, label: str, this_year: int) -> dict[str, str]:
    """refine/spending.summary() as the words and amounts the user reads.
    The keys are also the only {slots} a model may write."""
    shown = {"label": label, "so_far": format_cents(facts["so_far_cents"])}
    months = facts["months"]
    if months:
        shown.update(
            months=str(len(months)),
            first_month=month_name(months[0], this_year),
            latest_month=month_name(months[-1], this_year),
            latest=format_cents(facts["amounts"][-1]),
            total=format_cents(facts["total_cents"]),
            average=format_cents(facts["average_cents"]),
            highest_month=month_name(facts["highest"][0], this_year),
            highest=format_cents(facts["highest"][1]),
            lowest_month=month_name(facts["lowest"][0], this_year),
            lowest=format_cents(facts["lowest"][1]),
        )
    if "trend" in facts:
        shown["trend"] = TREND[facts["trend"]]
    return shown


def summary_sentence(shown: dict[str, str]) -> str:
    """The engine's own sentence, true every time. A model's wording replaces
    it only when it passes the check."""
    label = shown["label"]
    if "months" not in shown:
        return (
            "There's less than a whole month of history so far."
            f" This month, {label} has come to {shown['so_far']}."
        )
    if "trend" not in shown:
        return f"{_capital(label)} came to {shown['latest']} in {shown['latest_month']}."
    return (
        f"Over the last {shown['months']} months, {label} averaged {shown['average']} a month."
        f" {shown['latest_month']} came to {shown['latest']}, {shown['trend']}."
    )


def _date_range(start: date | None, end: date | None) -> str:
    if start and end:
        return f"{_day(start)} – {_day(end)}"
    if start:
        return f"Since {_day(start)}"
    if end:
        return f"Through {_day(end)}"
    return "All dates"


def _day(day: date) -> str:
    return f"{MONTHS[day.month - 1][:3]} {day.day}, {day.year}"


def _count(n: int) -> str:
    return "1 transaction" if n == 1 else f"{n} transactions"


def _these(n: int) -> str:
    return "this transaction" if n == 1 else f"these {n} transactions"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]
