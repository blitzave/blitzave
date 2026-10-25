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
