import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app

VALID_TOKEN = "aHR0cHM6Ly9icmlkZ2Uuc2ltcGxlZmluLm9yZy9jbGFpbS9hYmM="
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "simplefin_accounts.json").read_text()
)
HEB_TXN = "TRN-80100615-54b8-43ff-9675-2873d12e7a1f"  # H-E-B -121.01 on the Amex Gold


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))


@pytest.fixture
def client(monkeypatch):
    with TestClient(create_app()) as c:
        monkeypatch.setattr(
            httpx,
            "post",
            lambda *a, **k: httpx.Response(200, text="https://u:p@bridge.simplefin.org/access"),
        )
        c.post("/api/connect", json={"setup_token": VALID_TOKEN})
        monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))
        c.post("/api/sync")
        yield c


# --- categories --------------------------------------------------------------


def test_category_crud_cycle(client):
    created = client.post("/api/categories", json={"name": "Groceries", "kind": "expense"})
    assert created.status_code == 201
    category_id = created.json()["id"]

    assert client.post(
        "/api/categories", json={"name": "Groceries", "kind": "expense"}
    ).status_code == 409  # duplicate name

    renamed = client.patch(f"/api/categories/{category_id}", json={"name": "Food"})
    assert renamed.status_code == 200
    assert renamed.json() == {"id": category_id, "name": "Food", "kind": "expense"}

    assert client.delete(f"/api/categories/{category_id}").status_code == 204
    assert client.get("/api/categories").json() == []


def test_category_delete_blocked_while_in_use(client):
    category_id = client.post(
        "/api/categories", json={"name": "Food", "kind": "expense"}
    ).json()["id"]
    client.patch(f"/api/transactions/{HEB_TXN}", json={"categories": {str(category_id): 1.0}})

    assert client.delete(f"/api/categories/{category_id}").status_code == 409


# --- tags ---------------------------------------------------------------------


def test_tag_create_list_delete(client):
    created = client.post("/api/tags", json={"name": "work"})
    assert created.status_code == 201
    tag_id = created.json()["id"]

    assert client.post("/api/tags", json={"name": "work"}).status_code == 409

    client.patch(f"/api/transactions/{HEB_TXN}", json={"tags": [tag_id]})
    assert client.delete(f"/api/tags/{tag_id}").status_code == 204  # unlinks, then deletes

    txn = next(
        t
        for t in client.get("/api/transactions").json()["transactions"]
        if t["id"] == HEB_TXN
    )
    assert txn["tags"] == []


# --- transactions: list ------------------------------------------------------


def test_transactions_list_has_display_money_and_sorts_by_amount(client):
    body = client.get("/api/transactions", params={"sort": "amount", "order": "asc"}).json()
    assert body["total"] == 10
    amounts = [t["amount_cents"] for t in body["transactions"]]
    assert amounts == sorted(amounts)
    first = body["transactions"][0]
    assert first["description"] == "CAPITAL ONE MEMBER FEE"
    assert first["amount_display"] == "-395.00"


def test_transactions_list_filters_by_account(client):
    body = client.get(
        "/api/transactions",
        params={"account_id": "ACT-a5ec3a62-0c88-4149-88b2-505a923c64fc"},
    ).json()
    assert body["total"] == 3
    assert all(
        t["account_id"] == "ACT-a5ec3a62-0c88-4149-88b2-505a923c64fc"
        for t in body["transactions"]
    )


# --- transactions: metadata PATCH --------------------------------------------


def test_patch_transaction_sets_split_categories_and_tags(client):
    food = client.post("/api/categories", json={"name": "Food", "kind": "expense"}).json()["id"]
    fun = client.post("/api/categories", json={"name": "Fun", "kind": "expense"}).json()["id"]
    tag = client.post("/api/tags", json={"name": "family"}).json()["id"]

    response = client.patch(
        f"/api/transactions/{HEB_TXN}",
        json={"categories": {str(food): 0.6, str(fun): 0.4}, "tags": [tag]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["categories"] == {str(food): 0.6, str(fun): 0.4}
    assert body["categories_set_by"] == "user"
    assert body["tags"] == [{"id": tag, "name": "family"}]
    assert body["amount_display"] == "-121.01"  # the bank's record, untouched


def test_patch_transaction_rejects_bad_splits_and_unknown_ids(client):
    assert client.patch(
        f"/api/transactions/{HEB_TXN}", json={"categories": {"1": 0.5}}
    ).status_code == 422  # doesn't sum to 1.0

    assert client.patch(
        f"/api/transactions/{HEB_TXN}", json={"categories": {"9999": 1.0}}
    ).status_code == 422  # unknown category

    assert client.patch(
        f"/api/transactions/{HEB_TXN}", json={"tags": [12345]}
    ).status_code == 422  # unknown tag

    assert client.patch(f"/api/transactions/{HEB_TXN}", json={}).status_code == 422

    assert client.patch(
        "/api/transactions/TRN-nope", json={"tags": []}
    ).status_code == 404


def test_patch_transaction_cannot_touch_the_banks_record(client):
    before = next(
        t
        for t in client.get("/api/transactions").json()["transactions"]
        if t["id"] == HEB_TXN
    )
    # extra fields are ignored by the model, never applied
    client.patch(
        f"/api/transactions/{HEB_TXN}",
        json={"tags": [], "amount_cents": 1, "description": "hacked", "posted_on": "1999-01-01"},
    )
    after = next(
        t
        for t in client.get("/api/transactions").json()["transactions"]
        if t["id"] == HEB_TXN
    )
    assert after["amount_cents"] == before["amount_cents"]
    assert after["description"] == before["description"]
    assert after["posted_on"] == before["posted_on"]


# --- category summary ---------------------------------------------------------


def test_category_summary_math_with_splits(client):
    food = client.post("/api/categories", json={"name": "Food", "kind": "expense"}).json()["id"]
    fun = client.post("/api/categories", json={"name": "Fun", "kind": "expense"}).json()["id"]
    client.patch(
        f"/api/transactions/{HEB_TXN}",
        json={"categories": {str(food): 0.6, str(fun): 0.4}},
    )

    body = client.get(
        "/api/categories/summary", params={"start": "2020-01-01", "end": "2030-01-01"}
    ).json()
    totals = {c["name"]: c["total_cents"] for c in body["categories"]}
    # -12101 split 60/40: -7260.6 -> -7261 and -4840.4 -> -4840; no cent invented or lost
    assert totals["Food"] == -7261
    assert totals["Fun"] == -4840
    assert totals["Food"] + totals["Fun"] == -12101
    # everything else is uncategorized: full fixture sum minus the H-E-B txn
    all_cents = sum(
        t["amount_cents"] for t in client.get("/api/transactions").json()["transactions"]
    )
    assert body["uncategorized_cents"] == all_cents - (-12101)


# --- accounts: balances -------------------------------------------------------


def test_accounts_include_latest_balance_display(client):
    rows = client.get("/api/accounts").json()
    venture_x = next(
        r for r in rows if r["id"] == "ACT-a5ec3a62-0c88-4149-88b2-505a923c64fc"
    )
    assert venture_x["balance_cents"] == -838788
    assert venture_x["balance_display"] == "-8387.88"
    assert venture_x["balance_as_of"] is not None
