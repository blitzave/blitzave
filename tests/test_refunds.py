"""Refunds and credits: pairs whose two legs share an account. Since
migration 007 they are rows in `transfers` like any other pair, served by the
same /api/transfers endpoints; only what they're called differs."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app
from blitzave.sources.base import AccountRow, TxRow
from blitzave.store import categories, db, ingest, transactions, transfers, users


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


def test_candidates_are_the_exact_other_side_in_any_account_and_currency(conn):
    # Not the partial or larger credit, not the euro credit.
    rows = transfers.list_candidates(conn, "charge")
    assert [(r["id"], r["same_account"]) for r in rows] == [
        ("other-account-credit", False),  # same day, so it sorts first
        ("full-credit", True),
    ]
    assert transfers.list_candidates(conn, "partial-credit") == []
    assert transfers.list_candidates(conn, "large-credit") == []


def test_a_same_account_pair_links_in_either_order_and_both_leave_activity(conn):
    user_id = users.get_or_create_user(conn)
    created = transfers.create_transfer(conn, user_id, "full-credit", "charge")
    assert created["amount_cents"] == 10000
    assert created["debit"]["id"] == "charge"
    assert created["credit"]["id"] == "full-credit"
    assert created["debit"]["account_display_name"] == "liability-1111"
    assert created["transfer_date"] == "2026-10-04"
    assert created["matched_by"] == "user"
    assert created["same_account"] is True
    assert transfers.list_transfers(conn) == [created]
    # Like a transfer, the pair counts as nothing: 8 rows, minus both legs.
    assert conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 6


@pytest.mark.parametrize("other", [
    "euro-credit", "other-charge", "large-credit", "charge",
    "partial-credit",  # partial credits aren't supported for now
])
def test_invalid_currency_sign_or_amount_rejected(conn, other):
    with pytest.raises(transfers.InvalidTransferPairError):
        transfers.create_transfer(conn, users.get_or_create_user(conn), "charge", other)
    assert transfers.list_transfers(conn) == []


def test_pair_must_belong_to_selected_user(conn):
    conn.execute("INSERT INTO users (username, created_at) VALUES ('other-user', 0)")
    user_id = conn.execute("SELECT id FROM users WHERE username = 'other-user'").fetchone()[0]
    with pytest.raises(transfers.InvalidTransferPairError):
        transfers.create_transfer(conn, user_id, "charge", "full-credit")


def test_link_leaves_categories_alone_and_unlinking_restores_the_totals(conn):
    user_id = users.get_or_create_user(conn)
    travel = categories.create_category(conn, user_id, "Travel", "expense")
    shopping = categories.create_category(conn, user_id, "Shopping", "expense")
    transactions.update_metadata(conn, "charge", categories={travel["id"]: 1.0})
    transactions.update_metadata(conn, "full-credit", categories={shopping["id"]: 1.0})

    def totals():
        summary, _ = categories.summary(conn, "2026-10-01", "2026-10-31")
        return {category["name"]: category["total_cents"] for category in summary}

    before = totals()
    assert before == {"Travel": -10000, "Shopping": 10000}
    pair = transfers.create_transfer(conn, user_id, "charge", "full-credit")
    credit = transactions.get_transaction(conn, "full-credit")
    assert credit["categories"] == {str(shopping["id"]): 1.0}
    assert credit["categories_set_by"] == "user"
    # Both legs left activity, so neither category counts them.
    assert totals() == {"Travel": 0, "Shopping": 0}
    transfers.delete_transfer(conn, pair["id"])
    assert totals() == before


def test_a_leg_can_be_in_only_one_pair(conn):
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "charge", "full-credit")
    for action in [
        lambda: transfers.create_transfer(conn, user_id, "card-payment", "full-credit"),
        lambda: transfers.list_candidates(conn, "full-credit"),
    ]:
        with pytest.raises(transfers.AlreadyTransferLegError):
            action()
    # The €100 credit is not the other side of a $100 payment.
    assert [row["id"] for row in transfers.list_candidates(conn, "card-payment")] == [
        "other-account-credit",
    ]


def test_unlink_sticks_and_never_changes_bank_fields(conn):
    user_id = users.get_or_create_user(conn)
    bank_fields = "SELECT id, account_id, external_id, amount_cents, posted_on, description " \
        "FROM transactions ORDER BY id"
    before = conn.execute(bank_fields).fetchall()
    created = transfers.create_transfer(conn, user_id, "charge", "full-credit")
    transfers.delete_transfer(conn, created["id"])
    assert transfers.list_transfers(conn) == []
    assert conn.execute(bank_fields).fetchall() == before
    # Unlinking sticks: the sync's matcher never links these two again.
    for ext in ("charge", "full-credit"):
        meta = conn.execute(
            "SELECT meta FROM transactions WHERE external_id = ?", (ext,)
        ).fetchone()[0]
        assert json.loads(meta) == {"no_auto_transfer": True}
    # The user can still link them again by hand.
    transfers.create_transfer(conn, user_id, "charge", "full-credit")
    with pytest.raises(transfers.TransferNotFoundError):
        transfers.delete_transfer(conn, 999)


def _insert_pair(conn, user_id, debit, credit, amount_cents=10000):
    conn.execute(
        "INSERT INTO transfers (user_id, debit_transaction_id, credit_transaction_id, "
        "transfer_amount_cents, transfer_date, matched_by, created_at) "
        "VALUES (?, ?, ?, ?, '2026-10-04', 'user', 0)",
        (user_id, _internal_id(conn, debit), _internal_id(conn, credit), amount_cents),
    )


@pytest.mark.parametrize("a,b", [
    ("charge", "large-credit"), ("charge", "euro-credit"),
    ("full-credit", "charge"), ("charge", "partial-credit"),
])
def test_database_rejects_invalid_pair_directly(conn, a, b):
    with pytest.raises(sqlite3.IntegrityError, match="transfer legs must be"):
        _insert_pair(conn, users.get_or_create_user(conn), a, b)


def test_database_rejects_double_use_and_link_edits(conn):
    user_id = users.get_or_create_user(conn)
    pair = transfers.create_transfer(conn, user_id, "charge", "full-credit")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert_pair(conn, user_id, "charge", "full-credit")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert_pair(conn, user_id, "card-payment", "full-credit")
    with pytest.raises(sqlite3.IntegrityError, match="unlink the pair"):
        conn.execute("UPDATE transfers SET created_at = 1 WHERE id = ?", (pair["id"],))


def test_unknown_and_ambiguous_transaction_ids_are_never_guessed(conn):
    with pytest.raises(transactions.TransactionNotFoundError):
        transfers.list_candidates(conn, "missing")
    with pytest.raises(transactions.TransactionNotFoundError):
        transfers.create_transfer(conn, users.get_or_create_user(conn), "charge", "missing")
    conn.execute(
        "UPDATE transactions SET external_id = 'charge' WHERE external_id = 'card-payment'"
    )
    with pytest.raises(transactions.AmbiguousTransactionError):
        transfers.list_candidates(conn, "charge")


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


def test_api_candidates_say_what_linking_would_make(client):
    response = client.get("/api/transactions/charge/transfer-candidates")
    assert response.status_code == 200
    candidates = response.json()
    assert [(row["id"], row["kind"]) for row in candidates] == [
        ("other-account-credit", "transfer"),
        ("full-credit", "credit"),  # same card; "Synthetic full-credit" names another merchant
    ]
    assert candidates[1]["amount_display"] == "$100.00"
    assert candidates[1]["account_display_name"] == "liability-1111"
    assert candidates[1]["transfer_id"] is None


def test_api_same_account_link_is_a_credit_and_labels_both_legs(client):
    response = client.post(
        "/api/transfers", json={"transaction_id": "full-credit", "counterpart_id": "charge"}
    )
    assert response.status_code == 201
    created = response.json()
    assert created["kind"] == "credit"
    assert created["amount_display"] == "$100.00"
    assert created["matched_by"] == "user"
    assert "same_account" not in created
    assert created["debit"]["amount_display"] == "-$100.00"
    assert client.get("/api/transfers").json() == [created]
    rows = client.get("/api/transactions").json()["transactions"]
    by_id = {row["id"]: row for row in rows}
    for ext in ("charge", "full-credit"):
        assert by_id[ext]["transfer_id"] == created["id"]
        assert by_id[ext]["transfer_kind"] == "credit"
        assert "refund_id" not in by_id[ext]
    assert by_id["card-payment"]["transfer_id"] is None
    assert by_id["card-payment"]["transfer_kind"] is None
    assert client.get("/api/transactions/charge/transfer-candidates").status_code == 409


def test_api_same_merchant_is_a_refund(client, conn):
    conn.close()
    db_conn = db.connect()
    db_conn.execute(
        "UPDATE transactions SET description = 'COSTCO WHSE #0669' "
        "WHERE external_id IN ('charge', 'full-credit')"
    )
    db_conn.commit()
    db_conn.close()
    created = client.post(
        "/api/transfers", json={"transaction_id": "charge", "counterpart_id": "full-credit"}
    ).json()
    assert created["kind"] == "refund"


def test_api_kind_filters_the_list(client):
    def link(a, b):
        return client.post("/api/transfers", json={"transaction_id": a, "counterpart_id": b})

    credit = link("charge", "full-credit").json()
    transfer = link("card-payment", "other-account-credit").json()
    assert {p["id"] for p in client.get("/api/transfers").json()} == {credit["id"], transfer["id"]}
    assert client.get("/api/transfers", params={"kind": "credit"}).json() == [credit]
    assert client.get("/api/transfers", params={"kind": "transfer"}).json() == [transfer]
    assert client.get("/api/transfers", params={"kind": "refund"}).json() == []
    assert client.get("/api/transfers", params={"kind": "refunds"}).status_code == 422


def test_api_paired_legs_refuse_a_category_and_leave_category_lists(client):
    category = client.post("/api/categories", json={"name": "Travel", "kind": "expense"}).json()
    client.patch("/api/transactions/charge", json={"categories": {str(category["id"]): 1.0}})
    assert client.post(
        "/api/transfers", json={"transaction_id": "charge", "counterpart_id": "full-credit"}
    ).status_code == 201
    response = client.patch(
        "/api/transactions/full-credit", json={"categories": {str(category["id"]): 1.0}}
    )
    assert response.status_code == 409
    assert "credit" in response.json()["detail"]
    params = {"start": "2026-10-01", "end": "2026-10-31"}
    summary = client.get("/api/categories/summary", params=params).json()
    assert next(row for row in summary["categories"] if row["id"] == category["id"])[
        "total_cents"
    ] == 0
    # A leg doesn't count toward its category, so it isn't listed under it.
    rows = client.get("/api/transactions", params={"category_id": category["id"]}).json()[
        "transactions"
    ]
    assert rows == []


def test_api_unlink_removes_label_without_changing_categories(client):
    category = client.post("/api/categories", json={"name": "Travel", "kind": "expense"}).json()
    client.patch("/api/transactions/charge", json={"categories": {str(category["id"]): 1.0}})
    created = client.post(
        "/api/transfers", json={"transaction_id": "charge", "counterpart_id": "full-credit"}
    ).json()
    assert client.delete(f"/api/transfers/{created['id']}").status_code == 204
    assert client.get("/api/transfers").json() == []
    rows = client.get("/api/transactions").json()["transactions"]
    by_id = {row["id"]: row for row in rows}
    assert by_id["charge"]["transfer_id"] is None
    assert by_id["full-credit"]["transfer_id"] is None
    assert by_id["charge"]["categories"] == {str(category["id"]): 1.0}


def test_api_missing_invalid_and_conflicting_pairs(client):
    def link(a, b):
        return client.post("/api/transfers", json={"transaction_id": a, "counterpart_id": b})

    assert client.get("/api/transactions/missing/transfer-candidates").status_code == 404
    assert link("charge", "missing").status_code == 404
    assert client.delete("/api/transfers/999").status_code == 404
    for other in ("euro-credit", "other-charge", "large-credit", "partial-credit"):
        assert link("charge", other).status_code == 422
    assert client.post("/api/transfers", json={"transaction_id": "charge"}).status_code == 422
    assert link("charge", "full-credit").status_code == 201
    assert link("charge", "other-account-credit").status_code == 409
    assert link("card-payment", "full-credit").status_code == 409
    assert client.get("/api/transactions/full-credit/transfer-candidates").status_code == 409


def test_old_refund_endpoints_are_gone(client):
    assert client.get("/api/refunds").status_code == 404
    assert client.get("/api/transactions/charge/refund-candidates").status_code == 404


def _card_items(conn, **kwargs):
    return transactions.list_transactions(
        conn, account_external_id="card", group_pairs=True, limit=500, **kwargs
    )


def test_grouped_list_collapses_a_same_account_pair_into_one_zero_net_item(conn):
    user_id = users.get_or_create_user(conn)
    pair = transfers.create_transfer(conn, user_id, "charge", "full-credit")

    total, items = _card_items(conn)
    groups = [item for item in items if item["kind"] == "pair"]
    standalone = {item["id"] for item in items if item["kind"] == "transaction"}

    assert len(groups) == 1
    group = groups[0]
    assert group["id"] == f"pair-{pair['id']}"
    assert group["transfer_id"] == pair["id"]
    assert group["transfer_kind"] == "credit"
    assert group["amount_cents"] == 0
    assert group["posted_on"] == "2026-10-04"  # the later of the two
    assert group["description"] == "Synthetic charge + Synthetic full-credit"
    assert [leg["id"] for leg in group["legs"]] == ["charge", "full-credit"]
    assert {"charge", "full-credit"}.isdisjoint(standalone)
    assert total == 5  # six card rows, the pair counted once


def test_grouping_in_one_account_leaves_transfer_legs_alone(conn):
    # Each account shows its own side of a transfer (maintainer call, 2026-10-26).
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "card-payment", "full-credit")
    _, items = _card_items(conn)
    assert all(item["kind"] == "transaction" for item in items)


def test_grouping_across_accounts_collapses_a_transfer_into_one_item(conn):
    # Every account at once: both sides would be the same money listed twice.
    user_id = users.get_or_create_user(conn)
    pair = transfers.create_transfer(conn, user_id, "card-payment", "full-credit")
    total, items = transactions.list_transactions(conn, group_pairs=True, limit=500)
    groups = [item for item in items if item["kind"] == "pair"]
    assert len(groups) == 1
    group = groups[0]
    assert group["id"] == f"pair-{pair['id']}"
    assert group["transfer_kind"] == "transfer"
    assert group["amount_cents"] == 0
    assert group["account_id"] == "checking"  # where the money left from
    assert [leg["id"] for leg in group["legs"]] == ["card-payment", "full-credit"]
    assert total == 7  # eight rows, the pair counted once


def _ids(conn, **kwargs):
    _, rows = transactions.list_transactions(conn, limit=500, **kwargs)
    return {row["id"] for row in rows}


def test_kind_narrows_the_list_to_one_sort_of_row(conn):
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "card-payment", "full-credit")
    transfers.create_transfer(conn, user_id, "charge", "other-account-credit")
    paired = {"card-payment", "full-credit", "charge", "other-account-credit"}

    assert _ids(conn, kind="transfer") == paired
    assert _ids(conn, kind="transfer", account_external_id="card") == {"full-credit", "charge"}
    assert _ids(conn, kind="refund") == set()
    assert _ids(conn, kind="transaction") == _ids(conn) - paired


def test_kind_tells_a_refund_from_a_credit(conn):
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "charge", "full-credit")
    assert _ids(conn, kind="credit") == {"charge", "full-credit"}  # different descriptions
    assert _ids(conn, kind="refund") == set()


def test_grouping_is_opt_in(conn):
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "charge", "full-credit")
    total, rows = transactions.list_transactions(conn, account_external_id="card", limit=500)
    assert total == 6
    assert all("kind" not in row for row in rows)


def test_a_pair_never_splits_across_pages(conn):
    # Sorted by amount, the -$100 charge and +$100 credit sit at opposite ends;
    # grouping before paging is the only way they stay together.
    user_id = users.get_or_create_user(conn)
    pair = transfers.create_transfer(conn, user_id, "charge", "full-credit")
    seen = []
    for offset in range(8):
        total, items = transactions.list_transactions(
            conn, account_external_id="card", group_pairs=True,
            sort="amount", order="asc", limit=1, offset=offset,
        )
        seen.extend(item["id"] for item in items)
    assert total == 5
    assert len(seen) == len(set(seen)) == 5
    assert f"pair-{pair['id']}" in seen
    assert "charge" not in seen and "full-credit" not in seen


def test_a_pair_is_listed_when_either_half_matches_the_search(conn):
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "charge", "full-credit")
    _, items = transactions.list_transactions(conn, q="full-credit", group_pairs=True)
    assert [item["kind"] for item in items] == ["pair"]


def test_migration_007_moves_exact_refunds_into_transfers_and_drops_partial_ones(tmp_path):
    # A database at version 6 that already holds one exact and one partial link.
    connection = sqlite3.connect(tmp_path / "v6.db")
    for migration in sorted(db.MIGRATIONS_DIR.glob("00[1-6]_*.sql")):
        connection.executescript(migration.read_text())
    connection.execute("PRAGMA user_version = 6")
    user_id = users.get_or_create_user(connection)
    account_id = ingest.upsert_account(connection, user_id, AccountRow(
        external_id="card", account_type="liability", account_number="1111",
        issuer_name="Demo card", liability_type="credit_card",
    ))
    ids = {}
    for ext, cents, posted in [
        ("a", -10000, "2026-10-03"), ("a-back", 10000, "2026-10-05"),
        ("b", -10000, "2026-10-03"), ("b-part", 3500, "2026-10-03"),
    ]:
        ingest.upsert_transaction(connection, user_id, account_id, TxRow(
            account_external_id="card", external_id=ext, amount_cents=cents, currency="USD",
            transaction_type="debit" if cents < 0 else "credit", posted_on=posted,
            description=ext, merchant_name=None,
        ))
        ids[ext] = _internal_id(connection, ext)
    for debit, credit in [("a", "a-back"), ("b", "b-part")]:
        connection.execute(
            "INSERT INTO refunds (user_id, debit_transaction_id, credit_transaction_id, created_at) "
            "VALUES (?, ?, ?, 7)", (user_id, ids[debit], ids[credit]),
        )
    connection.commit()

    db.migrate(connection)

    assert connection.execute(
        "SELECT debit_transaction_id, credit_transaction_id, transfer_amount_cents, "
        "transfer_date, matched_by, created_at FROM transfers"
    ).fetchall() == [(ids["a"], ids["a-back"], 10000, "2026-10-05", "user", 7)]
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'refunds'"
    ).fetchall() == []
    activity = {r[0] for r in connection.execute("SELECT external_id FROM activity")}
    assert activity == {"b", "b-part"}  # the partial pair counted before and still does
    connection.close()


def test_api_grouped_list_formats_the_group_and_its_legs(client):
    created = client.post(
        "/api/transfers", json={"transaction_id": "full-credit", "counterpart_id": "charge"}
    ).json()
    body = client.get(
        "/api/transactions", params={"group_pairs": "true", "account_id": "card"}
    ).json()
    group = next(item for item in body["transactions"] if item["kind"] == "pair")
    assert group["transfer_id"] == created["id"]
    assert group["amount_display"] == "$0.00"
    assert [leg["amount_display"] for leg in group["legs"]] == ["-$100.00", "$100.00"]


def test_api_list_takes_a_kind_and_refuses_an_unknown_one(client):
    client.post("/api/transfers", json={"transaction_id": "charge", "counterpart_id": "full-credit"})
    body = client.get("/api/transactions", params={"kind": "credit"}).json()
    assert {row["id"] for row in body["transactions"]} == {"charge", "full-credit"}
    assert client.get("/api/transactions", params={"kind": "refunds"}).status_code == 422
