"""Goose's planner eval (ARCHITECTURE.md "Goose"). Not a test: it asks a real
model, so it's run by hand, never in CI.

    uv run python evals/run.py ollama qwen3:8b
    uv run python evals/run.py anthropic claude-haiku-5-5
    uv run python evals/run.py --report evals/runs/*.jsonl
    uv run python evals/run.py --compare OLD.jsonl NEW.jsonl
    uv run python evals/run.py --privacy ollama qwen3:8b   # privacy.toml: what goes out

Each case in planner.toml is a question, asked against made-up labels, and
the plan a careful reader would make. The question goes through the same
assistant._plan() the app uses, one repair included. A case passes when the
plan has the same intents in the same order, and every field matches what
the case expects, empty fields included. Summary cases also put the question
to the writer, and fill()'s rules say whether its sentence would be shown.

A run writes one JSON line per case to evals/runs/, with the model's calls
as a dev trace has them (traces.Turn, minus the system prompt, which is the
same for every case). Every line carries the run's stamp: the answer key's
and the prompts' hashes, the commit, the model and its digest, the settings
sent, and the machine. Then it prints the report.

A run keeps every plan and writer sentence, so --report and --compare score
them again against today's planner.toml and fill(), without asking a model.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from blitzave import assistant, traces
from blitzave.refine import spending
from blitzave.sources import models
from blitzave.sources.base import AccountRow, BalanceRow, TxRow
from blitzave.store import categories, db, ingest, tags, transactions, users
from blitzave.words import goose as words

HERE = Path(__file__).parent
CASES = HERE / "planner.toml"
PROMPTS = HERE / "prompts.toml"
RUNS = HERE / "runs"
GROUPS = ["show", "tag", "category", "summary", "clarify", "decline", "multi"]
LENIENT = {"months"}  # the model's pick, unless the case names it
EMPTY = "the model server sent back nothing"
EMPTY_IN_A_ROW = 3  # this many cases of nothing in a row, and the run stops
# The stamp's fields --compare lines up: what can make two runs differ.
COMPARED = ["provider", "model", "digest", "server", "prompt", "think", "settings", "machine"]


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


def writer_label(case: dict) -> str:
    """What a summary case is about, as the writer's {label} reads."""
    scope = first_choice(case["expect"])[0]
    return scope.get("category") or scope.get("tag") or "your spending"


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
        assistant._phrase(turn, question, writer_facts(writer_label(case)))
        call = turn.calls[-1]
        result["writer"] = {
            "reply": call.get("reply"),
            "verdict": call.get("verdict") or f"error: {call.get('error')}",
            "seconds": call["seconds"],
        }
    result["calls"] = [{key: value for key, value in call.items() if key != "system"} for call in turn.calls]
    return result


# --- the stamp: what a run ran on -------------------------------------------------------


def prompt_version() -> str:
    """Changes whenever what the planner or the writer is told changes."""
    told = assistant.PLANNER + assistant.WRITER + json.dumps(assistant.Plan.model_json_schema(), sort_keys=True)
    return hashlib.sha256(told.encode()).hexdigest()[:8]


def suite_version(path: Path = CASES) -> str:
    """Changes whenever the answer key changes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def prompt_log(path: Path = PROMPTS) -> dict[str, dict]:
    """prompts.toml: each prompt hash's name, date, and what changed."""
    return tomllib.loads(path.read_text())


def prompt_name(prompt: str | None, log: dict[str, dict]) -> str:
    if prompt in log:
        return f"{log[prompt]['name']} ({prompt})"
    return f"{prompt} (not in prompts.toml)" if prompt else "not recorded"


