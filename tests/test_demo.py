"""Demo mode never touches the user's real setup: not ~/BlitZave, not the
keychain, not the bank, not the network. And it cleans up after itself."""

import os
import shutil
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

from blitzave import __main__ as app_main
from blitzave import assistant, demo, jobs
from blitzave.__main__ import app
from blitzave.sources import models, simplefin
from blitzave.sources.base import Fetched, TxRow
from blitzave.store import db


class TrapKeyring(KeyringBackend):
    """Stands in for the real keychain: any call means demo mode didn't
    replace it. No priority, so keyring never picks it on its own."""

    def get_password(self, service, username):
        raise AssertionError("demo mode asked the real keychain")

    def set_password(self, service, username, password):
        raise AssertionError("demo mode wrote to the real keychain")

    def delete_password(self, service, username):
        raise AssertionError("demo mode deleted from the real keychain")


class RealKeyring(KeyringBackend):
    """The keychain a fresh install goes back to when the user connects."""

    def __init__(self, secrets: dict):
        super().__init__()
        self.secrets = secrets

    def get_password(self, service, username):
        return self.secrets.get((service, username))

    def set_password(self, service, username, password):
        self.secrets[(service, username)] = password

    def delete_password(self, service, username):
        self.secrets.pop((service, username))


@pytest.fixture
def demo_state(monkeypatch, tmp_path):
    """Starting a demo changes process-wide state. Each piece is registered
    with monkeypatch first, so a test leaves everything as it found it."""
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path / "set-before-demo"))
    monkeypatch.setattr(jobs, "SOURCE", jobs.SOURCE)
    monkeypatch.setattr(jobs, "DEMO", None)
    monkeypatch.setattr(keyring.core, "_keyring_backend", TrapKeyring())
    # conftest fakes keyring's functions with a dict; put the real ones back
    # here, so what answers is whichever backend is set.
    monkeypatch.setattr(keyring, "get_password", keyring.core.get_password)
    monkeypatch.setattr(keyring, "set_password", keyring.core.set_password)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "real-home")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture
def in_demo(demo_state):
    """`blitzave --demo`: a demo and nothing else."""
    started = jobs.start_demo()
    yield started.home
    shutil.rmtree(started.home, ignore_errors=True)


@pytest.fixture
def trying_demo(demo_state):
    """Plain `blitzave` on a fresh install: a demo until the user connects."""
    real = RealKeyring({})
    started = jobs.start_demo(real)
    yield started, real
    shutil.rmtree(started.home, ignore_errors=True)


def _client():
    return TestClient(app, base_url=f"http://127.0.0.1:{demo.PORT}")


def test_demo_lives_in_a_new_temporary_folder_never_blitzave(in_demo, tmp_path):
    demo.set_up(app)

    assert db.home_dir() == in_demo
    assert in_demo.parent == tmp_path and in_demo.name.startswith("blitzave-demo-")
    assert (in_demo / "blitzave.db").exists()
    assert not (tmp_path / "real-home" / "BlitZave").exists()


def test_demo_keeps_every_secret_in_memory(in_demo):
    assert isinstance(keyring.get_keyring(), demo.MemoryKeyring)

    demo.set_up(app)
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


def test_demo_never_touches_the_network_at_startup(in_demo, monkeypatch):
    def offline(self, request):
        raise AssertionError(f"demo mode called the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", offline)

    demo.set_up(app)  # syncs the fictional bank
    jobs.set_update_check(True)  # even with the update check on
    with _client() as client:  # a second startup
        assert client.get("/api/connect").json() == {
            "connected": True, "demo": True, "can_connect": False
        }


