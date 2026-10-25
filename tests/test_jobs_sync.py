import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from blitzave import jobs
from blitzave.sources import simplefin
from blitzave.store import accounts, db

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "simplefin_accounts.json").read_text()
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    return tmp_path


def test_sync_requires_connection():
    with pytest.raises(jobs.SyncError):
        jobs.sync()


def test_sync_ingests_accounts_transactions_and_balances(monkeypatch):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))

    result = jobs.sync()

    assert result["accounts"] == 2
    assert result["transactions"] == 4  # 4 posted, 1 pending excluded
    assert result["balances"] == 2


def test_sync_sets_the_watermark(monkeypatch, isolated_home):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))

    jobs.sync()

    conn = sqlite3.connect(isolated_home / "blitzave.db")
    watermark = conn.execute(
        "SELECT watermark FROM job_runs WHERE job = ?", (jobs.SYNC_JOB,)
    ).fetchone()[0]
    conn.close()
    assert watermark is not None


def test_sync_twice_creates_no_duplicates(monkeypatch, isolated_home):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))

    jobs.sync()
    jobs.sync()

    conn = sqlite3.connect(isolated_home / "blitzave.db")
    accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    transactions = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    balances = conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0]
    conn.close()

    assert accounts == 2
    assert transactions == 4
    assert balances == 2  # same (account_id, as_of) — overwritten, not duplicated


def test_sync_surfaces_revoked_access_as_sync_error(monkeypatch):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(403, text="forbidden"))

    with pytest.raises(jobs.SyncError):
        jobs.sync()


def test_corrected_account_type_survives_the_next_sync(monkeypatch, isolated_home):
    # TODOS.md task 6's exact done-criteria.
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))

    jobs.sync()  # first sync: Chase account guessed as asset/checking

    conn = db.connect()
    checking_id = conn.execute(
        "SELECT id FROM accounts WHERE account_number = '1234'"
    ).fetchone()[0]
    accounts.update_account_type(conn, checking_id, "asset", "savings", None)
    conn.close()

    jobs.sync()  # resync: upsert_account must not touch the existing row at all

    conn = db.connect()
    asset_type = conn.execute(
        "SELECT asset_type FROM assets WHERE account_id = ?", (checking_id,)
    ).fetchone()[0]
    conn.close()
    assert asset_type == "savings"  # not reverted back to "checking"
