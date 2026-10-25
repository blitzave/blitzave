import sqlite3

import pytest

from blitzave.sources.base import AccountRow, BalanceRow, TxRow
from blitzave.store import db, ingest, users


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "test.db")
    connection.execute("PRAGMA foreign_keys = ON")
    db.migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def user_id(conn):
    return users.get_or_create_user(conn)


CHECKING_ROW = AccountRow(
    external_id="ACT-1",
    account_type="asset",
    account_number="1234",
    bank_name="Chase",
    asset_type="checking",
    meta={"name": "Checking ...1234"},
)

CARD_ROW = AccountRow(
    external_id="ACT-2",
    account_type="liability",
    account_number="7d6c",
    issuer_name="Amex",
    liability_type="credit_card",
    meta={},
)


def test_upsert_account_inserts_into_accounts_and_assets(conn, user_id):
    account_id = ingest.upsert_account(conn, user_id, CHECKING_ROW)

    account_type = conn.execute(
        "SELECT account_type, account_number FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    assert account_type == ("asset", "1234")

    asset = conn.execute(
        "SELECT bank_name, asset_type FROM assets WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert asset == ("Chase", "checking")


def test_upsert_account_liability_goes_into_liabilities(conn, user_id):
    account_id = ingest.upsert_account(conn, user_id, CARD_ROW)

    liability = conn.execute(
        "SELECT issuer_name, liability_type FROM liabilities WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert liability == ("Amex", "credit_card")


def test_upsert_account_is_idempotent_and_never_overwrites(conn, user_id):
    first_id = ingest.upsert_account(conn, user_id, CHECKING_ROW)

    changed_row = AccountRow(
        external_id="ACT-1",  # same external_id
        account_type="asset",
        account_number="9999",  # pretend the bank renamed it
        bank_name="Different Bank",
        asset_type="savings",
    )
    second_id = ingest.upsert_account(conn, user_id, changed_row)

    assert first_id == second_id
    count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    assert count == 1
    # the original values survive — a future user correction must too
    account_number = conn.execute(
        "SELECT account_number FROM accounts WHERE id = ?", (first_id,)
    ).fetchone()[0]
    assert account_number == "1234"


def test_upsert_transaction_inserts_new_row(conn, user_id):
    account_id = ingest.upsert_account(conn, user_id, CHECKING_ROW)
    row = TxRow(
        account_external_id="ACT-1",
        external_id="TXN-1",
        amount_cents=-450,
        currency="USD",
        transaction_type="debit",
        posted_on="2026-01-15",
        description="Coffee",
    )

    ingest.upsert_transaction(conn, user_id, account_id, row)

    stored = conn.execute(
        "SELECT amount_cents, description FROM transactions WHERE external_id = ?", ("TXN-1",)
    ).fetchone()
    assert stored == (-450, "Coffee")


def test_upsert_transaction_never_changes_an_existing_amount(conn, user_id, caplog):
    account_id = ingest.upsert_account(conn, user_id, CHECKING_ROW)
    original = TxRow(
        account_external_id="ACT-1",
        external_id="TXN-1",
        amount_cents=-450,
        currency="USD",
        transaction_type="debit",
        posted_on="2026-01-15",
        description="Coffee",
    )
    ingest.upsert_transaction(conn, user_id, account_id, original)

    different_amount = TxRow(
        account_external_id="ACT-1",
        external_id="TXN-1",
        amount_cents=-999,
        currency="USD",
        transaction_type="debit",
        posted_on="2026-01-15",
        description="Coffee",
    )
    with caplog.at_level("WARNING"):
        ingest.upsert_transaction(conn, user_id, account_id, different_amount)

    stored = conn.execute(
        "SELECT amount_cents FROM transactions WHERE external_id = ?", ("TXN-1",)
    ).fetchone()[0]
    assert stored == -450  # unchanged
    assert any("TXN-1" in record.message for record in caplog.records)


def test_upsert_balance_overwrites_same_day_snapshot(conn, user_id):
    account_id = ingest.upsert_account(conn, user_id, CHECKING_ROW)
    ingest.upsert_balance(
        conn, account_id, BalanceRow(account_external_id="ACT-1", as_of="2026-01-15", balance_cents=1000)
    )
    ingest.upsert_balance(
        conn, account_id, BalanceRow(account_external_id="ACT-1", as_of="2026-01-15", balance_cents=2000)
    )

    rows = conn.execute("SELECT balance_cents FROM balances WHERE account_id = ?", (account_id,)).fetchall()
    assert rows == [(2000,)]


def test_watermark_round_trips(conn):
    assert ingest.get_watermark(conn, "ingest") is None

    ingest.set_watermark(conn, "ingest", "2026-01-15")
    assert ingest.get_watermark(conn, "ingest") == "2026-01-15"

    ingest.set_watermark(conn, "ingest", "2026-01-22")
    assert ingest.get_watermark(conn, "ingest") == "2026-01-22"
