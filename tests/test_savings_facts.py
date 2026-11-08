"""The Savings arc's facts (refine/savings.py): set aside, went first, and
when each goal gets there. Synthetic fixtures only."""

from datetime import date

from blitzave.refine.goals import fill
from blitzave.refine.savings import (
    account_pace,
    add_months,
    complete_months,
    goal_trajectories,
    monthly_flows,
    pay_yourself_first,
    spending_pace,
)

TODAY = date(2026, 11, 8)
FIRST = date(2026, 1, 1)


def _in(day, cents, account="ALLY", description="Transfer from Chase"):
    return {"account_id": account, "posted_on": day, "amount_cents": cents,
            "description": description}


def _spend(day, cents, description="Store"):
    return {"posted_on": day, "amount_cents": cents, "description": description}


# --- P0.4 savings flow ----------------------------------------------------------


def test_a_normal_month_a_withdrawal_and_a_month_with_nothing():
    flows = monthly_flows([
        _in("2026-08-01", 64000),
        _in("2026-08-31", 312, description="INTEREST PAID"),   # not behavior
        _in("2026-09-01", 64000),
        _in("2026-09-20", -120000),                             # a withdrawal
    ])
    august, september = flows["2026-08"], flows["2026-09"]
    assert (august["in_cents"], august["out_cents"], august["net_cents"]) == (64000, 0, 64000)
    assert august["deposits"] == [("2026-08-01", 64000)]
    assert (september["in_cents"], september["out_cents"], september["net_cents"]) == (
        64000, 120000, -56000
    )
    assert "2026-10" not in flows   # nothing set aside: no entry, read as zero


def test_whole_months_never_reach_back_before_the_data():
    assert complete_months(TODAY, date(2026, 9, 14), 6) == ["2026-09", "2026-10"]
    assert complete_months(TODAY, FIRST, 3) == ["2026-08", "2026-09", "2026-10"]
    assert complete_months(TODAY, None, 3) == []
    assert add_months("2026-11", 3) == "2027-02"


# --- P0.5 pay yourself first -----------------------------------------------------


HISTORY = [_in(f"2026-{m:02d}-01", 64000) for m in (8, 9, 10)]


def test_a_good_month_savings_went_first():
    flows = monthly_flows(HISTORY + [_in("2026-11-01", 64000)])
    spending = [_spend("2026-11-02", 4000), _spend("2026-11-06", 9000)]
    facts = pay_yourself_first(flows, spending, TODAY, FIRST)
    assert facts["went_first"] is True
    assert (facts["in_cents"], facts["first_in_on"], facts["single_day"]) == (
        64000, "2026-11-01", True
    )
    assert facts["spending_share_before_half"] == 0.0
    assert (facts["usual_in_cents"], facts["usual_day"]) == (64000, 1)


def test_a_bad_month_spending_went_first_and_its_just_a_fact():
    flows = monthly_flows(HISTORY + [_in("2026-11-07", 20000)])
    spending = [_spend("2026-11-02", 30000), _spend("2026-11-05", 10000),
                _spend("2026-11-08", 2000)]
    facts = pay_yourself_first(flows, spending, TODAY, FIRST)
    assert facts["went_first"] is False
    assert facts["spending_share_before_half"] == 0.952
    # The other rule reads the same month differently: something moved by the 7th.
    assert pay_yourself_first(flows, spending, TODAY, FIRST, rule="by_day")["went_first"] is True


def test_nothing_moved_yet_this_month():
    flows = monthly_flows(HISTORY)
    facts = pay_yourself_first(flows, [_spend("2026-11-03", 5000)], TODAY, FIRST)
    assert (facts["in_cents"], facts["went_first"], facts["first_in_on"]) == (0, False, None)
    assert facts["usual_day"] == 1


def test_spending_pace_against_the_same_point_in_usual_months():
    usual = [_spend(f"2026-{m:02d}-03", 30000) for m in (8, 9, 10)]
    late = [_spend(f"2026-{m:02d}-20", 90000) for m in (8, 9, 10)]   # after the 8th: not counted
    heavy = usual + late + [_spend("2026-11-02", 48000), _spend("2026-11-05", 2000)]
    facts = spending_pace(heavy, TODAY, FIRST)
    assert (facts["spent_cents"], facts["usual_to_date_cents"]) == (50000, 30000)
    assert facts["heavier"] and facts["one_charge_explains_it"]
    assert facts["largest"]["posted_on"] == "2026-11-02"
    calm = spending_pace(usual + [_spend("2026-11-02", 31000)], TODAY, FIRST)
    assert not calm["heavier"] and not calm["lighter"]


# --- P0.6 goal trajectory --------------------------------------------------------


def _home(balance, goals):
    filled, _ = fill(balance, goals)
    return {"id": "ALLY", "balance_cents": balance, "goals": filled}


def _goal(goal_id, amount, level="regular", target_on=None):
    return {"id": goal_id, "name": f"Goal {goal_id}", "budget_cents": amount,
            "goal_level": level, "target_on": target_on}


