import json
import sqlite3
import time


class CategoryNotFoundError(Exception):
    """No category exists with the given id."""


class DuplicateCategoryError(Exception):
    """A category with that name already exists for this user."""


class CategoryInUseError(Exception):
    """The category is referenced by at least one transaction's split map."""


class UnknownAccountError(Exception):
    """A goal names an account that doesn't exist."""


class GoalRuleError(Exception):
    """The database refused a goal's home (migration 009's triggers)."""


class GoalOrderError(Exception):
    """A new order doesn't name each of the account's active goals once."""


class GoalQuestionError(Exception):
    """A "was that the goal?" answer that no longer fits: the withdrawal was
    already answered, isn't from the goal's account, or doesn't match it."""


# A category's amount and its schedule travel together (migration 008):
#   once       budget_cents by target_on
#   recurring  budget_cents every period_months, until ends_on if set
# The API checks a plan's shape; the database's triggers refuse a bad one too.
PLAN_FIELDS = ("budget_cents", "schedule", "period_months", "target_on", "ends_on")
NO_PLAN = dict.fromkeys(PLAN_FIELDS)

# A goal is a savings category with a level and a home (migration 009):
#   critical  has a targeted savings account to itself
#   regular   shares one; the account's money fills goals in fill_order
# completed_on marks a goal that's been bought. Outside this module,
# account_id is the account's public id (SimpleFIN's), never the row id.
GOAL_FIELDS = ("goal_level", "account_id", "fill_order", "completed_on")
NO_GOAL = dict.fromkeys(GOAL_FIELDS)

# The messages migration 009's triggers raise.
_GOAL_RULES = (
    "a goal is a savings category",
    "goals live only in targeted savings accounts",
    "a critical goal needs a targeted savings account to itself",
)

_SELECT = (
    "SELECT c.id, c.name, c.kind, "
    + ", ".join(f"c.{f}" for f in PLAN_FIELDS)
    + ", c.goal_level, a.external_id, c.fill_order, c.completed_on"
    " FROM categories c LEFT JOIN accounts a ON a.id = c.account_id"
)


def _row_to_dict(row: tuple) -> dict:
    return dict(zip(("id", "name", "kind", *PLAN_FIELDS, *GOAL_FIELDS), row))


def _is_duplicate_name(error: sqlite3.IntegrityError) -> bool:
    # A bad plan also raises IntegrityError (from a trigger or CHECK), and it
    # must not be reported as a duplicate name.
    return "UNIQUE constraint failed" in str(error)


def _raise_refused(error: sqlite3.IntegrityError, name: str) -> None:
    """Turn a refused write into the error the caller can act on."""
    if _is_duplicate_name(error):
        raise DuplicateCategoryError(f"a category named {name!r} already exists") from error
    if str(error).startswith(_GOAL_RULES):
        raise GoalRuleError(str(error)) from error
    raise error


def _account_row_id(conn: sqlite3.Connection, external_id: str) -> int:
    row = conn.execute(
        "SELECT id FROM accounts WHERE source = 'simplefin' AND external_id = ?",
        (external_id,),
    ).fetchone()
    if row is None:
        raise UnknownAccountError(f"no account with id {external_id}")
    return row[0]


def _end_of_line(conn: sqlite3.Connection, account_row_id: int, category_id: int | None) -> int:
    """The place after the last active goal in an account."""
    return conn.execute(
        "SELECT COALESCE(MAX(fill_order), 0) + 1 FROM categories"
        " WHERE account_id = ? AND completed_on IS NULL AND id IS NOT ?",
        (account_row_id, category_id),
    ).fetchone()[0]


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(f"{_SELECT} ORDER BY c.name COLLATE NOCASE").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_category(conn: sqlite3.Connection, category_id: int) -> dict:
    row = conn.execute(f"{_SELECT} WHERE c.id = ?", (category_id,)).fetchone()
    if row is None:
        raise CategoryNotFoundError(f"no category with id {category_id}")
    return _row_to_dict(row)


def create_category(
    conn: sqlite3.Connection,
    user_id: int,
    name: str,
    kind: str,
    plan: dict | None = None,
    goal: dict | None = None,
) -> dict:
    """plan holds every key in PLAN_FIELDS; None means no amount. goal holds
    goal_level and account_id; None means it isn't a goal. A new goal joins
    the end of its account's line."""
    plan = plan or NO_PLAN
    columns = dict(NO_GOAL)
    if goal is not None:
        account = _account_row_id(conn, goal["account_id"])
        columns.update(
            goal_level=goal["goal_level"],
            account_id=account,
            fill_order=_end_of_line(conn, account, None),
        )
    try:
        cursor = conn.execute(
            "INSERT INTO categories (user_id, name, kind, "
            + ", ".join(PLAN_FIELDS + GOAL_FIELDS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, name, kind,
                *(plan[f] for f in PLAN_FIELDS),
                *(columns[f] for f in GOAL_FIELDS),
            ),
        )
    except sqlite3.IntegrityError as e:
        _raise_refused(e, name)
    conn.commit()
    return get_category(conn, cursor.lastrowid)


