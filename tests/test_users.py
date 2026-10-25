import sqlite3

from blitzave.store import db, users


def test_get_or_create_user_creates_a_row(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    db.migrate(conn)

    user_id = users.get_or_create_user(conn, username="rohit")

    row = conn.execute(
        "SELECT username FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    assert row == ("rohit",)


def test_get_or_create_user_is_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    db.migrate(conn)

    first_id = users.get_or_create_user(conn, username="rohit")
    second_id = users.get_or_create_user(conn, username="someone-else")

    assert first_id == second_id
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert count == 1


def test_simplefin_hash_round_trips(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    db.migrate(conn)
    user_id = users.get_or_create_user(conn)

    assert users.get_simplefin_hash(conn, user_id) is None

    users.set_simplefin_hash(conn, user_id, "abc123")
    assert users.get_simplefin_hash(conn, user_id) == "abc123"
