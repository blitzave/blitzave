"""Figures for the privacy eval, drawn from its run files and nothing else.

    uv run --group eval python evals/privacy_charts.py evals/runs/privacy/*.jsonl --out DIR

Writes requests.svg, bytes.svg, answers.svg and writer.svg to DIR, and prints
the same numbers as Markdown tables, for the post's text and for readers who
can't see a chart. Same palette, size and helpers as charts.py, and the same
rule: before anything is drawn, the checks must pass, or nothing is written.

- every count agrees with a plain-Python recount of the same files;
- every request in the files is searched again, here, for the household's
  canaries, account numbers and amounts. The run said it found none (or, for
  the leaky control, some). A search that finds one the run didn't is an error;
- every request is sorted into the one call that sends it (a plan, a repair,
  a writer call, Ollama's model check), and the sort must agree with the run:
  one plan per question, at most one repair, a writer call per writer verdict.

A square in requests.svg is one request, as the tap recorded it, colored by
that call. bytes.svg splits each run's median-size plan request, one real
request, so its parts add up to its bytes on the wire. The SVGs are
deterministic: the same runs give the same bytes.
"""

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import charts  # evals/charts.py, beside this file
import httpx
import pandas as pd
import run  # evals/run.py, beside this file

from blitzave import assistant
from blitzave.sources import models

W = charts.WIDTH * 72  # 510 points. One unit is one point, so a square is as big as it looks.
INK, INK_2, MUTED, NEUTRAL = charts.INK, charts.INK_2, charts.MUTED, charts.NEUTRAL
BLUE, ORANGE, AQUA, YELLOW = charts.BLUE, charts.ORANGE, charts.AQUA, charts.YELLOW

# A segmented bar takes the palette's slots in its own order (blue, orange,
# aqua, yellow), which is the order validated for touching segments: orange
# and yellow fail it side by side, so they never are.
PARTS = [  # what a plan request is made of, in the order it's built
    ("instructions", "Instructions", BLUE),
    ("labels", "Your labels", ORANGE),
    ("question", "Your question", AQUA),
    ("date", "The date", YELLOW),
    ("schema_and_framing", "Plan schema and framing", NEUTRAL),
]
# The calls that put a request on the wire, in the order a question makes them.
# Orange is the control's, on a row of its own under the blue, so it never
# touches yellow.
KINDS = [
    ("plan", "Plan", BLUE),
    ("repair", "Repair", AQUA),
    ("write", "Writer", YELLOW),
    ("check", "Model check", NEUTRAL),
]
NOUN = {"plan": ("plan", "plans"), "repair": ("repair", "repairs"), "write": ("writer call", "writer calls"),
        "check": ("model check", "model checks")}
PLANNER_HEAD = assistant.PLANNER[: assistant.PLANNER.index(run.LABELS_START)]
CARDS = [  # what the engine made of a question
    ("transactions", "Listed rows", BLUE),
    ("summary", "Summarized", ORANGE),
    ("proposal", "Previewed a change", AQUA),
    ("clarify", "Asked which you meant", YELLOW),
    ("message", "Said so in its own words", NEUTRAL),
]
SENTENCES = [  # what fill() did with the writer's sentence
    ("kept", "Shown", BLUE),
    ("slot", "Invented a slot", ORANGE),
    ("figure", "Typed a figure or a comparison", AQUA),
    ("other", "Too long, or not allowed", NEUTRAL),
]


# --- the data ------------------------------------------------------------------------


def verdict_kind(verdict: str | None) -> str:
    verdict = verdict or ""
    if verdict == "kept":
        return "kept"
    if verdict.startswith("a slot this summary"):
        return "slot"
    if verdict.startswith(("typed a figure", "typed a comparison")):
        return "figure"
    return "other"


def as_request(recorded: dict) -> httpx.Request:
    """A recorded request, as httpx would have seen it, so run.leaks() can read it."""
    body = recorded["body"]
    content = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return httpx.Request(recorded["method"], recorded["url"], headers=recorded["headers"], content=content.encode())


