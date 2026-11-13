"""DNS rebinding: a page on evil.example can point its own name at 127.0.0.1,
and the browser then treats this server as part of that page's site. The
request still carries Host: evil.example, so the engine refuses any name but
its own. See ALLOWED_HOSTS in __main__.py."""

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import URL, create_app

FOREIGN_HOST = "evil.example:8742"
LOCAL_HOSTS = ["127.0.0.1:8742", "localhost:8742"]

# One of each kind of thing the server answers: the API, the docs page, the
# debug page and the UI. The check wraps the whole app, mounts included.
SERVED_PATHS = ["/api/health", "/api/transactions", "/docs", "/dev/", "/"]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))


@pytest.fixture
def client():
    with TestClient(create_app(), base_url=URL) as c:
        yield c


@pytest.mark.parametrize("path", SERVED_PATHS)
def test_a_foreign_host_is_refused_everywhere(client, path):
    response = client.get(path, headers={"Host": FOREIGN_HOST})
    assert response.status_code == 400
    assert response.text == "Invalid host header"


def test_a_foreign_host_cannot_write(client):
    response = client.post(
        "/api/categories",
        json={"name": "Coffee", "kind": "expense"},
        headers={"Host": FOREIGN_HOST},
    )
    assert response.status_code == 400
    assert client.get("/api/categories").json() == []


@pytest.mark.parametrize("host", LOCAL_HOSTS)
@pytest.mark.parametrize("path", SERVED_PATHS)
def test_both_local_names_are_served(client, host, path):
    response = client.get(path, headers={"Host": host})
    assert response.status_code == 200


def test_a_request_with_no_host_header_is_refused(client):
    # HTTP/1.1 requires Host, and a browser always sends one.
    response = client.get("/api/health", headers={"Host": ""})
    assert response.status_code == 400
