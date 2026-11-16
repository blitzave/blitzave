import sqlite3


class TagNotFoundError(Exception):
    """No tag exists with the given id."""


class DuplicateTagError(Exception):
    """A tag with that name already exists for this user."""


def list_tags(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM tags ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


def create_tag(conn: sqlite3.Connection, user_id: int, name: str) -> dict:
    try:
        cursor = conn.execute(
            "INSERT INTO tags (user_id, name) VALUES (?, ?)", (user_id, name)
        )
    except sqlite3.IntegrityError as e:
        raise DuplicateTagError(f"a tag named {name!r} already exists") from e
    conn.commit()
    return {"id": cursor.lastrowid, "name": name}


def tag_transactions(
    conn: sqlite3.Connection, user_id: int, name: str, txn_ids: list[int]
) -> tuple[int, bool, list[int]]:
    """Puts one tag, found or made by name, on these rows (internal ids),
    keeping every tag they already have. One commit. Returns the tag's id,
    whether it was made just now, and the rows that gained it: all an undo
    needs."""
    row = conn.execute(
        "SELECT id FROM tags WHERE user_id = ? AND name = ?", (user_id, name)
    ).fetchone()
    created = row is None
    if created:
        tag_id = conn.execute(
            "INSERT INTO tags (user_id, name) VALUES (?, ?)", (user_id, name)
        ).lastrowid
    else:
        tag_id = row[0]
    added = [
        txn_id
        for txn_id in txn_ids
        if conn.execute(
            "INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
            (txn_id, tag_id),
        ).rowcount
    ]
    conn.commit()
    return tag_id, created, added


def untag_transactions(
    conn: sqlite3.Connection, tag_id: int, txn_ids: list[int], drop_tag: bool
) -> None:
    """Undoes tag_transactions: takes the tag off exactly those rows, and
    with drop_tag, removes the tag itself if nothing else uses it."""
    conn.executemany(
        "DELETE FROM transaction_tags WHERE tag_id = ? AND transaction_id = ?",
        [(tag_id, txn_id) for txn_id in txn_ids],
    )
    if drop_tag:
        conn.execute(
            "DELETE FROM tags WHERE id = ? "
            "AND NOT EXISTS (SELECT 1 FROM transaction_tags WHERE tag_id = ?)",
            (tag_id, tag_id),
        )
    conn.commit()


def monthly_totals(conn: sqlite3.Connection, tag_id: int, start: str, end: str) -> dict[str, int]:
    """A tag's net amount_cents per calendar month ("YYYY-MM") over activity
    rows (never a transfer leg) in the date range."""
    rows = conn.execute(
        """
        SELECT substr(a.posted_on, 1, 7), SUM(a.amount_cents)
        FROM activity a JOIN transaction_tags tt ON tt.transaction_id = a.id
        WHERE tt.tag_id = ? AND a.posted_on >= ? AND a.posted_on <= ?
        GROUP BY 1
        """,
        (tag_id, start, end),
    ).fetchall()
    return dict(rows)


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> None:
    exists = conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if exists is None:
        raise TagNotFoundError(f"no tag with id {tag_id}")
    conn.execute("DELETE FROM transaction_tags WHERE tag_id = ?", (tag_id,))
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()
