"""Home's one message: a headline and one supporting sentence, per rule
(design canvas "Home message templates"). Variants exist so every sentence
stays true: "spending is right where it usually is" is only said when it is."""

from calendar import monthrange
from datetime import date

from blitzave.api.money import format_cents
from blitzave.copy import day_of, month_name, ordinal


def _spending_phrase(spending: dict) -> str | None:
    if not spending.get("usual_to_date_cents"):
        return None
    if spending["lighter"]:
        return "spending is running lighter than usual"
    return "spending is right where it usually is"


def home_words(card: dict, today: date) -> tuple[str, str]:
    rule = card["rule"]

    if rule == "getting_started":
        return (
            "Getting to know your money.",
            "Once a full month is in, the one thing worth knowing will show up here.",
        )

    if rule == "goal_reached":
        goal = card["goal"]
        support = (
            f"You set aside {format_cents(goal['budget_cents'])} for it, a bit at a time. "
            "It's ready when you are."
        )
        return f"{goal['name']}: done.", support

    if rule == "heavier_month":
        spending = card["spending"]
        largest = spending["largest"]
        if spending["one_charge_explains_it"] and largest:
            support = (
                f"Most of it is one {format_cents(largest['amount_cents'])} charge on "
                f"{day_of(largest['posted_on'])}. Your three-month average hasn't moved."
            )
        else:
            support = "It's spread across a few days. Your three-month average hasn't moved."
        return "A heavier start. That happens.", support

    if rule == "goal_close":
        goal, trajectory = card["goal"], card["trajectory"] or {}
        support = f"{goal['name']} is {format_cents(goal['to_go_cents'])} away."
        rate = trajectory.get("rate_cents")
        if rate and rate > 0 and trajectory.get("eta_from"):
            support += (
                f" At {format_cents(rate)} a month, that's "
                f"{month_name(trajectory['eta_from'], today.year)}."
            )
        return "Almost there.", support

    if rule == "saved_first":
        pay_first = card["pay_first"]
        when = (
            f"on {day_of(pay_first['first_in_on'])}"
            if pay_first["single_day"]
            else f"starting on {day_of(pay_first['first_in_on'])}"
        )
        support = f"Savings went first this month, {format_cents(pay_first['in_cents'])} {when}"
        phrase = _spending_phrase(card["spending"])
        support += f", and {phrase}." if phrase else "."
        return "You're in good shape.", support

    if rule == "not_yet":
        pay_first = card["pay_first"]
        days_left = monthrange(today.year, today.month)[1] - today.day
        tail = "There's plenty of month left." if days_left >= 10 else "There's still time this month."
        support = (
            f"Your usual {format_cents(pay_first['usual_in_cents'])} tends to move around "
            f"the {ordinal(pay_first['usual_day'])}. {tail}"
        )
        return "A quiet month for savings so far.", support

    # steady: always matches
    spending = _spending_phrase(card["spending"])
    if spending == "spending is right where it usually is":
        spending = "spending is in line with usual"
    goals = "your goals are moving" if card["goals_moving"] else None
    if spending and goals:
        support = f"{spending.capitalize()}, and {goals}."
    elif spending:
        support = f"{spending.capitalize()}."
    elif goals:
        support = f"{goals.capitalize()}."
    else:
        support = "Everything's synced and up to date."
    return "Nothing needs you today.", support

