import sqlite3

import pytest

from blitzave.store import db

EXPECTED_TABLES = {
    "users",
    "accounts",
    "assets",
    "liabilities",
    "categories",
    "tags",
    "transactions",
    "transaction_tags",
    "transfers",
}


def test_migrate_creates_all_tables(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    db.migrate(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert EXPECTED_TABLES <= tables


def test_migrate_twice_is_a_noop(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    db.migrate(conn)
    version_once = conn.execute("PRAGMA user_version").fetchone()[0]

    db.migrate(conn)  # must not re-run 001_init.sql against existing tables
    version_twice = conn.execute("PRAGMA user_version").fetchone()[0]

    assert version_once == version_twice == 1


def test_connect_creates_home_dir_with_0700(tmp_path, monkeypatch):
    home = tmp_path / "BlitZaveHome"
    monkeypatch.setenv("BLITZAVE_HOME", str(home))

    conn = db.connect()
    conn.close()

    assert (home / "blitzave.db").exists()
    assert oct(home.stat().st_mode & 0o777) == oct(0o700)


def test_connect_turns_on_foreign_keys_and_wal(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))

    conn = db.connect()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


@pytest.fixture
def seeded_conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.execute("PRAGMA foreign_keys = ON")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO users (id, username, created_at) VALUES (1, 'rohit', 0)"
    )
    conn.execute(
        """
        INSERT INTO accounts (id, user_id, account_type, account_number, external_id)
        VALUES (1, 1, 'asset', '1111', 'acc-checking'),
               (2, 1, 'asset', '2222', 'acc-savings'),
               (3, 1, 'liability', '3333', 'acc-cc')
        """
    )
    conn.commit()
    yield conn
    conn.close()


def test_asset_row_must_match_account_type(seeded_conn):
    seeded_conn.execute(
        "INSERT INTO assets (account_id, bank_name, asset_type) VALUES (1, 'Chase', 'checking')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        seeded_conn.execute(
            "INSERT INTO assets (account_id, bank_name, asset_type) VALUES (3, 'Chase', 'checking')"
        )


def test_liability_row_must_match_account_type(seeded_conn):
    seeded_conn.execute(
        "INSERT INTO liabilities (account_id, issuer_name, liability_type) VALUES (3, 'Amex', 'credit_card')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        seeded_conn.execute(
            "INSERT INTO liabilities (account_id, issuer_name, liability_type) VALUES (1, 'Amex', 'credit_card')"
        )


def test_transaction_sign_must_match_type(seeded_conn):
    seeded_conn.execute(
        """
        INSERT INTO transactions
            (id, user_id, account_id, external_id, amount_cents, transaction_type, posted_on, description, created_at)
        VALUES (1, 1, 1, 'tx-1', -500, 'debit', '2026-10-01', 'coffee', 0)
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        seeded_conn.execute(
            """
            INSERT INTO transactions
                (id, user_id, account_id, external_id, amount_cents, transaction_type, posted_on, description, created_at)
            VALUES (2, 1, 1, 'tx-2', 500, 'debit', '2026-10-01', 'bad sign', 0)
            """
        )


def test_transfer_must_balance_and_leaves_activity_empty(seeded_conn):
    seeded_conn.executescript(
        """
        INSERT INTO transactions
            (id, user_id, account_id, external_id, amount_cents, transaction_type, posted_on, description, created_at)
        VALUES
            (1, 1, 1, 'tx-debit', -5000, 'debit', '2026-10-01', 'to savings', 0),
            (2, 1, 2, 'tx-credit', 5000, 'credit', '2026-10-01', 'from checking', 0);
        """
    )
    seeded_conn.execute(
        """
        INSERT INTO transfers
            (id, user_id, debit_transaction_id, credit_transaction_id, transfer_amount_cents, transfer_date, matched_by, created_at)
        VALUES (1, 1, 1, 2, 5000, '2026-10-01', 'rule', 0)
        """
    )
    assert seeded_conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 0


def test_transfer_rejects_mismatched_amount(seeded_conn):
    seeded_conn.executescript(
        """
        INSERT INTO transactions
            (id, user_id, account_id, external_id, amount_cents, transaction_type, posted_on, description, created_at)
        VALUES
            (1, 1, 1, 'tx-debit', -2000, 'debit', '2026-10-02', 'to savings', 0),
            (2, 1, 2, 'tx-credit', 2000, 'credit', '2026-10-02', 'from checking', 0);
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        seeded_conn.execute(
            """
            INSERT INTO transfers
                (id, user_id, debit_transaction_id, credit_transaction_id, transfer_amount_cents, transfer_date, matched_by, created_at)
            VALUES (1, 1, 1, 2, 9999, '2026-10-02', 'rule', 0)
            """
        )
