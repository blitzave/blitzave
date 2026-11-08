"""A goal's pace line on the Savings page: when it gets there at the recent
pace, as a range, and what a month would take to make its date. No pace means
no date, said kindly, never "never"."""

from datetime import date

from blitzave.api.money import format_cents
from blitzave.copy import month_name


def pace_line(goal: dict, trajectory: dict | None, today: date) -> str | None:
    if goal["state"] == "done" or trajectory is None:
        return None
    year = today.year
    soonest, latest = trajectory["eta_from"], trajectory["eta_to"]
    if soonest is None:
        return "Nothing has come into this account lately, so there's no date yet. Any amount gets it moving."

    if latest is None:
        when = f"around {month_name(soonest, year)}, maybe later"
    elif soonest == latest:
        when = f"around {month_name(soonest, year)}"
    else:
        start = month_name(soonest, year)
        end = month_name(latest, year)
        # "May–June 2027" rather than "May 2027–June 2027".
        if soonest[:4] == latest[:4] and soonest[:4] != str(year):
            start = start.split(" ")[0]
        when = f"around {start}–{end}"

    line = f"At about {format_cents(trajectory['rate_cents'])} a month, it gets there {when}."
    target = trajectory["target_month"]
    per_month = trajectory["per_month_for_target_cents"]
    if target and not trajectory["on_pace"] and per_month:
        line += f" About {format_cents(per_month)} a month gets it there by {month_name(target, year)}."
    months = trajectory["history_months"]
    if 0 < months < 3:
        line += f" That's from {months} month{'s' if months > 1 else ''} of history so far."
    return line
