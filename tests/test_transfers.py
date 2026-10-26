import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app
from blitzave.sources.base import AccountRow, TxRow
from blitzave.store import categories, db, ingest, transfers, users

# Synthetic data only. A checking account pays off a credit card: the
# checking debit and the card credit are the two legs of one transfer.
ACCOUNTS = [
    AccountRow(
        external_id="ACT-checking",
        account_type="asset",
        account_number="1111",
        bank_name="Chase",
        asset_type="checking",
    ),
    AccountRow(
        external_id="ACT-card",
        account_type="liability",
        account_number="2222",
        issuer_name="Amex",
        liability_type="credit_card",
    ),
    AccountRow(
        external_id="ACT-savings",
        account_type="asset",
        account_number="3333",
        bank_name="Ally",
        asset_type="savings",
    ),
]
TXNS = [
    # account, external id, cents, posted_on, description
    ("ACT-checking", "TXN-pay", -50000, "2026-03-10", "AMEX EPAYMENT"),
    ("ACT-card", "TXN-paid", 50000, "2026-03-12", "PAYMENT THANK YOU"),
    ("ACT-savings", "TXN-save-in", 50000, "2026-03-01", "TRANSFER IN"),
    ("ACT-card", "TXN-far", 50000, "2026-01-01", "PAYMENT THANK YOU"),
    ("ACT-checking", "TXN-same-acct", 50000, "2026-03-10", "REFUND"),
    ("ACT-card", "TXN-other-amt", 49999, "2026-03-10", "PAYMENT"),
    ("ACT-card", "TXN-food", -1070, "2026-03-11", "H-E-B"),
    # The maintainer's case: a $3,000 card purchase, then checking pays the card off.
    ("ACT-card", "TXN-fragomen", -300000, "2026-04-02", "FRAGOMEN"),
    ("ACT-checking", "TXN-card-pay", -300000, "2026-04-20", "AMEX EPAYMENT"),
    ("ACT-card", "TXN-card-paid", 300000, "2026-04-22", "PAYMENT THANK YOU"),
]


def _seed(conn: sqlite3.Connection) -> None:
    user_id = users.get_or_create_user(conn)
    account_ids = {a.external_id: ingest.upsert_account(conn, user_id, a) for a in ACCOUNTS}
    for account_ext, ext, cents, posted, desc in TXNS:
        ingest.upsert_transaction(
            conn,
            user_id,
            account_ids[account_ext],
            TxRow(
                account_external_id=account_ext,
                external_id=ext,
                amount_cents=cents,
                currency="USD",
                transaction_type="debit" if cents < 0 else "credit",
                posted_on=posted,
                description=desc,
                merchant_name=None,
            ),
        )


# --- store --------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "test.db")
    connection.execute("PRAGMA foreign_keys = ON")
    db.migrate(connection)
    _seed(connection)
    yield connection
    connection.close()


def test_candidates_are_exact_opposite_other_account_closest_date_first(conn):
    rows = transfers.list_candidates(conn, "TXN-pay")
    # same-account +500.00 and the +499.99 are left out
    assert [r["id"] for r in rows] == ["TXN-paid", "TXN-save-in", "TXN-far"]
    assert all(r["amount_cents"] == 50000 for r in rows)
    assert rows[0]["account_display_name"] == "liability-2222"


def test_candidates_leave_out_rows_already_in_a_transfer(conn):
    user_id = users.get_or_create_user(conn)
    transfers.create_transfer(conn, user_id, "TXN-pay", "TXN-save-in")
    # TXN-pay is now a leg, so asking from the other side finds nothing free
    assert [r["id"] for r in transfers.list_candidates(conn, "TXN-paid")] == []
    with pytest.raises(transfers.AlreadyTransferLegError):
        transfers.list_candidates(conn, "TXN-pay")


def test_create_transfer_works_out_debit_and_credit_in_either_order(conn):
    user_id = users.get_or_create_user(conn)
    transfer = transfers.create_transfer(conn, user_id, "TXN-paid", "TXN-pay")
    assert transfer["debit"]["id"] == "TXN-pay"
    assert transfer["credit"]["id"] == "TXN-paid"
    assert transfer["amount_cents"] == 50000
    assert transfer["transfer_date"] == "2026-03-12"  # the later leg
    assert transfer["matched_by"] == "user"


