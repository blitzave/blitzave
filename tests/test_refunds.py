import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app
from blitzave.sources.base import AccountRow, TxRow
from blitzave.store import (
    categories,
    db,
    ingest,
    refunds,
    transactions,
    transfers,
    users,
)


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "test.db")
    connection.execute("PRAGMA foreign_keys = ON")
    db.migrate(connection)
    user_id = users.get_or_create_user(connection)
    accounts = [
        AccountRow(
            external_id="card", account_type="liability", account_number="1111",
            issuer_name="Demo card", liability_type="credit_card",
        ),
        AccountRow(
            external_id="other-card", account_type="liability", account_number="2222",
            issuer_name="Other demo card", liability_type="credit_card",
        ),
        AccountRow(
            external_id="checking", account_type="asset", account_number="3333",
            bank_name="Demo bank", asset_type="checking",
        ),
    ]
    account_ids = {
        account.external_id: ingest.upsert_account(connection, user_id, account)
        for account in accounts
    }
    for account, ext, cents, posted, currency in [
        ("card", "charge", -10000, "2026-10-03", "USD"),
        ("card", "full-credit", 10000, "2026-10-04", "USD"),
        ("card", "partial-credit", 3500, "2026-10-05", "USD"),
        ("card", "large-credit", 15000, "2026-10-06", "USD"),
        ("card", "euro-credit", 10000, "2026-10-04", "EUR"),
        ("card", "other-charge", -5000, "2026-10-02", "USD"),
        ("other-card", "other-account-credit", 10000, "2026-10-03", "USD"),
        ("checking", "card-payment", -10000, "2026-10-04", "USD"),
    ]:
        ingest.upsert_transaction(
            connection, user_id, account_ids[account],
            TxRow(
                account_external_id=account, external_id=ext, amount_cents=cents,
                currency=currency, transaction_type="debit" if cents < 0 else "credit",
                posted_on=posted, description=f"Synthetic {ext}", merchant_name=None,
            ),
        )
    connection.commit()
    yield connection
    connection.close()


def _internal_id(conn, external_id):
    return conn.execute(
        "SELECT id FROM transactions WHERE external_id = ?", (external_id,)
    ).fetchone()[0]


def test_candidates_are_only_the_exact_other_side_in_the_same_account_and_currency(conn):
    # Not the partial or larger credit, not the euro credit, not another card's.
    assert [row["id"] for row in refunds.list_candidates(conn, "charge")] == ["full-credit"]
    assert [row["id"] for row in refunds.list_candidates(conn, "full-credit")] == ["charge"]
    assert refunds.list_candidates(conn, "partial-credit") == []
    assert refunds.list_candidates(conn, "large-credit") == []


@pytest.mark.parametrize("credit,amount", [("full-credit", 10000), ("partial-credit", 3500)])
def test_manual_link_accepts_full_or_partial_credit_in_either_order(conn, credit, amount):
    user_id = users.get_or_create_user(conn)
    created = refunds.create_refund(conn, user_id, credit, "charge")
    assert created["amount_cents"] == amount
    assert created["debit"]["id"] == "charge"
    assert created["credit"]["id"] == credit
    assert created["debit"]["account_display_name"] == "liability-1111"
    assert created["adjustment_date"] == ("2026-10-04" if credit == "full-credit" else "2026-10-05")
    assert refunds.list_refunds(conn) == [created]
    assert conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 8


@pytest.mark.parametrize("other", [
    "other-account-credit", "euro-credit", "other-charge", "large-credit", "charge",
])
def test_invalid_account_currency_sign_or_amount_rejected(conn, other):
    with pytest.raises(refunds.InvalidRefundPairError):
        refunds.create_refund(conn, users.get_or_create_user(conn), "charge", other)
    assert refunds.list_refunds(conn) == []


def test_pair_must_belong_to_selected_user(conn):
    conn.execute("INSERT INTO users (username, created_at) VALUES ('other-user', 0)")
    user_id = conn.execute("SELECT id FROM users WHERE username = 'other-user'").fetchone()[0]
    with pytest.raises(refunds.InvalidRefundPairError):
        refunds.create_refund(conn, user_id, "charge", "full-credit")


