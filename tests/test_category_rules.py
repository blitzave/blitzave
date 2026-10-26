import copy
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from blitzave import jobs
from blitzave.__main__ import create_app
from blitzave.store import db

VALID_TOKEN = "aHR0cHM6Ly9icmlkZ2Uuc2ltcGxlZmluLm9yZy9jbGFpbS9hYmM="
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "simplefin_accounts.json").read_text()
)
VENTURE_X = "ACT-a5ec3a62-0c88-4149-88b2-505a923c64fc"
COSTCO_A = "TRN-458ebd8b-9989-4d64-bd7e-86b3e064ed57"  # COSTCO WHSE #0669 -185.06
COSTCO_B = "TRN-a0ff986e-9eff-4e82-b109-7f5410f2eb14"  # COSTCO WHSE #0669 +89.79


def _fixture_with(*extra_transactions: dict) -> dict:
    """The standard fixture plus synthetic transactions on the Venture X card."""
    fixture = copy.deepcopy(FIXTURE)
    venture_x = next(a for a in fixture["accounts"] if a.get("id") == VENTURE_X)
    venture_x["transactions"].extend(extra_transactions)
    return fixture


def _txn(external_id: str, description: str, amount: str = "-10.00") -> dict:
    return {
        "amount": amount,
        "description": description,
        "id": external_id,
        "posted": 1792756800,
        "transacted_at": 1792756800,
    }


# The same merchant as COSTCO_A/B, spelled the ways banks actually vary it.
VARIANTS = (
    _txn("TRN-costco-lower", "costco whse #0669"),  # different case
    _txn("TRN-costco-space", "   COSTCO  WHSE #0669 "),  # extra spaces
    _txn("TRN-costco-store", "COSTCO WHSE #1234"),  # different store number
    _txn("TRN-costco-aplpay", "AplPay COSTCO WHSE #0669"),  # wallet prefix
)
DIFFERENT_MERCHANT = _txn("TRN-costco-gas", "COSTCO GAS #0669")  # "costco gas", not "costco whse"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))


def _serve(monkeypatch, fixture: dict) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json=fixture))


@pytest.fixture
def client(monkeypatch):
    with TestClient(create_app()) as c:
        monkeypatch.setattr(
            httpx,
            "post",
            lambda *a, **k: httpx.Response(200, text="https://u:p@bridge.simplefin.org/access"),
        )
        c.post("/api/connect", json={"setup_token": VALID_TOKEN})
        _serve(monkeypatch, _fixture_with(*VARIANTS, DIFFERENT_MERCHANT))
        c.post("/api/sync")
        yield c


def _by_id(client) -> dict[str, dict]:
    body = client.get("/api/transactions", params={"limit": 500}).json()
    return {t["id"]: t for t in body["transactions"]}


def _category(client, name: str) -> int:
    return client.post("/api/categories", json={"name": name, "kind": "expense"}).json()["id"]


def _rules() -> list[tuple]:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT match_key, description, category_id FROM category_rules ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# --- validation at the wall ---------------------------------------------------


def test_apply_to_all_rejects_a_split(client):
    food = _category(client, "Food")
    fun = _category(client, "Fun")
    response = client.patch(
        f"/api/transactions/{COSTCO_A}",
        json={"categories": {str(food): 0.6, str(fun): 0.4}, "apply_to_all": True},
    )
    assert response.status_code == 422
    assert _rules() == []


def test_apply_to_all_rejects_missing_or_empty_categories(client):
    tag = client.post("/api/tags", json={"name": "work"}).json()["id"]
    assert client.patch(
        f"/api/transactions/{COSTCO_A}", json={"tags": [tag], "apply_to_all": True}
    ).status_code == 422
    assert client.patch(
        f"/api/transactions/{COSTCO_A}", json={"categories": {}, "apply_to_all": True}
    ).status_code == 422


# --- apply to all -------------------------------------------------------------