def test_create_transfer_rejects_invalid_pairs(conn):
    user_id = users.get_or_create_user(conn)
    with pytest.raises(transfers.InvalidTransferPairError):
        transfers.create_transfer(conn, user_id, "TXN-pay", "TXN-same-acct")
    with pytest.raises(transfers.InvalidTransferPairError):
        transfers.create_transfer(conn, user_id, "TXN-pay", "TXN-other-amt")
    assert transfers.list_transfers(conn) == []


def test_delete_transfer_sets_no_auto_transfer_on_both_legs(conn):
    user_id = users.get_or_create_user(conn)
    transfer = transfers.create_transfer(conn, user_id, "TXN-pay", "TXN-paid")
    transfers.delete_transfer(conn, transfer["id"])
    metas = [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT meta FROM transactions WHERE external_id IN ('TXN-pay', 'TXN-paid')"
        )
    ]
    assert metas == [{"no_auto_transfer": True}, {"no_auto_transfer": True}]
    with pytest.raises(transfers.TransferNotFoundError):
        transfers.delete_transfer(conn, transfer["id"])


# --- API ----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    seed_conn = db.connect()
    try:
        _seed(seed_conn)
    finally:
        seed_conn.close()
    # No SimpleFIN connection, so the startup sync is skipped and only the
    # synthetic rows above exist.
    with TestClient(create_app()) as c:
        yield c


def _transfer_ids(client, **params) -> dict[str, int | None]:
    """{transaction id: transfer_id} for every row the list returns."""
    rows = client.get("/api/transactions", params=params).json()["transactions"]
    return {t["id"]: t["transfer_id"] for t in rows}


def test_api_candidates_have_display_money(client):
    response = client.get("/api/transactions/TXN-pay/transfer-candidates")
    assert response.status_code == 200
    body = response.json()
    assert [r["id"] for r in body] == ["TXN-paid", "TXN-save-in", "TXN-far"]
    assert body[0]["amount_display"] == "$500.00"


def test_api_linked_legs_stay_listed_but_leave_category_views_and_totals(client):
    bills = client.post("/api/categories", json={"name": "Bills", "kind": "expense"}).json()
    client.patch("/api/transactions/TXN-pay", json={"categories": {str(bills["id"]): 1.0}})

    response = client.post(
        "/api/transfers", json={"transaction_id": "TXN-pay", "counterpart_id": "TXN-paid"}
    )
    assert response.status_code == 201
    created = response.json()
    assert created["amount_display"] == "$500.00"
    assert created["debit"]["id"] == "TXN-pay"
    assert created["debit"]["amount_display"] == "-$500.00"
    assert created["credit"]["id"] == "TXN-paid"

    assert client.get("/api/transfers").json() == [created]
    # Each account still shows its real debit and credit, labelled as a leg.
    listed = _transfer_ids(client)
    assert listed["TXN-pay"] == created["id"]
    assert listed["TXN-paid"] == created["id"]
    assert listed["TXN-food"] is None
    assert _transfer_ids(client, account_id="ACT-checking")["TXN-pay"] == created["id"]
    assert _transfer_ids(client, account_id="ACT-card")["TXN-paid"] == created["id"]
    # ...but not under a category, since it no longer counts toward one.
    assert "TXN-pay" not in _transfer_ids(client, category_id=bills["id"])

    summary = client.get(
        "/api/categories/summary", params={"start": "2026-01-01", "end": "2026-12-31"}
    ).json()
    totals = {c["name"]: c["total_cents"] for c in summary["categories"]}
    assert totals["Bills"] == 0  # the card payment is no longer counted


def test_api_linked_leg_refuses_a_category_but_takes_tags(client):
    bills = client.post("/api/categories", json={"name": "Bills", "kind": "expense"}).json()
    tag = client.post("/api/tags", json={"name": "card"}).json()
    client.post("/api/transfers", json={"transaction_id": "TXN-pay", "counterpart_id": "TXN-paid"})

    for body in (
        {"categories": {str(bills["id"]): 1.0}},
        {"categories": {str(bills["id"]): 1.0}, "apply_to_all": True},
    ):
        response = client.patch("/api/transactions/TXN-paid", json=body)
        assert response.status_code == 409
    with db.connect() as check:
        assert check.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0] == 0

    response = client.patch("/api/transactions/TXN-paid", json={"tags": [tag["id"]]})
    assert response.status_code == 200
    assert [t["name"] for t in response.json()["tags"]] == ["card"]


