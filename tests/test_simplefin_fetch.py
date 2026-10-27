import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from blitzave.sources import simplefin

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "simplefin_accounts.json").read_text()
)


def test_fetch_requires_connection():
    with pytest.raises(simplefin.NotConnectedError):
        simplefin.fetch(date(2026, 1, 1))


def test_fetch_uses_basic_auth_and_strips_credentials_from_the_request_url(monkeypatch):
    simplefin.store_access_url("https://demo_user:demo_pass@bridge.simplefin.org/simplefin")

    captured = {}

    def fake_get(url, *, params=None, auth=None):
        captured["url"] = url
        captured["params"] = params
        captured["auth"] = auth
        return httpx.Response(200, json=FIXTURE)

    monkeypatch.setattr(httpx, "get", fake_get)

    fetched = simplefin.fetch(date(2026, 1, 1))

    assert captured["url"] == "https://bridge.simplefin.org/simplefin/accounts"
    assert "demo_user" not in captured["url"]
    assert "demo_pass" not in captured["url"]
    assert captured["auth"] == ("demo_user", "demo_pass")
    assert "start-date" in captured["params"]
    assert len(fetched.accounts) == 4  # 5 in the fixture, minus the skipped 401k account


def test_fetch_raises_access_revoked_on_403(monkeypatch):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(403, text="forbidden"))

    with pytest.raises(simplefin.AccessRevokedError):
        simplefin.fetch(date(2026, 1, 1))


def test_fetch_raises_subscription_required_on_402(monkeypatch):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(402, text="payment required"))

    with pytest.raises(simplefin.SubscriptionRequiredError):
        simplefin.fetch(date(2026, 1, 1))


def test_fetch_sends_end_date_only_when_until_is_given(monkeypatch):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    captured = []
    monkeypatch.setattr(
        httpx, "get", lambda url, *, params=None, auth=None: captured.append(params) or httpx.Response(200, json=FIXTURE)
    )

    simplefin.fetch(date(2026, 10, 1))
    simplefin.fetch(date(2026, 10, 1), date(2026, 11, 1))

    assert "end-date" not in captured[0]
    assert captured[1]["start-date"] == int(datetime(2026, 10, 1).timestamp())  # local midnight
    assert captured[1]["end-date"] == int(datetime(2026, 11, 1).timestamp())
