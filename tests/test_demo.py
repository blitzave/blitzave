"""Demo is a profile like Live: same engine, its own folder, no bank, and it
starts over every time it opens. A fresh install opens it; real data never
does. `blitzave --demo` is Demo alone, and never touches the real setup:
not ~/BlitZave, not the keychain, not the bank, not the network."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import keyring
import keyring.backend
import keyring.core
import pytest
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend

from blitzave import assistant, demo, jobs
from blitzave.__main__ import app
from blitzave.sources import models, simplefin
from blitzave.sources.base import Fetched, TxRow
from blitzave.store import db, users
from blitzave.words import goose as goose_words

CLAIM_TOKEN = "aHR0cHM6Ly9icmlkZ2Uuc2ltcGxlZmluLm9yZy9jbGFpbS9hYmM="  # base64 of a claim URL
ACCESS_URL = "https://user:pass@bridge.simplefin.org/simplefin"


@pytest.fixture
def home(monkeypatch, tmp_path):
    """The data folder plain `blitzave` would use: Live's, with Demo inside."""
    folder = tmp_path / "BlitZave"
    monkeypatch.setenv("BLITZAVE_HOME", str(folder))
    return folder


def _client():
    return TestClient(app, base_url="http://127.0.0.1:8742")


def _claim_answers(monkeypatch, response: httpx.Response) -> list:
    claimed = []

    def post(url, *args, **kwargs):
        claimed.append(url)
        return response

    monkeypatch.setattr(httpx, "post", post)
    return claimed


def _live_database_with_a_category(home: Path) -> None:
    conn = db.connect()
    try:
        users.get_or_create_user(conn)
    finally:
        conn.close()
    with _client() as client:
        made = client.post("/api/categories", json={"name": "Mine, not made up", "kind": "expense"})
        assert made.status_code in (200, 201), made.text


def _category_names(client) -> set[str]:
    return {c["name"] for c in client.get("/api/categories").json()}


# --- which profile opens ----------------------------------------------------------


def test_a_fresh_install_opens_demo_in_its_own_folder(home):
    assert jobs.start() is jobs.DEMO

    assert jobs.PROFILE is jobs.DEMO
    assert jobs.DEMO.home == home / "demo"
    assert db.home_dir() == home / "demo"
    assert (home / "demo" / "blitzave.db").exists()
    assert not (home / "blitzave.db").exists()  # Live is never made for it


def test_anyone_with_a_database_opens_live(home):
    db.connect().close()  # real data, even with the keychain entry gone

    assert jobs.start() is jobs.LIVE
    assert jobs.PROFILE.source is simplefin
    assert not (home / "demo").exists()


def test_anyone_with_a_token_opens_live(home):
    simplefin.store_access_url(ACCESS_URL)

    assert jobs.start() is jobs.LIVE
    assert not (home / "demo").exists()


# --- Demo starts over; nothing else is ever deleted -------------------------------


def test_demo_starts_over_every_time_it_opens(home):
    jobs.start()
    with _client() as client:
        accounts = len(client.get("/api/accounts").json())
        client.post("/api/categories", json={"name": "Tried in the demo", "kind": "expense"})
        assert "Tried in the demo" in _category_names(client)

    jobs.open_profile(jobs.DEMO)

    with _client() as client:
        assert "Tried in the demo" not in _category_names(client)
        assert len(client.get("/api/accounts").json()) == accounts == len(demo.ACCOUNTS)


def test_switching_never_touches_the_other_profiles_data(home):
    _live_database_with_a_category(home)
    jobs.start()  # Live: there's a database

    jobs.open_profile(jobs.DEMO)
    with _client() as client:
        assert "Mine, not made up" not in _category_names(client)

    jobs.open_profile(jobs.LIVE)
    with _client() as client:
        assert "Mine, not made up" in _category_names(client)
    assert db.home_dir() == home
    assert (home / "demo" / "blitzave.db").exists()  # kept, until Demo opens again


