"""A targeted savings account's balance fills its goals in line, top first
(refine/goals.py), and the line can be reordered."""

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import URL, create_app
from blitzave.refine.goals import fill, savings_by_account
from blitzave.sources.base import AccountRow, BalanceRow
from blitzave.store import db, ingest, users


def _goal(goal_id: int, name: str, amount: int, place: int = 1, **extra) -> dict:
    return {"id": goal_id, "name": name, "budget_cents": amount, "fill_order": place,
            "goal_level": "regular", "account_id": "A1", "completed_on": None, **extra}


def _split(filled: list[dict]) -> dict:
    return {g["name"]: (g["saved_cents"], g["state"]) for g in filled}


# --- the waterfall, pure --------------------------------------------------------


def test_the_order_decides_who_fills_first():
    # The maintainer's example: $500 in the account, a $300 PS5 and $12,000 Japan.
    ps5, japan = _goal(1, "PS5", 30000), _goal(2, "Japan", 1200000)
    japan_first, left = fill(50000, [japan, ps5])
    assert _split(japan_first) == {"Japan": (50000, "in_progress"), "PS5": (0, "backlog")}
    assert left == 0
    ps5_first, left = fill(50000, [ps5, japan])
    assert _split(ps5_first) == {"PS5": (30000, "done"), "Japan": (20000, "in_progress")}
    assert [g["to_go_cents"] for g in ps5_first] == [0, 1180000]
    assert left == 0


def test_a_full_regular_goal_passes_the_rest_on_and_extra_stays_unassigned():
    filled, left = fill(100000, [_goal(1, "PS5", 30000), _goal(2, "Bike", 60000)])
    assert _split(filled) == {"PS5": (30000, "done"), "Bike": (60000, "done")}
    assert [g["saved_percent"] for g in filled] == [100.0, 100.0]
    assert left == 10000


def test_a_critical_goal_has_the_whole_balance_even_past_its_amount():
    fund = _goal(1, "Emergency fund", 900000, goal_level="critical")
    filled, left = fill(1000000, [fund])
    assert _split(filled) == {"Emergency fund": (1000000, "done")}
    assert (filled[0]["saved_percent"], filled[0]["to_go_cents"]) == (111.1, 0)
    assert left == 0
    partway, left = fill(450000, [fund])
    assert (_split(partway), partway[0]["saved_percent"], left) == (
        {"Emergency fund": (450000, "in_progress")}, 50.0, 0
    )
    empty, _ = fill(-100, [fund])
    assert _split(empty) == {"Emergency fund": (0, "backlog")}


def test_an_empty_or_overdrawn_account_leaves_every_goal_waiting():
    for balance in (0, -2500):
        filled, left = fill(balance, [_goal(1, "PS5", 30000), _goal(2, "Japan", 1200000)])
        assert _split(filled) == {"PS5": (0, "backlog"), "Japan": (0, "backlog")}
        assert [g["saved_percent"] for g in filled] == [0.0, 0.0]
        assert left == 0


def test_percent_is_rounded_to_one_place():
    filled, _ = fill(10000, [_goal(1, "Japan", 30000)])
    assert filled[0]["saved_percent"] == 33.3


def test_savings_lists_every_targeted_account_with_its_line():
    def account(account_id, job, balance):
        return {"id": account_id, "name": f"Account {account_id}", "bank_name": "Ally",
                "account_number": "0000", "asset_type": job, "balance_cents": balance,
                "balance_as_of": "2026-11-06", "account_type": "asset"}

    accounts = [
        account("A1", "targeted", 50000),
        account("A2", "checking", 900000),  # not a goal home: left out
        account("A3", "targeted", None),  # never synced: nothing to share yet
        account("A4", "targeted", 7000),  # empty: still listed
    ]
    categories = [
        _goal(1, "Japan", 1200000, place=2),
        _goal(2, "PS5", 30000, place=1),
        _goal(3, "Trip", 40000, place=3, completed_on="2026-08-01"),  # bought
        _goal(4, "Laptop", 90000, place=1, completed_on="2026-10-02"),
        _goal(5, "Runway", 600000, place=1, account_id="A3"),
        {"id": 6, "name": "Groceries", "budget_cents": 50000, "fill_order": None,
         "account_id": None, "completed_on": None},  # not a goal
    ]
    view = savings_by_account(accounts, categories)
    by_id = {a["id"]: a for a in view["accounts"]}
    assert list(by_id) == ["A1", "A3", "A4"]
    assert view["total_cents"] == 57000  # goal accounts only; A3 has no balance yet
    assert [g["name"] for g in by_id["A1"]["goals"]] == ["PS5", "Japan"]
    assert _split(by_id["A1"]["goals"]) == {"PS5": (30000, "done"), "Japan": (20000, "in_progress")}
    assert _split(by_id["A3"]["goals"]) == {"Runway": (0, "backlog")}
    assert (by_id["A4"]["goals"], by_id["A4"]["unassigned_cents"]) == ([], 7000)
    assert "asset_type" not in by_id["A1"]
    # Completed goals don't share the money; the latest comes first.
    assert [(g["name"], g["state"]) for g in view["completed"]] == [
        ("Laptop", "completed"), ("Trip", "completed")
    ]


