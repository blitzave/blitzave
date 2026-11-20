"""Cross-site writes: any website the user visits can make their browser send
a request to 127.0.0.1. It can't read the answer, but a write would land, so
the engine refuses any write whose Origin isn't its own page. See
same_origin_writes and SECURITY_HEADERS in __main__.py."""

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import URL, create_app

OURS = URL  # http://127.0.0.1:8742
COFFEE = {"name": "Coffee", "kind": "expense"}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))


@pytest.fixture
def client():
    with TestClient(create_app(), base_url=URL) as c:
        yield c


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "null",  # a sandboxed frame or a file:// page
        "http://localhost:8742",  # our name, but not the one this page was opened on
        "http://127.0.0.1:9999",  # another server on this laptop
    ],
)
def test_a_write_from_another_page_is_refused(client, origin):
    response = client.post("/api/categories", json=COFFEE, headers={"Origin": origin})
    assert response.status_code == 403
    assert client.get("/api/categories").json() == []


def test_a_bodyless_sync_from_another_page_is_refused(client):
    # The one write a page could send blind, with no JSON body to give it away.
    assert client.post("/api/sync", headers={"Origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8742", "localhost:8742"])
def test_a_write_from_our_own_page_goes_through(client, host):
    response = client.post(
        "/api/categories", json=COFFEE, headers={"Host": host, "Origin": f"http://{host}"}
    )
    assert response.status_code == 201


def test_a_write_from_no_page_at_all_goes_through(client):
    # curl, a script, the tests: no browser, so no Origin and nothing to forge.
    assert client.post("/api/categories", json=COFFEE).status_code == 201


@pytest.mark.parametrize("content_type", [None, "text/plain"])
def test_json_a_form_could_send_is_refused(client, content_type):
    # A page can send a body as text/plain, or with no type at all, without
    # asking the server first. FastAPI only reads JSON sent as JSON; this
    # holds it to that, whatever the Origin.
    headers = {"Content-Type": content_type} if content_type else {"Content-Type": ""}
    response = client.post("/api/categories", content=b'{"name": "Coffee", "kind": "expense"}', headers=headers)
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/", "/api/health"])
def test_pages_and_answers_carry_the_security_headers(client, path):
    headers = client.get(path).headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
