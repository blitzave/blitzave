"""Home's one card (refine/home.py picks, copy/home.py says) and the Savings
pace lines (copy/savings.py). Every sentence is held to the Prime Directive,
including on the bad-month fixtures."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from blitzave.__main__ import create_app
from blitzave.copy.home import home_words
from blitzave.copy.savings import pace_line
from blitzave.refine.goals import fill
from blitzave.refine.home import newly_reached, pick
from blitzave.refine.savings import add_months, month_key
from blitzave.sources.base import AccountRow, BalanceRow, TxRow
from blitzave.store import db, ingest, users

TODAY = date(2026, 11, 8)

# Words a scorekeeper would use. None of them may appear in anything Home or
# Savings says.
SCOREKEEPER = ("streak", "missed", "fail", "overspent", "behind", "should", "warning",
               "only ", "again", "!")


def _assert_ally(*texts):
    for text in texts:
        if text is None:
            continue
        lowered = text.lower()
        for word in SCOREKEEPER:
            assert word not in lowered, f"{word!r} in {text!r}"


def _goal(goal_id, name, amount, *, saved=0, level="regular", schedule="once"):
    state = "done" if saved >= amount else "in_progress" if saved else "backlog"
    return {"id": goal_id, "name": name, "budget_cents": amount, "goal_level": level,
            "schedule": schedule, "state": state, "to_go_cents": max(amount - saved, 0),
            "saved_cents": saved, "target_on": None}


QUIET_SPENDING = {"spent_cents": 41200, "usual_to_date_cents": 40000, "heavier": False,
                  "lighter": False, "largest": None, "one_charge_explains_it": False}
HEAVY_SPENDING = {"spent_cents": 96800, "usual_to_date_cents": 40000, "heavier": True,
                  "lighter": False, "one_charge_explains_it": True,
                  "largest": {"posted_on": "2026-11-02", "amount_cents": 48000}}
SAVED_FIRST = {"in_cents": 64000, "first_in_on": "2026-11-01", "single_day": True,
               "went_first": True, "usual_in_cents": 64000, "usual_day": 1}
NOTHING_YET = {"in_cents": 0, "first_in_on": None, "single_day": False,
               "went_first": False, "usual_in_cents": 64000, "usual_day": 1}


def _pick(**overrides):
    facts = {"today": TODAY, "first_posted_on": "2026-05-01", "reached": [],
             "spending": QUIET_SPENDING, "homes": [], "trajectories": {},
             "pay_first": NOTHING_YET | {"usual_in_cents": 0}}
    return pick(**(facts | overrides))


# --- which rule wins -----------------------------------------------------------------


def test_less_than_a_month_of_history_is_getting_started():
    assert _pick(first_posted_on="2026-10-20")["rule"] == "getting_started"
    assert _pick(first_posted_on=None)["rule"] == "getting_started"


def test_a_win_shows_before_a_heavy_month():
    ps5 = _goal(1, "PS5", 30000, saved=30000)
    card = _pick(reached=[ps5], spending=HEAVY_SPENDING)
    assert card["rule"] == "goal_reached"
    assert home_words(card, TODAY) == (
        "PS5: done.", "You set aside $300.00 for it, a bit at a time. It's ready when you are.")


def test_a_heavy_month_is_absorbed_not_scolded():
    card = _pick(spending=HEAVY_SPENDING, pay_first=NOTHING_YET)
    assert card["rule"] == "heavier_month"
    headline, support = home_words(card, TODAY)
    assert headline == "A heavier start. That happens."
    assert support == ("Most of it is one $480.00 charge on the 2nd. "
                       "Your three-month average hasn't moved.")
    spread = home_words({**card, "spending": HEAVY_SPENDING | {"one_charge_explains_it": False}},
                        TODAY)[1]
    assert spread.startswith("It's spread across a few days.")
    _assert_ally(headline, support, spread)


def test_almost_there_names_the_month_when_money_is_coming_in():
    ps5 = _goal(1, "PS5", 50000, saved=38000)
    card = _pick(homes=[{"goals": [ps5]}],
                 trajectories={1: {"rate_cents": 4000, "eta_from": "2027-02"}})
    assert card["rule"] == "goal_close"
    assert home_words(card, TODAY) == (
        "Almost there.", "PS5 is $120.00 away. At $40.00 a month, that's February 2027.")
    # Nothing coming in: just the distance, no date.
    quiet = _pick(homes=[{"goals": [ps5]}], trajectories={1: {"rate_cents": 0, "eta_from": None}})
    assert home_words(quiet, TODAY)[1] == "PS5 is $120.00 away."


def test_saved_first_says_so_only_with_what_is_true():
    card = _pick(pay_first=SAVED_FIRST)
    assert card["rule"] == "saved_first"
    assert home_words(card, TODAY)[1] == (
        "Savings went first this month, $640.00 on the 1st, "
        "and spending is right where it usually is.")
    spread = home_words(_pick(pay_first=SAVED_FIRST | {"single_day": False}), TODAY)[1]
    assert "starting on the 1st" in spread
    no_history = home_words(_pick(pay_first=SAVED_FIRST,
                                  spending=QUIET_SPENDING | {"usual_to_date_cents": 0}), TODAY)[1]
    assert no_history == "Savings went first this month, $640.00 on the 1st."


def test_a_quiet_month_is_a_gentle_note_not_a_reminder():
    card = _pick(pay_first=NOTHING_YET)
    assert card["rule"] == "not_yet"
    headline, support = home_words(card, TODAY)
    assert (headline, support) == (
        "A quiet month for savings so far.",
        "Your usual $640.00 tends to move around the 1st. There's plenty of month left.")
    late = home_words(card, date(2026, 11, 26))[1]
    assert late.endswith("There's still time this month.")
    _assert_ally(headline, support, late)
    # Before the usual day there's nothing to say about it.
    assert _pick(pay_first=NOTHING_YET | {"usual_day": 15})["rule"] == "steady"


def test_steady_always_matches_and_stays_true():
    card = _pick(homes=[{"goals": []}], trajectories={})
    assert card["rule"] == "steady"
    assert home_words(card, TODAY) == ("Nothing needs you today.",
                                       "Spending is in line with usual.")
    moving = _pick(trajectories={1: {"rate_cents": 5000}})
    assert home_words(moving, TODAY)[1] == (
        "Spending is in line with usual, and your goals are moving.")
    fresh = _pick(spending=QUIET_SPENDING | {"usual_to_date_cents": 0})
    assert home_words(fresh, TODAY)[1] == "Everything's synced and up to date."


def test_a_goal_is_news_for_a_week_after_its_balance_covered_it():
    goals = [{"id": 1, "name": "PS5", "budget_cents": 30000, "goal_level": "regular",
              "schedule": "once"}]
    filled, _ = fill(31000, goals)
    home = {"id": "MARCUS", "goals": filled}
    crossed = {"MARCUS": [("2026-11-01", 25000), ("2026-11-05", 31000), ("2026-11-08", 31000)]}
    assert [g["reached_on"] for g in newly_reached([home], crossed, TODAY)] == ["2026-11-05"]
    long_ago = {"MARCUS": [("2026-10-20", 25000), ("2026-10-25", 31000)]}
    assert newly_reached([home], long_ago, TODAY) == []
    always_full = {"MARCUS": [("2026-11-02", 31000), ("2026-11-07", 31000)]}
    assert newly_reached([home], always_full, TODAY) == []


# --- pace lines on Savings ------------------------------------------------------------


def _trajectory(**facts):
    return {"eta_from": "2027-05", "eta_to": "2027-06", "rate_cents": 60000,
            "target_month": None, "on_pace": False, "per_month_for_target_cents": None,
            "history_months": 6} | facts


def test_pace_lines_give_a_range_and_a_way_to_make_the_date():
    goal = _goal(1, "Japan", 1200000, saved=50000)
    assert pace_line(goal, _trajectory(), TODAY) == (
        "At about $600.00 a month, it gets there around May–June 2027.")
    assert pace_line(goal, _trajectory(eta_to="2027-05"), TODAY) == (
        "At about $600.00 a month, it gets there around May 2027.")
    behind = pace_line(goal, _trajectory(target_month="2027-03", per_month_for_target_cents=287500),
                       TODAY)
    assert behind.endswith("About $2,875.00 a month gets it there by March 2027.")
    young = pace_line(goal, _trajectory(history_months=2), TODAY)
    assert young.endswith("That's from 2 months of history so far.")
    _assert_ally(behind, young)


def test_no_pace_is_never_never():
    goal = _goal(1, "Japan", 1200000)
    line = pace_line(goal, _trajectory(eta_from=None, eta_to=None, rate_cents=0), TODAY)
    assert line == ("Nothing has come into this account lately, so there's no date yet. "
                    "Any amount gets it moving.")
    assert "never" not in line.lower()
    assert pace_line(_goal(2, "PS5", 300, saved=300), _trajectory(), TODAY) is None


# --- through the API -----------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        yield client


def test_a_fresh_install_is_getting_to_know_you(client):
    home = client.get("/api/home").json()
    assert home["rule"] == "getting_started"
    assert home["headline"] == "Getting to know your money."
    assert (home["spent_display"], home["set_aside_display"], home["goals_underway"]) == (
        "$0.00", "$0.00", 0)
    today = date.today()
    assert (home["day"], home["date"]) == (today.day, today.isoformat())


def test_home_and_savings_read_real_history(client):
    today = date.today()
    conn = db.connect()
    try:
        user = users.get_or_create_user(conn)
        chase = ingest.upsert_account(conn, user, AccountRow(
            external_id="ACT-chase", account_type="asset", account_number="1001",
            bank_name="Chase", asset_type="checking"))
        ally = ingest.upsert_account(conn, user, AccountRow(
            external_id="ACT-ally", account_type="asset", account_number="4821",
            bank_name="Ally", asset_type="savings"))
        ingest.upsert_balance(conn, ally, BalanceRow("ACT-ally", today.isoformat(), 400000))
        # Four months ago onward: $500 set aside on the 1st, $300 spent on the 2nd.
        for back in range(4, -1, -1):
            first = date.fromisoformat(f"{add_months(month_key(today), -back)}-01")
            ingest.upsert_transaction(conn, user, ally, TxRow(
                "ACT-ally", f"IN{back}", 50000, "USD", "credit", first.isoformat(),
                "Transfer from Chase"))
            ingest.upsert_transaction(conn, user, chase, TxRow(
                "ACT-chase", f"OUT{back}", -30000, "USD", "debit",
                first.replace(day=2).isoformat(), "Groceries"))
    finally:
        conn.close()
    client.patch("/api/accounts/ACT-ally", json={"account_type": "asset", "asset_type": "targeted"})
    client.post("/api/categories", json={
        "name": "Emergency fund", "kind": "savings", "budget_amount": "9000",
        "schedule": "once", "target_on": "2035-01-01",
        "goal_level": "critical", "account_id": "ACT-ally"})

    home = client.get("/api/home").json()
    assert home["rule"] in {"saved_first", "steady"}
    assert home["set_aside_display"] == "$500.00"
    assert home["goals_underway"] == 1
    _assert_ally(home["headline"], home["support"])

    [fund] = client.get("/api/goals").json()["accounts"][0]["goals"]
    # $5,000 to go at $500 a month: ten months out.
    assert fund["pace_line"].startswith("At about $500.00 a month, it gets there around ")
    _assert_ally(fund["pace_line"])
