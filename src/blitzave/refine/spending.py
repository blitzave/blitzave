"""Facts about one scope's money month by month, for Goose's summaries: one
category, one tag, or all spending.

Pure: cents in, facts out. An amount is positive when money went out (for an
income category, when it came in). The current month is never in an average;
it's reported on its own as "so far", so half a month never reads as a drop.
"""

from datetime import date

from blitzave.refine.savings import add_months, complete_months, month_key

# The latest whole month this far above or below the months before it reads
# as higher or lower than usual. Anything closer is close to usual.
TREND_RATIO = 0.15


def whole_months(today: date, first_day: date | None, count: int) -> list[str]:
    """The last `count` whole months before this one, oldest first. A month
    the data starts partway through isn't whole, so it's left out."""
    if first_day is not None and first_day.day > 1:
        first_day = date.fromisoformat(f"{add_months(month_key(first_day), 1)}-01")
    return complete_months(today, first_day, count)


def by_month(rows: list[dict]) -> dict[str, int]:
    """Rows with posted_on and amount_cents, totaled per "YYYY-MM"."""
    totals: dict[str, int] = {}
    for row in rows:
        key = row["posted_on"][:7]
        totals[key] = totals.get(key, 0) + row["amount_cents"]
    return totals


def summary(amounts_by_month: dict[str, int], months: list[str], this_month: str) -> dict:
    """Every figure a summary sentence could want, so nothing downstream does
    arithmetic: each whole month's amount, the total, the average, the
    highest and lowest month, how the latest compares with the ones before
    it, and this month so far."""
    amounts = [amounts_by_month.get(key, 0) for key in months]
    facts = {
        "months": months,
        "amounts": amounts,
        "so_far_cents": amounts_by_month.get(this_month, 0),
    }
    if not months:
        return facts
    highest = max(range(len(months)), key=lambda i: amounts[i])
    lowest = min(range(len(months)), key=lambda i: amounts[i])
    facts.update(
        total_cents=sum(amounts),
        average_cents=round(sum(amounts) / len(amounts)),
        highest=(months[highest], amounts[highest]),
        lowest=(months[lowest], amounts[lowest]),
    )
    if len(months) > 1:
        usual = sum(amounts[:-1]) / (len(amounts) - 1)
        if amounts[-1] > usual * (1 + TREND_RATIO):
            facts["trend"] = "higher"
        elif amounts[-1] < usual * (1 - TREND_RATIO):
            facts["trend"] = "lower"
        else:
            facts["trend"] = "usual"
    return facts
