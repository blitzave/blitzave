"""Figures and tables for an eval report, drawn from run files and nothing else.

    uv run --group eval python evals/charts.py RUN.jsonl [RUN.jsonl ...] --out DIR
    uv run --group eval python evals/charts.py --compare OLD.jsonl NEW.jsonl --out DIR

pandas reads the runs and does the counting; matplotlib draws those counts.
Before anything is drawn, the checks must pass, or nothing is written:

- every count agrees with run.py's tally(), which counts the same runs in
  plain Python, without pandas;
- every wrong plan, scored again against today's planner.toml, gives the reason
  its run recorded, so a case edited since the run can't be charted wrongly;
- the seconds by stage and the tokens agree with run.py's stage_seconds() and
  tally(), and the flips with run.flips().

Writes outcomes.svg, by-group.svg, seconds.svg and misses.svg to DIR, and
efficiency.svg when a run kept the server's usage (runs from 2026-11-17 on);
flips.svg with --compare, and thinking.svg with --thinking. It prints the
same numbers as Markdown tables, for the report's text and for readers who
can't see a chart. The SVGs are deterministic: the same runs give the same
bytes, so `git diff` shows whether a figure was redrawn from new data.
"""

import argparse
import math
from collections import Counter
from pathlib import Path

import matplotlib
import pandas as pd
import run  # evals/run.py, beside this file
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle

from blitzave import assistant
from blitzave.sources import models

# The site's own surface and ink (docs/_layouts/post.html), and the first four
# categorical slots of the dataviz reference palette, in its validated order.
SURFACE, INK, INK_2, MUTED, GRID = "#fcfdff", "#10161f", "#677283", "#8792a3", "#e6ecf3"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
RAMP = [  # one hue, light to dark, for a rate
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
    "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]
OUTCOME_COLOR = {"pass": BLUE, "wrong": ORANGE, "no plan": AQUA, "error": YELLOW}
# Where a question's seconds went: three stages in categorical order, and a
# neutral gray for time no stage accounts for. The gray reads as no series.
NEUTRAL = "#aab4c1"
STAGE_COLOR = {"load_seconds": BLUE, "prompt_seconds": ORANGE, "output_seconds": AQUA, "other_seconds": NEUTRAL}
STAGE_NAME = {
    "load_seconds": "Loading the model", "prompt_seconds": "Reading the prompt",
    "output_seconds": "Writing the plan", "other_seconds": "Other",
}
# Two runs, case by case: color only on what changed, gray on what didn't,
# lighter where it passed both times. Every pair checked with the palette
# validator, all pairs, for normal and colorblind vision.
FLIP_COLOR = {"passed both": "#d3dae3", "fixed": BLUE, "broke": ORANGE, "wrong both": MUTED}
FLIP_NAME = {"passed both": "Passed both times", "fixed": "Fixed", "broke": "Broke", "wrong both": "Wrong both times"}
GROUP_NAME = {
    "show": "Show", "tag": "Tag", "category": "File under", "summary": "Summarize",
    "clarify": "Clarify", "decline": "Decline", "multi": "Two steps",
}
MODEL_NAME = {"claude-haiku-5-5": "Claude Haiku 5.5"}
WHERE = {"anthropic": "Anthropic's servers", "ollama": "On this laptop"}
KINDS = [
    "Added a filter nobody asked for",
    "Left out a filter",
    "Got a filter's value wrong",
    "Picked the wrong label or reason",
    "Planned another kind of answer",
    "Planned too many or too few steps",
    "Answered when it should have declined",
    "Declined a fair question",
]
WIDTH = 510 / 72  # 680 CSS pixels, the post's measure

matplotlib.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 9.5,
    "svg.fonttype": "path",  # the text looks the same in every browser
    "svg.hashsalt": "blitzave",  # stable ids, so the same data gives the same file
})


# --- the data ------------------------------------------------------------------------


