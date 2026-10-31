import json
import socket
import webbrowser
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

import blitzave
from blitzave.__main__ import URL, WEB_DIR, BrowserOpeningServer, create_app

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


def test_ui_lives_inside_the_package_so_uv_tool_install_ships_it():
    # Outside the package, a source checkout still works but an installed
    # copy starts with no UI at all, because create_app() skips a missing dir.
    assert WEB_DIR.parent == Path(blitzave.__file__).resolve().parent
    assert (WEB_DIR / "index.html").is_file()


def test_browser_tab_opens_only_once_the_server_accepts_connections(monkeypatch):
    server = BrowserOpeningServer(
        uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_config=None)
    )
    opened = []

    def fake_open(url):
        port = server.servers[0].sockets[0].getsockname()[1]
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        opened.append(url)
        server.should_exit = True  # stop right after startup

    monkeypatch.setattr(webbrowser, "open", fake_open)
    server.run()
    assert opened == [URL]


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


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "26-10", "2026-W01", "0000-01", "2999-01"])
def test_post_sync_month_rejects_a_bad_or_future_month(client, month):
    response = client.post("/api/sync/month", json={"month": month})
    assert response.status_code == 422


def test_post_sync_month_without_connecting_returns_400(client):
    response = client.post("/api/sync/month", json={"month": "2026-01"})
    assert response.status_code == 400


def test_post_sync_month_after_connecting_ingests_the_fixture(client, monkeypatch):
    _connect_and_sync(client, monkeypatch)

    response = client.post("/api/sync/month", json={"month": "2026-01"})
    assert response.status_code == 200
    assert response.json()["month"] == "2026-01"


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