def test_apply_to_all_matches_the_same_merchant_however_its_spelled(client):
    groceries = _category(client, "Groceries")

    response = client.patch(
        f"/api/transactions/{COSTCO_A}",
        json={"categories": {str(groceries): 1.0}, "apply_to_all": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["categories"] == {str(groceries): 1.0}
    assert body["categories_set_by"] == "user"  # the one the user actually edited
    assert body["applied_to_count"] == 1 + len(VARIANTS)  # COSTCO_B plus every variant

    txns = _by_id(client)
    for same_merchant in (COSTCO_B, *(v["id"] for v in VARIANTS)):
        assert txns[same_merchant]["categories"] == {str(groceries): 1.0}
        assert txns[same_merchant]["categories_set_by"] == "rule"
    assert txns[DIFFERENT_MERCHANT["id"]]["categories"] == {}
    assert txns[DIFFERENT_MERCHANT["id"]]["categories_set_by"] is None

    # The rule is in the database, so a fresh connection still sees it.
    assert _rules() == [("costco whse", "COSTCO WHSE #0669", groceries)]


def test_apply_to_all_rejects_a_description_with_no_letters(client, monkeypatch):
    groceries = _category(client, "Groceries")
    _serve(monkeypatch, _fixture_with(_txn("TRN-digits", "#0669 - 12/31")))
    jobs.sync()

    response = client.patch(
        "/api/transactions/TRN-digits",
        json={"categories": {str(groceries): 1.0}, "apply_to_all": True},
    )
    assert response.status_code == 422
    assert _rules() == []
    assert _by_id(client)["TRN-digits"]["categories"] == {}  # nothing half-saved


def test_apply_to_all_overrides_an_earlier_user_choice(client):
    food = _category(client, "Food")
    groceries = _category(client, "Groceries")
    client.patch(f"/api/transactions/{COSTCO_B}", json={"categories": {str(food): 1.0}})

    client.patch(
        f"/api/transactions/{COSTCO_A}",
        json={"categories": {str(groceries): 1.0}, "apply_to_all": True},
    )

    assert _by_id(client)[COSTCO_B]["categories"] == {str(groceries): 1.0}


def test_second_apply_to_all_replaces_the_rule(client):
    groceries = _category(client, "Groceries")
    household = _category(client, "Household")
    client.patch(
        f"/api/transactions/{COSTCO_A}",
        json={"categories": {str(groceries): 1.0}, "apply_to_all": True},
    )

    response = client.patch(
        f"/api/transactions/{COSTCO_B}",
        json={"categories": {str(household): 1.0}, "apply_to_all": True},
    )
    assert response.json()["applied_to_count"] == 1 + len(VARIANTS)

    txns = _by_id(client)
    assert txns[COSTCO_A]["categories"] == {str(household): 1.0}
    assert txns[COSTCO_A]["categories_set_by"] == "rule"
    assert txns[COSTCO_B]["categories_set_by"] == "user"
    assert _rules() == [("costco whse", "COSTCO WHSE #0669", household)]


def test_apply_to_all_false_behaves_as_before(client):
    groceries = _category(client, "Groceries")

    response = client.patch(
        f"/api/transactions/{COSTCO_A}", json={"categories": {str(groceries): 1.0}}
    )
    assert response.status_code == 200
    assert "applied_to_count" not in response.json()
    assert _by_id(client)[COSTCO_B]["categories"] == {}
    assert _rules() == []


# --- later syncs --------------------------------------------------------------


def test_sync_categorizes_new_matches_and_never_touches_user_rows(client, monkeypatch):
    groceries = _category(client, "Groceries")
    food = _category(client, "Food")
    client.patch(
        f"/api/transactions/{COSTCO_A}",
        json={"categories": {str(groceries): 1.0}, "apply_to_all": True},
    )
    # Afterwards the user moves one Costco trip by hand — "just this one".
    client.patch(f"/api/transactions/{COSTCO_B}", json={"categories": {str(food): 1.0}})

    _serve(
        monkeypatch,
        _fixture_with(
            *VARIANTS,
            DIFFERENT_MERCHANT,
            _txn("TRN-costco-new", "Costco Whse #0042"),
            _txn("TRN-other-new", "TRADER JOE'S #123"),
        ),
    )
    result = jobs.sync()
    assert result["categorized"] == 1

    txns = _by_id(client)
    assert txns["TRN-costco-new"]["categories"] == {str(groceries): 1.0}
    assert txns["TRN-costco-new"]["categories_set_by"] == "rule"
    assert txns["TRN-other-new"]["categories"] == {}
    assert txns["TRN-other-new"]["categories_set_by"] is None
    assert txns[COSTCO_B]["categories"] == {str(food): 1.0}  # user correction wins
    assert txns[COSTCO_B]["categories_set_by"] == "user"

    assert jobs.sync()["categorized"] == 0  # idempotent
    assert _by_id(client) == txns


# --- merchant matching beyond identical keys ----------------------------------

STARBUCKS = _txn("TRN-sbux", "STARBUCKS COFFEE #123")  # key "starbucks coffee"
STARBUCKS_TYPO = _txn("TRN-sbux-typo", "STARBUCK COFFEE #9")  # one typo in a long word
STARBUCKS_REF = _txn("TRN-sbux-ref", "STARBUCKS COFFEE * HM4K2PQ7")  # reference code
STARBRIGHT = _txn("TRN-starbright", "STARBRIGHT CAFE")  # a different business


def test_apply_to_all_catches_typos_and_reference_codes_and_overwrites_a_hand_set_row(
    client, monkeypatch
):
    _serve(
        monkeypatch,
        _fixture_with(STARBUCKS, STARBUCKS_TYPO, STARBUCKS_REF, STARBRIGHT, DIFFERENT_MERCHANT),
    )
    jobs.sync()
    coffee = _category(client, "Coffee")
    snacks = _category(client, "Snacks")
    client.patch(
        f"/api/transactions/{STARBUCKS_TYPO['id']}", json={"categories": {str(snacks): 1.0}}
    )

    response = client.patch(
        f"/api/transactions/{STARBUCKS['id']}",
        json={"categories": {str(coffee): 1.0}, "apply_to_all": True},
    )
    assert response.json()["applied_to_count"] == 2

    txns = _by_id(client)
    for same_merchant in (STARBUCKS_TYPO, STARBUCKS_REF):
        assert txns[same_merchant["id"]]["categories"] == {str(coffee): 1.0}
        assert txns[same_merchant["id"]]["categories_set_by"] == "rule"  # hand-set one too
    for unrelated in (STARBRIGHT, DIFFERENT_MERCHANT):
        assert txns[unrelated["id"]]["categories"] == {}
    # The rule is stored under the edited row's own key, not the typo's.
    assert _rules() == [("starbucks coffee", "STARBUCKS COFFEE #123", coffee)]


def test_sync_prefers_the_exact_key_rule_then_the_oldest_match(client, monkeypatch):
    rule_a_txn = _txn("TRN-rule-a", "TRADER JOES #552")  # key "trader joes"
    rule_b_txn = _txn("TRN-rule-b", "TRADER JOES MARKET #1")  # key "trader joes market"
    _serve(monkeypatch, _fixture_with(rule_a_txn, rule_b_txn))
    jobs.sync()
    first = _category(client, "First")
    second = _category(client, "Second")
    client.patch(
        f"/api/transactions/{rule_a_txn['id']}",
        json={"categories": {str(first): 1.0}, "apply_to_all": True},
    )
    client.patch(
        f"/api/transactions/{rule_b_txn['id']}",
        json={"categories": {str(second): 1.0}, "apply_to_all": True},
    )
    hand_set = _txn("TRN-hand-set", "TRADER JOES #9")

    _serve(
        monkeypatch,
        _fixture_with(
            rule_a_txn,
            rule_b_txn,
            hand_set,
            # Matches both rules, but is exactly the newer rule's key.
            _txn("TRN-exact-b", "TRADER JOES MARKET #77"),
            # Matches both rules and is neither key: the oldest rule wins.
            _txn("TRN-both", "TRADER JOES MARKET FRESH"),
        ),
    )
    jobs.sync()  # hand_set arrives (and is categorized) here; the user then overrides it
    client.patch(f"/api/transactions/{hand_set['id']}", json={"categories": {}})
    assert jobs.sync()["categorized"] == 0  # second sync changes nothing

    txns = _by_id(client)
    assert txns["TRN-exact-b"]["categories"] == {str(second): 1.0}
    assert txns["TRN-both"]["categories"] == {str(first): 1.0}
    assert txns[hand_set["id"]]["categories"] == {}  # the user's choice stands
    assert txns[hand_set["id"]]["categories_set_by"] == "user"


# --- deleting a category ------------------------------------------------------


def test_deleting_a_category_removes_its_rule(client):
    groceries = _category(client, "Groceries")
    client.patch(
        f"/api/transactions/{COSTCO_A}",
        json={"categories": {str(groceries): 1.0}, "apply_to_all": True},
    )
    # Still in use by transactions, so delete is refused and the rule stays.
    assert client.delete(f"/api/categories/{groceries}").status_code == 409
    assert _rules() == [("costco whse", "COSTCO WHSE #0669", groceries)]

    for txn in (COSTCO_A, COSTCO_B, *(v["id"] for v in VARIANTS)):
        client.patch(f"/api/transactions/{txn}", json={"categories": {}})
    assert client.delete(f"/api/categories/{groceries}").status_code == 204
    assert _rules() == []