def update_category(
    conn: sqlite3.Connection,
    category_id: int,
    name: str | None,
    kind: str | None,
    *,
    plan: dict | None = None,
    goal: dict | None = None,
) -> dict:
    """None leaves a field as it is. A plan replaces all of PLAN_FIELDS at
    once, so the amount and its schedule can never disagree.

    goal is the whole goal as it should be: goal_level, account_id and
    completed_on (account_id None: no longer a goal). Its place in line is
    kept while it stays in the same account; a goal that moves, or comes back
    from completed, joins the end of the line."""
    current = get_category(conn, category_id)
    values = {
        "name": name if name is not None else current["name"],
        "kind": kind if kind is not None else current["kind"],
        **(plan if plan is not None else {f: current[f] for f in PLAN_FIELDS}),
    }
    if goal is not None:
        if goal["account_id"] is None:
            # Passed on, not dropped: a completed_on here is refused below.
            values.update(NO_GOAL, completed_on=goal["completed_on"])
        else:
            account = _account_row_id(conn, goal["account_id"])
            reopened = current["completed_on"] is not None and goal["completed_on"] is None
            same_place = current["account_id"] == goal["account_id"] and not reopened
            values.update(
                goal_level=goal["goal_level"],
                account_id=account,
                fill_order=(
                    current["fill_order"]
                    if same_place
                    else _end_of_line(conn, account, category_id)
                ),
                completed_on=goal["completed_on"],
            )
    try:
        conn.execute(
            "UPDATE categories SET "
            + ", ".join(f"{column} = ?" for column in values)
            + " WHERE id = ?",
            (*values.values(), category_id),
        )
    except sqlite3.IntegrityError as e:
        _raise_refused(e, values["name"])
    conn.commit()
    return get_category(conn, category_id)


def reorder_goals(conn: sqlite3.Connection, account_id: str, goal_ids: list[int]) -> None:
    """Line up an account's active goals in the order given, top first.
    account_id is the account's public id. The order must name each active
    goal in the account exactly once, so a page that's out of date can't
    drop a goal or count one twice."""
    account = _account_row_id(conn, account_id)
    active = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM categories WHERE account_id = ? AND completed_on IS NULL",
            (account,),
        )
    }
    if len(goal_ids) != len(active) or set(goal_ids) != active:
        raise GoalOrderError(
            "the new order must list every goal in this account once; refresh and try again"
        )
    conn.executemany(
        "UPDATE categories SET fill_order = ? WHERE id = ?",
        [(place, goal_id) for place, goal_id in enumerate(goal_ids, start=1)],
    )
    conn.commit()


def goal_withdrawals(conn: sqlite3.Connection, since: str) -> list[dict]:
    """Money that left a goal account on or after `since` and hasn't been
    asked about yet, newest first. Ids are public: SimpleFIN's transaction
    and account ids."""
    rows = conn.execute(
        """
        SELECT t.external_id, a.external_id, t.posted_on, t.amount_cents
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        JOIN assets s ON s.account_id = a.id AND s.asset_type = 'targeted'
        WHERE t.amount_cents < 0 AND t.posted_on >= ?
          AND json_extract(t.meta, '$.goal_question') IS NULL
        ORDER BY t.posted_on DESC, t.id DESC
        """,
        (since,),
    ).fetchall()
    return [
        {"transaction_id": r[0], "account_id": r[1], "posted_on": r[2], "amount_cents": r[3]}
        for r in rows
    ]


