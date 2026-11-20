"""Figures for the privacy eval, drawn from its run files and nothing else.

    uv run --group eval python evals/privacy_charts.py evals/runs/privacy/*.jsonl --out DIR

Writes requests.svg, bytes.svg, answers.svg and writer.svg to DIR, and prints
the same numbers as Markdown tables, for the post's text and for readers who
can't see a chart. Same palette, size and helpers as charts.py, and the same
rule: before anything is drawn, the checks must pass, or nothing is written.

- every count agrees with a plain-Python recount of the same files;
- every request in the files is searched again, here, for the household's
  canaries, account numbers and amounts. The run said it found none (or, for
  the leaky control, some). A search that finds one the run didn't is an error.

A square in requests.svg is one request, as the tap recorded it. The SVGs are
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

from blitzave.sources import models

W = charts.WIDTH * 72  # 510 points. One unit is one point, so a square is as big as it looks.
INK, INK_2, MUTED, NEUTRAL = charts.INK, charts.INK_2, charts.MUTED, charts.NEUTRAL
BLUE, ORANGE, AQUA, YELLOW = charts.BLUE, charts.ORANGE, charts.AQUA, charts.YELLOW

PARTS = [  # what a request is made of, in the order it's built
    ("instructions", "Instructions", BLUE),
    ("labels", "Your labels", AQUA),
    ("question", "Your question", ORANGE),
    ("other_messages", "The date", YELLOW),
    ("schema_and_framing", "Plan schema and framing", NEUTRAL),
]
CARDS = [  # what the engine made of a question
    ("transactions", "Listed rows", BLUE),
    ("summary", "Summarized", AQUA),
    ("proposal", "Previewed a change", ORANGE),
    ("clarify", "Asked which you meant", YELLOW),
    ("message", "Said so in its own words", NEUTRAL),
]
SENTENCES = [  # what fill() did with the writer's sentence
    ("kept", "Shown", BLUE),
    ("slot", "Invented a slot", ORANGE),
    ("figure", "Typed a figure or a comparison", YELLOW),
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


def rescan(lines: list[dict], secrets: set[str]) -> list[list[bool]]:
    """For each line of a run, whether each of its requests holds a canary,
    an account number or a seeded amount. Searched here, not read from the run."""
    provider = lines[0]["provider"]
    base_url = models.ANTHROPIC_URL if provider == "anthropic" else models.OLLAMA_URL
    return [[bool(run.leaks([as_request(q)], base_url, secrets, None)) for q in line["requests"]] for line in lines]


def load(paths: list[Path]) -> tuple[pd.DataFrame, dict[str, list[tuple[str, bool]]]]:
    """One row per question (the control left out), and per run the squares of
    requests.svg: (question or control, whether the request holds a canary)."""
    secrets = run.seeded_secrets(run.load_privacy()["household"])
    rows, squares = [], {}
    for path in paths:
        lines = run.read_run(path)
        found = rescan(lines, secrets)
        squares[path.name] = [
            (line["group"] == "control", hit) for line, hits in zip(lines, found) for hit in hits
        ]
        for line, hits in zip(lines, found):
            if line["group"] == "control":
                continue
            rows.append({
                "run": path.name, "model": line["model"], "provider": line["provider"], "group": line["group"],
                "hits": len(line["hits"]), "rescanned": sum(hits), "requests": len(line["requests"]),
                "cards": line.get("cards", []), "kinds": [verdict_kind(w["verdict"]) for w in line["writer"]],
                **{f"bytes_{key}": line["inventory"][key] for key, _, _ in PARTS},
            })
    df = pd.DataFrame(rows)
    df["name"] = df["model"].map(lambda m: charts.MODEL_NAME.get(m, m))
    df["where"] = df["provider"].map(lambda p: charts.WHERE.get(p, p))
    return df, squares


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
        control = next((line for line in lines if line["group"] == "control"), None)
        if control is None or not control["caught"] or not any(hit for is_control, hit in squares[path.name] if is_control):
            raise SystemExit(f"{path.name}: the leaky control wasn't caught, so a clean run proves nothing")


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per run, in the order the runs were named."""
    grouped = df.groupby("run", sort=False)
    out = grouped.agg(
        name=("name", "first"), where=("where", "first"), questions=("group", "size"),
        requests=("requests", "sum"), hits=("hits", "sum"),
    )
    for key, _, _ in PARTS:
        out[key] = grouped[f"bytes_{key}"].median()
    out["wire"] = out[[key for key, _, _ in PARTS]].sum(axis=1)
    return out.reset_index()


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
    """One square per request, a row of them per model. Orange squares are the
    leaky control's, which the check had to catch."""
    cols, x0 = 44, 128
    pitch = (W - 10 - x0) / cols
    size = pitch - 1.7
    top, hits = 64, sum(table["hits"])
    blocks = []
    for _, row in table.iterrows():
        marks = squares[row["run"]]
        real, control = [m for m in marks if not m[0]], [m for m in marks if m[0]]
        blocks.append((row, real, control, math.ceil(len(real) / cols) * pitch + 6 + pitch + 14))
    fig, ax = canvas(top + sum(b[3] for b in blocks) + 4)
    total = int(table["requests"].sum())
    heading(ax, "Every request, one square", (
        f"{total:,} requests from {int(table['questions'].sum())} questions, each searched again for the household's data. "
        f"{'No square holds any.' if not hits else str(hits) + ' did.'}"
    ))
    key(fig, ax, 48, [("A request, clean", BLUE), ("The leaky adapter's, caught", ORANGE)]
        + ([("A canary in a real request", YELLOW)] if any(hit for m in squares.values() for c, hit in m if hit and not c) else []))
    y = top
    for row, real, control, height in blocks:
        model_label(ax, y + pitch / 2, row["name"], row["where"])
        for n, (_, hit) in enumerate(real):
            r, c = divmod(n, cols)
            cx, cy = x0 + c * pitch, y + r * pitch + size / 2
            charts.block(ax, cx, cx + size, cy, size, YELLOW if hit else BLUE, radius_pt=1.2)
        cy = y + math.ceil(len(real) / cols) * pitch + 6
        for n, (_, hit) in enumerate(control):
            cx = x0 + n * pitch
            charts.block(ax, cx, cx + size, cy + size / 2, size, ORANGE if hit else BLUE, radius_pt=1.2)
        ax.text(x0 + len(control) * pitch + 4, cy + size / 2, "the control: a leaky adapter, caught on the wire", fontsize=7.5, color=INK_2, va="center")
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
        "labels {:,.0f} B · question {:,.0f} B · date {:,.0f} B".format(r["labels"], r["question"], r["other_messages"])
        for _, r in sums.iterrows()
    ]
    stacked(sums, values, PARTS, path, (
        "What a request is made of",
        "Median bytes of each part, as sent on the wire. No byte comes from a transaction, a balance or an account.",
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
    df, squares = load(args.runs)
    check(df, args.runs, squares)
    sums = summary(df)
    args.out.mkdir(parents=True, exist_ok=True)
    requests_figure(sums, squares, args.out / "requests.svg")
    bytes_figure(sums, args.out / "bytes.svg")
    answers_figure(df, sums, args.out / "answers.svg")
    writer_figure(df, sums, args.out / "writer.svg")
    print(tables(df, sums))


if __name__ == "__main__":
    main()