def test_a_steady_saver_gets_a_tight_range():
    flows = monthly_flows([_in(f"2026-{m:02d}-01", 50000) for m in range(5, 11)])
    pace = account_pace(flows, "ALLY", TODAY, FIRST)
    assert (pace["avg3_cents"], pace["avg6_cents"]) == (50000, 50000)
    home = _home(400000, [_goal(1, 900000, "critical", target_on="2027-12-01")])
    facts = goal_trajectories(home, pace, TODAY)[1]
    # $5,000 to go at $500 a month: ten months, both ends.
    assert (facts["need_cents"], facts["eta_from"], facts["eta_to"]) == (500000, "2027-09", "2027-09")
    assert facts["on_pace"] is True


def test_a_rising_saver_gets_a_range_and_one_bad_month_barely_moves_it():
    rising = [_in(f"2026-{m:02d}-01", c) for m, c in
              [(5, 20000), (6, 20000), (7, 20000), (8, 60000), (9, 60000), (10, 60000)]]
    pace = account_pace(monthly_flows(rising), "ALLY", TODAY, FIRST)
    assert (pace["avg3_cents"], pace["avg6_cents"]) == (60000, 40000)
    home = _home(0, [_goal(1, 240000)])
    facts = goal_trajectories(home, pace, TODAY)[1]
    assert (facts["eta_from"], facts["eta_to"]) == ("2027-03", "2027-05")
    # October saved nothing. The range slides two months (May to July); it
    # still gives a date, and the 6-month end keeps it from swinging further.
    bad = rising[:-1] + [_in("2026-10-01", 0)]
    bad_facts = goal_trajectories(home, account_pace(monthly_flows(bad), "ALLY", TODAY, FIRST),
                                  TODAY)[1]
    assert (bad_facts["eta_from"], bad_facts["eta_to"]) == ("2027-05", "2027-07")


def test_zero_savings_gives_no_date_rather_than_never():
    pace = account_pace(monthly_flows([]), "ALLY", TODAY, FIRST)
    facts = goal_trajectories(_home(10000, [_goal(1, 30000)]), pace, TODAY)[1]
    assert (facts["eta_from"], facts["eta_to"], facts["rate_cents"]) == (None, None, 0)
    assert facts["need_cents"] == 20000


def test_a_shared_account_fills_its_line_in_order():
    flows = monthly_flows([_in(f"2026-{m:02d}-01", 10000) for m in range(5, 11)])
    pace = account_pace(flows, "ALLY", TODAY, FIRST)
    home = _home(50000, [_goal(1, 30000), _goal(2, 1200000), _goal(3, 7000)])
    facts = goal_trajectories(home, pace, TODAY)
    assert facts[1]["eta_from"] == "2026-11"            # already covered
    assert facts[2]["need_cents"] == 1180000            # $12,300 for both, minus $500
    assert facts[2]["eta_from"] == add_months("2026-11", 118)
    assert facts[3]["need_cents"] == 1187000            # waits for everything above it


# --- the store's rows -------------------------------------------------------------


def test_spending_leaves_out_goal_accounts_transfers_and_savings(tmp_path):
    import sqlite3

    from blitzave.sources.base import AccountRow, BalanceRow, TxRow
    from blitzave.store import (
        accounts,
        categories,
        db,
        ingest,
        transactions,
        transfers,
        users,
    )

    conn = sqlite3.connect(tmp_path / "rows.db")
    conn.execute("PRAGMA foreign_keys = ON")
    db.migrate(conn)
    user = users.get_or_create_user(conn)
    ids = {}
    for ext, number, job in [("CHK", "1001", "checking"), ("ALLY", "4821", "targeted")]:
        ids[ext] = ingest.upsert_account(conn, user, AccountRow(
            external_id=ext, account_type="asset", account_number=number,
            bank_name="Bank", asset_type=job))
    rows = [
        ("CHK", "T1", -5000, "Groceries"),                 # spending
        ("CHK", "T2", -64000, "Transfer to Ally"),         # transfer leg: not spending
        ("ALLY", "T3", 64000, "Transfer from Chase"),      # set aside
        ("ALLY", "T4", -30000, "PS5 at Target"),           # in a goal account: not spending
        ("CHK", "T5", -10000, "To my credit union"),       # filed under savings: not spending
    ]
    for ext, tx_id, cents, description in rows:
        ingest.upsert_transaction(conn, user, ids[ext], TxRow(
            ext, tx_id, cents, "USD", "debit" if cents < 0 else "credit", "2026-11-02",
            description))
    transfers.create_transfer(conn, user, "T2", "T3")
    elsewhere = categories.create_category(conn, user, "Credit union", "savings")
    transactions.update_metadata(conn, "T5", categories={elsewhere["id"]: 1.0})

    assert [r["description"] for r in transactions.spending_rows(conn, "2026-11-01")] == [
        "Groceries"
    ]
    assert [(r["account_id"], r["amount_cents"])
            for r in transactions.goal_account_rows(conn, "2026-11-01")] == [
        ("ALLY", 64000), ("ALLY", -30000)
    ]
    assert transactions.first_posted_on(conn) == "2026-11-02"
    ingest.upsert_balance(conn, ids["ALLY"], BalanceRow("ALLY", "2026-11-02", 34000))
    assert accounts.goal_balance_history(conn, "2026-11-01") == {"ALLY": [("2026-11-02", 34000)]}
    conn.close()
