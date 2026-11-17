"""Goose's planner eval (ARCHITECTURE.md "Goose"). Not a test: it asks a real
model, so it's run by hand, never in CI.

    uv run python evals/run.py ollama qwen3:8b
    uv run python evals/run.py anthropic claude-haiku-5-5
    uv run python evals/run.py --report evals/runs/*.jsonl

Each case in planner.toml is a question, asked against made-up labels, and
the plan a careful reader would make. The question goes through the same
assistant._plan() the app uses, one repair included. A case passes when the
plan has the same intents in the same order, and every field matches what
the case expects, empty fields included. Summary cases also put the question
to the writer, and fill()'s rules say whether its sentence would be shown.

A run writes one JSON line per case to evals/runs/, with the model's calls
as a dev trace has them (traces.Turn, minus the system prompt, which is the
same for every case) and a hash of the prompts, so runs before and after a
prompt change can be told apart. Then it prints the report.
"""

import argparse
import hashlib
import json
import statistics
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from blitzave import assistant, traces
from blitzave.refine import spending
from blitzave.sources import models
from blitzave.words import goose as words

HERE = Path(__file__).parent
CASES = HERE / "planner.toml"
RUNS = HERE / "runs"
GROUPS = ["show", "tag", "category", "summary", "clarify", "decline", "multi"]
LENIENT = {"months"}  # the model's pick, unless the case names it
EMPTY = "the model server sent back nothing"
EMPTY_IN_A_ROW = 3  # this many cases of nothing in a row, and the run stops


@dataclass(frozen=True)
class Suite:
    today: date
    labels: assistant.Labels
    cases: list[dict]


def load(path: Path = CASES) -> Suite:
    """The cases, each checked to be a plan the grammar accepts."""
    data = tomllib.loads(path.read_text())
    kinds = data["labels"]["accounts"]
    labels = assistant.Labels(
        categories={name: {"name": name} for name in data["labels"]["categories"]},
        tags={name: n for n, name in enumerate(data["labels"]["tags"], start=1)},
        accounts={
            f"A{n}": {
                "asset_type": None if kind in ("credit_card", "loan") else kind,
                "liability_type": kind if kind in ("credit_card", "loan") else None,
            }
            for n, kind in enumerate(kinds, start=1)
        },
    )
    cases = data["case"]
    ids = [case["id"] for case in cases]
    if len(set(ids)) < len(ids):
        raise SystemExit("planner.toml: two cases share an id")
    for case in cases:
        if case["group"] not in GROUPS:
            raise SystemExit(f"{case['id']}: group must be one of {GROUPS}")
        try:
            assistant.Plan.model_validate({"intents": first_choice(case["expect"])}, context=labels.names())
        except ValueError as e:
            raise SystemExit(f"{case['id']}: expect isn't a plan the grammar accepts\n{e}") from e
    return Suite(data["today"], labels, cases)


def first_choice(expect: list[dict]) -> list[dict]:
    """The expected plan with the first answer of every list: one plan that
    passes the case."""

    def pick(key, value):
        if isinstance(value, dict):
            return {k: pick(k, v) for k, v in value.items()}
        if isinstance(value, list) and key != "options":
            return value[0]
        return value

    return [pick(None, intent) for intent in expect]


# --- scoring ------------------------------------------------------------------------


def mismatch(plan: assistant.Plan, expect: list[dict], today: date) -> str | None:
    """The first way the plan differs from what the case expects, or None."""
    found = differences(plan, expect, today)
    if not found:
        return None
    n, key, got, want = found[0]
    if key == "intents":
        return f"intents {got}, expected {want}"
    where = f"intent {n}: " if len(expect) > 1 else ""
    return f"{where}{key} is {_shown(got)}, expected {_shown(want)}"


def differences(plan: assistant.Plan, expect: list[dict], today: date) -> list[tuple[int, str, object, object]]:
    """Every way the plan differs from what the case expects, as (intent
    number, field, what the plan has, what the case expects). Different
    intents are one difference, numbered 0: their fields can't be compared."""
    got = [intent.model_dump() for intent in plan.intents]
    if [g["intent"] for g in got] != [e["intent"] for e in expect]:
        return [(0, "intents", [g["intent"] for g in got], [e["intent"] for e in expect])]
    found = []
    for n, (g, e) in enumerate(zip(got, expect), start=1):
        g, e = _flat(g), _flat(e)
        if g.get("filter.end") and g["filter.end"] >= today:
            g["filter.end"] = None  # nothing has posted after today
        for key in sorted(g.keys() | e.keys()):
            if key in LENIENT and key not in e:
                continue
            if not _same(g.get(key), e.get(key)):
                found.append((n, key, g.get(key), e.get(key)))
    return found


