"""How a targeted savings account's money is shared by its goals.

The split comes from the account's latest balance, not deposit by deposit
(TODOS.md P0.3). The money fills the account's active goals in fill_order,
top first, each up to its amount. Whatever is left once every goal is full
stays with the account, unassigned. Money that leaves the account lowers the
balance, so it comes off the bottom of the line.

A critical goal is alone in its account, so it has the whole balance, even
past its amount (maintainer call, 2026-11-07): an emergency fund of $9,000
in an account holding $10,000 has $10,000, 111.1%.

A goal's state is a fact, never a grade:
  backlog      nothing has reached it yet
  in_progress  part of its amount is there
  done         its whole amount is there
  completed    bought (completed_on is stored); it no longer shares the money
"""

ACCOUNT_FIELDS = ("id", "name", "bank_name", "account_number", "balance_cents", "balance_as_of")


def fill(balance_cents: int, goals: list[dict]) -> tuple[list[dict], int]:
    """Fill goals, already in fill_order, from a balance. Returns each goal
    with saved_cents, to_go_cents (what it still needs), saved_percent and
    state added, and what's left over."""
    left = max(balance_cents, 0)
    filled = []
    for goal in goals:
        amount = goal["budget_cents"]
        saved = left if goal["goal_level"] == "critical" else min(left, amount)
        left -= saved
        if saved == 0:
            state = "backlog"
        elif saved < amount:
            state = "in_progress"
        else:
            state = "done"
        filled.append(
            {
                **goal,
                "saved_cents": saved,
                "to_go_cents": max(amount - saved, 0),
                "saved_percent": round(saved * 100 / amount, 1),
                "state": state,
            }
        )
    return filled, left


def savings_by_account(accounts: list[dict], categories: list[dict]) -> dict:
    """The Savings page's facts. Every targeted savings account, including an
    empty one (a new critical goal needs one), with its active goals filled in
    line, and what they hold between them. Then the completed goals, most
    recent first.

    accounts as store.accounts.list_accounts gives them; categories as
    store.categories.list_categories does (a goal's account_id is the
    account's public id, the same as the account's id)."""
    homes = []
    for account in accounts:
        if account["asset_type"] != "targeted":
            continue
        line = sorted(
            (
                c
                for c in categories
                if c["account_id"] == account["id"] and c["completed_on"] is None
            ),
            key=lambda goal: goal["fill_order"],
        )
        # An account that hasn't synced a balance yet has nothing to share.
        goals, unassigned = fill(account["balance_cents"] or 0, line)
        homes.append(
            {
                **{f: account[f] for f in ACCOUNT_FIELDS},
                "goals": goals,
                "unassigned_cents": unassigned,
            }
        )
    completed = sorted(
        ({**c, "state": "completed"} for c in categories if c["completed_on"] is not None),
        key=lambda goal: goal["completed_on"],
        reverse=True,
    )
    total = sum(home["balance_cents"] or 0 for home in homes)
    return {"total_cents": total, "accounts": homes, "completed": completed}
