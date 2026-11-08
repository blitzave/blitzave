"""Rounds (refine/rounds.py): a recurring amount covers a calendar-aligned
stretch, starts fresh each round, and each closed round has a result."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app
from blitzave.refine.rounds import category_rounds, round_label, round_of
from blitzave.sources.base import AccountRow, TxRow
from blitzave.store import db, ingest, users

TODAY = date(2026, 11, 8)


@pytest.mark.parametrize(
    "period, start, end, label",
    [
        (1, date(2026, 11, 1), date(2026, 11, 30), "November"),
        (2, date(2026, 11, 1), date(2026, 12, 31), "Nov–Dec"),
        (3, date(2026, 10, 1), date(2026, 12, 31), "Oct–Dec"),
        (4, date(2026, 9, 1), date(2026, 12, 31), "Sep–Dec"),
        (6, date(2026, 7, 1), date(2026, 12, 31), "Jul–Dec"),
        (12, date(2026, 1, 1), date(2026, 12, 31), "2026"),
    ],
)
def test_rounds_follow_the_calendar(period, start, end, label):
    assert round_of(TODAY, period) == (start, end)
    assert round_label(start, end) == label


def _shopping(**plan):
    return {"kind": "expense", "schedule": "recurring", "budget_cents": 40000,
            "period_months": 3, "ends_on": None, "plan_set_on": "2026-01-15", **plan}


# Spending is negative; a refund in May comes back positive.
TOTALS = {
    "2026-01": -10000, "2026-02": -15000, "2026-03": -7000,   # $320 → +$80
    "2026-04": -20000, "2026-05": -30000, "2026-06": 4000,    # $460 → -$60
    "2026-07": -40000,                                        # $400 → $0
    "2026-10": -9000, "2026-11": -3000,                       # this round so far: $120
}


def test_each_round_closes_with_a_result_and_the_year_adds_them_up():
    rounds = category_rounds(_shopping(), TOTALS, TODAY)
    assert [(r["label"], r["spent_cents"], r["result_cents"]) for r in rounds["closed_rounds"]] == [
        ("Jan–Mar", 32000, 8000),
        ("Apr–Jun", 46000, -6000),
        ("Jul–Sep", 40000, 0),
    ]
    assert rounds["closed_this_year_cents"] == 2000
    current = rounds["current_round"]
    assert (current["label"], current["spent_cents"], current["remaining_cents"]) == (
        "Oct–Dec", 12000, 28000
    )
    # 39 of 92 days have passed, and 30% of the amount is spent.
    assert (current["elapsed_percent"], current["used_percent"]) == (42.4, 30.0)
    assert current["pace_label"] == "Spending is within this round's pace."


def test_only_rounds_since_the_plan_was_set_count():
    # Set in August: the Jul–Sep round counts (it was set during it), the
    # earlier ones never existed.
    rounds = category_rounds(_shopping(plan_set_on="2026-08-20"), TOTALS, TODAY)
    assert [r["label"] for r in rounds["closed_rounds"]] == ["Jul–Sep"]
    # Set today: nothing has closed yet.
    fresh = category_rounds(_shopping(plan_set_on=TODAY.isoformat()), TOTALS, TODAY)
    assert (fresh["closed_rounds"], fresh["closed_this_year_cents"]) == ([], 0)


def test_a_plan_that_ended_has_no_current_round():
    rounds = category_rounds(_shopping(ends_on="2026-06-30"), TOTALS, TODAY)
    assert rounds["current_round"] is None
    assert [r["label"] for r in rounds["closed_rounds"]] == ["Jan–Mar", "Apr–Jun"]


def test_a_monthly_plan_closes_every_month():
    rounds = category_rounds(
        _shopping(period_months=1, budget_cents=12000, plan_set_on="2026-09-03"), TOTALS, TODAY
    )
    assert [(r["label"], r["result_cents"]) for r in rounds["closed_rounds"]] == [
        ("September", 12000), ("October", 3000)
    ]


def test_ahead_of_pace_and_credits_are_reported_plainly():
    ahead = category_rounds(_shopping(), {"2026-10": -39000}, TODAY)["current_round"]
    assert ahead["pace_label"] == "Spending is ahead of this round's pace."
    credits = category_rounds(_shopping(), {"2026-11": 5000}, TODAY)["current_round"]
    assert credits["pace_label"] == "Credits exceed this round's spending so far."


def test_categories_without_a_recurring_amount_have_no_rounds():
    assert category_rounds(_shopping(kind="savings"), TOTALS, TODAY) is None
    assert category_rounds(_shopping(schedule="once"), TOTALS, TODAY) is None


# --- through the API ----------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        yield client


def test_the_summary_reports_the_current_round_and_plan_changes_restart_it(client):
    today = date.today()
    created = client.post("/api/categories", json={
        "name": "Shopping", "kind": "expense", "budget_amount": "400",
        "schedule": "recurring", "period_months": 3})
    shopping = created.json()
    assert shopping["plan_set_on"] == today.isoformat()

    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        account = ingest.upsert_account(conn, user_id, AccountRow(
            external_id="ACT-chase", account_type="asset", account_number="1001",
            bank_name="Chase", asset_type="checking"))
        ingest.upsert_transaction(conn, user_id, account, TxRow(
            "ACT-chase", "T1", -12000, "USD", "debit", today.isoformat(), "Target"))
    finally:
        conn.close()
    assert client.patch("/api/transactions/T1",
                        json={"categories": {str(shopping["id"]): 1.0}}).status_code == 200

    row = next(c for c in client.get("/api/categories/summary").json()["categories"]
               if c["name"] == "Shopping")
    current = row["rounds"]["current_round"]
    assert (current["spent_display"], current["remaining_display"]) == ("$120.00", "$280.00")
    assert row["rounds"]["closed_rounds"] == []
    assert row["rounds"]["closed_this_year_display"] == "$0.00"

    # A name change keeps the plan's date; an amount change starts it again.
    conn = db.connect()
    try:
        conn.execute("UPDATE categories SET plan_set_on = '2026-01-02' WHERE id = ?",
                     (shopping["id"],))
        conn.commit()
    finally:
        conn.close()
    renamed = client.patch(f"/api/categories/{shopping['id']}", json={"name": "Shops"})
    assert renamed.json()["plan_set_on"] == "2026-01-02"
    changed = client.patch(f"/api/categories/{shopping['id']}", json={"budget_amount": "450"})
    assert changed.json()["plan_set_on"] == today.isoformat()
