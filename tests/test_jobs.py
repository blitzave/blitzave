import sqlite3

import httpx
import pytest

from blitzave import jobs
from blitzave.store import users


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    return tmp_path


def test_simplefin_connected_reflects_keychain_state():
    assert jobs.simplefin_connected() is False


def test_connect_simplefin_stores_hash_on_the_user(monkeypatch, isolated_home):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, text="https://user:pass@bridge.simplefin.org/access"),
    )
    token = "aHR0cHM6Ly9icmlkZ2Uuc2ltcGxlZmluLm9yZy9jbGFpbS9hYmM="  # base64 claim url

    jobs.connect_simplefin(token)

    assert jobs.simplefin_connected() is True
    conn = sqlite3.connect(isolated_home / "blitzave.db")
    user_id = users.get_or_create_user(conn)
    assert users.get_simplefin_hash(conn, user_id) is not None
    conn.close()


def test_connect_simplefin_raises_connect_error_on_bad_token(monkeypatch, isolated_home):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(403, text="forbidden"))
    token = "aHR0cHM6Ly9icmlkZ2Uuc2ltcGxlZmluLm9yZy9jbGFpbS9hYmM="

    with pytest.raises(jobs.ConnectError):
        jobs.connect_simplefin(token)

    assert jobs.simplefin_connected() is False
