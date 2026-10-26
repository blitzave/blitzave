import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app
from blitzave.refine.budgets import monthly_budget
from blitzave.store import db, users


def test_upgrade_preserves_existing_categories_and_bank_rows(tmp_path):
    conn = sqlite3.connect(tmp_path / "upgrade.db")
    try:
        for migration in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            if int(migration.name.split("_", 1)[0]) <= 4:
                conn.executescript(migration.read_text())
        conn.execute("PRAGMA user_version = 4")
        conn.execute(
            "INSERT INTO users (id, username, created_at) VALUES (1, 'demo', 0)"
        )
        conn.execute(
            "INSERT INTO categories (id, user_id, name, kind) VALUES (1, 1, 'Travel', 'expense')"
        )
        conn.execute(
            "INSERT INTO accounts (id, user_id, account_type, account_number, external_id) VALUES (1, 1, 'liability', '2222', 'card')"
        )
        conn.execute(
            "INSERT INTO transactions (user_id, account_id, external_id, amount_cents, transaction_type, posted_on, description, categories, categories_set_by, created_at) VALUES (1, 1, 'charge', -12345, 'debit', '2026-10-01', 'Demo purchase', '{\"1\":1.0}', 'user', 0)"
        )
        conn.commit()
        before = conn.execute("SELECT * FROM transactions").fetchall()
        db.migrate(conn)
        db.migrate(conn)
        assert conn.execute("SELECT * FROM transactions").fetchall() == before
        assert conn.execute(
            "SELECT id, name, kind, budget_cents FROM categories"
        ).fetchall() == [(1, "Travel", "expense", None)]
        assert conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        yield client


def test_recurring_budget_survives_other_edits_and_can_be_removed(client):
    created = client.post(
        "/api/categories",
        json={
            "name": "Travel",
            "kind": "expense",
            "budget_amount": "600.25",
        },
    )
    assert created.status_code == 201
    category_id = created.json()["id"]
    assert created.json()["budget_cents"] == 60025
    assert created.json()["budget_display"] == "$600.25"
    renamed = client.patch(f"/api/categories/{category_id}", json={"name": "Trips"})
    assert renamed.json()["budget_cents"] == 60025
    # A new connection sees the same saved budget, independent of the browser.
    assert client.get("/api/categories").json()[0]["budget_cents"] == 60025
    updated = client.patch(
        f"/api/categories/{category_id}", json={"budget_amount": "400"}
    )
    assert updated.json()["budget_display"] == "$400.00"
    for month in ("2026-09", "2026-10"):
        summary = client.get(
            "/api/categories/summary",
            params={
                "start": f"{month}-01",
                "end": f"{month}-28",
            },
        )
        assert summary.json()["categories"][0]["budget_cents"] == 40000
    # The budget editor pre-fills with budget_display and may submit it back
    # unchanged — the display string must round-trip.
    resubmitted = client.patch(
        f"/api/categories/{category_id}",
        json={"budget_amount": updated.json()["budget_display"]},
    )
    assert resubmitted.status_code == 200
    assert resubmitted.json()["budget_cents"] == 40000
    cleared = client.patch(
        f"/api/categories/{category_id}", json={"budget_amount": None}
    )
    assert cleared.json()["budget_cents"] is None
    assert cleared.json()["budget_display"] is None


@pytest.mark.parametrize(
    "amount",
    [
        "-1",
        "0",
        "1.005",
        "abc",
        "NaN",
        "Infinity",
        "1e20",
        "1e999999",
        "1.000000000000000000000000000001",
        100,
    ],
)
def test_budget_rejects_invalid_amount_without_changing_saved_value(client, amount):
    row = client.post(
        "/api/categories",
        json={
            "name": "Food",
            "kind": "expense",
            "budget_amount": "500",
        },
    ).json()
    response = client.patch(
        f"/api/categories/{row['id']}", json={"budget_amount": amount}
    )
    assert response.status_code == 422
    assert client.get("/api/categories").json()[0]["budget_cents"] == 50000


def test_refund_net_spending_and_budget_summary_are_formatted(client):
    row = client.post(
        "/api/categories",
        json={
            "name": "Travel",
            "kind": "expense",
            "budget_amount": "600",
        },
    ).json()
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        conn.execute(
            "INSERT INTO accounts (id, user_id, account_type, account_number, external_id) "
            "VALUES (1, ?, 'liability', '2222', 'card')",
            (user_id,),
        )
        for ext, cents, kind in (
            ("charge", -10000, "debit"),
            ("refund", 12000, "credit"),
        ):
            conn.execute(
                "INSERT INTO transactions (user_id, account_id, external_id, amount_cents, "
                "transaction_type, posted_on, description, categories, created_at) "
                "VALUES (?, 1, ?, ?, ?, '2026-10-01', 'Travel', ?, 0)",
                (user_id, ext, cents, kind, '{"' + str(row["id"]) + '":1.0}'),
            )
        conn.commit()
    finally:
        conn.close()
    summary = client.get(
        "/api/categories/summary",
        params={
            "start": "2026-10-01",
            "end": "2026-10-31",
        },
    ).json()["categories"][0]
    assert summary["total_cents"] == 2000
    assert (
        summary["spent_cents"] == -2000
    )  # a net credit is never made positive with abs()
    assert summary["spent_display"] == "-$20.00"
    assert summary["remaining_display"] == "$620.00"
    assert summary["remaining_magnitude_display"] == "$620.00"


def test_budget_pace_counts_calendar_days_and_rounds_projection_in_cents():
    facts = monthly_budget(
        -20001, 60000, date(2026, 10, 1), date(2026, 10, 31), date(2026, 10, 10)
    )
    assert facts["month_elapsed_percent"] == 32.3
    assert facts["projected_cents"] == 62003
    assert facts["budget_used_percent"] == 33.3
    assert facts["pace_label"] == "Spending is ahead of this month's pace."
    assert facts["remaining_cents"] == 39999


def test_budget_pace_handles_bad_month_future_past_and_unset_budget():
    start, end = date(2024, 2, 1), date(2024, 2, 29)
    future = monthly_budget(0, 10000, start, end, date(2024, 1, 31))
    assert future["month_elapsed_percent"] == 0
    assert future["projected_cents"] is None
    complete = monthly_budget(-20000, 10000, start, end, date(2024, 3, 1))
    assert complete["remaining_cents"] == -10000
    assert complete["remaining_label"] == "Above budget"
    assert complete["pace_label"] == "Month complete."
    assert complete["month_elapsed_percent"] == 100
    no_budget = monthly_budget(-20000, None, start, end, date(2024, 2, 10))
    assert no_budget["budget_used_percent"] is None
    assert no_budget["remaining_cents"] is None


def test_budget_pace_uses_only_days_covered_by_the_requested_period():
    facts = monthly_budget(
        -20000, 60000, date(2026, 10, 1), date(2026, 10, 10), date(2026, 10, 26)
    )
    assert facts["month_elapsed_percent"] == 32.3
    assert facts["projected_cents"] == 62000
    assert facts["pace_label"] == "Spending is ahead of this month's pace."


def test_empty_category_does_not_claim_there_were_credits():
    facts = monthly_budget(
        0, 60000, date(2026, 10, 1), date(2026, 10, 31), date(2026, 10, 26)
    )
    assert facts["pace_label"] == "No net spending this month so far."


def test_category_period_rejects_invalid_dates(client):
    assert client.get("/api/categories/summary?start=bad").status_code == 422
    assert (
        client.get(
            "/api/categories/summary?start=2026-10-02&end=2026-10-01"
        ).status_code
        == 422
    )