def test_demo_has_nothing_to_sync(home, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("Demo asked SimpleFIN")

    monkeypatch.setattr(simplefin, "fetch", never)
    simplefin.store_access_url(ACCESS_URL)  # even with a token saved
    jobs.start()
    jobs.open_profile(jobs.DEMO)

    with _client() as client:
        response = client.post("/api/sync")

    assert response.status_code == 400
    assert "no bank" in response.json()["detail"]


def test_demo_never_touches_the_network(home, monkeypatch):
    def offline(self, request):
        raise AssertionError(f"Demo called the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", offline)

    jobs.start()
    jobs.set_update_check(True)  # even with the update check on
    with _client() as client:  # a startup: the sync and the update check
        assert client.get("/api/connect").json() == {
            "connected": False, "demo": True, "can_connect": True
        }


def _home_words(client) -> str:
    home = client.get("/api/home").json()
    return f"{home['headline']} {home['support']}"


def test_home_on_the_demo_household_reads_as_an_ally(home):
    jobs.start()

    with _client() as client:
        words = _home_words(client)

    assert words.strip()
    assert not assistant.NOT_ALLY.search(words), words


def test_home_on_a_bad_month_still_reads_as_an_ally(home):
    # The same household, but nothing set aside lately and a big splurge.
    today = date.today()
    recent = (today - timedelta(days=45)).isoformat()
    good = demo.build(today)
    kept = [
        t for t in good.transactions
        if not (t.description.startswith("Online Transfer") and t.posted_on >= recent)
    ]
    splurge = [
        TxRow(demo.CEDAR, f"demo-bad-{i}", -350_00, "USD", "debit",
              (today - timedelta(days=d)).isoformat(), "LANTERN BISTRO")
        for i, d in enumerate((2, 5, 9))
    ]
    jobs.start()
    jobs.start_demo_over(
        Fetched(accounts=good.accounts, transactions=kept + splurge, balances=good.balances)
    )

    with _client() as client:
        words = _home_words(client)

    assert words.strip()
    assert not assistant.NOT_ALLY.search(words), words


# --- bringing in real data opens Live ---------------------------------------------


def test_connecting_from_demo_opens_live_and_keeps_demo(home, monkeypatch, fake_keyring):
    jobs.start()
    models.save_api_key("sk-typed-in-the-demo")
    _claim_answers(monkeypatch, httpx.Response(200, text=ACCESS_URL))

    with _client() as client:
        assert client.post("/api/connect", json={"setup_token": CLAIM_TOKEN}).status_code == 200
        after = client.get("/api/connect").json()

    assert after == {"connected": True, "demo": False, "can_connect": True}
    assert jobs.PROFILE is jobs.LIVE
    assert db.home_dir() == home
    assert (home / "blitzave.db").exists()
    assert (home / "demo" / "blitzave.db").exists()  # nothing deleted
    # One keychain: the bank's access URL, and the key typed in Demo, both kept.
    assert fake_keyring[(simplefin.KEYRING_SERVICE, simplefin.KEYRING_USERNAME)] == ACCESS_URL
    assert models.has_api_key()


def test_a_bad_token_leaves_demo_as_it_was(home, monkeypatch):
    jobs.start()
    _claim_answers(monkeypatch, httpx.Response(403, text="forbidden"))

    with _client() as client:
        assert client.post("/api/connect", json={"setup_token": CLAIM_TOKEN}).status_code == 400
        assert client.get("/api/connect").json()["demo"] is True

    assert jobs.PROFILE is jobs.DEMO
    assert not (home / "blitzave.db").exists()
    assert not simplefin.is_connected()


def test_bringing_in_an_export_from_demo_lands_in_live(home):
    jobs.start()

    with _client() as client:
        document = client.get("/api/export").json()  # any valid export will do
        response = client.post("/api/import", json=document)
        status = client.get("/api/connect").json()

    assert response.status_code == 200, response.text
    assert status["demo"] is False
    assert jobs.PROFILE is jobs.LIVE
    assert (home / "blitzave.db").exists()


def test_goose_settings_in_demo_say_the_key_goes_to_the_keychain(home):
    jobs.start()
    with _client() as client:
        disclosure = client.get("/api/goose/settings").json()["disclosure"]

    assert disclosure == goose_words.DISCLOSURE  # Demo shares the real keychain


# --- `blitzave --demo`: Demo alone, and nothing real touched ------------------------


class TrapKeyring(KeyringBackend):
    """Stands in for the real keychain: any call means `--demo` didn't
    replace it. No priority, so keyring never picks it on its own."""

    def get_password(self, service, username):
        raise AssertionError("--demo asked the real keychain")

    def set_password(self, service, username, password):
        raise AssertionError("--demo wrote to the real keychain")

    def delete_password(self, service, username):
        raise AssertionError("--demo deleted from the real keychain")


@pytest.fixture
def throwaway(monkeypatch, tmp_path):
    """`blitzave --demo`. Each process-wide change is registered with
    monkeypatch first, so a test leaves everything as it found it."""
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path / "set-before-demo"))
    monkeypatch.setattr(keyring.core, "_keyring_backend", TrapKeyring())
    # conftest fakes keyring's functions with a dict; put the real ones back
    # here, so what answers is whichever backend is set.
    monkeypatch.setattr(keyring, "get_password", keyring.core.get_password)
    monkeypatch.setattr(keyring, "set_password", keyring.core.set_password)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "real-home")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    jobs.start(demo_only=True)
    return jobs.DEMO.home