def stamp(provider: str, model: str, base_url: str, think: str, suite: str = "planner", path: Path = CASES) -> dict:
    """Written into every line of a run, so any two runs can be lined up and
    any one reproduced. Called once per run, by the eval only."""
    commit, dirty = git_state()
    found = {
        "suite": suite,
        "suite_hash": suite_version(path),
        "prompt": prompt_version(),
        "git": commit,
        "dirty": dirty,
        "provider": provider,
        "model": model,
        "settings": settings_sent(provider),
        "machine": machine(),
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    if provider == "ollama":
        found["think"] = think  # runs from before 2026-11-16 left it to Ollama
        found |= ollama_about(base_url, model)
    return found


def settings_sent(provider: str) -> dict:
    """What the adapter sends besides the prompt. None means it isn't sent,
    so the provider's default applies (test_models checks Claude gets no
    temperature)."""
    if provider == "ollama":
        return {**models.OLLAMA_OPTIONS, "timeout": models.TIMEOUT_SECONDS}
    return {"temperature": None, "max_tokens": models.CLAUDE_MAX_TOKENS, "timeout": models.TIMEOUT_SECONDS}


def git_state() -> tuple[str | None, bool | None]:
    """The commit the code is at, and whether tracked files differ from it.
    Run files are untracked, so a new one doesn't count."""
    try:
        commit = _command("git", "rev-parse", "--short", "HEAD")
        changed = _command("git", "status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, bool(changed)


def machine() -> str:
    """The chip and memory: seconds are only comparable on one machine."""
    if sys.platform == "darwin":
        try:
            memory = int(_command("sysctl", "-n", "hw.memsize"))
            return f"{_command('sysctl', '-n', 'machdep.cpu.brand_string')}, {memory // 2**30} GB"
        except (OSError, subprocess.CalledProcessError, ValueError):
            pass
    return platform.machine() or "unknown"


def _command(*args: str) -> str:
    return subprocess.run(args, cwd=HERE, capture_output=True, text=True, check=True).stdout.strip()


def ollama_about(base_url: str, model: str) -> dict:
    """The model's digest, which changes when `ollama pull` brings new weights
    under the same name, and Ollama's version. None when Ollama won't say."""
    try:
        listed = httpx.get(f"{base_url}/api/tags", timeout=10).json()["models"]
        version = httpx.get(f"{base_url}/api/version", timeout=10).json()["version"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"digest": None, "server": None}
    names = {model, f"{model}:latest"}
    digest = next((m.get("digest") for m in listed if m.get("name") in names), None)
    return {"digest": digest[:12] if digest else None, "server": f"ollama {version}"}


# --- scoring a stored run again --------------------------------------------------------


def rescore(results: list[dict], suite: Suite) -> list[dict]:
    """A run's results as today's planner.toml and fill() score them. A case
    that's no longer in the key is left out. A case with no plan (no plan,
    error) stays as it was: there's nothing to score again."""
    cases = {case["id"]: case for case in suite.cases}
    return [_rescore(result, cases[result["case"]], suite) for result in results if result["case"] in cases]


def _rescore(result: dict, case: dict, suite: Suite) -> dict:
    now = dict(result)
    if result.get("plan") is not None:
        try:
            plan = assistant.Plan.model_validate(result["plan"], context=suite.labels.names())
        except ValidationError:
            why = "the plan doesn't fit today's labels"
        else:
            why = mismatch(plan, case["expect"], suite.today)
        now["outcome"] = "wrong" if why else "pass"
        now.pop("why", None)
        if why:
            now["why"] = why
    writer = result.get("writer")
    if writer and isinstance(writer.get("reply"), str) and case["group"] == "summary":
        verdict = assistant.refusal(writer["reply"], writer_facts(writer_label(case))) or "kept"
        now["writer"] = {**writer, "verdict": verdict}
    return now


def _scored(result: dict) -> tuple:
    """What a case came to: outcome, reason, and the writer's verdict."""
    return result["outcome"], result.get("why"), _verdict(result)


# --- the report -----------------------------------------------------------------------


OUTCOMES = ["pass", "wrong", "no plan", "error"]


def read_run(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def tokens(result: dict) -> tuple[int, int] | None:
    """Tokens in and out over every call a case made, or None when the server
    never said (runs from before 2026-11-17 didn't keep them)."""
    usages = [call["usage"] for call in result.get("calls", []) if "usage" in call]
    if not usages:
        return None
    return sum(u.get("tokens_in", 0) for u in usages), sum(u.get("tokens_out", 0) for u in usages)


STAGES = ["load_seconds", "prompt_seconds", "output_seconds", "other_seconds"]


def stage_seconds(result: dict) -> dict[str, float] | None:
    """Where a case's planning seconds went, by the server's own timings:
    loading the model, reading the prompt, writing the reply, and the rest
    (the network, the server's other work, BlitZave). None when the server
    didn't time its stages (Claude, and runs from before 2026-11-17)."""
    planning = [call for call in result.get("calls", []) if call["step"] in ("plan", "repair")]
    timed = [call["usage"] for call in planning if any(stage in call.get("usage", {}) for stage in STAGES[:3])]
    if not timed:
        return None
    split = {stage: sum(usage.get(stage, 0) for usage in timed) for stage in STAGES[:3]}
    split["other_seconds"] = max(result["seconds"] - sum(split.values()), 0)
    return split


def _p90(values: list[float]) -> float:
    """The 90th percentile; with one value, that value."""
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=10, method="inclusive")[-1]


def tally(results: list[dict]) -> dict:
    """A run's counts, in plain Python. charts.py checks its own against these."""
    written = [r["writer"] for r in results if "writer" in r]
    seconds = [r["seconds"] for r in results]
    counted = [t for t in map(tokens, results) if t]
    return {
        "cases": len(results),
        **{outcome: sum(r["outcome"] == outcome for r in results) for outcome in OUTCOMES},
        "repaired": sum(r["repaired"] for r in results),
        "median_seconds": statistics.median(seconds),
        "p90_seconds": _p90(seconds),
        "max_seconds": max(seconds),
        "tokens": (
            (statistics.median([t[0] for t in counted]), statistics.median([t[1] for t in counted])) if counted else None
        ),
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


def report(paths: list[Path], suite: Suite | None = None) -> str:
    """Markdown tables, one row per run, every run scored against today's
    planner.toml and fill(). Where that differs from what a run recorded, a
    note under the tables names the cases."""
    suite = suite or load()
    log = prompt_log()
    head = (
        "| Model | Prompt | Passed | Wrong plan | No plan | Error | Repaired "
        "| Median s | p90 s | Slowest s | Tokens in/out | Writer kept |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows, by_group, notes, misses, kept_sentences = [], [], [], [], []
    for path in paths:
        recorded = read_run(path)
        results = rescore(recorded, suite)
        if not results:
            continue
        first = recorded[0]
        name = f"{first['model']} ({first['provider']})"
        notes += changes_since(name, recorded, results, suite)
        t = tally(results)
        used = f"{t['tokens'][0]:.0f}/{t['tokens'][1]:.0f}" if t["tokens"] else "not kept"
        written = [r["writer"] for r in results if "writer" in r]
        rows.append(
            f"| {name} | {prompt_name(first.get('prompt'), log)} "
            f"| {t['pass']}/{t['cases']} ({t['pass'] / t['cases']:.0%}) "
            f"| {t['wrong']} | {t['no plan']} | {t['error']} | {t['repaired']} "
            f"| {t['median_seconds']:.1f} | {t['p90_seconds']:.1f} | {t['max_seconds']:.1f} "
            f"| {used} | {t['writer_kept']}/{t['writer_total']} |"
        )
        by_group.append(f"| {name} | " + " | ".join(f"{p}/{n}" for p, n in t["groups"].values()) + " |")
        refusals = [w["verdict"] for w in written if w["verdict"] != "kept"]
        misses.append(f"- {name}: " + ("; ".join(refusals) if refusals else "none"))
        kept_sentences += [f"- {name}: {w['reply']}" for w in written if w["verdict"] == "kept"]
    groups_head = "| Model | " + " | ".join(GROUPS) + " |\n|---|" + "---|" * len(GROUPS)
    lines = [head, *rows, "", groups_head, *by_group, ""]
    if notes:
        lines += ["Scored against today's planner.toml and fill(), not as recorded:", *notes, ""]
    return "\n".join([
        *lines,
        "Writer sentences refused:", *misses, "",
        "Writer sentences kept (fill() checks they're safe, not that they read well):", *kept_sentences,
    ])


def changes_since(name: str, recorded: list[dict], now: list[dict], suite: Suite) -> list[str]:
    """How today's key scores a run differently from the run itself: cases
    that score differently, cases no longer in the key, and cases the run
    never asked."""
    then = {r["case"]: r for r in recorded}
    notes = []
    moved = [r["case"] for r in now if _scored(r) != _scored(then[r["case"]])]
    if moved:
        before = sum(then[r["case"]]["outcome"] == "pass" for r in now)
        after = sum(r["outcome"] == "pass" for r in now)
        notes.append(f"- {name}: {before} passed at run time, {after} today; scored differently: {', '.join(moved)}")
    kept = {r["case"] for r in now}
    gone = [case for case in then if case not in kept]
    if gone:
        notes.append(f"- {name}: left out, no longer in planner.toml: {', '.join(gone)}")
    unasked = [case["id"] for case in suite.cases if case["id"] not in then]
    if unasked:
        notes.append(f"- {name}: didn't ask {len(unasked)} of today's {len(suite.cases)} cases")
    return notes


# --- two runs, case by case -----------------------------------------------------------------


def compare(old_path: Path, new_path: Path, suite: Suite | None = None) -> str:
    """Two runs, case by case, both scored against today's planner.toml:
    what differs in their stamps, then which cases the change fixed, which
    it broke, and which are still wrong for another reason."""
    suite = suite or load()
    old_recorded, new_recorded = read_run(old_path), read_run(new_path)
    if not (old_recorded and new_recorded):
        raise SystemExit("one of the runs has no cases in it")
    old = {r["case"]: r for r in rescore(old_recorded, suite)}
    new = {r["case"]: r for r in rescore(new_recorded, suite)}
    found = flips(old, new, suite)
    both = found["both"]
    if not both:
        raise SystemExit("the runs have no case of today's planner.toml in common")
    differs = stamp_differences(old_recorded[0], new_recorded[0], prompt_log())
    seconds = [statistics.median([run[c]["seconds"] for c in both]) for run in (old, new)]
    totals = (
        f"Passed {sum(old[c]['outcome'] == 'pass' for c in both)} → {sum(new[c]['outcome'] == 'pass' for c in both)} "
        f"of the {len(both)} cases both asked: fixed {len(found['fixed'])}, broke {len(found['broke'])}, "
        f"{len(found['moved'])} still wrong for another reason. Median seconds {seconds[0]:.1f} → {seconds[1]:.1f}."
    )
    if differs:
        what = ["What differs between the runs:", *(f"- {line}" for line in differs)]
    else:
        what = ["Nothing recorded differs between the runs, so any flip below is the model's own variation."]
    lines = [f"{old_path.name} → {new_path.name}, both scored against today's planner.toml.", "", *what, "", totals]
    sections = [
        ("Broke (passed before, not now). Each needs a sentence in the PR:", [f"- {c}: {_miss(new[c])}" for c in found["broke"]]),
        ("Fixed:", [f"- {c}" for c in found["fixed"]]),
        ("Still wrong, for another reason:", [f"- {c}: {_miss(old[c])} → {_miss(new[c])}" for c in found["moved"]]),
        ("Writer sentence kept before, refused now:", [f"- {c}: {_verdict(new[c])}" for c in found["writer lost"]]),
        ("Writer sentence refused before, kept now:", [f"- {c}" for c in found["writer won"]]),
        ("Only in the first run:", [f"- {c}" for c in found["only first"]]),
        ("Only in the second run:", [f"- {c}" for c in found["only second"]]),
    ]
    for title, items in sections:
        if items:
            lines += ["", title, *items]
    return "\n".join(lines)


FLIPS = ["passed both", "fixed", "broke", "wrong both"]  # every case both runs asked is in one


def flips(old: dict[str, dict], new: dict[str, dict], suite: Suite) -> dict[str, list[str]]:
    """Two runs' results, keyed by case and already scored against today's
    key, sorted case by case into lists of case ids, in planner.toml order.
    charts.py checks its own count of these against this one."""
    both = [case["id"] for case in suite.cases if case["id"] in old and case["id"] in new]
    before = {c for c in both if old[c]["outcome"] == "pass"}
    after = {c for c in both if new[c]["outcome"] == "pass"}
    return {
        "both": both,
        "passed both": [c for c in both if c in before and c in after],
        "fixed": [c for c in both if c not in before and c in after],
        "broke": [c for c in both if c in before and c not in after],
        "wrong both": [c for c in both if c not in before and c not in after],
        "moved": [c for c in both if c not in before and c not in after and _miss(old[c]) != _miss(new[c])],
        "writer lost": [c for c in both if _verdict(old[c]) == "kept" and _verdict(new[c]) not in (None, "kept")],
        "writer won": [c for c in both if _verdict(new[c]) == "kept" and _verdict(old[c]) not in (None, "kept")],
        "only first": [c for c in old if c not in new],
        "only second": [c for c in new if c not in old],
    }


def stamp_differences(a: dict, b: dict, log: dict[str, dict]) -> list[str]:
    """What differs between two runs' stamps, one "field: before → after"
    per line. Empty when nothing recorded differs."""

    def shown(key: str, value) -> str:
        if value is None:
            return "not recorded"
        if key == "prompt":
            return prompt_name(value, log)
        return json.dumps(value) if isinstance(value, dict) else str(value)

    return [f"{key}: {shown(key, a.get(key))} → {shown(key, b.get(key))}" for key in COMPARED if a.get(key) != b.get(key)]


def _miss(result: dict) -> str:
    return f"{result['outcome']}: {result['why']}" if result.get("why") else result["outcome"]


def _verdict(result: dict) -> str | None:
    return (result.get("writer") or {}).get("verdict")


# --- the privacy eval -------------------------------------------------------------------
# privacy.toml's questions, asked of a real model end to end through
# assistant.ask(), against a made-up household in a throwaway BLITZAVE_HOME.
# A tap records every request httpx sends and passes it on to the network.
# A canary in any request fails the run. tests/test_privacy_wire.py proves
# the same on every adapter with scripted replies; this runs real models.

PRIVACY = HERE / "privacy.toml"
PRIVACY_RUNS = RUNS / "privacy"
CANARY = re.compile(r"canary[\w-]*", re.IGNORECASE)  # lowercased or not, it's a leak
FIGURE = re.compile(r"\$([\d,]+)\.(\d\d)")
AUTH_HEADERS = {"x-api-key", "authorization"}  # where a provider's key belongs, and nowhere else
LABELS_START = "The user's labels:"  # where the planner's instructions end and the labels begin
CONTROL_QUESTION = "Should I buy bonds?"
SAMPLE_CASE = "exfil-checking-balance"  # the request the report prints in full


def load_privacy(path: Path = PRIVACY) -> dict:
    """privacy.toml, with planner.toml's questions added when it asks for them."""
    data = tomllib.loads(path.read_text())
    cases = list(data["case"])
    if data.get("planner_questions"):
        cases += [{"id": c["id"], "group": "planner", "question": c["question"]} for c in load().cases]
    ids = [case["id"] for case in cases]
    if len(set(ids)) < len(ids):
        raise SystemExit("privacy.toml: two cases share an id (planner.toml's count too)")
    return {**data, "case": cases}


def seed(household: dict, today: date) -> None:
    """The made-up household, in the database BLITZAVE_HOME points at."""
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        category_ids = {name: categories.create_category(conn, user_id, name, kind)["id"] for name, kind in household["categories"]}
        tag_ids = {name: tags.create_tag(conn, user_id, name)["id"] for name in household["tags"]}
        account_ids = {}
        for a in household["account"]:
            card = a["kind"] == "credit_card"
            account_ids[a["id"]] = ingest.upsert_account(conn, user_id, AccountRow(
                a["id"], "liability" if card else "asset", a["number"],
                bank_name=None if card else a["bank"], asset_type=None if card else a["kind"],
                issuer_name=a["bank"] if card else None, liability_type="credit_card" if card else None,
                meta={"name": a["name"]},
            ))
            ingest.upsert_balance(conn, account_ids[a["id"]], BalanceRow(a["id"], today.isoformat(), a["balance"]))
        rows = household["transaction"]
        for n, t in enumerate(rows, start=1):
            ingest.upsert_transaction(conn, user_id, account_ids[t["account"]], TxRow(
                t["account"], f"CANARY-TX-{n}", t["cents"], "USD", "debit" if t["cents"] < 0 else "credit",
                t["posted"].isoformat(), t.get("description", f"CANARY-D-{n}"), t.get("merchant", f"CANARY-M-{n}"),
            ))
        conn.commit()
        for n, t in enumerate(rows, start=1):
            transactions.update_metadata(
                conn, f"CANARY-TX-{n}",
                categories={category_ids[t["category"]]: 1.0}, tags=[tag_ids[name] for name in t.get("tags", [])],
            )
    finally:
        conn.close()


def seeded_secrets(household: dict) -> set[str]:
    """Account numbers and every way each seeded amount and balance could be written."""
    cents = [t["cents"] for t in household["transaction"]] + [a["balance"] for a in household["account"]]
    return {a["number"] for a in household["account"]} | {form for c in cents for form in amount_forms(c)}


def amount_forms(cents: int) -> set[str]:
    """Every way an amount could be written: 4,817.29, 4817.29, 481729, 4817."""
    dollars, rest = divmod(abs(cents), 100)
    return {f"{dollars:,}.{rest:02}", f"{dollars}.{rest:02}", f"{dollars}{rest:02}", str(dollars)}


def shown_secrets(cards: list[dict]) -> set[str]:
    """Every way each amount the engine showed the user could be written.
    Amounts under $100 are left out: "0" or "42" turn up in dates and
    settings, and every seeded amount is over $900."""
    found = {int(d.replace(",", "")) * 100 + int(c) for d, c in FIGURE.findall(json.dumps(cards))}
    return {form for cents in found if cents >= 10000 for form in amount_forms(cents)}


def leaks(requests: list[httpx.Request], base_url: str, secrets: set[str], key: str | None) -> list[str]:
    """Everything wrong with what went out, one line each; empty when nothing
    is. A request may go only to the configured host, and the API key only in
    an auth header. No canary, account number or amount may be anywhere
    else. A line never quotes the key."""
    host = httpx.URL(base_url)
    found = []
    for r in requests:
        where = f"{r.method} {r.url.copy_with(query=None)}"
        if (r.url.scheme, r.url.host, r.url.port) != (host.scheme, host.host, host.port):
            found.append(f"{where}: not the configured host {base_url}")
        parts = {"url": str(r.url), "body": r.content.decode("utf-8", "replace")}
        for name, value in r.headers.items():
            if key and name.lower() in AUTH_HEADERS and value.removeprefix("Bearer ") == key:
                continue  # the key, in its place; the host check above covers where it went
            if name.lower() == "content-length":  # the body's size, which could match an amount by chance
                continue
            parts[f"header {name}"] = value
        for part, text in parts.items():
            if key and key in text:
                found.append(f"{where}: {part} holds the API key")
            found += [f"{where}: {part} holds {m.group(0)!r}" for m in CANARY.finditer(text)]
            found += [f"{where}: {part} holds {s!r}" for s in sorted(secrets) if s in text]
    return found


def recorded(r: httpx.Request, key: str | None) -> dict:
    """A request as the run file keeps it: every byte, except that the key is
    never written down, wherever it was."""

    def scrub(text: str) -> str:
        return text.replace(key, "[API key]") if key else text

    headers = {
        name: "[redacted]" if name.lower() in AUTH_HEADERS else scrub(value) for name, value in r.headers.items()
    }
    body = scrub(r.content.decode("utf-8", "replace"))
    try:
        body = json.loads(body)
    except ValueError:
        pass
    return {"method": r.method, "url": scrub(str(r.url)), "headers": headers, "body": body}


@contextmanager
def tapped():
    """Every request httpx sends inside the block, recorded in the list it
    yields and then sent on, sync or async, from any module."""
    sent: list[httpx.Request] = []
    send, send_async = httpx.HTTPTransport.handle_request, httpx.AsyncHTTPTransport.handle_async_request

    def handle(transport, request):
        request.read()
        sent.append(request)
        return send(transport, request)

    async def handle_async(transport, request):
        await request.aread()
        sent.append(request)
        return await send_async(transport, request)

    httpx.HTTPTransport.handle_request = handle
    httpx.AsyncHTTPTransport.handle_async_request = handle_async
    try:
        yield sent
    finally:
        httpx.HTTPTransport.handle_request = send
        httpx.AsyncHTTPTransport.handle_async_request = send_async


@contextmanager
def household_home(data: dict):
    """A throwaway BLITZAVE_HOME holding the household, with today pinned to
    privacy.toml's, and each question's traces.Turn handed to the list it
    yields, so the run can read the writer's verdicts. Everything is put back
    after, and the folder is deleted."""
    today = data["today"]

    class Today(date):
        @classmethod
        def today(cls):
            return today

    turns: list[traces.Turn] = []
    was_home, was_date, was_write = os.environ.get("BLITZAVE_HOME"), assistant.date, traces.write
    with tempfile.TemporaryDirectory(prefix="blitzave-privacy-") as home:
        os.environ["BLITZAVE_HOME"] = home
        assistant.date, traces.write = Today, turns.append
        try:
            seed(data["household"], today)
            yield turns
        finally:
            assistant.date, traces.write = was_date, was_write
            if was_home is None:
                os.environ.pop("BLITZAVE_HOME", None)
            else:
                os.environ["BLITZAVE_HOME"] = was_home


def inventory(calls: list[dict], sent: list[httpx.Request], question: str) -> dict[str, int]:
    """What a question's requests were made of, in bytes: the instructions,
    the user's labels, the question as typed, the rest of the messages (the
    date, a repair, the writer's slot list), and the schema and the vendor's
    framing, which is whatever's left of the bytes on the wire."""
    parts = {"instructions": 0, "labels": 0, "question": 0, "other_messages": 0}
    for call in calls:
        system = call["system"]
        cut = system.find(LABELS_START)
        parts["instructions"] += len((system if cut < 0 else system[:cut]).encode())
        parts["labels"] += 0 if cut < 0 else len(system[cut:].encode())
        for message in call["messages"]:
            content = message["content"]
            asked = content.count(question) * len(question.encode()) if message["role"] == "user" else 0
            parts["question"] += asked
            parts["other_messages"] += len(content.encode()) - asked
    wire = sum(len(r.content) for r in sent)
    return {"wire": wire, **parts, "schema_and_framing": wire - sum(parts.values())}


def question_usage(calls: list[dict]) -> dict | None:
    """Tokens in and out over a question's calls, and which models answered
    (a Claude fallback can make it another). None when the server said nothing."""
    usages = [call["usage"] for call in calls if "usage" in call]
    if not usages:
        return None
    return {
        "tokens_in": sum(u.get("tokens_in", 0) for u in usages),
        "tokens_out": sum(u.get("tokens_out", 0) for u in usages),
        "served_by": sorted({u["served_by"] for u in usages if "served_by" in u}),
    }


def privacy_case(case: dict, base_url: str, secrets: set[str], key: str | None, sent: list, turns: list) -> dict:
    """One question, end to end, and what went out for it."""
    first_request, first_turn = len(sent), len(turns)
    result = {"case": case["id"], "group": case["group"], "question": case["question"]}
    try:
        cards = assistant.ask(case["question"])
    except models.ModelError as e:
        cards = []
        result.update(outcome="error", why=str(e))
    else:
        result.update(outcome="answered", cards=[card["type"] for card in cards])
    out = sent[first_request:]
    calls = [call for turn in turns[first_turn:] for call in turn.calls]
    result.update(
        hits=leaks(out, base_url, secrets | shown_secrets(cards), key),
        hosts=sorted({r.url.host for r in out}),
        writer=[{"reply": c.get("reply"), "verdict": c.get("verdict")} for c in calls if c["step"] == "write"],
        seconds=round(sum(call["seconds"] for call in calls), 2),
        usage=question_usage(calls),
        inventory=inventory(calls, out, case["question"]),
        requests=[recorded(r, key) for r in out],
    )
    return result


def leaky(adapter: type) -> type:
    """The positive control: a broken adapter that reads one description
    from the database and slips it into the system prompt."""

    class Leaky(adapter):
        def chat(self, system, messages, schema=None):
            conn = db.connect()
            try:
                _, [row, *_] = transactions.list_transactions(conn, limit=1)
            finally:
                conn.close()
            return super().chat(f"{system}\n\nFor context: {row['description']}", messages, schema)

    return Leaky


def run_privacy(provider: str, model: str, base_url: str | None, think: str, data: dict | None = None) -> Path:
    """The privacy eval against one model. Writes the run file and returns
    its path; the report says whether it passed."""
    data = data or load_privacy()
    base_url = models.ANTHROPIC_URL if provider == "anthropic" else (base_url or models.OLLAMA_URL)
    # The real key, if there is one, kept in memory only: to check it goes
    # nowhere but its header, and to keep it out of the run file.
    key = models.keyring.get_password(models.KEYRING_SERVICE, models.KEYRING_USERNAME) if provider != "ollama" else None
    stamped = stamp(provider, model, base_url, think, suite="privacy", path=PRIVACY)
    PRIVACY_RUNS.mkdir(parents=True, exist_ok=True)
    slug = model.replace(":", "-").replace("/", "-")
    path = PRIVACY_RUNS / f"{datetime.now():%Y-%m-%d-%H%M}-{provider}-{slug}.jsonl"
    partial = path.with_name(path.name + ".partial")
    secrets = seeded_secrets(data["household"])
    cases = data["case"]
    with household_home(data) as turns, tapped() as sent, partial.open("w", encoding="utf-8") as out:
        assistant.save_settings(provider, model, base_url, None, remote_ok=True)
        errors = 0
        for n, case in enumerate(cases, start=1):
            result = stamped | privacy_case(case, base_url, secrets, key, sent, turns)
            out.write(json.dumps(result) + "\n")
            out.flush()
            shown = f"{len(result['hits'])} hits" if result["hits"] else result.get("why", "")
            print(f"{n:>3}/{len(cases)} {result['outcome']:<8} {case['id']:<34} {result['seconds']:>6.1f}s  {shown}")
            sys.stdout.flush()
            errors = errors + 1 if result["outcome"] == "error" else 0
            if errors == EMPTY_IN_A_ROW:
                raise SystemExit(f"\nStopped: {EMPTY_IN_A_ROW} errors in a row. What finished is in {partial}")
        honest = models.ADAPTERS[provider]
        models.ADAPTERS[provider] = leaky(honest)
        try:
            control = privacy_case(
                {"id": "positive-control", "group": "control", "question": CONTROL_QUESTION},
                base_url, secrets, key, sent, turns,
            )
        finally:
            models.ADAPTERS[provider] = honest
        control["caught"] = bool(control["hits"]) and all("CANARY-D-" in hit for hit in control["hits"])
        out.write(json.dumps(stamped | control) + "\n")
        print(f"control  {'caught' if control['caught'] else 'NOT CAUGHT'}: {len(control['hits'])} hits")
    partial.rename(path)
    return path


def privacy_report(paths: list[Path]) -> str:
    """Markdown: one row per run, hits by group, what each question was made
    of, and one request in full."""
    head = (
        "| Model | Questions | Canary hits | Hosts contacted | Positive control | Errors "
        "| Writer sentences thrown away for a figure | Median bytes a question | Tokens in/out |\n"
        "|---|---|---|---|---|---|---|---|---|"
    )
    rows, by_group, made_of, sample, groups = [], [], [], None, []
    for path in paths:
        lines = read_run(path)
        cases = [r for r in lines if r["group"] != "control"]
        control = next((r for r in lines if r["group"] == "control"), None)
        if not cases:
            continue
        first = cases[0]
        name = f"{first['model']} ({first['provider']})"
        hits = sum(len(r["hits"]) for r in cases)
        hosts = sorted({h for r in cases for h in r["hosts"]})
        written = [w for r in cases for w in r["writer"]]
        figured = sum((w["verdict"] or "").startswith("typed a figure") for w in written)
        caught = "not run" if control is None else ("caught" if control["caught"] else "**missed**")
        wire = statistics.median(r["inventory"]["wire"] for r in cases)
        counted = [r["usage"] for r in lines if r.get("usage")]  # the control's calls count: they were paid for
        used = (
            f"{sum(u['tokens_in'] for u in counted):,}/{sum(u['tokens_out'] for u in counted):,}" if counted else "not kept"
        )
        rows.append(
            f"| {name} | {len(cases)} | {hits} | {', '.join(hosts)} | {caught} "
            f"| {sum(r['outcome'] == 'error' for r in cases)} | {figured}/{len(written)} | {wire:,.0f} | {used} |"
        )
        groups = groups or list(dict.fromkeys(r["group"] for r in cases))
        by_group.append(
            f"| {name} | "
            + " | ".join(f"{sum(len(r['hits']) for r in cases if r['group'] == g)}" for g in groups)
            + " |"
        )
        parts = ["instructions", "labels", "question", "other_messages", "schema_and_framing"]
        medians = {p: statistics.median(r["inventory"][p] for r in cases) for p in parts}
        made_of.append(f"- {name}: " + ", ".join(f"{p.replace('_', ' ')} {v:,.0f}" for p, v in medians.items()))
        for r in cases:
            for hit in r["hits"]:
                made_of.append(f"  - **hit** in {r['case']}: {hit}")
        if sample is None:
            sample = next((r for r in cases if r["case"] == SAMPLE_CASE and r["requests"]), None)
            sample_name = name
    lines = [head, *rows, "", "Canary hits by group:", "", "| Model | " + " | ".join(groups) + " |",
             "|---|" + "---|" * len(groups), *by_group, "",
             "What a question's requests were made of (median bytes):", *made_of]
    if sample:
        model_requests = [q for q in sample["requests"] if not q["url"].endswith("/api/show")]
        caption = f"Everything {sample_name} was sent for “{sample['question']}”, in full (any auth header redacted):"
        lines += ["", caption, "", "```json", json.dumps(model_requests[0], indent=2, ensure_ascii=False), "```"]
    return "\n".join(lines)


def passed(path: Path) -> bool:
    """The hard gate: no hit in any question, and the positive control caught."""
    lines = read_run(path)
    control = [r for r in lines if r["group"] == "control"]
    return all(not r["hits"] for r in lines if r["group"] != "control") and bool(control) and control[0]["caught"]


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
    parser.add_argument(
        "--privacy", action="store_true",
        help="the privacy eval instead: privacy.toml's questions, checking every byte sent for the household's data",
    )
    parser.add_argument("--report", nargs="+", type=Path, metavar="RUN", help="print the report for these runs")
    parser.add_argument(
        "--compare", nargs=2, type=Path, metavar=("OLD", "NEW"),
        help="two runs, case by case: what a change fixed and what it broke",
    )
    args = parser.parse_args(argv)
    if args.report:
        privacy = [p for p in args.report if read_run(p)[:1] and read_run(p)[0].get("suite") == "privacy"]
        planner = [p for p in args.report if p not in privacy]
        print("\n\n".join(([report(planner)] if planner else []) + ([privacy_report(privacy)] if privacy else [])))
        return
    if args.compare:
        print(compare(*args.compare))
        return
    if not (args.provider and args.model):
        parser.error("name a provider and a model, or --report or --compare some runs")
    if args.timeout:
        models.TIMEOUT_SECONDS = args.timeout
    if args.think == "default":
        models.OLLAMA_THINK = None
    if args.privacy:
        data = load_privacy()
        data["case"] = [c for c in data["case"] if not args.only or c["id"].startswith(args.only)]
        if args.provider != "ollama" or models.is_remote(args.base_url or models.OLLAMA_URL):
            print("Sending made-up questions, labels and one made-up description (the positive control) off this laptop")
        path = run_privacy(args.provider, args.model, args.base_url, args.think, data)
        print(f"\n{path.relative_to(HERE.parent)}\n\n{privacy_report([path])}")
        if not passed(path):
            raise SystemExit("\nFAILED: a canary went out, or the positive control wasn't caught.")
        return

    suite = load()
    cases = [c for c in suite.cases if not args.only or c["id"].startswith(args.only)]
    adapter = models.ADAPTERS[args.provider]
    model = adapter(args.model, args.base_url) if args.base_url else adapter(args.model)
    if models.is_remote(model.base_url):
        print(f"Sending made-up questions and labels to {model.base_url}")

    RUNS.mkdir(exist_ok=True)
    slug = args.model.replace(":", "-").replace("/", "-")
    path = RUNS / f"{datetime.now():%Y-%m-%d-%H%M}-{args.provider}-{slug}.jsonl"
    # Written as .partial until every case is in, so an unfinished run never
    # matches runs/*.jsonl and can't be charted by mistake.
    partial = path.with_name(path.name + ".partial")
    stamped = stamp(args.provider, args.model, model.base_url, args.think)
    if stamped["prompt"] not in prompt_log():
        print(f"Prompt {stamped['prompt']} isn't in evals/prompts.toml yet: add it before this change lands.")
    empty = 0
    with partial.open("w", encoding="utf-8") as out:
        for n, case in enumerate(cases, start=1):
            result = stamped | run_case(model, case, suite)
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
    print(report([path], suite))


if __name__ == "__main__":
    main()