def test_api_counterpart_order_does_not_matter(client):
    response = client.post(
        "/api/transfers", json={"transaction_id": "TXN-paid", "counterpart_id": "TXN-pay"}
    )
    assert response.status_code == 201
    assert response.json()["debit"]["id"] == "TXN-pay"


def test_api_rejects_invalid_and_already_linked_pairs(client):
    def link(a: str, b: str) -> int:
        return client.post(
            "/api/transfers", json={"transaction_id": a, "counterpart_id": b}
        ).status_code

    assert link("TXN-pay", "TXN-same-acct") == 422
    assert link("TXN-pay", "TXN-other-amt") == 422
    assert link("TXN-pay", "TXN-paid") == 201
    assert link("TXN-pay", "TXN-save-in") == 409  # TXN-pay is already a leg
    assert client.get("/api/transactions/TXN-pay/transfer-candidates").status_code == 409


def test_api_delete_restores_both_legs_with_categories_intact(client):
    bills = client.post("/api/categories", json={"name": "Bills", "kind": "expense"}).json()
    client.patch("/api/transactions/TXN-pay", json={"categories": {str(bills["id"]): 1.0}})
    transfer_id = client.post(
        "/api/transfers", json={"transaction_id": "TXN-pay", "counterpart_id": "TXN-paid"}
    ).json()["id"]

    assert client.delete(f"/api/transfers/{transfer_id}").status_code == 204
    assert client.get("/api/transfers").json() == []
    listed = _transfer_ids(client)
    assert listed["TXN-pay"] is None and listed["TXN-paid"] is None
    assert "TXN-pay" in _transfer_ids(client, category_id=bills["id"])
    pay = next(
        t for t in client.get("/api/transactions").json()["transactions"] if t["id"] == "TXN-pay"
    )
    assert pay["categories"] == {str(bills["id"]): 1.0}

    conn = db.connect()
    try:
        category_totals, _ = categories.summary(conn, "2026-01-01", "2026-12-31")
        meta = conn.execute(
            "SELECT meta FROM transactions WHERE external_id = 'TXN-paid'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert {c["name"]: c["total_cents"] for c in category_totals}["Bills"] == -50000
    assert json.loads(meta) == {"no_auto_transfer": True}


def test_api_404s(client):
    assert client.get("/api/transactions/TXN-nope/transfer-candidates").status_code == 404
    assert client.post(
        "/api/transfers", json={"transaction_id": "TXN-pay", "counterpart_id": "TXN-nope"}
    ).status_code == 404
    assert client.delete("/api/transfers/999").status_code == 404


def test_api_card_payment_counts_the_purchase_once(client):
    legal = client.post("/api/categories", json={"name": "Legal", "kind": "expense"}).json()
    client.patch("/api/transactions/TXN-fragomen", json={"categories": {str(legal["id"]): 1.0}})
    assert client.post(
        "/api/transfers",
        json={"transaction_id": "TXN-card-pay", "counterpart_id": "TXN-card-paid"},
    ).status_code == 201

    april = {"start": "2026-04-01", "end": "2026-04-30"}
    summary = client.get("/api/categories/summary", params=april).json()
    totals = {c["name"]: c["total_cents"] for c in summary["categories"]}
    assert totals["Legal"] == -300000
    assert summary["uncategorized_cents"] == 0  # the payment legs aren't counted

    conn = db.connect()
    try:
        spending = conn.execute(
            "SELECT SUM(amount_cents) FROM activity "
            "WHERE amount_cents < 0 AND posted_on BETWEEN ? AND ?",
            (april["start"], april["end"]),
        ).fetchone()[0]
    finally:
        conn.close()
    assert spending == -300000  # exactly $3,000 of spending, not $6,000

    # All three rows are still in the list: the purchase, and both payment legs.
    listed = _transfer_ids(client, **april)
    assert set(listed) == {"TXN-fragomen", "TXN-card-pay", "TXN-card-paid"}
    assert listed["TXN-fragomen"] is None