def _flat(intent: dict) -> dict:
    flat = {key: value for key, value in intent.items() if key != "filter"}
    flat.update({f"filter.{key}": value for key, value in (intent.get("filter") or {}).items()})
    return flat


def _same(got, want) -> bool:
    if isinstance(want, list) and isinstance(got, list):  # clarify: these must be offered
        return set(want) <= set(got)
    if isinstance(want, list):  # any of these answers counts
        return any(_same(got, option) for option in want)
    if isinstance(got, str) and isinstance(want, str):
        return got.casefold() == want.casefold()
    return got == want


def _shown(value) -> str:
    return value.isoformat() if isinstance(value, date) else repr(value)


# --- one case -------------------------------------------------------------------------


def writer_facts(label: str) -> dict[str, str]:
    """A summary's display strings, made up: six steady months and a heavier
    October, so every slot, {trend} included, has a value."""
    months = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    amounts = dict(zip(months, [10000] * 5 + [16000])) | {"2026-11": 4000}
    return words.summary_facts(spending.summary(amounts, months, "2026-11"), label, 2026)


def run_case(model: models.ChatModel, case: dict, suite: Suite) -> dict:
    question = case["question"]
    turn = traces.Turn(model, question)
    result = {"case": case["id"], "group": case["group"], "question": question}
    plan = None
    try:
        plan = assistant._plan(turn, question, suite.labels, suite.today)
    except models.ModelError as e:
        result.update(outcome="error", why=str(e))
    else:
        if plan is None and all(call.get("reply") == "" for call in turn.calls):
            # Nothing to judge: a model that thinks still says something. On
            # 2026-11-16 a Metal GPU fault left Ollama answering every request
            # at once with an empty 200, and those had been scored as no plan.
            result.update(outcome="error", why=EMPTY)
        elif plan is None:
            result.update(outcome="no plan", why=turn.calls[-1]["verdict"])
        elif why := mismatch(plan, case["expect"], suite.today):
            result.update(outcome="wrong", why=why)
        else:
            result.update(outcome="pass")
    planning = list(turn.calls)
    result.update(
        repaired=len(planning) > 1,
        seconds=round(sum(call["seconds"] for call in planning), 2),
        plan=plan.model_dump(mode="json") if plan else None,
    )
    if case["group"] == "summary":
        scope = first_choice(case["expect"])[0]
        label = scope.get("category") or scope.get("tag") or "your spending"
        assistant._phrase(turn, question, writer_facts(label))
        call = turn.calls[-1]
        result["writer"] = {
            "reply": call.get("reply"),
            "verdict": call.get("verdict") or f"error: {call.get('error')}",
            "seconds": call["seconds"],
        }
    result["calls"] = [{key: value for key, value in call.items() if key != "system"} for call in turn.calls]
    return result


def prompt_version() -> str:
    """Changes whenever what the planner or the writer is told changes."""
    told = assistant.PLANNER + assistant.WRITER + json.dumps(assistant.Plan.model_json_schema(), sort_keys=True)
    return hashlib.sha256(told.encode()).hexdigest()[:8]


# --- the report -----------------------------------------------------------------------


OUTCOMES = ["pass", "wrong", "no plan", "error"]


