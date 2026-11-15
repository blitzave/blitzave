from datetime import date

from blitzave.copy import goose as words
from blitzave.refine import spending

TODAY = date(2026, 11, 14)


def test_a_month_the_data_starts_partway_through_isnt_whole():
    assert spending.whole_months(TODAY, date(2026, 8, 17), 6) == ["2026-09", "2026-10"]
    assert spending.whole_months(TODAY, date(2026, 8, 1), 6) == ["2026-08", "2026-09", "2026-10"]
    assert spending.whole_months(TODAY, None, 6) == []


def test_a_heavier_month_is_higher_than_usual_and_reads_as_an_ally():
    months = ["2026-08", "2026-09", "2026-10"]
    facts = spending.summary({"2026-08": 30000, "2026-09": 30000, "2026-10": 52000, "2026-11": 9000}, months, "2026-11")
    assert facts["trend"] == "higher"
    assert facts["average_cents"] == 37333
    assert facts["highest"] == ("2026-10", 52000) and facts["so_far_cents"] == 9000
    sentence = words.summary_sentence(words.summary_facts(facts, "Dining", 2026))
    assert sentence == (
        "Over the last 3 months, Dining averaged $373.33 a month. October came to $520.00, higher than usual."
    )


def test_a_lighter_or_steady_month_says_so():
    months = ["2026-09", "2026-10"]
    assert spending.summary({"2026-09": 10000, "2026-10": 8000}, months, "2026-11")["trend"] == "lower"
    assert spending.summary({"2026-09": 10000, "2026-10": 11000}, months, "2026-11")["trend"] == "usual"


def test_short_history_reports_what_there_is():
    one = spending.summary({"2026-10": 8000}, ["2026-10"], "2026-11")
    assert "trend" not in one
    assert words.summary_sentence(words.summary_facts(one, "your spending", 2026)) == (
        "Your spending came to $80.00 in October."
    )
    none = spending.summary({"2026-11": 2500}, [], "2026-11")
    assert words.summary_sentence(words.summary_facts(none, "Groceries", 2026)) == (
        "There's less than a whole month of history so far. This month, Groceries has come to $25.00."
    )
