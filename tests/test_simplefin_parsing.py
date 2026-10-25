import json
from datetime import datetime
from pathlib import Path

from blitzave.sources import simplefin

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "simplefin_accounts.json").read_text()
)

CHASE_CHECKING = "ACT-chase-0001"
AMEX_PLATINUM = "ACT-6a7c0c03-a7f8-4ca4-aa2d-06b7f44ec3a1"
AMEX_GOLD = "ACT-e8ae7e1e-05a2-4955-b472-74381d28f9b2"
CAPITAL_ONE_VENTURE_X = "ACT-a5ec3a62-0c88-4149-88b2-505a923c64fc"
FIDELITY_401K = "ACT-fidelity-401k"


def _local_date(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds).date().isoformat()


def test_investment_accounts_are_skipped_entirely():
    fetched = simplefin.parse_fetched(FIXTURE)
    external_ids = {a.external_id for a in fetched.accounts}
    assert FIDELITY_401K not in external_ids
    assert all(b.account_external_id != FIDELITY_401K for b in fetched.balances)
    assert all(t.account_external_id != FIDELITY_401K for t in fetched.transactions)


def test_checking_account_classified_as_asset():
    fetched = simplefin.parse_fetched(FIXTURE)
    checking = next(a for a in fetched.accounts if a.external_id == CHASE_CHECKING)
    assert checking.account_type == "asset"
    assert checking.asset_type == "checking"
    assert checking.bank_name == "Chase"


def test_branded_card_without_card_keywords_still_classified_as_liability():
    # Regression: "Venture X" contains no "card"/"credit"/issuer keyword at
    # all — the old keyword-matching approach misclassified this as asset/
    # checking. Real cards are branded too unpredictably to keyword-match.
    fetched = simplefin.parse_fetched(FIXTURE)
    card = next(a for a in fetched.accounts if a.external_id == CAPITAL_ONE_VENTURE_X)
    assert card.account_type == "liability"
    assert card.liability_type == "credit_card"
    assert card.issuer_name == "Capital One"


def test_account_number_extracted_from_parenthesized_digits_in_name():
    fetched = simplefin.parse_fetched(FIXTURE)
    card = next(a for a in fetched.accounts if a.external_id == CAPITAL_ONE_VENTURE_X)
    assert card.account_number == "1863"
    assert card.meta["account_number_source"] == "name"


def test_pending_transactions_are_skipped():
    fetched = simplefin.parse_fetched(FIXTURE)
    external_ids = {t.external_id for t in fetched.transactions}
    assert "TXN-chase-0003" not in external_ids  # the pending one
    # chase: 3 - 1 pending = 2; amex platinum: 2; amex gold: 3; capital one: 3
    assert len(fetched.transactions) == 10


def test_transaction_amounts_and_sign_derived_type():
    fetched = simplefin.parse_fetched(FIXTURE)
    coffee = next(t for t in fetched.transactions if t.external_id == "TXN-chase-0001")
    assert coffee.amount_cents == -450
    assert coffee.transaction_type == "debit"

    payroll = next(t for t in fetched.transactions if t.external_id == "TXN-chase-0002")
    assert payroll.amount_cents == 150000
    assert payroll.transaction_type == "credit"


def test_refund_on_a_card_parses_as_a_credit():
    # real captured pair: a Walmart+ charge, then a statement credit for it
    fetched = simplefin.parse_fetched(FIXTURE)
    charge = next(t for t in fetched.transactions if t.external_id == "TRN-0981051b-e32e-48d0-a444-aecfeb0e099d")
    credit = next(t for t in fetched.transactions if t.external_id == "TRN-9bf55edc-dfed-4509-bbf5-6ab68a24f652")
    assert charge.amount_cents == -1402
    assert charge.transaction_type == "debit"
    assert credit.amount_cents == 1402
    assert credit.transaction_type == "credit"


def test_transaction_posted_date_uses_local_timezone():
    fetched = simplefin.parse_fetched(FIXTURE)
    coffee = next(t for t in fetched.transactions if t.external_id == "TXN-chase-0001")
    assert coffee.posted_on == _local_date(1759190400)


def test_balances_parsed_for_real_and_synthetic_accounts():
    fetched = simplefin.parse_fetched(FIXTURE)
    by_id = {b.account_external_id: b for b in fetched.balances}
    assert by_id[CHASE_CHECKING].balance_cents == 254310
    assert by_id[CAPITAL_ONE_VENTURE_X].balance_cents == -838788
