import sqlite3
from datetime import date

import httpx
import pytest

from blitzave import jobs
from blitzave.sources import simplefin
from blitzave.sources.base import AccountRow, Fetched, TxRow
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


# --- the SOURCE seam: where a sync gets its data ---------------------------------


class FakeSource:
    """Anything with is_connected() and fetch(since, until) can feed a sync."""

    def __init__(self, fetched):
        self.fetched = fetched
        self.asked = []

    def is_connected(self):
        return True

    def fetch(self, since, until=None):
        self.asked.append((since, until))
        return self.fetched


def _one_account_one_charge():
    return Fetched(
        accounts=[
            AccountRow(external_id="chk", account_type="asset", account_number="1234",
                       bank_name="Made-up bank", asset_type="checking"),
        ],
        transactions=[
            TxRow(account_external_id="chk", external_id="t1", amount_cents=-4_50,
                  currency="USD", transaction_type="debit", posted_on="2026-11-02",
                  description="MADE-UP COFFEE"),
        ],
        balances=[],
    )


def test_sync_reads_from_whatever_source_is_set(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("SimpleFIN must not be asked when another source is set")

    monkeypatch.setattr(simplefin, "is_connected", never)
    monkeypatch.setattr(simplefin, "fetch", never)
    source = FakeSource(_one_account_one_charge())
    monkeypatch.setattr(jobs, "SOURCE", source)

    result = jobs.sync()

    assert result["accounts"] == 1
    assert result["transactions"] == 1
    assert len(source.asked) == 1
    assert jobs.simplefin_connected() is True


def test_sync_month_reads_from_the_source_too(monkeypatch):
    source = FakeSource(_one_account_one_charge())
    monkeypatch.setattr(jobs, "SOURCE", source)

    jobs.sync_month(2026, 11)

    assert source.asked == [(date(2026, 11, 1), date(2026, 12, 1))]


def test_the_source_is_simplefin_unless_something_swaps_it():
    assert jobs.SOURCE is simplefin
