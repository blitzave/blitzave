import sqlite3

import pytest

from blitzave.sources.base import AccountRow
from blitzave.store import accounts, db, ingest, users


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


def _make_checking_account(conn, user_id, external_id="ACT-1", account_number="1234"):
    """Returns the internal integer id (for direct SQL assertions in these
    tests) — callers address update_account_type by external_id, since
    that's the public identifier now."""
    return ingest.upsert_account(
        conn,
        user_id,
        AccountRow(
            external_id=external_id,
            account_type="asset",
            account_number=account_number,
            bank_name="Chase",
            asset_type="checking",
            meta={"name": f"Checking ...{account_number}"},
        ),
    )


def test_list_accounts_shows_asset_and_liability_fields(conn, user_id):
    _make_checking_account(conn, user_id)

    rows = accounts.list_accounts(conn)

    assert rows == [
        {
            "id": "ACT-1",  # SimpleFIN's own account id, not an internal sequential one
            "account_type": "asset",
            "account_number": "1234",
            "name": "Checking ...1234",  # the human name from meta leads
            "display_name": "asset-1234",
            "bank_name": "Chase",
            "asset_type": "checking",
            "issuer_name": None,
            "liability_type": None,
            "balance_cents": None,  # no balances synced in this test
            "balance_as_of": None,
        }
    ]


def test_update_account_type_corrects_subtype_within_same_type(conn, user_id):
    account_id = _make_checking_account(conn, user_id)

    accounts.update_account_type(conn, "ACT-1", "asset", "savings", None)

    row = conn.execute(
        "SELECT a.account_type, assets.asset_type, assets.bank_name "
        "FROM accounts a JOIN assets ON assets.account_id = a.id WHERE a.id = ?",
        (account_id,),
    ).fetchone()
    assert row == ("asset", "savings", "Chase")  # bank_name preserved


def test_update_account_type_flips_asset_to_liability(conn, user_id):
    account_id = _make_checking_account(conn, user_id)

    accounts.update_account_type(conn, "ACT-1", "liability", None, "credit_card")

    account_type = conn.execute(
        "SELECT account_type FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()[0]
    assert account_type == "liability"

    assert conn.execute(
        "SELECT 1 FROM assets WHERE account_id = ?", (account_id,)
    ).fetchone() is None

    liability = conn.execute(
        "SELECT issuer_name, liability_type FROM liabilities WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    # the institution travels with the account: the bank becomes the issuer
    assert liability == ("Chase", "credit_card")


def test_update_account_type_round_trip_keeps_the_institution(conn, user_id):
    account_id = _make_checking_account(conn, user_id)

    accounts.update_account_type(conn, "ACT-1", "liability", None, "credit_card")
    accounts.update_account_type(conn, "ACT-1", "asset", "checking", None)

    bank_name = conn.execute(
        "SELECT bank_name FROM assets WHERE account_id = ?", (account_id,)
    ).fetchone()[0]
    assert bank_name == "Chase"  # not replaced by the account's own name


def test_update_account_type_raises_not_found_for_unknown_id(conn):
    with pytest.raises(accounts.AccountNotFoundError):
        accounts.update_account_type(conn, "ACT-nonexistent", "asset", "checking", None)


def test_update_account_type_raises_conflict_on_display_name_collision(conn, user_id):
    # two accounts that would both become "liability-1234" after the correction
    checking_id = _make_checking_account(conn, user_id, external_id="ACT-1", account_number="1234")
    _ = ingest.upsert_account(
        conn,
        user_id,
        AccountRow(
            external_id="ACT-2",
            account_type="liability",
            account_number="1234",
            issuer_name="Amex",
            liability_type="credit_card",
        ),
    )

    with pytest.raises(accounts.AccountConflictError):
        accounts.update_account_type(conn, "ACT-1", "liability", None, "credit_card")

    # the failed correction must not have partially applied
    account_type = conn.execute(
        "SELECT account_type FROM accounts WHERE id = ?", (checking_id,)
    ).fetchone()[0]
    assert account_type == "asset"
