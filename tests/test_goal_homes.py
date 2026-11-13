"""Migration 009: goals live in targeted savings accounts. A critical goal has
an account to itself; regular goals share one and fill in line."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import URL, create_app
from blitzave.sources.base import AccountRow
from blitzave.store import categories, db, ingest, users


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    with TestClient(create_app(), base_url=URL) as client:
        yield client


def _add_savings_account(external_id: str, number: str) -> None:
    """A savings account as a sync would first see it (BLITZAVE_HOME is the
    test's own folder)."""
    conn = db.connect()
    try:
        ingest.upsert_account(
            conn,
            users.get_or_create_user(conn),
            AccountRow(
                external_id=external_id, account_type="asset", account_number=number,
                bank_name="Ally", asset_type="savings", meta={"name": f"Savings {number}"},
            ),
        )
    finally:
        conn.close()


def _targeted(client, external_id: str, number: str) -> str:
    _add_savings_account(external_id, number)
    marked = client.patch(
        f"/api/accounts/{external_id}", json={"account_type": "asset", "asset_type": "targeted"}
    )
    assert marked.status_code == 200
    return external_id


def _goal(client, name: str, level: str, account_id: str, amount: str = "500"):
    return client.post(
        "/api/categories",
        json={"name": name, "kind": "savings", "budget_amount": amount,
              "schedule": "once", "target_on": "2027-05-06",
              "goal_level": level, "account_id": account_id},
    )


def test_upgrade_keeps_every_account_job_and_adds_no_goals(tmp_path):
    conn = sqlite3.connect(tmp_path / "upgrade.db")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for migration in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            if int(migration.name.split("_", 1)[0]) <= 8:
                conn.executescript(migration.read_text())
        conn.execute("PRAGMA user_version = 8")
        user_id = users.get_or_create_user(conn)
        for external_id, number, job in [("A1", "1111", "checking"), ("A2", "2222", "savings")]:
            ingest.upsert_account(conn, user_id, AccountRow(
                external_id=external_id, account_type="asset", account_number=number,
                bank_name="Chase", asset_type=job))
        conn.execute(
            "INSERT INTO categories (user_id, name, kind) VALUES (?, 'Travel', 'savings')",
            (user_id,))
        conn.commit()
        db.migrate(conn)

        jobs = conn.execute("SELECT account_id, asset_type FROM assets ORDER BY 1").fetchall()
        assert jobs == [(1, "checking"), (2, "savings")]
        travel = categories.list_categories(conn)[0]
        assert {k: travel[k] for k in categories.GOAL_FIELDS} == categories.NO_GOAL
        # The rebuilt table still refuses an asset row for a liability.
        conn.execute(
            "INSERT INTO accounts (user_id, account_type, account_number, external_id)"
            " VALUES (?, 'liability', '9999', 'L1')", (user_id,))
        with pytest.raises(sqlite3.IntegrityError, match="account_type = asset"):
            conn.execute(
                "INSERT INTO assets (account_id, bank_name, asset_type)"
                " VALUES (3, 'Chase', 'savings')")
    finally:
        conn.close()


def test_a_critical_goal_lives_alone_in_a_targeted_account(client):
    home = _targeted(client, "ACT-ally", "4821")
    created = _goal(client, "Emergency fund", "critical", home, "9000")
    assert created.status_code == 201
    body = created.json()
    assert (body["goal_level"], body["account_id"], body["fill_order"], body["completed_on"]) == (
        "critical", "ACT-ally", 1, None
    )
    # Nothing else moves in, critical or regular.
    assert _goal(client, "Runway", "critical", home).status_code == 409
    refused = _goal(client, "PS5", "regular", home)
    assert refused.status_code == 409
    assert "to itself" in refused.json()["detail"]


def test_regular_goals_share_an_account_and_line_up_in_order(client):
    home = _targeted(client, "ACT-marcus", "3390")
    lined_up = [_goal(client, n, "regular", home).json()["fill_order"]
                for n in ("PS5", "Japan", "GTA 6")]
    assert lined_up == [1, 2, 3]
    # A critical goal can't join them either.
    assert _goal(client, "Down payment", "critical", home).status_code == 409


def test_goals_live_only_in_targeted_accounts(client):
    _add_savings_account("ACT-buffer", "1156")
    refused = _goal(client, "PS5", "regular", "ACT-buffer")
    assert refused.status_code == 409
    assert "targeted savings" in refused.json()["detail"]
    assert client.get("/api/categories").json() == []


@pytest.mark.parametrize(
    "extra",
    [
        {"goal_level": "critical"},  # no account
        {"account_id": "ACT-ally"},  # no level
        {"goal_level": "urgent", "account_id": "ACT-ally"},
        {"goal_level": "critical", "account_id": "ACT-ally", "kind": "expense"},
        {"goal_level": "critical", "account_id": "ACT-ally", "budget_amount": None,
         "schedule": None, "target_on": None},  # no amount
        {"goal_level": "critical", "account_id": "ACT-nope"},  # unknown account
    ],
)
def test_half_or_unknown_goal_homes_are_rejected(client, extra):
    _targeted(client, "ACT-ally", "4821")
    body = {"name": "Fund", "kind": "savings", "budget_amount": "500",
            "schedule": "once", "target_on": "2027-05-06", **extra}
    assert client.post("/api/categories", json=body).status_code == 422
    assert client.get("/api/categories").json() == []


def test_an_account_holding_goals_keeps_its_job(client):
    home = _targeted(client, "ACT-ally", "4821")
    goal_id = _goal(client, "Emergency fund", "critical", home).json()["id"]
    for change in (
        {"account_type": "asset", "asset_type": "savings"},
        {"account_type": "liability", "liability_type": "loan"},
    ):
        refused = client.patch(f"/api/accounts/{home}", json=change)
        assert refused.status_code == 409
        assert "holds goals" in refused.json()["detail"]
    account = next(a for a in client.get("/api/accounts").json() if a["id"] == home)
    assert (account["account_type"], account["asset_type"]) == ("asset", "targeted")
    # Once its goal is completed, the account is free to change.
    client.patch(f"/api/categories/{goal_id}", json={"completed_on": "2026-11-06"})
    assert client.patch(
        f"/api/accounts/{home}", json={"account_type": "asset", "asset_type": "savings"}
    ).status_code == 200


def test_completing_a_goal_frees_its_account(client):
    home = _targeted(client, "ACT-c1", "2208")
    old = _goal(client, "Down payment", "critical", home).json()["id"]
    done = client.patch(f"/api/categories/{old}", json={"completed_on": "2026-11-06"})
    assert (done.status_code, done.json()["completed_on"]) == (200, "2026-11-06")
    assert _goal(client, "Car", "critical", home).status_code == 201
    # The account is taken now, so the old goal can't come back to it.
    assert client.patch(f"/api/categories/{old}", json={"completed_on": None}).status_code == 409


def test_a_goal_that_comes_back_or_moves_joins_the_end_of_the_line(client):
    home = _targeted(client, "ACT-marcus", "3390")
    other = _targeted(client, "ACT-wf", "6614")
    ps5, japan, gta = (_goal(client, n, "regular", home).json()["id"]
                       for n in ("PS5", "Japan", "GTA 6"))
    client.patch(f"/api/categories/{ps5}", json={"completed_on": "2026-12-14"})
    back = client.patch(f"/api/categories/{ps5}", json={"completed_on": None})
    assert back.json()["fill_order"] == 4
    renamed = client.patch(f"/api/categories/{japan}", json={"name": "Japan 2028"})
    assert renamed.json()["fill_order"] == 2  # an edit in place keeps its place
    moved = client.patch(
        f"/api/categories/{gta}", json={"goal_level": "regular", "account_id": other}
    )
    assert (moved.json()["account_id"], moved.json()["fill_order"]) == (other, 1)


def test_a_goal_can_go_back_to_being_a_plain_savings_category(client):
    home = _targeted(client, "ACT-ally", "4821")
    goal_id = _goal(client, "Emergency fund", "critical", home).json()["id"]
    plain = client.patch(
        f"/api/categories/{goal_id}", json={"goal_level": None, "account_id": None}
    )
    assert plain.status_code == 200
    assert {k: plain.json()[k] for k in categories.GOAL_FIELDS} == categories.NO_GOAL
    # Half a home is refused before it reaches the database.
    assert client.patch(
        f"/api/categories/{goal_id}", json={"goal_level": "regular"}
    ).status_code == 422
    # A completed goal can go back too; its completion goes with its home.
    again = _goal(client, "Runway", "critical", home).json()["id"]
    client.patch(f"/api/categories/{again}", json={"completed_on": "2026-11-06"})
    cleared = client.patch(
        f"/api/categories/{again}", json={"goal_level": None, "account_id": None}
    )
    assert {k: cleared.json()[k] for k in categories.GOAL_FIELDS} == categories.NO_GOAL


def test_a_goal_stays_a_savings_category_with_an_amount(client):
    home = _targeted(client, "ACT-ally", "4821")
    goal_id = _goal(client, "Emergency fund", "critical", home).json()["id"]
    assert client.patch(f"/api/categories/{goal_id}", json={"kind": "expense"}).status_code == 409
    assert client.patch(
        f"/api/categories/{goal_id}", json={"budget_amount": None}
    ).status_code == 409
    # A category that isn't a goal can't be completed.
    plain = client.post("/api/categories", json={"name": "Misc", "kind": "expense"}).json()
    assert client.patch(
        f"/api/categories/{plain['id']}", json={"completed_on": "2026-11-06"}
    ).status_code == 409


def test_the_database_refuses_a_second_goal_in_a_critical_account(tmp_path):
    """The API never sends this; the triggers are the backstop."""
    conn = sqlite3.connect(tmp_path / "direct.db")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        db.migrate(conn)
        user_id = users.get_or_create_user(conn)
        ingest.upsert_account(conn, user_id, AccountRow(
            external_id="A1", account_type="asset", account_number="4821",
            bank_name="Ally", asset_type="targeted"))
        plan = {**categories.NO_PLAN, "budget_cents": 900000, "schedule": "once",
                "target_on": "2027-05-06"}
        categories.create_category(
            conn, user_id, "Emergency fund", "savings", plan,
            {"goal_level": "critical", "account_id": "A1"})
        with pytest.raises(sqlite3.IntegrityError, match="to itself"):
            conn.execute(
                "INSERT INTO categories (user_id, name, kind, budget_cents, schedule, target_on,"
                " goal_level, account_id, fill_order)"
                " VALUES (?, 'PS5', 'savings', 50000, 'once', '2027-01-01', 'regular', 1, 2)",
                (user_id,))
    finally:
        conn.close()
