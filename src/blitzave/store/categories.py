import json
import sqlite3
import time


class CategoryNotFoundError(Exception):
    """No category exists with the given id."""


class DuplicateCategoryError(Exception):
    """A category with that name already exists for this user."""


class CategoryInUseError(Exception):
    """The category is referenced by at least one transaction's split map."""


# A category's amount and its schedule travel together (migration 008):
#   once       budget_cents by target_on
#   recurring  budget_cents every period_months, until ends_on if set
# The API checks a plan's shape; the database's triggers refuse a bad one too.
PLAN_FIELDS = ("budget_cents", "schedule", "period_months", "target_on", "ends_on")
NO_PLAN = dict.fromkeys(PLAN_FIELDS)

_SELECT = (
    "SELECT c.id, c.name, c.kind, "
    + ", ".join(f"c.{f}" for f in PLAN_FIELDS)
    + " FROM categories c"
)


def _row_to_dict(row: tuple) -> dict:
    return dict(zip(("id", "name", "kind", *PLAN_FIELDS), row))


def _is_duplicate_name(error: sqlite3.IntegrityError) -> bool:
    # A bad plan also raises IntegrityError (from a trigger or CHECK), and it
    # must not be reported as a duplicate name.
    return "UNIQUE constraint failed" in str(error)


def _raise_refused(error: sqlite3.IntegrityError, name: str) -> None:
    """Turn a refused write into the error the caller can act on."""
    if _is_duplicate_name(error):
        raise DuplicateCategoryError(f"a category named {name!r} already exists") from error
    raise error


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
) -> dict:
    """plan holds every key in PLAN_FIELDS; None means no amount."""
    plan = plan or NO_PLAN
    try:
        cursor = conn.execute(
            "INSERT INTO categories (user_id, name, kind, "
            + ", ".join(PLAN_FIELDS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, name, kind, *(plan[f] for f in PLAN_FIELDS)),
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
) -> dict:
    """None leaves a field as it is. A plan replaces all of PLAN_FIELDS at
    once, so the amount and its schedule can never disagree."""
    current = get_category(conn, category_id)
    values = {
        "name": name if name is not None else current["name"],
        "kind": kind if kind is not None else current["kind"],
        **(plan if plan is not None else {f: current[f] for f in PLAN_FIELDS}),
    }
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
