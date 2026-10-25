from decimal import Decimal, InvalidOperation


def parse_amount(value: str) -> int:
    """Parse a decimal amount string (e.g. SimpleFIN's "-33.00") into integer cents.

    Raises ValueError if the string isn't a valid number, or represents a
    fraction of a cent.
    """
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as e:
        raise ValueError(f"not a valid amount: {value!r}") from e

    cents = decimal_value * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"amount has a fraction of a cent: {value!r}")
    return int(cents)


def format_cents(cents: int) -> str:
    """Format integer cents as a decimal string, e.g. -1050 -> "-10.50"."""
    sign = "-" if cents < 0 else ""
    whole, remainder = divmod(abs(cents), 100)
    return f"{sign}{whole}.{remainder:02d}"
