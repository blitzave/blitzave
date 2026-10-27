import json
import sqlite3
from datetime import datetime
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

    assert result["accounts"] == 4  # 5 in the fixture, minus the skipped 401k account
    assert result["transactions"] == 10  # 11 posted, 1 pending excluded
    assert result["balances"] == 4


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
    account_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    transaction_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    balance_count = conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0]
    conn.close()

    assert account_count == 4
    assert transaction_count == 10
    assert balance_count == 4  # same (account_id, as_of) — overwritten, not duplicated


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

    checking_external_id = "ACT-chase-0001"
    conn = db.connect()
    accounts.update_account_type(conn, checking_external_id, "asset", "savings", None)
    conn.close()

    jobs.sync()  # resync: upsert_account must not touch the existing row at all

    conn = db.connect()
    asset_type = conn.execute(
        "SELECT asset_type FROM assets a "
        "JOIN accounts acc ON acc.id = a.account_id "
        "WHERE acc.external_id = ?",
        (checking_external_id,),
    ).fetchone()[0]
    conn.close()
    assert asset_type == "savings"  # not reverted back to "checking"


def test_sync_month_fetches_exactly_that_month(monkeypatch):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    captured = []
    monkeypatch.setattr(
        httpx, "get", lambda *a, params=None, **k: captured.append(params) or httpx.Response(200, json=FIXTURE)
    )

    result = jobs.sync_month(2025, 12)

    assert captured[0]["start-date"] == int(datetime(2025, 12, 1).timestamp())
    assert captured[0]["end-date"] == int(datetime(2026, 1, 1).timestamp())  # December rolls into the next year
    assert result["month"] == "2025-12"
    assert result["transactions"] == 10


def test_sync_month_leaves_the_watermark_alone(monkeypatch, isolated_home):
    # The watermark marks how far the recent sync reached; syncing an old
    # month must not move it, or the next startup sync would skip a gap.
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))

    jobs.sync_month(2025, 5)

    conn = sqlite3.connect(isolated_home / "blitzave.db")
    row = conn.execute("SELECT watermark FROM job_runs WHERE job = ?", (jobs.SYNC_JOB,)).fetchone()
    conn.close()
    assert row is None


def test_sync_month_twice_creates_no_duplicates(monkeypatch, isolated_home):
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))

    jobs.sync()
    jobs.sync_month(2026, 10)

    conn = sqlite3.connect(isolated_home / "blitzave.db")
    transaction_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()
    assert transaction_count == 10


def test_sync_month_requires_connection():
    with pytest.raises(jobs.SyncError):
        jobs.sync_month(2026, 10)
