import json
import sqlite3
import time


def get_or_create_user(conn: sqlite3.Connection, username: str = "me") -> int:
    row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if row is not None:
        return row[0]

    cursor = conn.execute(
        "INSERT INTO users (username, created_at) VALUES (?, ?)",
        (username, int(time.time())),
    )
    conn.commit()
    return cursor.lastrowid


def set_simplefin_hash(conn: sqlite3.Connection, user_id: int, simplefin_hash: str) -> None:
    conn.execute(
        "UPDATE users SET simplefin_hash = ? WHERE id = ?", (simplefin_hash, user_id)
    )
    conn.commit()


def get_simplefin_hash(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute(
        "SELECT simplefin_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else None


def get_meta(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT meta FROM users WHERE id = ?", (user_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def update_meta(conn: sqlite3.Connection, user_id: int, changes: dict) -> None:
    """Merge changes into users.meta, leaving every other key alone. It's a
    JSON merge patch, so a None value removes that key."""
    conn.execute(
        "UPDATE users SET meta = json_patch(meta, ?) WHERE id = ?",
        (json.dumps(changes), user_id),
    )
    conn.commit()