def chat_of(recorded: dict) -> tuple[str, list[dict]]:
    """The system prompt and the other messages of a chat request, for
    Anthropic (a system field) and Ollama (a first system message) alike."""
    body = recorded["body"]
    messages = body["messages"]
    if "system" in body:
        return body["system"], messages
    if messages and messages[0]["role"] == "system":
        return messages[0]["content"], messages[1:]
    raise SystemExit(f"a chat request with no system prompt: {recorded['url']}")


def request_kind(recorded: dict) -> str:
    """Which call sent a request, read from the request itself: Ollama's model
    check, or a chat whose system prompt is the writer's or the planner's. A
    planner chat that quotes the model's own reply back is the repair. Anything
    else stops the script, so no request is ever drawn as something it isn't."""
    path = httpx.URL(recorded["url"]).path
    if path == "/api/show":
        return "check"
    if path not in ("/api/chat", "/v1/messages"):
        raise SystemExit(f"a request this script can't name: {recorded['method']} {recorded['url']}")
    system, messages = chat_of(recorded)
    if system.startswith(assistant.WRITER):
        return "write"
    if system.startswith(PLANNER_HEAD):
        return "repair" if any(m["role"] == "assistant" for m in messages) else "plan"
    raise SystemExit(f"a chat request with a system prompt this script doesn't know: {recorded['url']}")


def plan_parts(recorded: dict, question: str) -> dict[str, int]:
    """A plan request's bytes on the wire, split into what it carries. The
    total is its Content-Length; the schema and the vendor's framing (JSON
    keys, quotes, escapes, settings) are whatever the text parts don't cover."""
    system, messages = chat_of(recorded)
    [message] = messages
    cut = system.find(run.LABELS_START)
    content = message["content"]
    if cut < 0 or not content.endswith(question):
        raise SystemExit(f"a plan request this script can't split: {recorded['url']}")
    wire = int(recorded["headers"]["content-length"])
    parts = {
        "instructions": len(system[:cut].encode()),
        "labels": len(system[cut:].encode()),
        "question": len(question.encode()),
        "date": len(content.encode()) - len(question.encode()),
    }
    return {"wire": wire, **parts, "schema_and_framing": wire - sum(parts.values())}


def rescan(lines: list[dict], secrets: set[str]) -> list[list[bool]]:
    """For each line of a run, whether each of its requests holds a canary,
    an account number or a seeded amount. Searched here, not read from the run."""
    provider = lines[0]["provider"]
    base_url = models.ANTHROPIC_URL if provider == "anthropic" else models.OLLAMA_URL
    return [[bool(run.leaks([as_request(q)], base_url, secrets, None)) for q in line["requests"]] for line in lines]