def load(paths: list[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        frame = pd.read_json(path, lines=True)
        frame["run"] = path.name
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)
    df["name"] = df["model"].map(lambda m: MODEL_NAME.get(m, m))
    if "think" not in df:
        df["think"] = None
    if "writer" not in df:  # a run with no summary case, like one made with --only
        df["writer"] = None
    # Ollama runs from before think was recorded left it to Ollama: on, for qwen3.
    df["where"] = [
        WHERE.get(provider, provider) + (f", thinking {'off' if think == 'off' else 'on'}" if provider == "ollama" else "")
        for provider, think in zip(df["provider"], df["think"])
    ]
    names = df.groupby("run")["name"].first()
    if names.duplicated().any():
        raise SystemExit(f"two runs are both {names[names.duplicated()].iloc[0]}: chart one run per model")
    return df


def runs_in_order(df: pd.DataFrame) -> list[str]:
    """Highest pass rate first, then by file name: the same order whatever
    order the runs were named in, so the same runs draw the same bytes."""
    rate = df["outcome"].eq("pass").groupby(df["run"]).mean()
    return sorted(rate.index, key=lambda name: (-rate[name], name))


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per run: the same counts as run.tally()."""
    by_run = df.groupby("run", sort=False)
    out = by_run["outcome"].value_counts().unstack(fill_value=0).reindex(columns=run.OUTCOMES, fill_value=0)
    out["cases"] = by_run.size()
    out["repaired"] = by_run["repaired"].sum()
    out["median_seconds"] = by_run["seconds"].median()
    out["max_seconds"] = by_run["seconds"].max()
    written = df[df["writer"].notna()]
    kept = written["writer"].map(lambda w: w["verdict"] == "kept")
    out["writer_kept"] = kept.groupby(written["run"]).sum().reindex(out.index, fill_value=0)
    out["writer_total"] = written.groupby("run").size().reindex(out.index, fill_value=0)
    out["name"] = by_run["name"].first()
    out["provider"] = by_run["provider"].first()
    out["where"] = by_run["where"].first()
    out["prompt"] = by_run["prompt"].first()
    return out.loc[runs_in_order(df)]


def by_group(df: pd.DataFrame) -> pd.DataFrame:
    """Passed and asked, per run and group."""
    table = df.assign(passed=df["outcome"].eq("pass")).groupby(["run", "group"], sort=False)["passed"]
    every = pd.MultiIndex.from_product([runs_in_order(df), run.GROUPS], names=["run", "group"])
    return pd.DataFrame({"passed": table.sum(), "cases": table.size()}).reindex(every, fill_value=0)


def check_against_tally(df: pd.DataFrame, paths: list[Path]) -> None:
    """Two counts of the same runs, one with pandas and one without, must agree."""
    sums, groups = summary(df), by_group(df)
    for path in paths:
        mine = sums.loc[path.name]
        theirs = run.tally(run.read_run(path))
        mine_groups = {
            g: (int(groups.loc[(path.name, g), "passed"]), int(groups.loc[(path.name, g), "cases"]))
            if (path.name, g) in groups.index else (0, 0)
            for g in run.GROUPS
        }
        agree = all(int(mine[key]) == theirs[key] for key in ["cases", *run.OUTCOMES, "repaired", "writer_kept", "writer_total"])
        agree &= all(math.isclose(float(mine[k]), theirs[k]) for k in ("median_seconds", "max_seconds"))
        agree &= mine_groups == theirs["groups"]
        if not agree:
            raise SystemExit(f"{path.name}: pandas and run.tally() disagree, so nothing was drawn")


def misses(df: pd.DataFrame, suite: run.Suite) -> pd.DataFrame:
    """One row per wrong plan and kind of mistake it made. Each wrong plan is
    scored again first, and must give the reason its run recorded."""
    cases = {case["id"]: case for case in suite.cases}
    rows = []
    for row in df[df["outcome"] == "wrong"].itertuples():
        case = cases.get(row.case)
        if case is None:
            raise SystemExit(f"{row.run}: case {row.case} isn't in planner.toml anymore")
        plan = assistant.Plan.model_validate(row.plan, context=suite.labels.names())
        if run.mismatch(plan, case["expect"], suite.today) != row.why:
            raise SystemExit(f"{row.run}: {row.case} scores differently now; planner.toml changed since the run")
        for kind, field in kinds_of(run.differences(plan, case["expect"], suite.today)):
            rows.append({"run": row.run, "name": row.name, "case": row.case, "kind": kind, "field": field})
    return pd.DataFrame(rows, columns=["run", "name", "case", "kind", "field"])


def kinds_of(found: list[tuple]) -> set[tuple[str, str | None]]:
    kinds = set()
    for _, key, got, want in found:
        if key == "intents":
            if "decline" in want and "decline" not in got:
                kinds.add((KINDS[6], None))
            elif "decline" in got and "decline" not in want:
                kinds.add((KINDS[7], None))
            elif len(got) != len(want):
                kinds.add((KINDS[5], None))
            else:
                kinds.add((KINDS[4], None))
        elif key.startswith("filter."):
            field = key.removeprefix("filter.")
            if want is None:
                kinds.add((KINDS[0], field))
            elif got is None:
                kinds.add((KINDS[1], field))
            else:
                kinds.add((KINDS[2], field))
        else:
            kinds.add((KINDS[3], key))
    return kinds


def efficiency(df: pd.DataFrame) -> pd.DataFrame:
    """One row per run, in summary() order: the mean seconds a question spent
    in each stage, over the cases whose server timed its stages; how many
    cases that is; the mean seconds over every case; and the median tokens in
    and out, over the cases whose server counted them. NaN where a run kept
    none of it (Claude has no stages; runs before 2026-11-17 kept no usage)."""
    calls = df[["run", "case", "calls"]].explode("calls", ignore_index=True).dropna(subset=["calls"])
    usage = pd.DataFrame([call.get("usage", {}) for call in calls["calls"]], index=calls.index)
    usage = usage.reindex(columns=["tokens_in", "tokens_out", *run.STAGES[:3]])
    calls = calls.join(usage)
    calls["has_usage"] = calls["calls"].map(lambda call: "usage" in call)
    planning = calls[calls["calls"].map(lambda call: call["step"] in ("plan", "repair"))]
    timed = planning.groupby(["run", "case"])[run.STAGES[:3]].sum(min_count=1).dropna(how="all").fillna(0)
    seconds = df.set_index(["run", "case"])["seconds"]
    timed["other_seconds"] = (seconds.reindex(timed.index) - timed.sum(axis=1)).clip(lower=0)
    counted = calls[calls["has_usage"]].fillna({"tokens_in": 0, "tokens_out": 0})
    tokens = counted.groupby(["run", "case"])[["tokens_in", "tokens_out"]].sum()
    order = runs_in_order(df)
    out = timed.groupby(level="run").mean().reindex(index=order, columns=run.STAGES)
    out["timed_cases"] = timed.groupby(level="run").size().reindex(order, fill_value=0)
    out["mean_seconds"] = df.groupby("run")["seconds"].mean()
    medians = tokens.groupby(level="run").median().reindex(order)
    out["tokens_in"], out["tokens_out"] = medians["tokens_in"], medians["tokens_out"]
    return out


def check_efficiency(eff: pd.DataFrame, paths: list[Path]) -> None:
    """pandas' stage means and token medians must match run.py's, counted
    case by case in plain Python."""
    for path in paths:
        results = run.read_run(path)
        splits = [split for split in map(run.stage_seconds, results) if split]
        mine = eff.loc[path.name]
        agree = int(mine["timed_cases"]) == len(splits)
        for stage in run.STAGES:
            theirs = sum(s[stage] for s in splits) / len(splits) if splits else None
            agree &= (theirs is None and pd.isna(mine[stage])) or (theirs is not None and math.isclose(mine[stage], theirs, abs_tol=1e-9))
        theirs = run.tally(results)["tokens"]
        if theirs is None:
            agree &= pd.isna(mine["tokens_in"]) and pd.isna(mine["tokens_out"])
        else:
            agree &= math.isclose(mine["tokens_in"], theirs[0]) and math.isclose(mine["tokens_out"], theirs[1])
        if not agree:
            raise SystemExit(f"{path.name}: pandas and run.py disagree on where the seconds went, so nothing was drawn")


def flip_table(old: pd.DataFrame, new: pd.DataFrame, suite: run.Suite) -> pd.DataFrame:
    """One row per case both runs asked and today's key still has, in
    planner.toml order: its group and what happened to it."""
    order = {case["id"]: n for n, case in enumerate(suite.cases)}
    both = old[["case", "group", "outcome"]].merge(new[["case", "outcome"]], on="case", suffixes=("_old", "_new"))
    both = both[both["case"].isin(order)].sort_values("case", key=lambda ids: ids.map(order), ignore_index=True)
    kind = {(True, True): "passed both", (False, True): "fixed", (True, False): "broke", (False, False): "wrong both"}
    both["flip"] = [kind[(a == "pass", b == "pass")] for a, b in zip(both["outcome_old"], both["outcome_new"])]
    return both


def check_flips(table: pd.DataFrame, old_path: Path, new_path: Path, suite: run.Suite) -> None:
    """pandas' sorting of the cases must match run.flips(), which scores both
    runs again against today's key: a disagreement means a case changed
    since one of the runs."""
    rescored = [{r["case"]: r for r in run.rescore(run.read_run(path), suite)} for path in (old_path, new_path)]
    theirs = run.flips(*rescored, suite)
    for flip in run.FLIPS:
        if table.loc[table["flip"] == flip, "case"].tolist() != theirs[flip]:
            raise SystemExit(
                f"pandas and run.flips() disagree on which cases {flip}, so nothing was drawn: "
                "planner.toml may have changed since one of the runs"
            )


def error_label(df: pd.DataFrame) -> str:
    """"Timed out" only when every error is a call that ran to the timeout."""
    errors = df[df["outcome"] == "error"]
    timed_out = all(
        call.get("error", "").startswith("couldn't reach") and call["seconds"] >= 0.95 * models.TIMEOUT_SECONDS
        for calls in errors["calls"] for call in calls if "error" in call
    )
    return f"Timed out ({models.TIMEOUT_SECONDS} s)" if timed_out else "Model error"


# --- drawing helpers -------------------------------------------------------------------


def new_figure(height_in: float) -> Figure:
    fig = Figure(figsize=(WIDTH, height_in), facecolor=SURFACE)
    FigureCanvasAgg(fig)  # a renderer to measure text with; savefig still writes SVG
    return fig


def title(fig: Figure, text: str, sub: str) -> None:
    fig.text(0.02, 1 - 0.16 / fig.get_figheight(), text, ha="left", va="top", fontsize=11.5, color=INK, weight="bold")
    fig.text(0.02, 1 - 0.40 / fig.get_figheight(), sub, ha="left", va="top", fontsize=9, color=INK_2)


def bare(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.tick_params(length=0, colors=MUTED, labelsize=8.5)


def points_per_unit(ax) -> tuple[float, float]:
    box = ax.get_window_extent()
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    return box.width / abs(x1 - x0), box.height / abs(y1 - y0)


def block(ax, x0: float, x1: float, y: float, h: float, color: str, radius_pt: float = 3, square_left: bool = False) -> None:
    """A bar from x0 to x1 with rounded corners. square_left squares the
    baseline end, so a bar reads as growing from its axis."""
    if x1 <= x0:
        return
    px, py = points_per_unit(ax)
    r = min(radius_pt / px, (x1 - x0) / 2)
    ax.add_patch(FancyBboxPatch(
        (x0, y - h / 2), x1 - x0, h, boxstyle=f"round,pad=0,rounding_size={r}",
        mutation_aspect=px / py, facecolor=color, edgecolor="none",
    ))
    if square_left:
        ax.add_patch(Rectangle((x0, y - h / 2), min(r, x1 - x0), h, facecolor=color, edgecolor="none"))


def ink_on(color: str) -> str:
    """White on a dark fill, near-black on a light one."""
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in to_rgb(color))
    return "#ffffff" if 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.3 else INK


def model_labels(fig: Figure, ax, ys: list[float], sums: pd.DataFrame, x: float = 0.02) -> None:
    for y, (_, row) in zip(ys, sums.iterrows()):
        _, fy = fig.transFigure.inverted().transform(ax.transData.transform((0, y)))
        fig.text(x, fy + 0.012, row["name"], ha="left", va="bottom", fontsize=9.5, color=INK)
        fig.text(x, fy - 0.012, row["where"], ha="left", va="top", fontsize=8, color=INK_2)


def legend(fig: Figure, y_in: float, items: list[tuple[str, str]], marker: str = "square") -> None:
    x = 0.02
    y = 1 - y_in / fig.get_figheight()
    for label, color in items:
        if marker == "square":
            fig.patches.append(FancyBboxPatch(
                (x, y - 0.012), 0.016, 0.024 * fig.get_figwidth() / fig.get_figheight() / 2.4,
                boxstyle="round,pad=0,rounding_size=0.003", transform=fig.transFigure, facecolor=color, edgecolor="none",
            ))
        else:
            fig.lines.append(matplotlib.lines.Line2D(
                [x + 0.008], [y], transform=fig.transFigure, marker="o", markersize=6,
                markerfacecolor=color, markeredgecolor=SURFACE, markeredgewidth=1, linestyle="none",
            ))
        text = fig.text(x + 0.024, y, label, ha="left", va="center", fontsize=8.5, color=INK_2)
        width = text.get_window_extent(renderer=fig.canvas.get_renderer()).width / (fig.get_figwidth() * fig.dpi)
        x += 0.024 + width + 0.03


def save(fig: Figure, path: Path) -> None:
    fig.savefig(path, format="svg", facecolor=SURFACE, metadata={"Date": None})


# --- the figures -----------------------------------------------------------------------


def outcomes_figure(df: pd.DataFrame, sums: pd.DataFrame, path: Path) -> None:
    """One tall segmented bar per model: where its plans landed."""
    n = len(sums)
    fig = new_figure(1.25 + 0.55 * n)
    title(fig, "Where each model's plans landed", "Every case, by outcome. A case passes when the plan matches the answer key exactly.")
    present = [o for o in run.OUTCOMES if sums[o].sum() > 0]
    names = {"pass": "Passed", "wrong": "Wrong plan", "no plan": "No plan after a repair", "error": error_label(df)}
    legend(fig, 0.72, [(names[o], OUTCOME_COLOR[o]) for o in present])
    top = 1 - 0.95 / fig.get_figheight()
    ax = fig.add_axes((0.25, 0.04, 0.63, top - 0.04))
    bare(ax)
    ax.set_xticks([])
    ax.set_yticks([])
    cases = int(sums["cases"].max())
    ax.set_xlim(0, cases)
    ax.set_ylim(n - 0.5, -0.5)
    px, _ = points_per_unit(ax)
    gap = 1.5 / px  # the surface gap between parts
    bar_h = 20 / points_per_unit(ax)[1]  # 20 pt, about 27 CSS px: a tall segmented bar
    for y, (_, row) in enumerate(sums.iterrows()):
        x = 0.0
        for outcome in present:
            count = int(row[outcome])
            if count == 0:
                continue
            block(ax, x + (gap / 2 if x > 0 else 0), x + count - (gap / 2 if x + count < row["cases"] else 0), y, bar_h, OUTCOME_COLOR[outcome])
            label = str(count)
            if count * px >= 7 * len(label) + 8:  # only inside when it fits with room
                ax.text(x + 0.35 * (1 / px) * 10, y, label, ha="left", va="center", fontsize=8.5, color=ink_on(OUTCOME_COLOR[outcome]))
            x += count
        share = row["pass"] / row["cases"]
        ax.text(cases + 0.6 / px * 10, y, f"{share:.0%}", ha="left", va="center", fontsize=10, color=INK, weight="bold")
        ax.text(cases + 0.6 / px * 10, y + 0.32, f"of {int(row['cases'])}", ha="left", va="center", fontsize=7.5, color=INK_2)
    model_labels(fig, ax, list(range(n)), sums)
    save(fig, path)


def group_figure(df: pd.DataFrame, sums: pd.DataFrame, path: Path) -> None:
    """Pass rate by kind of question: models down, groups across."""
    groups = by_group(df)
    n = len(sums)
    fig = new_figure(1.35 + 0.5 * n)
    title(fig, "Pass rate by kind of question", "Cells show passed / asked. Darker is a higher pass rate.")
    top = 1 - 1.05 / fig.get_figheight()
    ax = fig.add_axes((0.25, 0.04, 0.73, top - 0.04))
    bare(ax)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(-0.5, len(run.GROUPS) - 0.5)
    ax.set_ylim(n - 0.5, -0.5)
    cmap = LinearSegmentedColormap.from_list("rate", RAMP)
    px, py = points_per_unit(ax)
    gx, gy = 2 / px, 2 / py
    for col, group in enumerate(run.GROUPS):
        asked = int(df[df["group"] == group]["case"].nunique())
        # Baseline-aligned, so "Tag" and "Show" sit on the same line.
        ax.text(col, -0.5 - 9 / py, f"{asked} cases", ha="center", va="baseline", fontsize=7.5, color=INK_2)
        ax.text(col, -0.5 - 22 / py, GROUP_NAME[group], ha="center", va="baseline", fontsize=8.5, color=INK)
        for y, run_name in enumerate(sums.index):
            passed, cases = groups.loc[(run_name, group)] if (run_name, group) in groups.index else (0, 0)
            if cases == 0:
                continue
            color = matplotlib.colors.to_hex(cmap(passed / cases))
            ax.add_patch(FancyBboxPatch(
                (col - 0.5 + gx / 2, y - 0.5 + gy / 2), 1 - gx, 1 - gy,
                boxstyle=f"round,pad=0,rounding_size={3 / px}", mutation_aspect=px / py, facecolor=color, edgecolor="none",
            ))
            ax.text(col, y, f"{int(passed)}/{int(cases)}", ha="center", va="center", fontsize=9, color=ink_on(color))
    model_labels(fig, ax, list(range(n)), sums)
    save(fig, path)


def seconds_figure(df: pd.DataFrame, sums: pd.DataFrame, path: Path) -> None:
    """How long each plan took, one dot per case, on a log scale."""
    n = len(sums)
    fig = new_figure(1.45 + 0.6 * n)
    title(fig, "Seconds to a plan", "One dot per case, repair included. Log scale. The line is where Goose stops waiting.")
    legend(fig, 0.72, [("Passed", BLUE), ("Didn't pass", ORANGE)], marker="dot")
    top = 1 - 0.95 / fig.get_figheight()
    ax = fig.add_axes((0.25, 0.16, 0.72, top - 0.16))
    bare(ax)
    ax.set_xscale("log")
    floor = 0.1  # a log axis has no zero; nothing real is that fast
    low, high = max(df["seconds"].min(), floor), max(df["seconds"].max(), models.TIMEOUT_SECONDS)
    ax.set_xlim(low / 1.5, high * 1.6)
    ax.set_ylim(n - 0.5, -0.5)
    ticks = [t for t in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500) if low / 1.5 <= t <= high * 1.6]
    ax.set_xticks(ticks, [f"{t:g} s" for t in ticks])
    ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.set_yticks([])
    for t in ticks:
        ax.axvline(t, color=GRID, linewidth=0.75, zorder=0)
    ax.axvline(models.TIMEOUT_SECONDS, color=INK_2, linewidth=1, zorder=1)
    ax.text(models.TIMEOUT_SECONDS * 1.05, -0.47, f"{models.TIMEOUT_SECONDS} s", ha="left", va="top", fontsize=8, color=INK_2)
    for y, run_name in enumerate(sums.index):
        rows = df[df["run"] == run_name].reset_index(drop=True)
        # Spread the dots up and down by a fixed pattern, not a random one,
        # so the same runs always draw the same figure.
        offsets = [((i * 7) % 11 / 10 - 0.5) * 0.5 for i in range(len(rows))]
        for passed, color in ((False, ORANGE), (True, BLUE)):
            pick = rows["outcome"].eq("pass") == passed
            ax.plot(
                rows.loc[pick, "seconds"].clip(lower=floor), [y + offsets[i] for i in rows.index[pick]], "o",
                markersize=5.5, markerfacecolor=color, markeredgecolor=SURFACE, markeredgewidth=1, alpha=0.9, zorder=3,
            )
        median = sums.loc[run_name, "median_seconds"]
        ax.plot([median, median], [y - 0.36, y + 0.36], color=INK, linewidth=2, solid_capstyle="round", zorder=4)
        ax.text(median, y - 0.4, f"median {median:.1f} s", ha="center", va="bottom", fontsize=8, color=INK)
    model_labels(fig, ax, list(range(n)), sums)
    save(fig, path)


def misses_figure(miss: pd.DataFrame, sums: pd.DataFrame, path: Path) -> None:
    """Small multiples: for each model, how many wrong plans made each kind of mistake."""
    counts = miss.drop_duplicates(["run", "case", "kind"]).groupby(["run", "kind"]).size()
    kinds = [k for k in KINDS if k in set(miss["kind"])]
    n = len(sums)
    fig = new_figure(1.15 + 0.27 * len(kinds))
    title(fig, "What the wrong plans got wrong", "Wrong plans with each kind of mistake. One plan can make more than one.")
    top = 1 - 1.05 / fig.get_figheight()
    left, right = 0.36, 0.98
    width = (right - left) / n
    most = max(int(counts.max()) if len(counts) else 1, 1)
    for i, run_name in enumerate(sums.index):
        ax = fig.add_axes((left + i * width + 0.012, 0.04, width - 0.03, top - 0.04))
        bare(ax)
        ax.set_xlim(0, most * 1.25)
        ax.set_ylim(len(kinds) - 0.5, -0.5)
        ax.set_xticks([])
        ax.set_yticks(range(len(kinds)), kinds if i == 0 else [""] * len(kinds), fontsize=8.5, color=INK)
        ax.axvline(0, color=GRID, linewidth=1)
        wrong = int(sums.loc[run_name, "wrong"])
        ax.set_title(f"{sums.loc[run_name, 'name']}\n", fontsize=9, color=INK, loc="left", pad=2)
        ax.text(0, -0.5, f"{wrong} wrong plan{'s' if wrong != 1 else ''}", ha="left", va="bottom", fontsize=7.5, color=INK_2, transform=ax.transData)
        _, py = points_per_unit(ax)
        for y, kind in enumerate(kinds):
            count = int(counts.get((run_name, kind), 0))
            block(ax, 0, count, y, 9 / py, BLUE, radius_pt=2.5, square_left=True)
            ax.text(count + most * 0.04, y, str(count), ha="left", va="center", fontsize=8, color=INK if count else MUTED)
    save(fig, path)


def thinking_figure(pair: pd.DataFrame, path: Path) -> None:
    """One model, thinking on and off: what it buys and what it costs. Two
    panels, one measure each, never two scales on one axis."""
    sums = summary(pair)
    model = sums["name"].iloc[0]
    state = {r: sums.loc[r, "where"].rsplit(" ", 1)[-1] for r in sums.index}  # "on" or "off"
    if len(sums) != 2 or sums["name"].nunique() != 1 or set(state.values()) != {"on", "off"}:
        raise SystemExit("--thinking takes two runs of one model, one with thinking on and one with it off")
    rows = sorted(sums.index, key=lambda r: state[r] == "off")  # on first
    labels = [f"Thinking {state[r]}" for r in rows]
    fig = new_figure(1.9)
    title(fig, f"{model}, thinking on and off", "The same cases and prompts. Thinking buys plans and costs time.")
    top = 1 - 0.8 / fig.get_figheight()
    panels = [
        ("Plans passed", [sums.loc[r, "pass"] / sums.loc[r, "cases"] for r in rows],
         [f"{int(sums.loc[r, 'pass'])}/{int(sums.loc[r, 'cases'])} ({sums.loc[r, 'pass'] / sums.loc[r, 'cases']:.0%})" for r in rows]),
        ("Median seconds to a plan", [sums.loc[r, "median_seconds"] for r in rows],
         [f"{sums.loc[r, 'median_seconds']:.1f} s" for r in rows]),
    ]
    for i, (name, values, texts) in enumerate(panels):
        ax = fig.add_axes((0.2 + i * 0.4, 0.06, 0.3, top - 0.16))
        bare(ax)
        most = max(values) or 1
        ax.set_xlim(0, most * 1.55)
        ax.set_ylim(1.5, -0.5)
        ax.set_xticks([])
        ax.set_yticks([0, 1], labels if i == 0 else ["", ""], fontsize=9, color=INK)
        ax.axvline(0, color=GRID, linewidth=1)
        ax.set_title(name, fontsize=9, color=INK, loc="left", pad=4)
        _, py = points_per_unit(ax)
        for y, (value, text) in enumerate(zip(values, texts)):
            block(ax, 0, value, y, 14 / py, BLUE, radius_pt=3, square_left=True)
            ax.text(value + most * 0.05, y, text, ha="left", va="center", fontsize=8.5, color=INK)
    save(fig, path)


def efficiency_figure(eff: pd.DataFrame, sums: pd.DataFrame, path: Path) -> None:
    """One stacked bar per model on a shared axis: the average seconds a
    question spent planning, by stage. A run with no stage timings is one
    gray bar. Tokens sit in their own column: a different measure, so never
    a second scale on the same axis."""
    n = len(sums)
    notes = []  # under the axis, so a long one never runs into the tokens column
    for r in sums.index:
        timed, cases = int(eff.loc[r, "timed_cases"]), int(sums.loc[r, "cases"])
        if timed == 0:
            notes.append(f"{sums.loc[r, 'name']}: no stage timings kept, so its bar is one gray average.")
        elif timed < cases:
            notes.append(f"{sums.loc[r, 'name']}: stages timed on {timed} of {cases} cases; its bar averages those.")
    foot = 0.18 * len(notes)
    fig = new_figure(1.55 + 0.6 * n + foot)
    title(fig, "Where a question's seconds go", "Average seconds to a plan, by the model server's own timings. Gray is time no stage accounts for.")
    legend(fig, 0.72, [(STAGE_NAME[s], STAGE_COLOR[s]) for s in run.STAGES])
    top = 1 - 1.0 / fig.get_figheight()
    bottom = (0.5 + foot) / fig.get_figheight()
    ax = fig.add_axes((0.25, bottom, 0.52, top - bottom))
    for i, note in enumerate(notes):
        fig.text(0.02, (0.12 + 0.18 * (len(notes) - 1 - i)) / fig.get_figheight(), note, ha="left", va="bottom", fontsize=8, color=INK_2)
    bare(ax)
    totals = {
        r: eff.loc[r, run.STAGES].sum() if eff.loc[r, "timed_cases"] else eff.loc[r, "mean_seconds"] for r in sums.index
    }
    most = max(totals.values()) or 1
    ax.set_xlim(0, most * 1.3)
    ax.set_ylim(n - 0.5, -0.5)
    ticks = [t for t in matplotlib.ticker.MaxNLocator(nbins=4, steps=[1, 2, 5, 10]).tick_values(0, most) if 0 <= t <= most * 1.3]
    ax.set_xticks(ticks, [f"{t:g} s" for t in ticks])
    ax.set_yticks([])
    for t in ticks:
        ax.axvline(t, color=GRID, linewidth=0.75, zorder=0)
    px, py = points_per_unit(ax)
    gap, bar_h = 1.5 / px, 14 / py
    fig.text(0.8, 1 - 1.0 / fig.get_figheight(), "Median tokens\nin · out", ha="left", va="bottom", fontsize=8, color=INK_2)
    for y, r in enumerate(sums.index):
        row = eff.loc[r]
        timed = int(row["timed_cases"])
        parts = [(s, row[s]) for s in run.STAGES] if timed else [("other_seconds", row["mean_seconds"])]
        parts = [(s, v) for s, v in parts if v > 0]
        x = 0.0
        for i, (stage, value) in enumerate(parts):
            last = i == len(parts) - 1
            x0, x1 = x + (gap / 2 if i else 0), x + value - (0 if last else gap / 2)
            if last:  # the data end is rounded; the baseline and inner joins are square
                block(ax, x0, x1, y, bar_h, STAGE_COLOR[stage], square_left=True)
            elif x1 > x0:
                ax.add_patch(Rectangle((x0, y - bar_h / 2), x1 - x0, bar_h, facecolor=STAGE_COLOR[stage], edgecolor="none"))
            x += value
        ax.text(totals[r] + most * 0.03, y, f"{totals[r]:.1f} s", ha="left", va="center", fontsize=8.5, color=INK)
        _, fy = fig.transFigure.inverted().transform(ax.transData.transform((0, y)))
        used = "not kept" if pd.isna(row["tokens_in"]) else f"{row['tokens_in']:,.0f} · {row['tokens_out']:,.0f}"
        fig.text(0.8, fy, used, ha="left", va="center", fontsize=8.5, color=INK if used != "not kept" else MUTED)
    model_labels(fig, ax, list(range(n)), sums)
    save(fig, path)


def flips_figure(table: pd.DataFrame, differs: list[str], path: Path) -> None:
    """Two runs, one square per case both asked, a row per group in
    planner.toml order: color on what changed, gray on what didn't."""
    groups = [g for g in run.GROUPS if table["group"].eq(g).any()]
    widest = max(int(table["group"].eq(g).sum()) for g in groups)
    fig = new_figure(1.3 + 0.3 * len(groups))
    if not differs:
        sub = "Nothing recorded differs between the runs"
    elif len(differs) == 1:
        sub = differs[0]
    else:
        sub = "They differ in " + ", ".join(line.split(":", 1)[0] for line in differs)
    title(fig, "What changed between two runs", f"{sub}. One square per case both asked, scored against today's answer key.")
    counts = table["flip"].value_counts()
    legend(fig, 0.72, [(f"{FLIP_NAME[f]} ({int(counts.get(f, 0))})", FLIP_COLOR[f]) for f in run.FLIPS])
    top = 1 - 1.0 / fig.get_figheight()
    ax = fig.add_axes((0.2, 0.04, 0.58, top - 0.04))
    bare(ax)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(-0.5, widest - 0.5)
    ax.set_ylim(len(groups) - 0.5, -0.5)
    px, py = points_per_unit(ax)
    side = min(12, 0.8 * px, 0.8 * py)  # points
    for y, group in enumerate(groups):
        rows = table[table["group"] == group].reset_index(drop=True)
        for x, flip in enumerate(rows["flip"]):
            ax.add_patch(FancyBboxPatch(
                (x - side / px / 2, y - side / py / 2), side / px, side / py,
                boxstyle=f"round,pad=0,rounding_size={2.5 / px}", mutation_aspect=px / py,
                facecolor=FLIP_COLOR[flip], edgecolor="none",
            ))
        _, fy = fig.transFigure.inverted().transform(ax.transData.transform((0, y)))
        fig.text(0.02, fy, GROUP_NAME[group], ha="left", va="center", fontsize=9, color=INK)
        changed = [f"{name.lower()} {int(rows['flip'].eq(f).sum())}" for f, name in (("fixed", "Fixed"), ("broke", "Broke")) if rows["flip"].eq(f).any()]
        fig.text(0.8, fy, ", ".join(changed) or "no change", ha="left", va="center", fontsize=8.5, color=INK if changed else MUTED)
    save(fig, path)


# --- the tables --------------------------------------------------------------------------


def efficiency_table(eff: pd.DataFrame, sums: pd.DataFrame) -> str:
    out = [
        "\n| Model | Loading s | Reading s | Writing s | Other s | Seconds a question | Cases split | Median tokens in/out |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sums.index:
        row = eff.loc[r]
        stages = [f"{row[s]:.2f}" if row["timed_cases"] else "not kept" for s in run.STAGES]
        used = "not kept" if pd.isna(row["tokens_in"]) else f"{row['tokens_in']:,.0f}/{row['tokens_out']:,.0f}"
        out.append(
            f"| {sums.loc[r, 'name']} | {' | '.join(stages)} | {row['mean_seconds']:.1f} "
            f"| {int(row['timed_cases'])}/{int(sums.loc[r, 'cases'])} | {used} |"
        )
    return "\n".join(out)


def flips_tables(table: pd.DataFrame) -> str:
    out = ["\n| Group | " + " | ".join(FLIP_NAME[f] for f in run.FLIPS) + " |", "|---|" + "---|" * len(run.FLIPS)]
    for group in [g for g in run.GROUPS if table["group"].eq(g).any()] + [None]:
        rows = table if group is None else table[table["group"] == group]
        name = "All" if group is None else GROUP_NAME[group]
        out.append(f"| {name} | " + " | ".join(str(int(rows["flip"].eq(f).sum())) for f in run.FLIPS) + " |")
    for flip in ("fixed", "broke"):
        cases = table.loc[table["flip"] == flip, "case"].tolist()
        out.append(f"\n{FLIP_NAME[flip]}: " + (", ".join(cases) if cases else "none"))
    return "\n".join(out)


def tables(df: pd.DataFrame, sums: pd.DataFrame, miss: pd.DataFrame) -> str:
    out = ["| Model | Passed | Wrong plan | No plan | Timed out | Repaired | Median s | Slowest s | Writer kept |",
           "|---|---|---|---|---|---|---|---|---|"]
    for _, r in sums.iterrows():
        out.append(
            f"| {r['name']} | {int(r['pass'])}/{int(r['cases'])} ({r['pass'] / r['cases']:.0%}) | {int(r['wrong'])} "
            f"| {int(r['no plan'])} | {int(r['error'])} | {int(r['repaired'])} | {r['median_seconds']:.1f} "
            f"| {r['max_seconds']:.1f} | {int(r['writer_kept'])}/{int(r['writer_total'])} |"
        )
    groups = by_group(df)
    out += ["", "| Model | " + " | ".join(GROUP_NAME[g] for g in run.GROUPS) + " |", "|---|" + "---|" * len(run.GROUPS)]
    for run_name, r in sums.iterrows():
        cells = [f"{int(groups.loc[(run_name, g), 'passed'])}/{int(groups.loc[(run_name, g), 'cases'])}" for g in run.GROUPS]
        out.append(f"| {r['name']} | " + " | ".join(cells) + " |")
    kinds = [k for k in KINDS if k in set(miss["kind"])]
    counts = miss.drop_duplicates(["run", "case", "kind"]).groupby(["run", "kind"]).size()
    out += ["", "| Kind of mistake | " + " | ".join(sums["name"]) + " |", "|---|" + "---|" * len(sums)]
    for kind in kinds:
        out.append(f"| {kind} | " + " | ".join(str(int(counts.get((r, kind), 0))) for r in sums.index) + " |")
    added = miss[miss["kind"] == KINDS[0]]
    out += ["", "Filters added that nobody asked for, by field:"]
    for run_name, r in sums.iterrows():
        fields = Counter(added[added["run"] == run_name]["field"])
        out.append(f"- {r['name']}: " + (", ".join(f"{f} {c}" for f, c in fields.most_common()) or "none"))
    out += ["", "Writer sentences refused:"]
    for run_name, r in sums.iterrows():
        written = df[(df["run"] == run_name) & df["writer"].notna()]["writer"]
        refused = Counter(w["verdict"].split(":")[0] for w in written if w["verdict"] != "kept")
        out.append(f"- {r['name']}: " + (", ".join(f"{k} {c}" for k, c in refused.most_common()) or "none"))
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Figures and tables for an eval report.")
    parser.add_argument("runs", nargs="*", type=Path, help="one run per model")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--thinking", nargs=2, type=Path, metavar=("ON", "OFF"),
        help="also draw thinking.svg from two runs of one model, thinking on and off",
    )
    parser.add_argument(
        "--compare", nargs=2, type=Path, metavar=("OLD", "NEW"),
        help="also draw flips.svg: two runs, case by case, what changed",
    )
    args = parser.parse_args()
    if not (args.runs or args.compare):
        parser.error("name some runs, or --compare two")
    args.out.mkdir(parents=True, exist_ok=True)
    suite = run.load()
    if args.compare:
        old_path, new_path = args.compare
        table = flip_table(load([old_path]), load([new_path]), suite)  # one at a time: they may share a name
        check_flips(table, old_path, new_path, suite)
        differs = run.stamp_differences(run.read_run(old_path)[0], run.read_run(new_path)[0], run.prompt_log())
        flips_figure(table, differs, args.out / "flips.svg")
        print(flips_tables(table))
    if not args.runs:
        return
    df = load(args.runs)
    check_against_tally(df, args.runs)
    miss = misses(df, suite)
    sums = summary(df)
    eff = efficiency(df)
    check_efficiency(eff, args.runs)
    outcomes_figure(df, sums, args.out / "outcomes.svg")
    group_figure(df, sums, args.out / "by-group.svg")
    seconds_figure(df, sums, args.out / "seconds.svg")
    misses_figure(miss, sums, args.out / "misses.svg")
    print(tables(df, sums, miss))
    if eff["timed_cases"].sum() or eff["tokens_in"].notna().any():
        efficiency_figure(eff, sums, args.out / "efficiency.svg")
        print(efficiency_table(eff, sums))
    else:
        print("\nefficiency.svg isn't drawn: none of these runs kept usage (runs from before 2026-11-17 didn't).")
    if args.thinking:
        frames = [load([path]) for path in args.thinking]  # one at a time: they share a name
        for frame, path in zip(frames, args.thinking):
            check_against_tally(frame, [path])
        pair = pd.concat(frames, ignore_index=True)
        thinking_figure(pair, args.out / "thinking.svg")
        print("\n| Model | Thinking | Passed | Wrong plan | No plan | Timed out | Median s |\n|---|---|---|---|---|---|---|")
        for _, r in summary(pair).iterrows():
            print(
                f"| {r['name']} | {r['where'].rsplit(' ', 1)[-1]} | {int(r['pass'])}/{int(r['cases'])} "
                f"({r['pass'] / r['cases']:.0%}) | {int(r['wrong'])} | {int(r['no plan'])} | {int(r['error'])} "
                f"| {r['median_seconds']:.1f} |"
            )


if __name__ == "__main__":
    main()
