import sqlite3


class CategoryNotFoundError(Exception):
    """No category exists with the given id."""


class DuplicateCategoryError(Exception):
    """A category with that name already exists for this user."""


class CategoryInUseError(Exception):
    """The category is referenced by at least one transaction's split map."""


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, kind FROM categories ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [{"id": r[0], "name": r[1], "kind": r[2]} for r in rows]


def create_category(conn: sqlite3.Connection, user_id: int, name: str, kind: str) -> dict:
    try:
        cursor = conn.execute(
            "INSERT INTO categories (user_id, name, kind) VALUES (?, ?, ?)",
            (user_id, name, kind),
        )
    except sqlite3.IntegrityError as e:
        raise DuplicateCategoryError(f"a category named {name!r} already exists") from e
    conn.commit()
    return {"id": cursor.lastrowid, "name": name, "kind": kind}


def update_category(
    conn: sqlite3.Connection, category_id: int, name: str | None, kind: str | None
) -> dict:
    row = conn.execute(
        "SELECT name, kind FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    if row is None:
        raise CategoryNotFoundError(f"no category with id {category_id}")
    new_name = name if name is not None else row[0]
    new_kind = kind if kind is not None else row[1]
    try:
        conn.execute(
            "UPDATE categories SET name = ?, kind = ? WHERE id = ?",
            (new_name, new_kind, category_id),
        )
    except sqlite3.IntegrityError as e:
        raise DuplicateCategoryError(f"a category named {new_name!r} already exists") from e
    conn.commit()
    return {"id": category_id, "name": new_name, "kind": new_kind}


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
