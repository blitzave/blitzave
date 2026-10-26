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


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> None:
    exists = conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if exists is None:
        raise TagNotFoundError(f"no tag with id {tag_id}")
    conn.execute("DELETE FROM transaction_tags WHERE tag_id = ?", (tag_id,))
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()