def test_the_bank_is_the_fictional_one(in_demo, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("demo mode asked SimpleFIN")

    monkeypatch.setattr(simplefin, "is_connected", never)
    monkeypatch.setattr(simplefin, "fetch", never)

    demo.set_up(app)

    with _client() as client:
        accounts = client.get("/api/accounts").json()
    assert len(accounts) == len(demo.ACCOUNTS)


def _home_words(client) -> str:
    home = client.get("/api/home").json()
    return f"{home['headline']} {home['support']}"


def test_home_on_the_demo_household_reads_as_an_ally(in_demo):
    demo.set_up(app)

    with _client() as client:
        words = _home_words(client)

    assert words.strip()
    assert not assistant.NOT_ALLY.search(words), words


def test_home_on_a_bad_month_still_reads_as_an_ally(in_demo):
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
    jobs.SOURCE = demo.FictionalBank(
        Fetched(accounts=good.accounts, transactions=kept + splurge, balances=good.balances)
    )

    demo.set_up(app)

    with _client() as client:
        words = _home_words(client)

    assert words.strip()
    assert not assistant.NOT_ALLY.search(words), words


# --- the real command, start to stop ---------------------------------------------


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM], ids=["ctrl-c", "kill"])
def test_stopping_the_demo_deletes_its_folder(tmp_path, stop):
    env = {
        **os.environ,
        "TMPDIR": str(tmp_path),  # where mkdtemp puts the demo folder
        "HOME": str(tmp_path / "real-home"),
        "BROWSER": "true",  # "opens" the tab with /usr/bin/true
        # If demo mode ever forgot its own keychain, this one fails loudly
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


# --- plain `blitzave` on a fresh install: a demo until the bank is connected -----

CLAIM_TOKEN = "aHR0cHM6Ly9icmlkZ2Uuc2ltcGxlZmluLm9yZy9jbGFpbS9hYmM="  # base64 of a claim URL
ACCESS_URL = "https://user:pass@bridge.simplefin.org/simplefin"


def _claim_answers(monkeypatch, response: httpx.Response) -> list:
    claimed = []

    def post(url, *args, **kwargs):
        claimed.append(url)
        return response

    monkeypatch.setattr(httpx, "post", post)
    return claimed


def test_a_fresh_install_starts_as_a_demo(monkeypatch, tmp_path):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path / "BlitZave"))
    assert app_main._fresh_install()
    assert not (tmp_path / "BlitZave").exists()  # looking never creates the folder


def test_anyone_with_a_database_never_sees_the_demo(monkeypatch, tmp_path):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    db.connect().close()  # real data, even with the keychain entry gone
    assert not app_main._fresh_install()


def test_anyone_with_a_token_never_sees_the_demo(monkeypatch, tmp_path):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path / "BlitZave"))
    simplefin.store_access_url(ACCESS_URL)
    assert not app_main._fresh_install()


def test_connecting_from_the_demo_switches_to_the_real_setup(trying_demo, monkeypatch, tmp_path):
    started, real = trying_demo
    demo.set_up(app)
    models.save_api_key("sk-typed-in-the-demo")
    _claim_answers(monkeypatch, httpx.Response(200, text=ACCESS_URL))

    with _client() as client:
        assert client.get("/api/connect").json()["can_connect"] is True
        assert client.post("/api/connect", json={"setup_token": CLAIM_TOKEN}).status_code == 200
        after = client.get("/api/connect").json()

    assert after == {"connected": True, "demo": False, "can_connect": True}
    assert jobs.DEMO is None and jobs.SOURCE is simplefin
    assert keyring.get_keyring() is real
    assert real.secrets == {(simplefin.KEYRING_SERVICE, simplefin.KEYRING_USERNAME): ACCESS_URL}
    assert db.home_dir() == tmp_path / "set-before-demo"  # BLITZAVE_HOME as it was
    assert (tmp_path / "set-before-demo" / "blitzave.db").exists()
    assert not started.home.exists()  # the demo folder, made-up data and all


def test_a_bad_token_leaves_the_demo_as_it_was(trying_demo, monkeypatch):
    started, real = trying_demo
    demo.set_up(app)
    _claim_answers(monkeypatch, httpx.Response(403, text="forbidden"))

    with _client() as client:
        assert client.post("/api/connect", json={"setup_token": CLAIM_TOKEN}).status_code == 400
        assert client.get("/api/connect").json()["demo"] is True

    assert jobs.DEMO is started
    assert isinstance(keyring.get_keyring(), demo.MemoryKeyring)
    assert started.home.exists()
    assert real.secrets == {}


def test_the_demo_flag_refuses_to_connect_before_spending_the_token(in_demo, monkeypatch):
    demo.set_up(app)
    claimed = _claim_answers(monkeypatch, httpx.Response(200, text=ACCESS_URL))

    with _client() as client:
        response = client.post("/api/connect", json={"setup_token": CLAIM_TOKEN})

    assert response.status_code == 400
    assert "run `blitzave`" in response.json()["detail"]
    assert claimed == []  # a setup token works once; this one is still good
    assert jobs.DEMO is not None
