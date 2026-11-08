"""The Savings arc's facts (P0.4–P0.6): how much was set aside, whether it
went first, and when each goal gets there. Facts, never sentences; the
words live in copy/.

Maintainer calls (2026-11-08):
- Set aside means money into goal accounts (asset_type 'targeted') minus money
  out. Savings accounts are a buffer for this month's bills, so they don't
  count. Interest doesn't either: it isn't something the user did.
- "Went first" stays flexible. Both facts are reported, and WENT_FIRST_RULE
  picks which one decides.
- A goal's arrival is a range: one end at the last 3 complete months' pace,
  the other at the last 6. A bad month moves one end a little, never the
  whole range.

Pure: rows in, facts out. Every amount is integer cents.
"""

from calendar import monthrange
from datetime import date
from math import ceil
from statistics import median

# Credits with these words in the description are interest, not saving.
INTEREST_WORDS = ("interest", "dividend")

# "before_half_spending": half of the month's set-aside money had moved
#     before half of the month's spending had happened.
# "by_day": something moved into a goal account by WENT_FIRST_BY_DAY.
WENT_FIRST_RULE = "before_half_spending"
WENT_FIRST_BY_DAY = 7

# A month's spending this far above the usual pace for the same day reads as
# a heavier start (Home rule 3).
HEAVIER_RATIO = 1.25


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def add_months(key: str, months: int) -> str:
    year, month = int(key[:4]), int(key[5:])
    index = year * 12 + month - 1 + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def complete_months(today: date, first_day: date | None, count: int) -> list[str]:
    """The last `count` whole months before this one, oldest first, never
    reaching back before the data starts."""
    if first_day is None:
        return []
    keys = []
    key = add_months(month_key(today), -1)
    while len(keys) < count and key >= month_key(first_day):
        keys.append(key)
        key = add_months(key, -1)
    return list(reversed(keys))


def is_interest(description: str) -> bool:
    text = description.lower()
    return any(word in text for word in INTEREST_WORDS)


# --- P0.4 savings flow ---------------------------------------------------------


def monthly_flows(rows: list[dict]) -> dict[str, dict]:
    """Money in and out of goal accounts, per month.

    rows: transactions in goal accounts, each with account_id, posted_on,
    amount_cents and description. Per "YYYY-MM": in_cents (credits that
    aren't interest), out_cents (debits, as a positive number), net_cents,
    by_account (each account's net) and deposits (date, cents) in order."""
    flows: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r["posted_on"]):
        month = flows.setdefault(
            row["posted_on"][:7],
            {"in_cents": 0, "out_cents": 0, "net_cents": 0, "by_account": {}, "deposits": []},
        )
        amount = row["amount_cents"]
        if amount > 0 and is_interest(row["description"]):
            continue
        if amount > 0:
            month["in_cents"] += amount
            month["deposits"].append((row["posted_on"], amount))
        else:
            month["out_cents"] -= amount
        month["net_cents"] += amount
        month["by_account"][row["account_id"]] = (
            month["by_account"].get(row["account_id"], 0) + amount
        )
    return flows


def _empty_month() -> dict:
    return {"in_cents": 0, "out_cents": 0, "net_cents": 0, "by_account": {}, "deposits": []}


# --- P0.5 pay yourself first ----------------------------------------------------


def _ordinal_day(iso: str) -> int:
    return int(iso[8:10])


def pay_yourself_first(
    flows: dict[str, dict],
    spending: list[dict],
    today: date,
    first_day: date | None,
    rule: str = WENT_FIRST_RULE,
) -> dict:
    """This month's facts, and what's usual.

    spending: debits that count as spending (posted_on, amount_cents as a
    positive number), this month and the months before it."""
    key = month_key(today)
    month = flows.get(key, _empty_month())
    deposits = month["deposits"]
    in_cents = month["in_cents"]

    first_in_on = deposits[0][0] if deposits else None
    half_in_on = None
    running = 0
    for posted_on, cents in deposits:
        running += cents
        if running * 2 >= in_cents:
            half_in_on = posted_on
            break

    this_month = [s for s in spending if s["posted_on"][:7] == key]
    spent = sum(s["amount_cents"] for s in this_month)
    spent_before = (
        sum(s["amount_cents"] for s in this_month if s["posted_on"] < half_in_on)
        if half_in_on
        else spent
    )
    share_before = spent_before / spent if spent else 0.0

    if rule == "by_day":
        went_first = first_in_on is not None and _ordinal_day(first_in_on) <= WENT_FIRST_BY_DAY
    else:
        went_first = in_cents > 0 and share_before < 0.5

    past = complete_months(today, first_day, 3)
    past_in = [flows.get(k, _empty_month())["in_cents"] for k in past]
    first_days = [
        _ordinal_day(flows[k]["deposits"][0][0]) for k in past if flows.get(k, {}).get("deposits")
    ]
    return {
        "month": key,
        "in_cents": in_cents,
        "out_cents": month["out_cents"],
        "net_cents": month["net_cents"],
        "first_in_on": first_in_on,
        "half_in_on": half_in_on,
        "single_day": len({d for d, _ in deposits}) == 1,
        "spent_cents": spent,
        "spending_share_before_half": round(share_before, 3),
        "went_first": went_first,
        "rule": rule,
        "usual_in_cents": round(sum(past_in) / len(past_in)) if past_in else 0,
        "usual_day": round(median(first_days)) if first_days else None,
    }


