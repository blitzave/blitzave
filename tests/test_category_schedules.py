"""Migration 008: a category's amount comes with a schedule, one time or
recurring. Goals (savings-kind) and expense categories share it."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app
from blitzave.store import categories, db, users


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        yield client


def test_upgrade_makes_existing_budgets_monthly_and_leaves_the_rest_alone(tmp_path):
    conn = sqlite3.connect(tmp_path / "upgrade.db")
    try:
        for migration in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            if int(migration.name.split("_", 1)[0]) <= 7:
                conn.executescript(migration.read_text())
        conn.execute("PRAGMA user_version = 7")
        conn.execute("INSERT INTO users (id, username, created_at) VALUES (1, 'demo', 0)")
        conn.execute(
            "INSERT INTO categories (user_id, name, kind, budget_cents)"
            " VALUES (1, 'Groceries', 'expense', 50000)"
        )
        conn.execute("INSERT INTO categories (user_id, name, kind) VALUES (1, 'Misc', 'expense')")
        conn.commit()
        db.migrate(conn)
        assert categories.list_categories(conn) == [
            {"id": 1, "name": "Groceries", "kind": "expense", "budget_cents": 50000,
             "schedule": "recurring", "period_months": 1, "target_on": None, "ends_on": None,
             **categories.NO_GOAL},
            {"id": 2, "name": "Misc", "kind": "expense", "budget_cents": None,
             "schedule": None, "period_months": None, "target_on": None, "ends_on": None,
             **categories.NO_GOAL},
        ]
    finally:
        conn.close()


def test_one_time_goal_has_an_amount_and_a_target_date(client):
    created = client.post(
        "/api/categories",
        json={"name": "PS5", "kind": "savings", "budget_amount": "500",
              "schedule": "once", "target_on": "2026-12-20"},
    )
    assert created.status_code == 201
    body = created.json()
    assert (body["budget_display"], body["schedule"], body["target_on"]) == (
        "$500.00", "once", "2026-12-20"
    )
    assert body["period_months"] is None and body["ends_on"] is None
    assert client.get("/api/categories").json()[0]["target_on"] == "2026-12-20"


def test_recurring_amount_keeps_its_period_and_optional_end(client):
    created = client.post(
        "/api/categories",
        json={"name": "Shopping", "kind": "expense", "budget_amount": "400",
              "schedule": "recurring", "period_months": 3, "ends_on": "2027-12-31"},
    )
    assert created.status_code == 201
    body = created.json()
    assert (body["schedule"], body["period_months"], body["ends_on"]) == (
        "recurring", 3, "2027-12-31"
    )
    assert body["target_on"] is None


def test_an_amount_alone_still_means_monthly(client):
    """The budget editor sends only budget_amount; that keeps working."""
    created = client.post(
        "/api/categories", json={"name": "Dining", "kind": "expense", "budget_amount": "300"}
    )
    assert (created.json()["schedule"], created.json()["period_months"]) == ("recurring", 1)


@pytest.mark.parametrize(
    "plan",
    [
        {"budget_amount": "500", "schedule": "once"},  # no target date
        {"budget_amount": "500", "schedule": "once", "target_on": "2027-01-01",
         "period_months": 3},
        {"budget_amount": "500", "schedule": "once", "target_on": "2027-01-01",
         "ends_on": "2027-06-01"},
        {"budget_amount": "500", "schedule": "recurring", "target_on": "2027-01-01"},
        {"budget_amount": "500", "schedule": "recurring", "period_months": 5},  # not calendar
        {"budget_amount": "500", "schedule": "recurring", "period_months": "3"},
        {"schedule": "recurring", "period_months": 3},  # no amount
        {"budget_amount": "500", "schedule": "weekly"},
        {"budget_amount": "500", "schedule": "once", "target_on": "2026-13-01"},
    ],
)
def test_incomplete_or_contradictory_plans_are_rejected(client, plan):
    response = client.post("/api/categories", json={"name": "X", "kind": "savings", **plan})
    assert response.status_code == 422
    assert client.get("/api/categories").json() == []


def test_changing_only_the_amount_keeps_the_schedule(client):
    category_id = client.post(
        "/api/categories",
        json={"name": "Shopping", "kind": "expense", "budget_amount": "400",
              "schedule": "recurring", "period_months": 3},
    ).json()["id"]
    updated = client.patch(f"/api/categories/{category_id}", json={"budget_amount": "450"})
    assert updated.status_code == 200
    assert (updated.json()["budget_cents"], updated.json()["period_months"]) == (45000, 3)
    renamed = client.patch(f"/api/categories/{category_id}", json={"name": "Clothes"})
    assert renamed.json()["period_months"] == 3


def test_a_whole_new_plan_replaces_the_old_one_and_null_clears_it(client):
    category_id = client.post(
        "/api/categories",
        json={"name": "Travel", "kind": "savings", "budget_amount": "600",
              "schedule": "recurring", "period_months": 6, "ends_on": "2028-12-31"},
    ).json()["id"]
    switched = client.patch(
        f"/api/categories/{category_id}",
        json={"budget_amount": "4200", "schedule": "once", "target_on": "2026-12-18"},
    )
    assert switched.status_code == 200
    body = switched.json()
    assert (body["schedule"], body["target_on"], body["period_months"], body["ends_on"]) == (
        "once", "2026-12-18", None, None
    )
    # Schedule fields without an amount are half a plan.
    half = client.patch(f"/api/categories/{category_id}", json={"schedule": "recurring"})
    assert half.status_code == 422
    cleared = client.patch(f"/api/categories/{category_id}", json={"budget_amount": None})
    assert {k: cleared.json()[k] for k in categories.PLAN_FIELDS} == categories.NO_PLAN


def test_the_database_refuses_a_bad_plan_even_without_the_api(tmp_path):
    conn = sqlite3.connect(tmp_path / "direct.db")
    try:
        db.migrate(conn)
        user_id = users.get_or_create_user(conn)
        amount_without_schedule = {**categories.NO_PLAN, "budget_cents": 100}
        with pytest.raises(sqlite3.IntegrityError, match="one-time amount needs"):
            categories.create_category(
                conn, user_id, "Loose", "expense", amount_without_schedule
            )
        # A refused plan is not mistaken for a duplicate name.
        categories.create_category(conn, user_id, "Loose", "expense")
        with pytest.raises(categories.DuplicateCategoryError):
            categories.create_category(conn, user_id, "Loose", "expense")
    finally:
        conn.close()


def test_a_three_month_amount_is_not_measured_against_one_month(client):
    client.post(
        "/api/categories",
        json={"name": "Shopping", "kind": "expense", "budget_amount": "400",
              "schedule": "recurring", "period_months": 3},
    )
    row = client.get(
        "/api/categories/summary", params={"start": "2026-11-01", "end": "2026-11-30"}
    ).json()["categories"][0]
    assert row["budget_cents"] == 40000
    assert row["budget_used_percent"] is None
    assert row["remaining_cents"] is None
