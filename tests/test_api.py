import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app

VALID_TOKEN = "aHR0cHM6Ly9icmlkZ2Uuc2ltcGxlZmluLm9yZy9jbGFpbS9hYmM="  # base64 claim url
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "simplefin_accounts.json").read_text()
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ui_files_must_revalidate_so_updates_never_mix_old_and_new_modules(client):
    for path in ("/", "/js/main.js", "/css/tokens.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"


def test_connect_status_starts_false(client):
    response = client.get("/api/connect")
    assert response.status_code == 200
    assert response.json() == {"connected": False}


def test_post_connect_success_updates_status(client, monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, text="https://user:pass@bridge.simplefin.org/access"),
    )

    response = client.post("/api/connect", json={"setup_token": VALID_TOKEN})
    assert response.status_code == 200
    assert response.json() == {"connected": True}

    status = client.get("/api/connect")
    assert status.json() == {"connected": True}


def test_post_connect_bad_token_returns_400(client, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(403, text="forbidden"))

    response = client.post("/api/connect", json={"setup_token": VALID_TOKEN})
    assert response.status_code == 400

    status = client.get("/api/connect")
    assert status.json() == {"connected": False}


def test_post_sync_without_connecting_returns_400(client):
    response = client.post("/api/sync")
    assert response.status_code == 400


def test_post_sync_after_connecting_ingests_the_fixture(client, monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, text="https://user:pass@bridge.simplefin.org/access"),
    )
    client.post("/api/connect", json={"setup_token": VALID_TOKEN})

    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))

    response = client.post("/api/sync")
    assert response.status_code == 200
    body = response.json()
    assert body["accounts"] == 4
    assert body["transactions"] == 10
    assert body["balances"] == 4


def _connect_and_sync(client, monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, text="https://user:pass@bridge.simplefin.org/access"),
    )
    client.post("/api/connect", json={"setup_token": VALID_TOKEN})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))
    client.post("/api/sync")


def test_get_accounts_lists_ingested_accounts(client, monkeypatch):
    _connect_and_sync(client, monkeypatch)

    response = client.get("/api/accounts")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 4
    types = {r["account_type"] for r in rows}
    assert types == {"asset", "liability"}
    # the public id is SimpleFIN's own account id, not an internal sequential one
    assert all(r["id"].startswith("ACT-") for r in rows)


def test_patch_account_corrects_type(client, monkeypatch):
    _connect_and_sync(client, monkeypatch)
    checking = next(r for r in client.get("/api/accounts").json() if r["account_type"] == "asset")

    response = client.patch(
        f"/api/accounts/{checking['id']}",
        json={"account_type": "asset", "asset_type": "savings"},
    )
    assert response.status_code == 200
    # the PATCH response is the full updated account, same shape as the list
    body = response.json()
    assert body["asset_type"] == "savings"
    assert body["display_name"] == checking["display_name"]
    assert "balance_display" in body

    updated = next(r for r in client.get("/api/accounts").json() if r["id"] == checking["id"])
    assert updated["asset_type"] == "savings"


def test_patch_account_unknown_id_returns_404(client):
    response = client.patch(
        "/api/accounts/9999", json={"account_type": "asset", "asset_type": "checking"}
    )
    assert response.status_code == 404


def test_patch_account_missing_subtype_returns_422(client, monkeypatch):
    _connect_and_sync(client, monkeypatch)
    any_account = client.get("/api/accounts").json()[0]

    response = client.patch(f"/api/accounts/{any_account['id']}", json={"account_type": "asset"})
    assert response.status_code == 422