def load(paths: list[Path]) -> tuple[pd.DataFrame, dict[str, list[tuple[bool, str, bool]]], dict[str, dict]]:
    """One row per question (the control left out); per run the squares of
    requests.svg: (the control's or not, the call that sent it, whether it
    holds a canary); and per run its median-size plan request, split."""
    secrets = run.seeded_secrets(run.load_privacy()["household"])
    rows, squares, medians = [], {}, {}
    for path in paths:
        lines = run.read_run(path)
        found = rescan(lines, secrets)
        squares[path.name] = [
            (line["group"] == "control", request_kind(q), hit)
            for line, hits in zip(lines, found) for q, hit in zip(line["requests"], hits)
        ]
        plans = sorted(
            (plan_parts(q, line["question"]) for line in lines if line["group"] != "control"
             for q in line["requests"] if request_kind(q) == "plan"),
            key=lambda parts: parts["wire"],
        )
        medians[path.name] = plans[len(plans) // 2]  # an odd count: one real request, not an average
        for line, hits in zip(lines, found):
            if line["group"] == "control":
                continue
            rows.append({
                "run": path.name, "model": line["model"], "provider": line["provider"], "group": line["group"],
                "hits": len(line["hits"]), "rescanned": sum(hits), "requests": len(line["requests"]),
                "cards": line.get("cards", []), "kinds": [verdict_kind(w["verdict"]) for w in line["writer"]],
                "calls": [request_kind(q) for q in line["requests"]],
            })
    df = pd.DataFrame(rows)
    df["name"] = df["model"].map(lambda m: charts.MODEL_NAME.get(m, m))
    df["where"] = df["provider"].map(lambda p: charts.WHERE.get(p, p))
    return df, squares, medians


def check(df: pd.DataFrame, paths: list[Path], squares: dict) -> None:
    """Stops unless pandas' counts match a plain recount of the files, and the
    search made here agrees with the run's own."""
    for path in paths:
        lines = run.read_run(path)
        asked = [line for line in lines if line["group"] != "control"]
        mine = df[df["run"] == path.name]
        same = (
            len(mine) == len(asked)
            and int(mine["requests"].sum()) == sum(len(line["requests"]) for line in asked)
            and int(mine["hits"].sum()) == sum(len(line["hits"]) for line in asked)
            and sum(len(k) for k in mine["kinds"]) == sum(len(line["writer"]) for line in asked)
            and Counter(c for cards in mine["cards"] for c in cards)
            == Counter(c for line in asked for c in line.get("cards", []))
        )
        if not same:
            raise SystemExit(f"{path.name}: pandas and a plain recount disagree, so nothing is drawn")
        wrongly = [line["case"] for line, (_, row) in zip(asked, mine.iterrows()) if row["rescanned"] and not row["hits"]]
        if wrongly:
            raise SystemExit(f"{path.name}: searched again, a canary is in {wrongly}, which the run called clean")
        for line in lines:
            calls = [request_kind(q) for q in line["requests"]]
            chats = [c for c in calls if c != "check"]
            right = (
                calls.count("plan") == (1 if line["outcome"] == "answered" else calls.count("plan"))
                and calls.count("plan") <= 1 and calls.count("repair") <= calls.count("plan")
                and (not chats or chats[0] == "plan")
                and calls.count("write") == len(line["writer"])
            )
            if not right:
                raise SystemExit(f"{path.name}: {line['case']} sent {calls}, which isn't how a question goes")
        if sum(kind == "check" for _, kind, _ in squares[path.name]) > (1 if lines[0]["provider"] == "ollama" else 0):
            raise SystemExit(f"{path.name}: more model checks than one per run")
        control = next((line for line in lines if line["group"] == "control"), None)
        if control is None or not control["caught"] or not any(hit for is_control, _, hit in squares[path.name] if is_control):
            raise SystemExit(f"{path.name}: the leaky control wasn't caught, so a clean run proves nothing")


def summary(df: pd.DataFrame, medians: dict[str, dict]) -> pd.DataFrame:
    """One row per run, in the order the runs were named: the counts, the
    requests by call, and the parts of its median-size plan request."""
    grouped = df.groupby("run", sort=False)
    out = grouped.agg(
        name=("name", "first"), where=("where", "first"), questions=("group", "size"),
        requests=("requests", "sum"), hits=("hits", "sum"),
    ).reset_index()
    for key, _, _ in KINDS:
        out[f"calls_{key}"] = [sum(c.count(key) for c in df[df["run"] == r]["calls"]) for r in out["run"]]
    for key in ["wire", *(k for k, _, _ in PARTS)]:
        out[key] = [medians[r][key] for r in out["run"]]
    if not (out[[f"calls_{k}" for k, _, _ in KINDS]].sum(axis=1) == out["requests"]).all():
        raise SystemExit("requests by call don't add up to the requests")
    return out


# --- drawing -------------------------------------------------------------------------------


def canvas(height: float):
    fig = charts.new_figure(height / 72)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(height, 0)
    ax.axis("off")
    return fig, ax


def heading(ax, text: str, sub: str) -> None:
    ax.text(10, 10, text, fontsize=11.5, color=INK, weight="bold", va="top")
    ax.text(10, 28, sub, fontsize=9, color=INK_2, va="top")


def key(fig, ax, y: float, items: list[tuple[str, str]]) -> None:
    """A row of colored squares and their names, from the left edge."""
    x = 10
    for label, color in items:
        charts.block(ax, x, x + 8, y, 8, color, radius_pt=1.5)
        text = ax.text(x + 13, y, label, fontsize=8.5, color=INK_2, va="center")
        width = text.get_window_extent(renderer=fig.canvas.get_renderer()).width * 72 / fig.dpi
        x += 13 + width + 14


def model_label(ax, y: float, name: str, where: str) -> None:
    ax.text(10, y - 1.5, name, fontsize=9.5, color=INK, va="bottom")
    ax.text(10, y + 1.5, where, fontsize=8, color=INK_2, va="top")


def requests_figure(table: pd.DataFrame, squares: dict, path: Path) -> None:
    """One square per request, a row of them per model, colored by the call
    that sent it and grouped in the order a question makes them. Under each
    model, the counts in words. The control's requests, orange, sit apart:
    the check had to catch them. A canary in any other request would be ink."""
    cols, x0 = 44, 128
    pitch = (W - 10 - x0) / cols
    size = pitch - 1.7
    order = [key for key, _, _ in KINDS]
    color = {key: c for key, _, c in KINDS}
    top, hits = 72, int(table["hits"].sum())
    blocks = []
    for _, row in table.iterrows():
        marks = squares[row["run"]]
        real = sorted((m for m in marks if not m[0]), key=lambda m: order.index(m[1]))
        control = [m for m in marks if m[0]]
        grid = math.ceil(len(real) / cols) * pitch
        blocks.append((row, real, control, grid, grid + 15 + pitch + 16))
    fig, ax = canvas(top + sum(b[4] for b in blocks) + 4)
    heading(ax, "Every request, one square", (
        f"{int(table['requests'].sum()):,} requests from {int(table['questions'].sum())} questions, "
        f"each searched again for the household's data. {'None holds any.' if not hits else str(hits) + ' did.'}"
    ))
    present = [key for key in order if any(kind == key for m in squares.values() for c, kind, _ in m if not c)]
    leaked = any(hit for m in squares.values() for c, _, hit in m if hit and not c)
    key(fig, ax, 48, [(name, c) for k, name, c in KINDS if k in present] + [("Leaky adapter, caught", ORANGE)]
        + ([("A canary in a real request", INK)] if leaked else []))
    y = top
    for row, real, control, grid, height in blocks:
        model_label(ax, y + pitch / 2, row["name"], row["where"])
        for n, (_, kind, hit) in enumerate(real):
            r, c = divmod(n, cols)
            cx, cy = x0 + c * pitch, y + r * pitch + size / 2
            charts.block(ax, cx, cx + size, cy, size, INK if hit else color[kind], radius_pt=1.2)
        counts = Counter(kind for _, kind, _ in real)
        words = " · ".join(f"{counts[k]} {NOUN[k][counts[k] != 1]}" for k in order if counts[k])
        ax.text(x0, y + grid + 4, words, fontsize=7.5, color=INK_2, va="top")
        cy = y + grid + 15
        for n, (_, kind, hit) in enumerate(control):
            cx = x0 + n * pitch
            charts.block(ax, cx, cx + size, cy + size / 2, size, ORANGE if hit else BLUE, radius_pt=1.2)
        plural = "s" if len(control) != 1 else ""
        ax.text(x0 + len(control) * pitch + 4, cy + size / 2,
                f"the control: one more question, through a leaky adapter. {len(control)} request{plural}, caught",
                fontsize=7.5, color=INK_2, va="center")
        y += height
    charts.save(fig, path)


def stacked(table: pd.DataFrame, values: list[list[float]], parts: list[tuple[str, str, str]], path: Path,
            head: tuple[str, str], notes: list[str], unit: str = "") -> None:
    """A segmented bar per run. values[i] is run i's size of each part."""
    top, pitch, x0, bar_w = 72, 38 if notes else 30, 128, 320
    fig, ax = canvas(top + pitch * len(table) + 4)
    heading(ax, *head)
    key(fig, ax, 48, [(name, color) for _, name, color in parts])
    scale = bar_w / max(sum(v) for v in values)
    for i, (_, row) in enumerate(table.iterrows()):
        y = top + i * pitch
        model_label(ax, y + 7, row["name"], row["where"])
        x = x0
        for (_, _, color), value in zip(parts, values[i]):
            width = value * scale
            charts.block(ax, x, x + width, y + 7, 15, color, radius_pt=2)
            if width >= 34:
                ax.text(x + width / 2, y + 7, f"{value:,.0f}", fontsize=8, color=charts.ink_on(color), ha="center", va="center")
            x += width
        ax.text(x + 6, y + 7, f"{sum(values[i]):,.0f}{unit}", fontsize=8.5, color=INK, va="center")
        if notes:
            ax.text(x0, y + 22, notes[i], fontsize=7.5, color=INK_2, va="top")
    charts.save(fig, path)


def bytes_figure(sums: pd.DataFrame, path: Path) -> None:
    values = [[row[key] for key, _, _ in PARTS] for _, row in sums.iterrows()]
    notes = [
        "labels {:,.0f} B · question {:,.0f} B · date {:,.0f} B".format(r["labels"], r["question"], r["date"])
        for _, r in sums.iterrows()
    ]
    stacked(sums, values, PARTS, path, (
        "What a plan request is made of",
        "Each run's median-size plan request, in bytes on the wire. No byte comes from a transaction, a balance or an account.",
    ), notes, unit=" B")


def counted(df: pd.DataFrame, column: str, order: list[tuple[str, str, str]]) -> list[list[int]]:
    out = []
    for run_name in dict.fromkeys(df["run"]):
        seen = Counter(item for items in df[df["run"] == run_name][column] for item in items)
        out.append([seen[key] for key, _, _ in order])
    return out


def answers_figure(df: pd.DataFrame, sums: pd.DataFrame, path: Path) -> None:
    stacked(sums, counted(df, "cards", CARDS), CARDS, path, (
        "What the engine did with each question",
        "Cards shown. The model only plans; rows, previews and sentences come from the engine, on this laptop.",
    ), [], unit="")


def writer_figure(df: pd.DataFrame, sums: pd.DataFrame, path: Path) -> None:
    stacked(sums, counted(df, "kinds", SENTENCES), SENTENCES, path, (
        "What fill() did with the writer's sentences",
        "A summary's sentence is checked before it's shown. Thrown away means the engine's own sentence was shown instead.",
    ), [], unit="")


# --- the tables --------------------------------------------------------------------------


def tables(df: pd.DataFrame, sums: pd.DataFrame) -> str:
    asked = counted(df, "cards", CARDS)
    wrote = counted(df, "kinds", SENTENCES)
    lines = [
        "| Model | Questions | Requests | Canary hits |", "|---|---|---|---|",
        *(f"| {r['name']} | {r['questions']} | {r['requests']} | {r['hits']} |" for _, r in sums.iterrows()),
        "", "| Model | " + " | ".join(name for _, name, _ in KINDS) + " | Total |", "|---|" + "---|" * (len(KINDS) + 1),
        *(f"| {r['name']} | " + " | ".join(str(r[f'calls_{k}']) for k, _, _ in KINDS) + f" | {r['requests']} |"
          for _, r in sums.iterrows()),
        "", "| Model | " + " | ".join(name for _, name, _ in PARTS) + " | Total |",
        "|---|" + "---|" * (len(PARTS) + 1),
        *(f"| {r['name']} | " + " | ".join(f"{r[k]:,.0f}" for k, _, _ in PARTS) + f" | {r['wire']:,.0f} |" for _, r in sums.iterrows()),
        "", "| Model | " + " | ".join(name for _, name, _ in CARDS) + " |", "|---|" + "---|" * len(CARDS),
        *(f"| {r['name']} | " + " | ".join(map(str, counts)) + " |" for (_, r), counts in zip(sums.iterrows(), asked)),
        "", "| Model | " + " | ".join(name for _, name, _ in SENTENCES) + " |", "|---|" + "---|" * len(SENTENCES),
        *(f"| {r['name']} | " + " | ".join(map(str, counts)) + " |" for (_, r), counts in zip(sums.iterrows(), wrote)),
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Figures for the privacy eval, from its run files.")
    parser.add_argument("runs", nargs="+", type=Path, help="one run per model")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    df, squares, medians = load(args.runs)
    check(df, args.runs, squares)
    sums = summary(df, medians)
    args.out.mkdir(parents=True, exist_ok=True)
    requests_figure(sums, squares, args.out / "requests.svg")
    bytes_figure(sums, args.out / "bytes.svg")
    answers_figure(df, sums, args.out / "answers.svg")
    writer_figure(df, sums, args.out / "writer.svg")
    print(tables(df, sums))


if __name__ == "__main__":
    main()
