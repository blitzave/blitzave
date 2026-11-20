import base64
from datetime import date

import httpx
import pytest

from blitzave.sources import simplefin


def _token_for(claim_url: str) -> str:
    return base64.b64encode(claim_url.encode("utf-8")).decode("utf-8")


def test_claim_access_url_returns_stripped_body(monkeypatch):
    def fake_post(url, *args, **kwargs):
        assert url == "https://bridge.simplefin.org/claim/abc"
        return httpx.Response(200, text="https://user:pass@bridge.simplefin.org/access\n")

    monkeypatch.setattr(httpx, "post", fake_post)

    token = _token_for("https://bridge.simplefin.org/claim/abc")
    assert simplefin.claim_access_url(token) == "https://user:pass@bridge.simplefin.org/access"


def test_claim_access_url_raises_on_403(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(403, text="forbidden"))

    with pytest.raises(simplefin.ClaimAlreadyUsedError):
        simplefin.claim_access_url(_token_for("https://bridge.simplefin.org/claim/abc"))


def test_claim_access_url_raises_on_other_errors(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(500, text="oops"))

    with pytest.raises(simplefin.SimpleFinError):
        simplefin.claim_access_url(_token_for("https://bridge.simplefin.org/claim/abc"))


def test_claim_access_url_rejects_bad_base64():
    with pytest.raises(simplefin.SimpleFinError):
        simplefin.claim_access_url("not valid base64!!")


def _never(*args, **kwargs):
    raise AssertionError("no request may be made")


@pytest.mark.parametrize(
    "claim_url",
    [
        "http://bridge.simplefin.org/claim/abc",  # no TLS
        "http://192.168.1.1/admin",  # a box on the user's own network
        "file:///etc/passwd",
        "bridge.simplefin.org/claim/abc",  # no scheme at all
    ],
)
def test_a_token_that_isnt_https_is_never_called(monkeypatch, claim_url):
    monkeypatch.setattr(httpx, "post", _never)
    with pytest.raises(simplefin.SimpleFinError):
        simplefin.claim_access_url(_token_for(claim_url))


def test_an_access_url_that_isnt_https_is_not_kept(monkeypatch):
    # A made-up token whose "bridge" hands back its own plain-http address.
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, text="http://u:p@evil.example/sf"))
    with pytest.raises(simplefin.SimpleFinError):
        simplefin.connect(_token_for("https://evil.example/claim"))
    assert simplefin.load_access_url() is None


def test_a_failed_claim_never_repeats_what_the_server_said(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(500, text="internal-page secret=1"))
    with pytest.raises(simplefin.SimpleFinError) as raised:
        simplefin.claim_access_url(_token_for("https://bridge.simplefin.org/claim/abc"))
    assert "secret" not in str(raised.value)
    assert "500" in str(raised.value)


def test_a_saved_access_url_that_isnt_https_is_never_called(monkeypatch):
    simplefin.store_access_url("http://u:p@evil.example/sf")
    monkeypatch.setattr(httpx, "get", _never)
    with pytest.raises(simplefin.SimpleFinError):
        simplefin.fetch(date(2026, 11, 1))


def test_a_failed_fetch_never_repeats_what_the_server_said(monkeypatch):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(500, text="internal-page secret=1"))
    with pytest.raises(simplefin.SimpleFinError) as raised:
        simplefin.fetch(date(2026, 11, 1))
    assert "secret" not in str(raised.value)


def test_access_url_hash_is_deterministic_and_does_not_leak_the_url():
    digest = simplefin.access_url_hash("https://user:pass@bridge.simplefin.org/access")
    assert digest == simplefin.access_url_hash("https://user:pass@bridge.simplefin.org/access")
    assert "pass" not in digest


def test_store_and_load_access_url_round_trip():
    assert simplefin.is_connected() is False

    simplefin.store_access_url("https://user:pass@bridge.simplefin.org/access")

    assert simplefin.is_connected() is True
    assert simplefin.load_access_url() == "https://user:pass@bridge.simplefin.org/access"


def test_connect_claims_stores_and_returns_hash(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, text="https://user:pass@bridge.simplefin.org/access"),
    )

    returned_hash = simplefin.connect(_token_for("https://bridge.simplefin.org/claim/abc"))

    assert simplefin.is_connected() is True
    assert returned_hash == simplefin.access_url_hash(
        "https://user:pass@bridge.simplefin.org/access"
    )
