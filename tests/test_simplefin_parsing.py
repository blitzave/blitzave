import json
from datetime import datetime
from pathlib import Path

from blitzave.sources import simplefin

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "simplefin_accounts.json").read_text()
)


def _local_date(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds).date().isoformat()


def test_investment_accounts_are_skipped_entirely():
    fetched = simplefin.parse_fetched(FIXTURE)
    external_ids = {a.external_id for a in fetched.accounts}
    assert "ACT-fidelity-401k" not in external_ids
    assert all(b.account_external_id != "ACT-fidelity-401k" for b in fetched.balances)
    assert all(t.account_external_id != "ACT-fidelity-401k" for t in fetched.transactions)


def test_parses_both_accounts_with_guessed_type():
    fetched = simplefin.parse_fetched(FIXTURE)
    by_id = {a.external_id: a for a in fetched.accounts}

    checking = by_id["ACT-chase-0001"]
    assert checking.account_type == "asset"
    assert checking.asset_type == "checking"
    assert checking.bank_name == "Chase"

    card = by_id["ACT-amex-9f8e7d6c"]
    assert card.account_type == "liability"
    assert card.liability_type == "credit_card"
    assert card.issuer_name == "American Express"


def test_account_number_from_digits_in_name():
    fetched = simplefin.parse_fetched(FIXTURE)
    checking = next(a for a in fetched.accounts if a.external_id == "ACT-chase-0001")
    assert checking.account_number == "1234"
    assert checking.meta["account_number_source"] == "name"


def test_account_number_falls_back_to_id_suffix_when_name_has_no_digits():
    fetched = simplefin.parse_fetched(FIXTURE)
    card = next(a for a in fetched.accounts if a.external_id == "ACT-amex-9f8e7d6c")
    assert card.account_number == "7d6c"
    assert card.meta["account_number_source"] == "simplefin_id_fallback"


def test_pending_transactions_are_skipped():
    fetched = simplefin.parse_fetched(FIXTURE)
    external_ids = {t.external_id for t in fetched.transactions}
    assert "TXN-chase-0003" not in external_ids  # the pending one
    assert len(fetched.transactions) == 4  # 2 chase (of 3) + 2 amex


def test_transaction_amounts_and_sign_derived_type():
    fetched = simplefin.parse_fetched(FIXTURE)
    coffee = next(t for t in fetched.transactions if t.external_id == "TXN-chase-0001")
    assert coffee.amount_cents == -450
    assert coffee.transaction_type == "debit"

    payroll = next(t for t in fetched.transactions if t.external_id == "TXN-chase-0002")
    assert payroll.amount_cents == 150000
    assert payroll.transaction_type == "credit"


def test_transaction_posted_date_uses_local_timezone():
    fetched = simplefin.parse_fetched(FIXTURE)
    coffee = next(t for t in fetched.transactions if t.external_id == "TXN-chase-0001")
    assert coffee.posted_on == _local_date(1759190400)


def test_balances_parsed_for_both_accounts():
    fetched = simplefin.parse_fetched(FIXTURE)
    by_id = {b.account_external_id: b for b in fetched.balances}
    assert by_id["ACT-chase-0001"].balance_cents == 254310
    assert by_id["ACT-amex-9f8e7d6c"].balance_cents == -31245