def test_the_demo_flag_lives_in_a_temporary_folder_never_blitzave(throwaway, tmp_path):
    assert jobs.PROFILE is jobs.DEMO and jobs.LIVE is None
    assert db.home_dir() == throwaway
    assert throwaway.parent == tmp_path and throwaway.name.startswith("blitzave-demo-")
    assert (throwaway / "blitzave.db").exists()
    assert not (tmp_path / "real-home" / "BlitZave").exists()
    assert not (tmp_path / "set-before-demo").exists()


def test_the_demo_flag_keeps_every_secret_in_memory(throwaway):
    assert isinstance(keyring.get_keyring(), demo.MemoryKeyring)

    simplefin.store_access_url("https://u:p@bridge.example/simplefin")
    models.save_api_key("sk-made-up")

    assert simplefin.load_access_url() == "https://u:p@bridge.example/simplefin"
    assert models.has_api_key()


def test_keyring_never_picks_the_memory_keychain_on_its_own():
    # A machine with no keychain must get a loud failure, never secrets that
    # quietly vanish on exit.
    assert not demo.MemoryKeyring.viable
    with pytest.raises(TypeError):
        demo.MemoryKeyring()  # what keyring tries when it looks for a default
    assert not any(isinstance(k, demo.MemoryKeyring) for k in keyring.backend.get_all_keyring())


def test_the_demo_flag_refuses_to_connect_before_spending_the_token(throwaway, monkeypatch):
    claimed = _claim_answers(monkeypatch, httpx.Response(200, text=ACCESS_URL))

    with _client() as client:
        response = client.post("/api/connect", json={"setup_token": CLAIM_TOKEN})

    assert response.status_code == 400
    assert "run `blitzave`" in response.json()["detail"]
    assert claimed == []  # a setup token works once; this one is still good
    assert jobs.PROFILE is jobs.DEMO


def test_the_demo_flag_refuses_an_import(throwaway):
    with _client() as client:
        document = client.get("/api/export").json()
        response = client.post("/api/import", json=document)

    assert response.status_code == 409
    assert jobs.PROFILE is jobs.DEMO


def test_the_demo_flags_goose_settings_say_the_key_stays_in_memory(throwaway):
    with _client() as client:
        disclosure = client.get("/api/goose/settings").json()["disclosure"]

    assert disclosure == goose_words.DEMO_DISCLOSURE
    assert "keychain" not in disclosure
    assert "forgotten when you stop" in disclosure
    assert goose_words.DEMO_DISCLOSURE != goose_words.DISCLOSURE  # the replace found its sentence


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM], ids=["ctrl-c", "kill"])
def test_stopping_the_demo_flag_deletes_its_folder(tmp_path, stop):
    try:
        httpx.get(f"http://127.0.0.1:{demo.PORT}/api/health")
        pytest.skip(f"something is already listening on {demo.PORT} (a demo running?)")
    except httpx.TransportError:
        pass  # free, as it should be
    env = {
        **os.environ,
        "TMPDIR": str(tmp_path),  # where mkdtemp puts the demo folder
        "HOME": str(tmp_path / "real-home"),
        "BROWSER": "true",  # "opens" the tab with /usr/bin/true
        # If --demo ever forgot its own keychain, this one fails loudly
        # instead of reaching the real one.
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
    }
    env.pop("BLITZAVE_HOME", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "blitzave", "--demo"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{demo.PORT}/api/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if server.poll() is not None:
                pytest.fail(f"the demo exited early:\n{server.stderr.read()}")
            time.sleep(0.1)
        else:
            pytest.fail("the demo never answered")
        [folder] = tmp_path.glob("blitzave-demo-*")
        assert (folder / "blitzave.db").exists()

        server.send_signal(stop)
        server.wait(timeout=10)
    finally:
        if server.poll() is None:
            server.kill()

    assert server.returncode == 0, server.stderr.read()
    assert list(tmp_path.glob("blitzave-demo-*")) == []
    assert not (tmp_path / "real-home" / "BlitZave").exists()
