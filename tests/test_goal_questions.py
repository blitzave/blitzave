""""Was that the PS5?": money leaving a goal account that matches a funded goal
is asked about once (refine/goals.goal_questions)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app
from blitzave.refine.goals import fill, goal_questions
from blitzave.sources.base import AccountRow, BalanceRow, TxRow
from blitzave.store import db, ingest, users


def _goal(goal_id, name, amount, level="regular"):
    return {"id": goal_id, "name": name, "budget_cents": amount, "goal_level": level}


def _home(balance, goals, account_id="A1"):
    filled, _ = fill(balance, goals)
    return {"id": account_id, "name": "Marcus Online Savings", "balance_cents": balance,
            "goals": filled}


def _withdrawal(tx_id, cents, account_id="A1", posted_on="2026-12-14"):
    return {"transaction_id": tx_id, "account_id": account_id, "posted_on": posted_on,
            "amount_cents": -cents}


# --- the rule, pure ---------------------------------------------------------------


def test_a_withdrawal_that_matches_a_funded_goal_is_asked_about():
    # $500 with the PS5 first: $300 leaves, $200 is left. Before it left, the
    # PS5 was funded and $300 is exactly its amount.
    home = _home(20000, [_goal(1, "PS5", 30000), _goal(2, "Japan", 1200000)])
    [question] = goal_questions([home], [_withdrawal("T1", 30000)])
    assert (question["goal_name"], question["amount_cents"], question["transaction_id"]) == (
        "PS5", 30000, "T1"
    )


def test_no_question_when_the_amount_matches_no_funded_goal():
    # The emergency fund: $1,200 out of $9,000 matches nothing; it just goes
    # back to saving.
    fund = _home(780000, [_goal(1, "Emergency fund", 900000, "critical")])
    assert goal_questions([fund], [_withdrawal("T1", 120000)]) == []
    # The right amount, but the PS5 was still waiting behind Japan: the $300
    # was all Japan's, so no question either.
    waiting = _home(0, [_goal(1, "Japan", 1200000), _goal(2, "PS5", 30000)])
    assert goal_questions([waiting], [_withdrawal("T2", 30000)]) == []


def test_a_goal_lower_in_line_that_was_funded_counts_too():
    home = _home(30000, [_goal(1, "PS5", 30000), _goal(2, "GTA 6", 7000)])
    [question] = goal_questions([home], [_withdrawal("T1", 7000)])
    assert question["goal_name"] == "GTA 6"


def test_one_question_per_goal_newest_first():
    home = _home(0, [_goal(1, "PS5", 30000)])
    questions = goal_questions(
        [home], [_withdrawal("NEW", 30000, posted_on="2026-12-14"),
                 _withdrawal("OLD", 30000, posted_on="2026-12-01")])
    assert [q["transaction_id"] for q in questions] == ["NEW"]


def test_withdrawals_from_other_accounts_are_ignored():
    home = _home(0, [_goal(1, "PS5", 30000)])
    assert goal_questions([home], [_withdrawal("T1", 30000, account_id="CHECKING")]) == []


# --- through the API ----------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        yield client


TODAY = date.today()


def _account_with_history(client, balance_cents, withdrawals):
    """A goal account whose balance already reflects the withdrawals."""
    conn = db.connect()
    try:
        row_id = ingest.upsert_account(conn, users.get_or_create_user(conn), AccountRow(
            external_id="ACT-marcus", account_type="asset", account_number="3390",
            bank_name="Marcus", asset_type="savings", meta={"name": "Marcus Online Savings"}))
        ingest.upsert_balance(conn, row_id, BalanceRow("ACT-marcus", TODAY.isoformat(),
                                                       balance_cents))
        user_id = users.get_or_create_user(conn)
        for tx_id, cents, days_ago in withdrawals:
            ingest.upsert_transaction(conn, user_id, row_id, TxRow(
                account_external_id="ACT-marcus", external_id=tx_id, amount_cents=-cents,
                currency="USD", transaction_type="debit",
                posted_on=(TODAY - timedelta(days=days_ago)).isoformat(),
                description="Transfer to checking"))
    finally:
        conn.close()
    assert client.patch("/api/accounts/ACT-marcus",
                        json={"account_type": "asset", "asset_type": "targeted"}).status_code == 200


def _new_goal(client, name, amount):
    created = client.post("/api/categories", json={
        "name": name, "kind": "savings", "budget_amount": amount, "schedule": "once",
        "target_on": "2027-05-06", "goal_level": "regular", "account_id": "ACT-marcus"})
    assert created.status_code == 201
    return created.json()["id"]


def test_yes_completes_the_goal_on_the_day_the_money_left(client):
    _account_with_history(client, 20000, [("T-ps5", 30000, 2)])
    ps5 = _new_goal(client, "PS5", "300")
    _new_goal(client, "Japan", "12000")

    [question] = client.get("/api/goals").json()["questions"]
    assert (question["goal_id"], question["amount_display"]) == (ps5, "$300.00")

    answered = client.post(f"/api/goals/questions/{question['transaction_id']}",
                           json={"goal_id": ps5, "answer": "bought"})
    assert answered.status_code == 200
    view = answered.json()
    assert view["questions"] == []
    assert [(g["name"], g["completed_on"]) for g in view["completed"]] == [
        ("PS5", (TODAY - timedelta(days=2)).isoformat())
    ]
    # The $200 still there stays with Japan, now first in line.
    [japan] = view["accounts"][0]["goals"]
    assert (japan["name"], japan["saved_display"]) == ("Japan", "$200.00")


def test_something_else_is_never_asked_again_and_changes_nothing(client):
    _account_with_history(client, 20000, [("T-ps5", 30000, 2)])
    ps5 = _new_goal(client, "PS5", "300")

    answered = client.post("/api/goals/questions/T-ps5",
                           json={"goal_id": ps5, "answer": "other"})
    assert answered.status_code == 200
    view = answered.json()
    assert view["questions"] == [] and view["completed"] == []
    assert view["accounts"][0]["goals"][0]["saved_display"] == "$200.00"
    # Answering twice is refused.
    assert client.post("/api/goals/questions/T-ps5",
                       json={"goal_id": ps5, "answer": "bought"}).status_code == 409


def test_old_withdrawals_are_not_asked_about(client):
    _account_with_history(client, 0, [("T-old", 30000, 45)])
    _new_goal(client, "PS5", "300")
    assert client.get("/api/goals").json()["questions"] == []


def test_an_answer_that_doesnt_fit_is_refused(client):
    _account_with_history(client, 20000, [("T-ps5", 30000, 2)])
    _new_goal(client, "PS5", "300")
    japan = _new_goal(client, "Japan", "12000")
    # Japan's amount isn't what left.
    assert client.post("/api/goals/questions/T-ps5",
                       json={"goal_id": japan, "answer": "bought"}).status_code == 409
    assert client.post("/api/goals/questions/T-nope",
                       json={"goal_id": japan, "answer": "bought"}).status_code == 409
    assert client.post("/api/goals/questions/T-ps5",
                       json={"goal_id": 999, "answer": "bought"}).status_code == 404
    assert client.post("/api/goals/questions/T-ps5",
                       json={"goal_id": japan, "answer": "maybe"}).status_code == 422
