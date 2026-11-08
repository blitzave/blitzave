"""Which one thing Home says (P0.8). The rules are the design canvas's
"Home message templates" board: checked top to bottom, the first that matches
wins. A win shows before a heavy month, and a heavy month before anything that
looks like a reminder. This picks the rule and its facts; copy/home.py says it.

Milestones (25%, 50% and 75% of a goal) are wins too, so they follow "goal
reached" (100%), before the heavy month. Not on the canvas yet: a first pass
(2026-11-08), to refine.

Pure: facts in, one card's facts out.
"""

from datetime import date, timedelta

from blitzave.refine.goals import fill

GETTING_STARTED_DAYS = 30   # less history than this: still getting to know you
REACHED_SHOWS_DAYS = 7      # a reached goal is news for a week
CLOSE_SHARE = 0.25          # within 25% of the amount: almost there
MILESTONES = (25, 50, 75, 100)  # a moment each, in words (TODOS.md P0.7)


def _milestone(saved_cents: int, amount_cents: int) -> int:
    """The highest milestone a saved amount has passed: 0, 25, 50, 75 or 100."""
    return max((m for m in MILESTONES if saved_cents * 100 >= amount_cents * m), default=0)


def newly_reached(homes: list[dict], history: dict[str, list[tuple[str, int]]],
                  today: date) -> list[dict]:
    """One-time goals that passed a milestone (25%, 50%, 75% or 100% of
    their amount) in the last week, each with the highest one it's at now
    and the day it got there, newest first.

    The day is read from the recorded balances: the first balance after the
    last one that was below the milestone. A goal already past it in every
    recorded balance has no such day, so it isn't news. A dip is never news
    either: only reaching a milestone is, so nothing is ever "lost"."""
    reached = []
    since = (today - timedelta(days=REACHED_SHOWS_DAYS)).isoformat()
    for home in homes:
        balances = history.get(home["id"], [])
        for goal in home["goals"]:
            if goal["schedule"] != "once":
                continue
            milestone = _milestone(goal["saved_cents"], goal["budget_cents"])
            if milestone == 0:
                continue
            reached_on = None
            for as_of, cents in balances:
                saved = next(g["saved_cents"] for g in fill(cents, home["goals"])[0]
                             if g["id"] == goal["id"])
                if _milestone(saved, goal["budget_cents"]) < milestone:
                    reached_on = None
                elif reached_on is None:
                    reached_on = as_of
            first_recorded = balances[0][0] if balances else None
            if reached_on and reached_on != first_recorded and reached_on >= since:
                reached.append({**goal, "milestone": milestone, "reached_on": reached_on})
    return sorted(reached, key=lambda g: g["reached_on"], reverse=True)


def pick(
    *,
    today: date,
    first_posted_on: str | None,
    reached: list[dict],
    spending: dict,
    homes: list[dict],
    trajectories: dict[int, dict],
    pay_first: dict,
) -> dict:
    """The first rule that matches, with what its words need."""
    if first_posted_on is None or (today - date.fromisoformat(first_posted_on)).days < (
        GETTING_STARTED_DAYS
    ):
        return {"rule": "getting_started"}

    # A goal that's all there comes before one that's part way.
    done = [g for g in reached if g["milestone"] == 100]
    if done:
        return {"rule": "goal_reached", "goal": done[0]}
    if reached:
        goal = reached[0]
        return {"rule": "milestone", "goal": goal, "trajectory": trajectories.get(goal["id"])}

    if spending["heavier"]:
        return {"rule": "heavier_month", "spending": spending}

    close = [
        g for home in homes for g in home["goals"]
        if g["state"] == "in_progress" and g["to_go_cents"] <= g["budget_cents"] * CLOSE_SHARE
    ]
    if close:
        goal = min(close, key=lambda g: g["to_go_cents"] / g["budget_cents"])
        return {"rule": "goal_close", "goal": goal, "trajectory": trajectories.get(goal["id"])}

    if pay_first["went_first"]:
        return {"rule": "saved_first", "pay_first": pay_first, "spending": spending}

    if (
        pay_first["in_cents"] == 0
        and pay_first["usual_in_cents"] > 0
        and pay_first["usual_day"] is not None
        and today.day > pay_first["usual_day"]
    ):
        return {"rule": "not_yet", "pay_first": pay_first, "today": today}

    goals_moving = any(t["rate_cents"] and t["rate_cents"] > 0 for t in trajectories.values())
    return {
        "rule": "steady",
        "has_goals": any(home["goals"] for home in homes),
        "goals_moving": goals_moving,
        "spending": spending,
    }
