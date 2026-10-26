import json
import sqlite3
import time


class CategoryNotFoundError(Exception):
    """No category exists with the given id."""


class DuplicateCategoryError(Exception):
    """A category with that name already exists for this user."""


class CategoryInUseError(Exception):
    """The category is referenced by at least one transaction's split map."""


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, kind, budget_cents FROM categories ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "kind": r[2], "budget_cents": r[3]} for r in rows
    ]


def create_category(
    conn: sqlite3.Connection,
    user_id: int,
    name: str,
    kind: str,
    budget_cents: int | None = None,
) -> dict:
    try:
        cursor = conn.execute(
            "INSERT INTO categories (user_id, name, kind, budget_cents) VALUES (?, ?, ?, ?)",
            (user_id, name, kind, budget_cents),
        )
    except sqlite3.IntegrityError as e:
        raise DuplicateCategoryError(f"a category named {name!r} already exists") from e
    conn.commit()
    return {
        "id": cursor.lastrowid,
        "name": name,
        "kind": kind,
        "budget_cents": budget_cents,
    }


def update_category(
    conn: sqlite3.Connection,
    category_id: int,
    name: str | None,
    kind: str | None,
    *,
    budget_cents: int | None = None,
    budget_provided: bool = False,
) -> dict:
    row = conn.execute(
        "SELECT name, kind, budget_cents FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    if row is None:
        raise CategoryNotFoundError(f"no category with id {category_id}")
    new_name = name if name is not None else row[0]
    new_kind = kind if kind is not None else row[1]
    new_budget = budget_cents if budget_provided else row[2]
    try:
        conn.execute(
            "UPDATE categories SET name = ?, kind = ?, budget_cents = ? WHERE id = ?",
            (new_name, new_kind, new_budget, category_id),
        )
    except sqlite3.IntegrityError as e:
        raise DuplicateCategoryError(
            f"a category named {new_name!r} already exists"
        ) from e
    conn.commit()
    return {
        "id": category_id,
        "name": new_name,
        "kind": new_kind,
        "budget_cents": new_budget,
    }


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
