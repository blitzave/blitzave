import re

# An optional sign, an optional "$", then digits — either plain or grouped in
# threes with commas — and an optional fraction. Accepting exactly what
# format_cents() emits lets a display string round-trip back in (the budget
# editor pre-fills with one), while badly grouped input like "1,2,3" still
# fails loudly instead of being silently "fixed up" to 123. Exponents, NaN
# and Infinity never match.
_AMOUNT = re.compile(r"^([+-]?)\$?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?$")


def parse_amount(value: str) -> int:
    """Parse a decimal amount string (SimpleFIN's "-33.00", or a display
    string like "-$1,234.56") into integer cents.

    Pure integer arithmetic on the matched digits — no Decimal context, so a
    long input like "1.000000000000000000000000000001" can't be rounded into
    looking like a whole number of cents.

    Raises ValueError if the string isn't a valid amount, or represents a
    fraction of a cent.
    """
    match = _AMOUNT.match(value.strip())
    if match is None:
        raise ValueError(f"not a valid amount: {value!r}")
    sign, whole, fraction = match.groups()
    fraction = fraction or ""
    if len(fraction.rstrip("0")) > 2:
        raise ValueError(f"amount has a fraction of a cent: {value!r}")
    cents = int(whole.replace(",", "")) * 100 + int(fraction[:2].ljust(2, "0"))
    return -cents if sign == "-" else cents


def format_cents(cents: int) -> str:
    """Format integer cents for display, e.g. -333439 -> "-$3,334.39"."""
    sign = "-" if cents < 0 else ""
    whole, remainder = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{remainder:02d}"
