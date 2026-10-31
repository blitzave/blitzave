from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from blitzave import jobs
from blitzave.__main__ import create_app
from blitzave.sources import github
from blitzave.store import db, users

TODAY = date(2026, 10, 31)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def installed_version(monkeypatch):
    # Pin the running version so these tests don't depend on pyproject.toml.
    monkeypatch.setattr(jobs.metadata, "version", lambda name: "0.1.0")


@pytest.fixture
def github_calls(monkeypatch):
    """Answers like GitHub with a v0.2.0 release, and records every request."""
    calls = []

    def fake_get(url, *, timeout):
        calls.append(url)
        return httpx.Response(200, json={"tag_name": "v0.2.0", "name": "Savings arc"})

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def set_meta(changes):
    conn = db.connect()
    try:
        users.update_meta(conn, users.get_or_create_user(conn), changes)
    finally:
        conn.close()


# --- sources/github.py --------------------------------------------------------


def test_fetch_asks_for_the_latest_release_and_sends_nothing_else(monkeypatch):
    seen = {}

    def fake_get(url, *, timeout):  # no params, headers or auth can sneak in
        seen["url"] = url
        return httpx.Response(200, json={"tag_name": "v0.2.0"})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert github.fetch_latest_version() == "0.2.0"
    assert seen["url"] == "https://api.github.com/repos/blitzave/blitzave/releases/latest"


def test_fetch_raises_when_there_is_no_release_yet(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, *, timeout: httpx.Response(404))
    with pytest.raises(github.UpdateCheckError, match="404"):
        github.fetch_latest_version()


def test_fetch_raises_when_offline(monkeypatch):
    def offline(url, *, timeout):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", offline)
    with pytest.raises(github.UpdateCheckError, match="could not reach GitHub"):
        github.fetch_latest_version()


@pytest.mark.parametrize("body", [{"tag_name": "nightly"}, {"name": "no tag"}, ["not", "a", "dict"]])
def test_fetch_raises_on_a_release_without_a_version_tag(monkeypatch, body):
    monkeypatch.setattr(httpx, "get", lambda url, *, timeout: httpx.Response(200, json=body))
    with pytest.raises(github.UpdateCheckError):
        github.fetch_latest_version()


@pytest.mark.parametrize(
    ("candidate", "installed", "expected"),
    [
        ("0.2.0", "0.1.0", True),
        ("0.10.0", "0.9.0", True),  # numbers, not strings
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.2.0", False),
        ("0.2.0", "0.2.0.dev1", False),  # anything unexpected never shows an update
    ],
)
def test_is_newer(candidate, installed, expected):
    assert github.is_newer(candidate, installed) is expected


# --- jobs.check_for_update ---------------------------------------------------


def test_a_fresh_install_never_asks_github(github_calls):
    jobs.check_for_update(TODAY)
    assert github_calls == []


def test_turning_checks_off_means_github_is_never_asked(github_calls):
    jobs.set_update_check(False)
    jobs.check_for_update(TODAY)
    assert github_calls == []


def test_checks_at_most_once_a_day(github_calls):
    jobs.set_update_check(True)
    jobs.check_for_update(TODAY)
    jobs.check_for_update(TODAY)
    assert len(github_calls) == 1

    jobs.check_for_update(date(2026, 11, 1))
    assert len(github_calls) == 2


def test_a_failed_check_still_counts_as_that_days_one_request(monkeypatch):
    calls = []

    def offline(url, *, timeout):
        calls.append(url)
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", offline)
    jobs.set_update_check(True)
    with pytest.raises(jobs.UpdateCheckError):
        jobs.check_for_update(TODAY)
    jobs.check_for_update(TODAY)  # no second try today
    assert len(calls) == 1
    assert jobs.update_status()["available_version"] is None


# --- jobs.update_status and /api/updates --------------------------------------


def test_status_starts_unanswered_with_nothing_available():
    assert jobs.update_status() == {
        "enabled": None,
        "installed_version": "0.1.0",
        "available_version": None,
    }


def test_a_newer_release_shows_once_checked(github_calls):
    jobs.set_update_check(True)
    jobs.check_for_update(TODAY)
    assert jobs.update_status()["available_version"] == "0.2.0"


def test_turning_checks_off_hides_a_release_found_earlier(github_calls):
    jobs.set_update_check(True)
    jobs.check_for_update(TODAY)
    jobs.set_update_check(False)
    assert jobs.update_status()["available_version"] is None


def test_the_same_version_as_installed_is_not_an_update():
    set_meta({"update_check": True, "latest_version": "0.1.0"})
    assert jobs.update_status()["available_version"] is None


def test_api_reads_and_sets_the_choice():
    with TestClient(create_app()) as client:
        assert client.get("/api/updates").json()["enabled"] is None

        response = client.put("/api/updates", json={"enabled": True})
        assert response.status_code == 200
        assert response.json()["enabled"] is True


@pytest.mark.parametrize("value", ["yes", "true", 1, None])
def test_api_rejects_anything_but_true_or_false(value):
    with TestClient(create_app()) as client:
        response = client.put("/api/updates", json={"enabled": value})
        assert response.status_code == 422
        assert client.get("/api/updates").json()["enabled"] is None


def test_startup_runs_the_check_once_opted_in(github_calls):
    set_meta({"update_check": True})
    with TestClient(create_app()) as client:
        assert client.get("/api/updates").json()["available_version"] == "0.2.0"
    assert len(github_calls) == 1
