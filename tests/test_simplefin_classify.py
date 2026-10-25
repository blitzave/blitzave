import pytest

from blitzave.sources import simplefin


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Total Checking ...1234", ("asset", "checking")),
        ("High Yield Savings", ("asset", "savings")),
        ("Primary Savings Account", ("asset", "savings")),
        # branded card products that mention neither "checking" nor "saving"
        # must fall through to liability/credit_card (this was the bug: the
        # old keyword-matching approach missed exactly these)
        ("Venture X (1863)", ("liability", "credit_card")),
        ("Platinum Card® (1002)", ("liability", "credit_card")),
        ("Sapphire Reserve", ("liability", "credit_card")),
        ("American Express Gold Card (1007)", ("liability", "credit_card")),
    ],
)
def test_guess_account_kind_classifies_by_checking_savings_keywords(name, expected):
    assert simplefin._guess_account_kind(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "401k Rollover IRA",
        "Fidelity 401(k)",
        "Roth IRA",
        "Brokerage Account",
        "Home Mortgage",
        "Auto Loan",
        "Retirement Investment Account",
    ],
)
def test_guess_account_kind_skips_unsupported_account_types(name):
    assert simplefin._guess_account_kind(name) is None


def test_account_number_extracted_from_digits_in_name():
    assert simplefin._account_number("ACT-whatever", "Checking ...1234") == ("1234", "name")


def test_account_number_falls_back_to_id_suffix_when_name_has_no_digits():
    assert simplefin._account_number("ACT-abcd1234wxyz7d6c", "Sapphire Reserve") == (
        "7d6c",
        "simplefin_id_fallback",
    )
