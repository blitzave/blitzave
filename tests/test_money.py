import pytest

from blitzave.api.money import format_cents, parse_amount


@pytest.mark.parametrize(
    ("value", "expected_cents"),
    [
        ("0", 0),
        ("-0.10", -10),
        ("-33.00", -3300),
        ("10.5", 1050),
        ("1234.56", 123456),
        ("-1234.56", -123456),
        # display strings round-trip back in (the budget editor pre-fills with one)
        ("$1,234.56", 123456),
        ("-$3,334.39", -333439),
        ("$600.25", 60025),
        ("$1,000,000.00", 100000000),
        ("1.50000", 150),  # trailing zeros past the cents are fine
        ("+5", 500),
    ],
)
def test_parse_amount_valid(value, expected_cents):
    assert parse_amount(value) == expected_cents


@pytest.mark.parametrize(
    "value",
    [
        "1.005",          # a fraction of a cent
        "not-a-number",
        "",
        "1.2.3",
        "1,2,3",          # bad grouping must fail loudly, not become 123
        "12,34.00",
        "$$5.00",
        "Infinity",       # used to escape as OverflowError instead of ValueError
        "NaN",
        "1e3",
        # > 28 significant digits: Decimal arithmetic would round this to 1.00
        "1.000000000000000000000000000001",
    ],
)
def test_parse_amount_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_amount(value)


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "$0.00"),
        (-10, "-$0.10"),
        (-3300, "-$33.00"),
        (1050, "$10.50"),
        (5, "$0.05"),
        (-333439, "-$3,334.39"),
        (100000000, "$1,000,000.00"),
    ],
)
def test_format_cents(cents, expected):
    assert format_cents(cents) == expected


def test_display_strings_round_trip():
    for value in ["-$33.00", "$0.01", "$1,234.56", "-$1,000,000.00"]:
        assert format_cents(parse_amount(value)) == value
