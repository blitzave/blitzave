import json
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from blitzave import jobs
from blitzave.sources import simplefin
from blitzave.sources.base import AccountRow, Fetched, TxRow
from blitzave.store import accounts, categories, db, refunds, transfers

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


# --- automatic transfer and refund matching (TODOS.md P0.2) -----------------


def _card_and_checking(transactions):
    """A Fetched with one checking account and one card, and the given
    (account, id, cents, posted, description) rows."""
    return Fetched(
        accounts=[
            AccountRow(external_id="checking", account_type="asset", account_number="3333",
                       bank_name="Demo bank", asset_type="checking"),
            AccountRow(external_id="card", account_type="liability", account_number="1111",
                       issuer_name="Demo card", liability_type="credit_card"),
        ],
        transactions=[
            TxRow(account_external_id=account, external_id=ext, amount_cents=cents,
                  currency="USD", transaction_type="debit" if cents < 0 else "credit",
                  posted_on=posted, description=description)
            for account, ext, cents, posted, description in transactions
        ],
        balances=[],
    )


def _serve(monkeypatch, fetched):
    monkeypatch.setattr(simplefin, "is_connected", lambda: True)
    monkeypatch.setattr(simplefin, "fetch", lambda since, until=None: fetched)


def test_sync_links_the_fixture_card_credit_as_a_refund(monkeypatch):
    # "Platinum Walmart+ Credit" cancels "Walmart+ Member" exactly; the
    # partial Costco credit stays activity.
    simplefin.store_access_url("https://u:p@bridge.simplefin.org/simplefin")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=FIXTURE))

    result = jobs.sync()

    assert result["transfers_matched"] == 0
    assert result["refunds_matched"] == 1
    conn = db.connect()
    [pair] = refunds.list_refunds(conn)
    conn.close()
    assert pair["debit"]["description"] == "Walmart+ Member 10/28009666546 AR"
    assert pair["credit"]["description"] == "Platinum Walmart+ Credit"
    assert pair["matched_by"] == "rule"


def test_sync_turns_a_card_payment_into_one_transfer_and_zero_spending(monkeypatch):
    # TODOS.md task 7: a card payment becomes one transfer and zero spending,
    # and the purchase on the card is counted once, at the charge.
    _serve(monkeypatch, _card_and_checking([
        ("card", "dinner", -6400, "2026-10-01", "LA LA LAND KIND CAFE"),
        ("checking", "pay", -6400, "2026-10-09", "CHASE CARD AUTOPAY"),
        ("card", "paid", 6400, "2026-10-12", "AUTOMATIC PAYMENT - THANK YOU"),
    ]))

    result = jobs.sync()

    assert result["transfers_matched"] == 1
    conn = db.connect()
    [transfer] = transfers.list_transfers(conn)
    _, uncategorized = categories.summary(conn, "2026-10-01", "2026-10-31")
    conn.close()
    assert (transfer["debit"]["id"], transfer["credit"]["id"]) == ("pay", "paid")
    assert transfer["matched_by"] == "rule"
    assert transfer["transfer_date"] == "2026-10-12"
    assert uncategorized == -6400  # only the dinner


def test_sync_never_turns_a_card_refund_into_a_transfer(monkeypatch):
    # TODOS.md task 7: a $50 card refund beside an unrelated $50 checking debit.
    _serve(monkeypatch, _card_and_checking([
        ("checking", "venmo", -5000, "2026-10-03", "VENMO SARAH"),
        ("card", "refund", 5000, "2026-10-04", "AMAZON MKTPL REFUND"),
    ]))

    result = jobs.sync()

    assert (result["transfers_matched"], result["refunds_matched"]) == (0, 0)


def test_sync_never_relinks_a_pair_the_user_unlinked(monkeypatch):
    # TODOS.md task 7: unlinking sticks.
    _serve(monkeypatch, _card_and_checking([
        ("checking", "pay", -6400, "2026-10-09", "CHASE CARD AUTOPAY"),
        ("card", "paid", 6400, "2026-10-10", "PAYMENT THANK YOU"),
    ]))
    jobs.sync()
    conn = db.connect()
    [transfer] = transfers.list_transfers(conn)
    transfers.delete_transfer(conn, transfer["id"])
    conn.close()

    result = jobs.sync()

    assert result["transfers_matched"] == 0
    conn = db.connect()
    assert transfers.list_transfers(conn) == []
    conn.close()


def test_first_match_looks_at_all_history_then_only_the_fetched_range(monkeypatch):
    old = [
        ("checking", "old-pay", -6400, "2026-01-09", "CHASE CARD AUTOPAY"),
        ("card", "old-paid", 6400, "2026-01-10", "PAYMENT THANK YOU"),
    ]
    new = [
        ("checking", "pay", -7100, "2026-10-09", "CHASE CARD AUTOPAY"),
        ("card", "paid", 7100, "2026-10-10", "PAYMENT THANK YOU"),
    ]
    seen = []
    real_unlinked_legs = transfers.unlinked_legs

    def spy(conn, start):
        seen.append(start)
        return real_unlinked_legs(conn, start)

    monkeypatch.setattr(transfers, "unlinked_legs", spy)

    _serve(monkeypatch, _card_and_checking(old + new))
    assert jobs.sync()["transfers_matched"] == 2
    _serve(monkeypatch, _card_and_checking(new))
    jobs.sync()

    assert seen == [None, "2026-10-01"]  # 2026-10-09 minus two 4-day windows

