import sqlite3

import pytest

from blitzave.sources.base import AccountRow, TxRow
from blitzave.store import db, ingest, transactions, users


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "test.db")
    connection.execute("PRAGMA foreign_keys = ON")
    db.migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded(conn):
    user_id = users.get_or_create_user(conn)
    checking = ingest.upsert_account(
        conn,
        user_id,
        AccountRow(
            external_id="ACT-checking",
            account_type="asset",
            account_number="1111",
            bank_name="Chase",
            asset_type="checking",
        ),
    )
    card = ingest.upsert_account(
        conn,
        user_id,
        AccountRow(
            external_id="ACT-card",
            account_type="liability",
            account_number="2222",
            issuer_name="Amex",
            liability_type="credit_card",
        ),
    )
    rows = [
        ("ACT-checking", checking, "TXN-1", -450, "2026-01-10", "Blue Bottle Coffee", "Blue Bottle"),
        ("ACT-checking", checking, "TXN-2", 150000, "2026-01-15", "Acme Payroll", "Acme Corp"),
        ("ACT-card", card, "TXN-3", -12101, "2026-01-12", "H-E-B Groceries", "H-E-B"),
        ("ACT-card", card, "TXN-4", -8999, "2026-02-01", "delta air lines", "Delta"),
    ]
    for account_ext, account_id, ext, cents, posted, desc, merchant in rows:
        ingest.upsert_transaction(
            conn,
            user_id,
            account_id,
            TxRow(
                account_external_id=account_ext,
                external_id=ext,
                amount_cents=cents,
                currency="USD",
                transaction_type="debit" if cents < 0 else "credit",
                posted_on=posted,
                description=desc,
                merchant_name=merchant,
            ),
        )
    return conn


def test_sort_by_amount_ascending(seeded):
    _, rows = transactions.list_transactions(seeded, sort="amount", order="asc")
    amounts = [r["amount_cents"] for r in rows]
    assert amounts == sorted(amounts)
    assert rows[0]["id"] == "TXN-3"  # -12101 is the most negative


def test_sort_by_description_is_case_insensitive(seeded):
    _, rows = transactions.list_transactions(seeded, sort="description", order="asc")
    descriptions = [r["description"] for r in rows]
    assert descriptions == sorted(descriptions, key=str.lower)
    assert descriptions[0] == "Acme Payroll"
    assert descriptions[2] == "delta air lines"  # lowercase sorts with the Ds, not after Z


def test_filters_by_account_date_range_and_search(seeded):
    _, by_account = transactions.list_transactions(seeded, account_external_id="ACT-card")
    assert {r["id"] for r in by_account} == {"TXN-3", "TXN-4"}

    _, by_range = transactions.list_transactions(seeded, start="2026-01-11", end="2026-01-31")
    assert {r["id"] for r in by_range} == {"TXN-2", "TXN-3"}

    _, by_search = transactions.list_transactions(seeded, q="coffee")
    assert {r["id"] for r in by_search} == {"TXN-1"}


def test_pagination_reports_total_beyond_the_page(seeded):
    total, rows = transactions.list_transactions(seeded, sort="date", order="asc", limit=2, offset=0)
    assert total == 4
    assert len(rows) == 2
    total, rows = transactions.list_transactions(seeded, sort="date", order="asc", limit=2, offset=2)
    assert total == 4
    assert len(rows) == 2


def test_update_metadata_rejects_unknown_transaction(seeded):
    with pytest.raises(transactions.TransactionNotFoundError):
        transactions.update_metadata(seeded, "TXN-nope", tags=[])


def test_update_metadata_rejects_ambiguous_external_id(conn):
    # external ids are only unique per account — if two accounts ever carry
    # the same transaction id, addressing by external id must refuse.
    user_id = users.get_or_create_user(conn)
    for ext in ("ACT-a", "ACT-b"):
        account_id = ingest.upsert_account(
            conn,
            user_id,
            AccountRow(
                external_id=ext,
                account_type="asset",
                account_number=ext[-1] * 4,
                bank_name="Bank",
                asset_type="checking",
            ),
        )
        ingest.upsert_transaction(
            conn,
            user_id,
            account_id,
            TxRow(
                account_external_id=ext,
                external_id="TXN-shared",
                amount_cents=-100,
                currency="USD",
                transaction_type="debit",
                posted_on="2026-01-01",
                description="dup",
            ),
        )
    with pytest.raises(transactions.AmbiguousTransactionError):
        transactions.update_metadata(conn, "TXN-shared", tags=[])
