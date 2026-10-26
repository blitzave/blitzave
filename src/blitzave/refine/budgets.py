"""Monthly budget facts. Refunds reduce net spending; nothing is a grade."""

from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def monthly_budget(
    total_cents: int,
    budget_cents: int | None,
    start: date,
    end: date,
    today: date,
) -> dict:
    spent = -total_cents
    remaining = None if budget_cents is None else budget_cents - spent
    days = monthrange(start.year, start.month)[1]
    is_month = start.day == 1 and (start.year, start.month) == (end.year, end.month)
    cutoff = min(today, end)
    elapsed = min(days, max(0, (cutoff - start).days + 1))
    elapsed_percent = round(elapsed * 100 / days, 1) if is_month else None
    used = round(spent * 100 / budget_cents, 1) if budget_cents else None

    projected = None
    if not budget_cents:
        label = "Set a monthly budget to see its pace."
    elif not is_month:
        label = "Choose a calendar month to see its pace."
    elif elapsed == 0:
        label = "This month hasn't started yet."
    elif elapsed == days and today > date(start.year, start.month, days):
        label = "Month complete."
    else:
        # Integer cents in, integer cents out. Decimal gives deterministic
        # rounding of a projection; it never changes the observed spending.
        projected = int(
            (Decimal(spent) * days / elapsed).to_integral_value(rounding=ROUND_HALF_UP)
        )
        if spent < 0:
            label = "Credits exceed this month's spending so far."
        elif spent == 0:
            label = "No net spending this month so far."
        elif spent * days > budget_cents * elapsed:
            label = "Spending is ahead of this month's pace."
        else:
            label = "Spending is within this month's pace."

    return {
        "spent_cents": spent,
        "remaining_cents": remaining,
        "remaining_label": "Above budget"
        if remaining is not None and remaining < 0
        else "Left in budget",
        "budget_used_percent": used,
        "month_elapsed_percent": elapsed_percent,
        "projected_cents": projected,
        "pace_label": label,
    }
