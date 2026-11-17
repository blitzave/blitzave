"""evals/charts.py draws only what the runs say. Needs the eval group
(`uv run --group eval pytest tests/test_charts.py`); skipped without it."""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("matplotlib")

sys.path.insert(0, str(Path(__file__).parents[1] / "evals"))
import charts
import run

from blitzave.sources import models

SUITE = run.load()


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, system, messages, schema=None):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, default=str)


def _case(case_id):
    return next(case for case in SUITE.cases if case["id"] == case_id)


@pytest.fixture
def run_file(tmp_path):
    """A small run: one pass, one plan with a filter nobody asked for, one
    timeout, and one summary whose writer sentence is kept."""
    stamp = {"provider": "ollama", "model": "scripted", "prompt": "test"}
    results = [
        run.run_case(Scripted({"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW"}}]}), _case("show-tag"), SUITE),
        run.run_case(
            Scripted({"intents": [{"intent": "show_transactions", "filter": {"text": "uber", "account": "A1"}}]}),
            _case("show-merchant"), SUITE,
        ),
        run.run_case(Scripted(models.ModelError("couldn't reach the model at 127.0.0.1:11434")), _case("show-account"), SUITE),
        run.run_case(
            Scripted({"intents": [{"intent": "spending_summary", "category": "Groceries"}]}, "{label} averaged {average}."),
            _case("summary-category"), SUITE,
        ),
    ]
    path = tmp_path / "run.jsonl"
    path.write_text("".join(json.dumps(stamp | r) + "\n" for r in results))
    return path


def test_the_pandas_counts_agree_with_run_tally_and_name_the_mistake(run_file):
    df = charts.load([run_file])
    charts.check_against_tally(df, [run_file])  # raises if they disagree
    sums = charts.summary(df)
    assert sums[["pass", "wrong", "error", "writer_kept"]].iloc[0].tolist() == [2, 1, 1, 1]
    miss = charts.misses(df, SUITE)
    assert miss[["case", "kind", "field"]].values.tolist() == [["show-merchant", "Added a filter nobody asked for", "account"]]


def test_a_case_that_scores_differently_now_stops_the_drawing(run_file):
    lines = [json.loads(line) for line in run_file.read_text().splitlines()]
    lines[1]["why"] = "filter.text is 'uber', expected None"  # as if the case had changed
    run_file.write_text("".join(json.dumps(line) + "\n" for line in lines))
    with pytest.raises(SystemExit, match="scores differently now"):
        charts.misses(charts.load([run_file]), SUITE)


def test_thinking_needs_one_model_run_both_ways(run_file, tmp_path, monkeypatch, capsys):
    lines = [json.loads(line) for line in run_file.read_text().splitlines()]
    off = tmp_path / "off.jsonl"
    off.write_text("".join(json.dumps(line | {"think": "off"}) + "\n" for line in lines))
    monkeypatch.setattr(sys, "argv", ["charts.py", str(run_file), "--thinking", str(run_file), str(off), "--out", str(tmp_path / "f")])
    charts.main()
    assert (tmp_path / "f" / "thinking.svg").exists()
    assert "| scripted | off | 2/4 (50%) |" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["charts.py", str(run_file), "--thinking", str(off), str(off), "--out", str(tmp_path / "g")])
    with pytest.raises(SystemExit, match="one with thinking on and one with it off"):
        charts.main()


def test_the_same_runs_draw_the_same_bytes_in_any_order(run_file, tmp_path, monkeypatch, capsys):
    other = tmp_path / "other.jsonl"  # a second run, all passes but one
    lines = [json.loads(line) for line in run_file.read_text().splitlines()]
    other.write_text("".join(json.dumps(line | {"model": "other"}) + "\n" for line in lines))
    for out, runs in (("one", [run_file, other]), ("two", [other, run_file])):
        monkeypatch.setattr(sys, "argv", ["charts.py", *map(str, runs), "--out", str(tmp_path / out)])
        charts.main()
    drawn = sorted(p.name for p in (tmp_path / "one").iterdir())
    assert drawn == ["by-group.svg", "misses.svg", "outcomes.svg", "seconds.svg"]
    assert all((tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes() for name in drawn)
    assert "| scripted | 2/4 (50%) | 1 | 0 | 1 |" in capsys.readouterr().out
