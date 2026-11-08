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


# "Was that the PS5?" (TODOS.md P0.3). When money leaves a goal account and
# the amount is close to what a funded goal needed, BlitZave asks once whether
# that was the goal being bought. Only recent withdrawals count, so a goal set
# up today isn't asked about money that left months ago.
QUESTION_WINDOW_DAYS = 30

# How close counts (maintainer call, 2026-11-08: accept near-matches). Within
# 10% of the goal's amount either way, so a $500 PS5 still matches at $541.25
# with sales tax, or at $479.99 on sale. A wrong guess costs one tap on "It was
# something else".
QUESTION_NEAR_SHARE = 0.10


def _near(spent: int, amount: int) -> bool:
    return abs(spent - amount) <= amount * QUESTION_NEAR_SHARE


def goal_questions(homes: list[dict], withdrawals: list[dict]) -> list[dict]:
    """Which recent withdrawals to ask about, and which goal each might be.

    homes: savings_by_account(...)["accounts"]. withdrawals: unanswered
    debits from goal accounts in the window, newest first, each with
    transaction_id, account_id, posted_on and amount_cents (negative).

    The balance already reflects the withdrawal, so a goal it paid for no
    longer looks funded. Each withdrawal is checked against the split as it
    was just before it left: the money added back. A goal that was funded
    then, whose amount is near what left, is the question; when more than one
    is near, the closest (then the one higher in line). One question per goal,
    the newest withdrawal first."""
    by_account = {home["id"]: home for home in homes}
    questions = []
    asked = set()
    for withdrawal in withdrawals:
        home = by_account.get(withdrawal["account_id"])
        if home is None or not home["goals"]:
            continue
        spent = -withdrawal["amount_cents"]
        before, _ = fill((home["balance_cents"] or 0) + spent, home["goals"])
        near = [
            g
            for g in before
            if g["state"] == "done" and _near(spent, g["budget_cents"]) and g["id"] not in asked
        ]
        if not near:
            continue
        # min() keeps the first of equals, so a tie goes to the goal higher in line.
        goal = min(near, key=lambda g: abs(spent - g["budget_cents"]))
        asked.add(goal["id"])
        questions.append(
            {
                "transaction_id": withdrawal["transaction_id"],
                "account_id": home["id"],
                "account_name": home["name"],
                "posted_on": withdrawal["posted_on"],
                "amount_cents": spent,
                "goal_id": goal["id"],
                "goal_name": goal["name"],
            }
        )
    return questions
