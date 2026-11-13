"""Export and import (P0.1): every table out as one JSON document, and back
into a fresh install, all or nothing. The round trip changes nothing."""

import json

from fastapi.testclient import TestClient

from blitzave.__main__ import URL, create_app
from blitzave.sources.base import AccountRow, BalanceRow, TxRow
from blitzave.store import db, ingest, users


def _client(tmp_path, monkeypatch, name):
    home = tmp_path / name
    home.mkdir()
    monkeypatch.setenv("BLITZAVE_HOME", str(home))
    return TestClient(create_app(), base_url=URL)


def _fill(client):
    """A little of everything: three kinds of account, balances, a transfer
    pair, categories with a budget and a goal, a rule, a tag, watermarks."""
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        conn.execute("UPDATE users SET simplefin_hash = 'abc123' WHERE id = ?", (user_id,))
        conn.commit()
        users.update_meta(conn, user_id, {"update_check": True})
        rows = [
            ("ACT-chase", "1001", "asset", "checking", -0),
            ("ACT-ally", "4821", "asset", "savings", 0),
            ("ACT-amex", "1002", "liability", None, 0),
        ]
        ids = {}
        for ext, number, kind, job, _ in rows:
            ids[ext] = ingest.upsert_account(conn, user_id, AccountRow(
                external_id=ext, account_type=kind, account_number=number,
                bank_name="Bank" if kind == "asset" else None, asset_type=job,
                issuer_name="Amex" if kind == "liability" else None,
                liability_type="credit_card" if kind == "liability" else None,
                meta={"name": f"Account {number}"}))
        ingest.upsert_balance(conn, ids["ACT-chase"], BalanceRow("ACT-chase", "2026-11-07", 324000))
        ingest.upsert_balance(conn, ids["ACT-ally"], BalanceRow("ACT-ally", "2026-11-07", 1000000))
        for tx in [
            TxRow("ACT-chase", "T1", -5250, "USD", "debit", "2026-11-02", "STARBUCKS #123"),
            TxRow("ACT-chase", "T2", -60000, "USD", "debit", "2026-11-01", "Transfer to Ally"),
            TxRow("ACT-ally", "T3", 60000, "USD", "credit", "2026-11-01", "Transfer from Chase"),
        ]:
            ingest.upsert_transaction(conn, user_id, ids[tx.account_external_id], tx)
        ingest.set_watermark(conn, "ingest", "2026-11-07")
    finally:
        conn.close()

    assert client.patch("/api/accounts/ACT-ally",
                        json={"account_type": "asset", "asset_type": "targeted"}).status_code == 200
    coffee = client.post("/api/categories", json={
        "name": "Coffee", "kind": "expense", "budget_amount": "40",
        "schedule": "recurring", "period_months": 1}).json()
    client.post("/api/categories", json={
        "name": "Emergency fund", "kind": "savings", "budget_amount": "9000",
        "schedule": "once", "target_on": "2027-06-01",
        "goal_level": "critical", "account_id": "ACT-ally"})
    tag = client.post("/api/tags", json={"name": "Weekday"}).json()
    assert client.patch("/api/transactions/T1", json={
        "categories": {str(coffee["id"]): 1.0}, "tags": [tag["id"]],
        "apply_to_all": True}).status_code == 200
    assert client.post("/api/transfers", json={
        "transaction_id": "T2", "counterpart_id": "T3"}).status_code == 201


def test_a_round_trip_changes_nothing(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "old-laptop") as old:
        _fill(old)
        exported = old.get("/api/export")
        assert exported.status_code == 200
        assert exported.headers["content-disposition"].startswith(
            'attachment; filename="blitzave-export-'
        )
        document = exported.json()
        before_goals = old.get("/api/goals").json()

    # Every table holds something, and nothing about the connection leaks.
    assert all(dump["rows"] for name, dump in document["tables"].items())
    assert "simplefin_hash" not in document["tables"]["users"]["columns"]
    assert "display_name" not in document["tables"]["accounts"]["columns"]

    with _client(tmp_path, monkeypatch, "new-laptop") as new:
        imported = new.post("/api/import", json=document)
        assert imported.status_code == 200
        assert imported.json()["imported"]["transactions"] == 3
        assert new.get("/api/export").json() == document
        assert new.get("/api/goals").json() == before_goals
        # It's a fresh install no more: a second import is refused.
        assert new.post("/api/import", json=document).status_code == 409


def test_an_export_from_another_version_is_refused(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "old") as old:
        _fill(old)
        document = old.get("/api/export").json()
    document["schema_version"] -= 1
    with _client(tmp_path, monkeypatch, "new") as new:
        refused = new.post("/api/import", json=document)
        assert refused.status_code == 422
        assert "version" in refused.json()["detail"]


def test_a_bad_row_writes_nothing(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "old") as old:
        _fill(old)
        document = old.get("/api/export").json()
    # A transfer whose legs don't balance breaks the database's own rule.
    transfers = document["tables"]["transfers"]
    amount = transfers["columns"].index("transfer_amount_cents")
    transfers["rows"][0][amount] = 1
    with _client(tmp_path, monkeypatch, "new") as new:
        refused = new.post("/api/import", json=document)
        assert refused.status_code == 422
        assert "refused" in refused.json()["detail"]
        assert new.get("/api/accounts").json() == []
        assert new.get("/api/categories").json() == []


def test_something_that_isnt_an_export_is_refused(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "new") as new:
        assert new.post("/api/import", json={"hello": "world"}).status_code == 422
        assert new.post("/api/import", json={
            "format": "something-else", "schema_version": 10, "tables": {}}).status_code == 422
        assert new.post("/api/import", content=json.dumps([1, 2]),
                        headers={"Content-Type": "application/json"}).status_code == 422