def test_link_does_not_rewrite_categories_and_refund_offsets_manual_category(conn):
    user_id = users.get_or_create_user(conn)
    travel = categories.create_category(conn, user_id, "Travel", "expense")
    shopping = categories.create_category(conn, user_id, "Shopping", "expense")
    transactions.update_metadata(conn, "charge", categories={travel["id"]: 1.0})
    transactions.update_metadata(conn, "partial-credit", categories={shopping["id"]: 1.0})
    refund = refunds.create_refund(conn, user_id, "charge", "partial-credit")
    credit = transactions.get_transaction(conn, "partial-credit")
    assert credit["categories"] == {str(shopping["id"]): 1.0}
    assert credit["categories_set_by"] == "user"
    # The user explicitly chooses the same category to offset this charge.
    transactions.update_metadata(conn, "partial-credit", categories={travel["id"]: 1.0})
    summary, _ = categories.summary(conn, "2026-10-01", "2026-10-31")
    totals = {category["name"]: category["total_cents"] for category in summary}
    assert totals["Travel"] == -6500
    assert totals["Shopping"] == 0
    refunds.delete_refund(conn, refund["id"])
    # Unlinking changes the relationship only; recorded spending stays the same.
    assert categories.summary(conn, "2026-10-01", "2026-10-31")[0] == summary
    assert transactions.get_transaction(conn, "partial-credit")["categories_set_by"] == "user"


def test_refund_cannot_be_linked_twice_or_reused_as_transfer(conn):
    user_id = users.get_or_create_user(conn)
    refunds.create_refund(conn, user_id, "charge", "full-credit")
    for action in [
        lambda: refunds.create_refund(conn, user_id, "charge", "partial-credit"),
        lambda: refunds.list_candidates(conn, "full-credit"),
        lambda: transfers.create_transfer(conn, user_id, "card-payment", "full-credit"),
        lambda: transfers.list_candidates(conn, "full-credit"),
    ]:
        with pytest.raises(refunds.AlreadyAdjustmentLinkedError):
            action()
    # The €100 credit is not the other side of a $100 payment.
    assert [row["id"] for row in transfers.list_candidates(conn, "card-payment")] == [
        "other-account-credit",
    ]


def test_transfer_cannot_be_reused_as_refund(conn):
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "card-payment", "full-credit")
    with pytest.raises(refunds.AlreadyAdjustmentLinkedError):
        refunds.create_refund(conn, user_id, "charge", "full-credit")
    with pytest.raises(refunds.AlreadyAdjustmentLinkedError):
        refunds.list_candidates(conn, "full-credit")
    # Its only exact match is now a transfer leg, so nothing is offered.
    assert refunds.list_candidates(conn, "charge") == []


def test_unlink_is_reversible_and_never_changes_bank_fields(conn):
    user_id = users.get_or_create_user(conn)
    before = conn.execute("SELECT * FROM transactions ORDER BY id").fetchall()
    created = refunds.create_refund(conn, user_id, "charge", "full-credit")
    refunds.delete_refund(conn, created["id"])
    assert refunds.list_refunds(conn) == []
    assert conn.execute("SELECT * FROM transactions ORDER BY id").fetchall() == before
    assert json.loads(conn.execute("SELECT meta FROM transactions LIMIT 1").fetchone()[0]) == {}
    refunds.create_refund(conn, user_id, "charge", "full-credit")
    with pytest.raises(refunds.RefundNotFoundError):
        refunds.delete_refund(conn, 999)


@pytest.mark.parametrize("a,b", [
    ("charge", "other-account-credit"), ("charge", "large-credit"),
    ("charge", "euro-credit"), ("full-credit", "charge"),
])
def test_database_rejects_invalid_refund_pair_directly(conn, a, b):
    with pytest.raises(sqlite3.IntegrityError, match="refund requires"):
        conn.execute(
            "INSERT INTO refunds (user_id, debit_transaction_id, credit_transaction_id, created_at) "
            "VALUES (?, ?, ?, 0)",
            (users.get_or_create_user(conn), _internal_id(conn, a), _internal_id(conn, b)),
        )