def answer_goal_question(
    conn: sqlite3.Connection, transaction_id: str, goal_id: int, bought: bool
) -> None:
    """Record the answer to "was that the goal?" on the withdrawal, so it's
    never asked again. Yes also completes the goal, on the day the money
    left. Both happen together or not at all."""
    rows = conn.execute(
        "SELECT id, account_id, amount_cents, posted_on,"
        " json_extract(meta, '$.goal_question') IS NOT NULL"
        " FROM transactions WHERE external_id = ?",
        (transaction_id,),
    ).fetchall()
    if len(rows) != 1:
        raise GoalQuestionError(f"no single transaction with id {transaction_id}")
    tx_id, account, amount, posted_on, answered = rows[0]
    goal = conn.execute(
        "SELECT account_id, budget_cents, completed_on FROM categories"
        " WHERE id = ? AND goal_level IS NOT NULL",
        (goal_id,),
    ).fetchone()
    if goal is None:
        raise CategoryNotFoundError(f"no goal with id {goal_id}")
    if answered:
        raise GoalQuestionError("that withdrawal was already answered")
    if amount >= 0 or goal[0] != account or goal[1] != -amount or goal[2] is not None:
        raise GoalQuestionError("that withdrawal doesn't match this goal anymore")
    answer = json.dumps({"goal_id": goal_id, "answer": "bought" if bought else "other"})
    try:
        conn.execute(
            "UPDATE transactions SET meta = json_set(meta, '$.goal_question', json(?))"
            " WHERE id = ?",
            (answer, tx_id),
        )
        if bought:
            conn.execute(
                "UPDATE categories SET completed_on = ? WHERE id = ?", (posted_on, goal_id)
            )
    except sqlite3.Error:
        conn.rollback()
        raise
    conn.commit()


def delete_category(conn: sqlite3.Connection, category_id: int) -> None:
    exists = conn.execute(
        "SELECT 1 FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    if exists is None:
        raise CategoryNotFoundError(f"no category with id {category_id}")

    in_use = conn.execute(
        """
        SELECT 1 FROM transactions t, json_each(t.categories) je
        WHERE CAST(je.key AS INTEGER) = ? LIMIT 1
        """,
        (category_id,),
    ).fetchone()
    if in_use is not None:
        raise CategoryInUseError(
            "this category is still used by transactions — recategorize them first"
        )

    conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()


def upsert_rule(
    conn: sqlite3.Connection,
    user_id: int,
    match_key: str,
    description: str,
    category_id: int,
) -> None:
    """Remember "transactions from the merchant match_key names go in this
    category" (match_key and matching live in refine/categorize.py). A second
    rule for the same key replaces the first.
    Doesn't commit: the caller saves the rule together with the transactions
    it rewrote, so the two can never disagree."""
    conn.execute(
        """
        INSERT INTO category_rules (user_id, match_key, description, category_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (user_id, match_key) DO UPDATE SET
            description = excluded.description,
            category_id = excluded.category_id,
            created_at = excluded.created_at
        """,
        (user_id, match_key, description, category_id, int(time.time())),
    )


def list_rules(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Every rule the user has made, oldest first — so a caller breaking a
    tie by taking the first rule gets the same answer every time."""
    rows = conn.execute(
        "SELECT id, match_key, category_id FROM category_rules WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()
    return [{"id": r[0], "match_key": r[1], "category_id": r[2]} for r in rows]


def apply_rules(conn: sqlite3.Connection, assignments: dict[int, int]) -> int:
    """Write {transaction_id: category_id} pairs the caller matched against
    the rules. Still only touches activity rows nobody has categorized yet,
    even if the caller asks for more, so re-running is safe and user
    corrections always win. Returns how many rows it categorized."""
    categorized = 0
    for txn_id, category_id in assignments.items():
        cursor = conn.execute(
            """
            UPDATE transactions SET categories = ?, categories_set_by = 'rule'
            WHERE id = ? AND categories_set_by IS NULL
              AND id IN (SELECT id FROM activity)
            """,
            (json.dumps({str(category_id): 1.0}), txn_id),
        )
        categorized += cursor.rowcount
    conn.commit()
    return categorized


def summary(conn: sqlite3.Connection, start: str, end: str) -> tuple[list[dict], int]:
    """Per-category totals over activity rows (never transfer legs) in the
    date range, weighted by each transaction's split fractions, plus the
    total of rows with no category at all. Totals are signed cents — the
    engine reports what happened; it doesn't editorialize the sign."""
    totals: dict[int, int] = {}
    for category_id, total_cents in conn.execute(
        """
        SELECT CAST(je.key AS INTEGER),
               CAST(ROUND(SUM(a.amount_cents * je.value)) AS INTEGER)
        FROM activity a, json_each(a.categories) je
        WHERE a.posted_on >= ? AND a.posted_on <= ?
        GROUP BY 1
        """,
        (start, end),
    ):
        totals[category_id] = total_cents

    uncategorized = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) FROM activity
        WHERE categories = '{}' AND posted_on >= ? AND posted_on <= ?
        """,
        (start, end),
    ).fetchone()[0]

    categories = [
        {**c, "total_cents": totals.get(c["id"], 0)} for c in list_categories(conn)
    ]
    return categories, uncategorized
