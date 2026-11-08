"""Rounds: the stretch of calendar a recurring amount covers (P0.3).

A recurring amount repeats every 1, 2, 3, 4, 6 or 12 months, aligned to the
calendar: a 3-month round runs Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec. Each round
starts fresh. When one ends it closes with a result, the amount minus what
was spent: an unspent $80 of Shopping closes as +$80, a round that ran over
closes as a negative number. The year's closed results add up. Nothing here is
a grade.

Pure: monthly spending totals in, round facts out. Rounds are whole calendar
months, so totals per month are all it needs.
"""

from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def round_of(day: date, period_months: int) -> tuple[date, date]:
    """The first and last day of the round that `day` falls in."""
    first_month = (day.month - 1) // period_months * period_months + 1
    last_month = first_month + period_months - 1
    return (
        date(day.year, first_month, 1),
        date(day.year, last_month, monthrange(day.year, last_month)[1]),
    )


def round_label(start: date, end: date) -> str:
    """"November", "Oct–Dec", "2026" for a whole year."""
    if start.month == end.month:
        return MONTHS[start.month - 1]
    if start.month == 1 and end.month == 12:
        return str(start.year)
    return f"{MONTHS_SHORT[start.month - 1]}–{MONTHS_SHORT[end.month - 1]}"


def _months(start: date, end: date) -> list[str]:
    keys = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


def _spent(totals: dict[str, int], start: date, end: date) -> int:
    """Net spending in the round: debits count up, credits (refunds) down."""
    return -sum(totals.get(key, 0) for key in _months(start, end))


def category_rounds(category: dict, totals: dict[str, int], today: date) -> dict | None:
    """Round facts for one recurring expense category, or None if it has no
    recurring amount. totals: the category's net amount_cents per "YYYY-MM"
    (negative = spending), from store.categories.monthly_totals."""
    if category["kind"] != "expense" or category["schedule"] != "recurring":
        return None
    budget = category["budget_cents"]
    period = category["period_months"]
    ends_on = date.fromisoformat(category["ends_on"]) if category["ends_on"] else None
    set_on = date.fromisoformat(category["plan_set_on"]) if category["plan_set_on"] else today
    counts_from = round_of(set_on, period)[0]

    current = None
    start, end = round_of(today, period)
    if ends_on is None or start <= ends_on:
        spent = _spent(totals, start, today)
        days = (end - start).days + 1
        elapsed = (today - start).days + 1
        if spent < 0:
            pace = "Credits exceed this round's spending so far."
        elif spent == 0:
            pace = "No net spending this round so far."
        elif spent * days > budget * elapsed:
            pace = "Spending is ahead of this round's pace."
        else:
            pace = "Spending is within this round's pace."
        current = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": round_label(start, end),
            "spent_cents": spent,
            "remaining_cents": budget - spent,
            "used_percent": round(spent * 100 / budget, 1),
            "elapsed_percent": round(elapsed * 100 / days, 1),
            "projected_cents": int(
                (Decimal(spent) * days / elapsed).to_integral_value(rounding=ROUND_HALF_UP)
            ),
            "pace_label": pace,
        }

    # The rounds of this calendar year that have ended, since the plan was set
    # and before it ended.
    closed = []
    round_start = date(today.year, 1, 1)
    while True:
        r_start, r_end = round_of(round_start, period)
        if r_end >= today:
            break
        if r_start >= counts_from and (ends_on is None or r_start <= ends_on):
            spent = _spent(totals, r_start, r_end)
            closed.append(
                {
                    "start": r_start.isoformat(),
                    "end": r_end.isoformat(),
                    "label": round_label(r_start, r_end),
                    "spent_cents": spent,
                    "result_cents": budget - spent,
                }
            )
        round_start = date(r_end.year + (r_end.month == 12), r_end.month % 12 + 1, 1)
        if round_start.year != today.year:
            break

    return {
        "current_round": current,
        "closed_rounds": closed,
        "closed_this_year_cents": sum(r["result_cents"] for r in closed),
    }