def read_run(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def tally(results: list[dict]) -> dict:
    """A run's counts, in plain Python. charts.py checks its own against these."""
    written = [r["writer"] for r in results if "writer" in r]
    seconds = [r["seconds"] for r in results]
    return {
        "cases": len(results),
        **{outcome: sum(r["outcome"] == outcome for r in results) for outcome in OUTCOMES},
        "repaired": sum(r["repaired"] for r in results),
        "median_seconds": statistics.median(seconds),
        "max_seconds": max(seconds),
        "writer_kept": sum(w["verdict"] == "kept" for w in written),
        "writer_total": len(written),
        "groups": {
            group: (
                sum(r["outcome"] == "pass" for r in results if r["group"] == group),
                sum(r["group"] == group for r in results),
            )
            for group in GROUPS
        },
    }


def report(paths: list[Path]) -> str:
    """Markdown tables, one row per run."""
    head = (
        "| Model | Prompt | Passed | Wrong plan | No plan | Error | Repaired | Median s | Slowest s | Writer kept |\n"
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    rows, by_group, misses, kept_sentences = [], [], [], []
    for path in paths:
        results = read_run(path)
        if not results:
            continue
        first = results[0]
        name = f"{first['model']} ({first['provider']})"
        t = tally(results)
        written = [r["writer"] for r in results if "writer" in r]
        rows.append(
            f"| {name} | {first['prompt']} | {t['pass']}/{t['cases']} ({t['pass'] / t['cases']:.0%}) "
            f"| {t['wrong']} | {t['no plan']} | {t['error']} "
            f"| {t['repaired']} | {t['median_seconds']:.1f} | {t['max_seconds']:.1f} "
            f"| {t['writer_kept']}/{t['writer_total']} |"
        )
        by_group.append(f"| {name} | " + " | ".join(f"{p}/{n}" for p, n in t["groups"].values()) + " |")
        refusals = [w["verdict"] for w in written if w["verdict"] != "kept"]
        misses.append(f"- {name}: " + ("; ".join(refusals) if refusals else "none"))
        kept_sentences += [f"- {name}: {w['reply']}" for w in written if w["verdict"] == "kept"]
    groups_head = "| Model | " + " | ".join(GROUPS) + " |\n|---|" + "---|" * len(GROUPS)
    return "\n".join([
        head, *rows, "", groups_head, *by_group, "",
        "Writer sentences refused:", *misses, "",
        "Writer sentences kept (fill() checks they're safe, not that they read well):", *kept_sentences,
    ])


# --- the command ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Goose's planner eval. Asks a real model.")
    parser.add_argument("provider", nargs="?", choices=sorted(models.ADAPTERS))
    parser.add_argument("model", nargs="?", help="like qwen3:8b or claude-haiku-5-5")
    parser.add_argument("--base-url", help="where the model is, if not its usual place")
    parser.add_argument("--timeout", type=float, help=f"seconds per call (the app uses {models.TIMEOUT_SECONDS})")
    parser.add_argument(
        "--think", choices=["off", "default"], default="off",
        help="Ollama only: off, as the app sends it, or Ollama's default (on, for qwen3)",
    )
    parser.add_argument("--only", help="run only the cases whose id starts with this")
    parser.add_argument("--report", nargs="+", type=Path, metavar="RUN", help="print the report for these runs")
    args = parser.parse_args(argv)
    if args.report:
        print(report(args.report))
        return
    if not (args.provider and args.model):
        parser.error("name a provider and a model, or --report some runs")

    suite = load()
    cases = [c for c in suite.cases if not args.only or c["id"].startswith(args.only)]
    adapter = models.ADAPTERS[args.provider]
    model = adapter(args.model, args.base_url) if args.base_url else adapter(args.model)
    if args.timeout:
        models.TIMEOUT_SECONDS = args.timeout
    if args.think == "default":
        models.OLLAMA_THINK = None
    if models.is_remote(model.base_url):
        print(f"Sending made-up questions and labels to {model.base_url}")

    RUNS.mkdir(exist_ok=True)
    slug = args.model.replace(":", "-").replace("/", "-")
    path = RUNS / f"{datetime.now():%Y-%m-%d-%H%M}-{args.provider}-{slug}.jsonl"
    # Written as .partial until every case is in, so an unfinished run never
    # matches runs/*.jsonl and can't be charted by mistake.
    partial = path.with_name(path.name + ".partial")
    stamp = {"provider": args.provider, "model": args.model, "prompt": prompt_version()}
    if args.provider == "ollama":
        stamp["think"] = args.think  # runs from before 2026-11-16 left it to Ollama
    empty = 0
    with partial.open("w", encoding="utf-8") as out:
        for n, case in enumerate(cases, start=1):
            result = stamp | run_case(model, case, suite)
            out.write(json.dumps(result) + "\n")
            out.flush()  # a long run interrupted still keeps what it finished
            why = f"  {result['why']}" if result["outcome"] != "pass" else ""
            print(f"{n:>3}/{len(cases)} {result['outcome']:<7} {case['id']:<34} {result['seconds']:>6.1f}s{why}")
            sys.stdout.flush()
            empty = empty + 1 if result.get("why") == EMPTY else 0
            if empty == EMPTY_IN_A_ROW:
                raise SystemExit(
                    f"\nStopped: {EMPTY_IN_A_ROW} cases in a row got nothing back, so the model server"
                    f" looks unwell, not the model. Restart it and run again. What finished is in {partial}"
                )
    partial.rename(path)
    print(f"\n{path.relative_to(HERE.parent) if path.is_relative_to(HERE.parent) else path}\n")
    print(report([path]))


if __name__ == "__main__":
    main()
