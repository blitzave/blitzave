import base64

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