def test_database_rejects_double_use_and_link_edits(conn):
    user_id = users.get_or_create_user(conn)
    refund = refunds.create_refund(conn, user_id, "charge", "full-credit")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(
            "INSERT INTO refunds (user_id, debit_transaction_id, credit_transaction_id, created_at) "
            "VALUES (?, ?, ?, 0)",
            (user_id, _internal_id(conn, "charge"), _internal_id(conn, "partial-credit")),
        )
    with pytest.raises(sqlite3.IntegrityError, match="unlink the refund"):
        conn.execute("UPDATE refunds SET created_at = 1 WHERE id = ?", (refund["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="cannot also be a transfer"):
        conn.execute(
            "INSERT INTO transfers (user_id, debit_transaction_id, credit_transaction_id, "
            "transfer_amount_cents, transfer_date, matched_by, created_at) "
            "VALUES (?, ?, ?, 10000, '2026-10-04', 'user', 0)",
            (user_id, _internal_id(conn, "card-payment"), _internal_id(conn, "full-credit")),
        )


def test_database_rejects_refund_using_transfer_leg(conn):
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "card-payment", "full-credit")
    with pytest.raises(sqlite3.IntegrityError, match="cannot also be linked as a refund"):
        conn.execute(
            "INSERT INTO refunds (user_id, debit_transaction_id, credit_transaction_id, created_at) "
            "VALUES (?, ?, ?, 0)",
            (user_id, _internal_id(conn, "charge"), _internal_id(conn, "full-credit")),
        )


def test_unknown_and_ambiguous_transaction_ids_are_never_guessed(conn):
    with pytest.raises(transactions.TransactionNotFoundError):
        refunds.list_candidates(conn, "missing")
    with pytest.raises(transactions.TransactionNotFoundError):
        refunds.create_refund(conn, users.get_or_create_user(conn), "charge", "missing")
    conn.execute(
        "UPDATE transactions SET external_id = 'charge' WHERE external_id = 'card-payment'"
    )
    with pytest.raises(transactions.AmbiguousTransactionError):
        refunds.list_candidates(conn, "charge")


@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    destination = db.connect()
    try:
        conn.backup(destination)
    finally:
        destination.close()
    with TestClient(create_app()) as test_client:
        yield test_client


def test_api_candidates_show_display_money_and_same_account(client):
    response = client.get("/api/transactions/charge/refund-candidates")
    assert response.status_code == 200
    candidates = response.json()
    assert [row["id"] for row in candidates] == ["full-credit"]
    assert candidates[0]["amount_display"] == "$100.00"
    assert candidates[0]["account_display_name"] == "liability-1111"
    assert candidates[0]["refund_id"] is None


@pytest.mark.parametrize("credit,amount_display", [
    ("full-credit", "$100.00"), ("partial-credit", "$35.00"),
])
def test_api_manual_link_lists_pair_and_labels_both_transactions(client, credit, amount_display):
    response = client.post(
        "/api/refunds", json={"transaction_id": credit, "counterpart_id": "charge"}
    )
    assert response.status_code == 201
    created = response.json()
    assert created["amount_display"] == amount_display
    assert created["debit"]["amount_display"] == "-$100.00"
    assert created["credit"]["amount_display"] == amount_display
    assert client.get("/api/refunds").json() == [created]
    rows = client.get("/api/transactions").json()["transactions"]
    by_id = {row["id"]: row for row in rows}
    for ext in ("charge", credit):
        assert by_id[ext]["refund_id"] == created["id"]
        assert by_id[ext]["transfer_id"] is None
    assert by_id["card-payment"]["refund_id"] is None
    assert client.get("/api/transactions/charge/refund-candidates").status_code == 409


def test_api_refund_stays_editable_and_offsets_category_spending(client):
    category = client.post("/api/categories", json={"name": "Travel", "kind": "expense"}).json()
    assert client.post(
        "/api/refunds", json={"transaction_id": "charge", "counterpart_id": "partial-credit"}
    ).status_code == 201
    for ext in ("charge", "partial-credit"):
        response = client.patch(
            f"/api/transactions/{ext}", json={"categories": {str(category["id"]): 1.0}}
        )
        assert response.status_code == 200
        assert response.json()["categories_set_by"] == "user"
    params = {"start": "2026-10-01", "end": "2026-10-31"}
    summary = client.get("/api/categories/summary", params=params).json()
    assert next(row for row in summary["categories"] if row["id"] == category["id"])[
        "total_cents"
    ] == -6500
    rows = client.get("/api/transactions", params={"category_id": category["id"]}).json()[
        "transactions"
    ]
    assert {row["id"] for row in rows} == {"charge", "partial-credit"}


def test_api_unlink_removes_label_without_changing_categories(client):
    category = client.post("/api/categories", json={"name": "Travel", "kind": "expense"}).json()
    client.patch("/api/transactions/charge", json={"categories": {str(category["id"]): 1.0}})
    created = client.post(
        "/api/refunds", json={"transaction_id": "charge", "counterpart_id": "full-credit"}
    ).json()
    assert client.delete(f"/api/refunds/{created['id']}").status_code == 204
    assert client.get("/api/refunds").json() == []
    rows = client.get("/api/transactions").json()["transactions"]
    by_id = {row["id"]: row for row in rows}
    assert by_id["charge"]["refund_id"] is None
    assert by_id["full-credit"]["refund_id"] is None
    assert by_id["charge"]["categories"] == {str(category["id"]): 1.0}


def test_api_refund_missing_invalid_and_conflicting_pairs(client):
    def link(a, b):
        return client.post("/api/refunds", json={"transaction_id": a, "counterpart_id": b})

    assert client.get("/api/transactions/missing/refund-candidates").status_code == 404
    assert link("charge", "missing").status_code == 404
    assert client.delete("/api/refunds/999").status_code == 404
    for other in ("other-account-credit", "euro-credit", "other-charge", "large-credit"):
        assert link("charge", other).status_code == 422
    assert client.post("/api/refunds", json={"transaction_id": "charge"}).status_code == 422
    assert link("charge", "full-credit").status_code == 201
    assert link("charge", "partial-credit").status_code == 409
    assert client.post(
        "/api/transfers", json={"transaction_id": "card-payment", "counterpart_id": "full-credit"}
    ).status_code == 409
    assert client.get("/api/transactions/full-credit/transfer-candidates").status_code == 409


def test_api_transfer_cannot_be_reused_as_refund(client):
    assert client.post(
        "/api/transfers", json={"transaction_id": "card-payment", "counterpart_id": "full-credit"}
    ).status_code == 201
    assert client.post(
        "/api/refunds", json={"transaction_id": "charge", "counterpart_id": "full-credit"}
    ).status_code == 409
    assert client.get("/api/transactions/full-credit/refund-candidates").status_code == 409


def test_transfer_requires_the_same_currency(conn):
    user_id = users.get_or_create_user(conn)
    with pytest.raises(transfers.InvalidTransferPairError):
        transfers.create_transfer(conn, user_id, "card-payment", "euro-credit")


def _card_items(conn, **kwargs):
    return transactions.list_transactions(
        conn, account_external_id="card", group_refunds=True, limit=500, **kwargs
    )


def test_grouped_list_collapses_a_refund_pair_into_one_zero_net_item(conn):
    user_id = users.get_or_create_user(conn)
    refund = refunds.create_refund(conn, user_id, "charge", "full-credit")

    total, items = _card_items(conn)
    groups = [item for item in items if item["kind"] == "refund_group"]
    standalone = {item["id"] for item in items if item["kind"] == "transaction"}

    assert len(groups) == 1
    group = groups[0]
    assert group["id"] == f"refund-{refund['id']}"
    assert group["amount_cents"] == 0
    assert group["posted_on"] == "2026-10-04"  # the later of the two
    assert group["description"] == "Synthetic charge + Synthetic full-credit"
    assert [leg["id"] for leg in group["legs"]] == ["charge", "full-credit"]
    assert {"charge", "full-credit"}.isdisjoint(standalone)
    assert total == 5  # six card rows, the pair counted once


def test_grouping_is_opt_in(conn):
    user_id = users.get_or_create_user(conn)
    refunds.create_refund(conn, user_id, "charge", "full-credit")
    total, rows = transactions.list_transactions(conn, account_external_id="card", limit=500)
    assert total == 6
    assert all("kind" not in row for row in rows)


def test_a_refund_pair_never_splits_across_pages(conn):
    # Sorted by amount, the -$100 charge and +$100 credit sit at opposite ends;
    # grouping before paging is the only way they stay together.
    user_id = users.get_or_create_user(conn)
    refund = refunds.create_refund(conn, user_id, "charge", "full-credit")
    seen = []
    for offset in range(8):
        total, items = transactions.list_transactions(
            conn, account_external_id="card", group_refunds=True,
            sort="amount", order="asc", limit=1, offset=offset,
        )
        seen.extend(item["id"] for item in items)
    assert total == 5
    assert len(seen) == len(set(seen)) == 5
    assert f"refund-{refund['id']}" in seen
    assert "charge" not in seen and "full-credit" not in seen


def test_a_pair_is_listed_when_either_half_matches_the_search(conn):
    user_id = users.get_or_create_user(conn)
    refunds.create_refund(conn, user_id, "charge", "full-credit")
    _, items = transactions.list_transactions(conn, q="full-credit", group_refunds=True)
    assert [item["kind"] for item in items] == ["refund_group"]


def test_a_partial_refund_group_shows_what_is_still_owed(conn):
    user_id = users.get_or_create_user(conn)
    refunds.create_refund(conn, user_id, "charge", "partial-credit")
    _, items = _card_items(conn)
    group = next(item for item in items if item["kind"] == "refund_group")
    assert group["amount_cents"] == -6500


def test_api_grouped_list_formats_the_group_and_its_legs(client):
    created = client.post(
        "/api/refunds", json={"transaction_id": "full-credit", "counterpart_id": "charge"}
    ).json()
    body = client.get(
        "/api/transactions", params={"group_refunds": "true", "account_id": "card"}
    ).json()
    group = next(item for item in body["transactions"] if item["kind"] == "refund_group")
    assert group["refund_id"] == created["id"]
    assert group["amount_display"] == "$0.00"
    assert [leg["amount_display"] for leg in group["legs"]] == ["-$100.00", "$100.00"]
