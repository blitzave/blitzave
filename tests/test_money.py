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
    ],
)
def test_parse_amount_valid(value, expected_cents):
    assert parse_amount(value) == expected_cents


@pytest.mark.parametrize("value", ["1.005", "not-a-number", "", "1.2.3"])
def test_parse_amount_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_amount(value)


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "0.00"),
        (-10, "-0.10"),
        (-3300, "-33.00"),
        (1050, "10.50"),
        (5, "0.05"),
    ],
)
def test_format_cents(cents, expected):
    assert format_cents(cents) == expected


def test_parse_then_format_round_trips():
    for value in ["-33.00", "0.01", "1234.56"]:
        assert format_cents(parse_amount(value)) == value