# --- through the API ------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    with TestClient(create_app(), base_url=URL) as client:
        yield client


def _targeted(client, external_id: str, number: str, balance_cents: int) -> str:
    conn = db.connect()
    try:
        row_id = ingest.upsert_account(conn, users.get_or_create_user(conn), AccountRow(
            external_id=external_id, account_type="asset", account_number=number,
            bank_name="Ally", asset_type="savings", meta={"name": f"Savings {number}"}))
        ingest.upsert_balance(conn, row_id, BalanceRow(
            account_external_id=external_id, as_of="2026-11-06", balance_cents=balance_cents))
    finally:
        conn.close()
    marked = client.patch(
        f"/api/accounts/{external_id}", json={"account_type": "asset", "asset_type": "targeted"}
    )
    assert marked.status_code == 200
    return external_id


def _new_goal(client, name: str, amount: str, account_id: str, level: str = "regular") -> int:
    created = client.post("/api/categories", json={
        "name": name, "kind": "savings", "budget_amount": amount, "schedule": "once",
        "target_on": "2027-05-06", "goal_level": level, "account_id": account_id})
    assert created.status_code == 201
    return created.json()["id"]


def test_goals_show_what_each_one_has(client):
    home = _targeted(client, "ACT-marcus", "3390", 50000)
    _new_goal(client, "Japan", "12000", home)
    _new_goal(client, "PS5", "300", home)
    _targeted(client, "ACT-free", "1156", 2500)

    view = client.get("/api/goals").json()
    marcus, free = view["accounts"]
    assert view["total_display"] == "$525.00"
    assert (marcus["id"], marcus["balance_display"], marcus["unassigned_display"]) == (
        "ACT-marcus", "$500.00", "$0.00"
    )
    assert [(g["name"], g["saved_display"], g["budget_display"], g["to_go_display"], g["state"])
            for g in marcus["goals"]] == [
        ("Japan", "$500.00", "$12,000.00", "$11,500.00", "in_progress"),
        ("PS5", "$0.00", "$300.00", "$300.00", "backlog"),
    ]
    assert (free["goals"], free["unassigned_display"]) == ([], "$25.00")
    assert view["completed"] == []


def test_reordering_moves_the_money_right_away(client):
    home = _targeted(client, "ACT-marcus", "3390", 50000)
    japan = _new_goal(client, "Japan", "12000", home)
    ps5 = _new_goal(client, "PS5", "300", home)

    reordered = client.put(f"/api/accounts/{home}/goal-order", json={"goal_ids": [ps5, japan]})
    assert reordered.status_code == 200
    assert [(g["name"], g["fill_order"], g["saved_cents"], g["state"])
            for g in reordered.json()["goals"]] == [
        ("PS5", 1, 30000, "done"),
        ("Japan", 2, 20000, "in_progress"),
    ]
    # The new order is kept.
    kept = client.get("/api/goals").json()["accounts"][0]
    assert [g["name"] for g in kept["goals"]] == ["PS5", "Japan"]


def test_a_completed_goal_leaves_the_line(client):
    home = _targeted(client, "ACT-marcus", "3390", 50000)
    ps5 = _new_goal(client, "PS5", "300", home)
    japan = _new_goal(client, "Japan", "12000", home)
    client.patch(f"/api/categories/{ps5}", json={"completed_on": "2026-11-06"})

    view = client.get("/api/goals").json()
    assert _split(view["accounts"][0]["goals"]) == {"Japan": (50000, "in_progress")}
    assert [(g["name"], g["state"]) for g in view["completed"]] == [("PS5", "completed")]
    # It's out of the line, so a new order is only the goals still in it.
    assert client.put(
        f"/api/accounts/{home}/goal-order", json={"goal_ids": [ps5, japan]}
    ).status_code == 409
    assert client.put(
        f"/api/accounts/{home}/goal-order", json={"goal_ids": [japan]}
    ).status_code == 200


def test_an_order_from_a_stale_page_changes_nothing(client):
    home = _targeted(client, "ACT-marcus", "3390", 50000)
    other = _targeted(client, "ACT-wf", "6614", 0)
    japan = _new_goal(client, "Japan", "12000", home)
    ps5 = _new_goal(client, "PS5", "300", home)
    elsewhere = _new_goal(client, "Bike", "800", other)

    for stale in ([japan], [ps5, japan, elsewhere], [ps5, elsewhere]):
        refused = client.put(f"/api/accounts/{home}/goal-order", json={"goal_ids": stale})
        assert refused.status_code == 409
        assert "every goal in this account" in refused.json()["detail"]
    line = client.get("/api/goals").json()["accounts"][0]["goals"]
    assert [(g["name"], g["fill_order"]) for g in line] == [("Japan", 1), ("PS5", 2)]


@pytest.mark.parametrize("goal_ids", [[], [1, 1], ["PS5"]])
def test_a_malformed_order_is_rejected(client, goal_ids):
    _targeted(client, "ACT-marcus", "3390", 50000)
    assert client.put(
        "/api/accounts/ACT-marcus/goal-order", json={"goal_ids": goal_ids}
    ).status_code == 422


def test_reordering_an_unknown_account_is_not_found(client):
    assert client.put(
        "/api/accounts/ACT-nope/goal-order", json={"goal_ids": [1]}
    ).status_code == 404