def spending_pace(spending: list[dict], today: date, first_day: date | None) -> dict:
    """This month's spending so far against the usual for the same point in
    the month: the average of the last 3 whole months up to the same day."""
    key = month_key(today)
    this_month = [s for s in spending if s["posted_on"][:7] == key]
    so_far = sum(s["amount_cents"] for s in this_month)
    past = complete_months(today, first_day, 3)
    usual_parts = []
    for k in past:
        year, month = int(k[:4]), int(k[5:])
        cutoff = f"{k}-{min(today.day, monthrange(year, month)[1]):02d}"
        usual_parts.append(
            sum(s["amount_cents"] for s in spending if s["posted_on"][:7] == k
                and s["posted_on"] <= cutoff)
        )
    usual = round(sum(usual_parts) / len(usual_parts)) if usual_parts else 0
    largest = max(this_month, key=lambda s: s["amount_cents"], default=None)
    return {
        "spent_cents": so_far,
        "usual_to_date_cents": usual,
        "heavier": usual > 0 and so_far > usual * HEAVIER_RATIO,
        "lighter": usual > 0 and so_far < usual * (2 - HEAVIER_RATIO),
        "largest": largest,
        # "Most of it is one charge": the biggest charge covers at least half
        # of what's above usual.
        "one_charge_explains_it": bool(
            largest and usual and largest["amount_cents"] * 2 >= so_far - usual
        ),
    }


# --- P0.6 goal trajectory -------------------------------------------------------


def account_pace(flows: dict[str, dict], account_id: str, today: date,
                 first_day: date | None) -> dict:
    """An account's average net per month over the last 3 and last 6 whole
    months (fewer if the data is younger)."""
    def average(keys: list[str]) -> int | None:
        if not keys:
            return None
        nets = [flows.get(k, _empty_month())["by_account"].get(account_id, 0) for k in keys]
        return round(sum(nets) / len(nets))

    three = complete_months(today, first_day, 3)
    six = complete_months(today, first_day, 6)
    return {"avg3_cents": average(three), "avg6_cents": average(six), "months": len(six)}


def _arrival(need: int, rate: int | None, now: str) -> str | None:
    """The month a goal needing `need` more arrives at `rate` a month. None
    when nothing is coming in at that pace: there's no date to give."""
    if need <= 0:
        return now
    if rate is None or rate <= 0:
        return None
    return add_months(now, ceil(need / rate))


def goal_trajectories(home: dict, pace: dict, today: date) -> dict[int, dict]:
    """When each of an account's active goals gets there, by goal id.

    home: one account from refine.goals.savings_by_account. A shared
    account's money fills its goals top first, so a goal arrives once the
    balance covers it and every goal above it."""
    avg3, avg6 = pace["avg3_cents"], pace["avg6_cents"]
    paces = [p for p in (avg3, avg6) if p is not None]
    fast = max(paces) if paces else None
    slow = min(paces) if paces else None
    balance = max(home["balance_cents"] or 0, 0)
    now = month_key(today)

    facts = {}
    covered = 0
    for goal in home["goals"]:
        if goal["goal_level"] == "critical":
            need = goal["budget_cents"] - balance
        else:
            covered += goal["budget_cents"]
            need = covered - balance

        target_month = goal["target_on"][:7] if goal.get("target_on") else None
        soonest, latest = _arrival(need, fast, now), _arrival(need, slow, now)
        facts[goal["id"]] = {
            "need_cents": max(need, 0),
            "rate_cents": avg3 if avg3 is not None else avg6,
            "eta_from": soonest,
            "eta_to": latest,
            "history_months": pace["months"],
            "target_month": target_month,
            # On pace: even the slower end lands by the target month.
            "on_pace": bool(target_month and latest and latest <= target_month),
        }
    return facts
