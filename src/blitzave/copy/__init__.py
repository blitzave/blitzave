"""The words BlitZave says, as fixed templates (AGENTS.md "Detectors").

Facts come from refine/; these turn them into sentences. Every number in a
sentence is a format_cents() string of an engine figure; nothing here does
arithmetic on money. Every template is held to the Prime Directive: ally,
never scorekeeper. A dip is absorbed, a win is said plainly, and no line
counts streaks or grades.
"""

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def ordinal(n: int) -> str:
    """1 -> "1st", 22 -> "22nd", 13 -> "13th"."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def day_of(iso: str) -> str:
    """"2026-11-02" -> "the 2nd"."""
    return f"the {ordinal(int(iso[8:10]))}"


def month_name(key: str, this_year: int) -> str:
    """"2027-02" -> "February 2027", or just "February" in the same year."""
    name = MONTHS[int(key[5:7]) - 1]
    return name if int(key[:4]) == this_year else f"{name} {key[:4]}"
