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
        return models.Reply(reply if isinstance(reply, str) else json.dumps(reply, default=str))


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


class Timed(Scripted):
    """A scripted model whose replies carry usage, as models.Ollama's do."""

    def __init__(self, usage, *replies):
        super().__init__(*replies)
        self.usage = usage

    def chat(self, system, messages, schema=None):
        return models.Reply(super().chat(system, messages, schema).text, self.usage)


OLLAMA_USAGE = {"tokens_in": 1800, "tokens_out": 60, "load_seconds": 0.5, "prompt_seconds": 0.0, "output_seconds": 0.0}


@pytest.fixture
def timed_run(tmp_path):
    """Three cases: two timed by stage, one whose server kept tokens only."""
    tag = {"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW"}}]}
    stamp = {"provider": "ollama", "model": "timed", "prompt": "test"}
    results = [
        run.run_case(Timed(OLLAMA_USAGE, tag), _case("show-tag"), SUITE),
        run.run_case(Timed(OLLAMA_USAGE | {"load_seconds": 1.5}, tag), _case("show-merchant"), SUITE),
        run.run_case(Timed({"tokens_in": 900, "tokens_out": 20}, tag), _case("show-account"), SUITE),
    ]
    path = tmp_path / "timed.jsonl"
    path.write_text("".join(json.dumps(stamp | r) + "\n" for r in results))
    return path


def test_seconds_by_stage_and_tokens_agree_with_run_py(timed_run):
    eff = charts.efficiency(charts.load([timed_run]))
    charts.check_efficiency(eff, [timed_run])  # raises if they disagree
    row = eff.loc[timed_run.name]
    assert row["timed_cases"] == 2
    assert row["load_seconds"] == pytest.approx(1.0)  # the mean of 0.5 and 1.5
    assert (row["tokens_in"], row["tokens_out"]) == (1800, 60)  # the median of 1800, 1800, 900


def test_a_run_without_usage_has_none_to_chart(run_file):
    eff = charts.efficiency(charts.load([run_file]))
    charts.check_efficiency(eff, [run_file])
    assert eff["timed_cases"].sum() == 0 and eff["tokens_in"].isna().all()


def test_seconds_that_disagree_with_run_py_stop_the_drawing(timed_run, monkeypatch):
    eff = charts.efficiency(charts.load([timed_run]))
    monkeypatch.setattr(run, "stage_seconds", lambda result: None)
    with pytest.raises(SystemExit, match="disagree on where the seconds went"):
        charts.check_efficiency(eff, [timed_run])


def test_efficiency_is_drawn_only_from_runs_that_kept_usage(timed_run, run_file, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["charts.py", str(timed_run), "--out", str(tmp_path / "f")])
    charts.main()
    assert (tmp_path / "f" / "efficiency.svg").exists()
    # Loading averaged over the two cases split; scripted calls take no time, so nothing is left for Other.
    assert "| timed | 1.00 | 0.00 | 0.00 | 0.00 | 0.0 | 2/3 | 1,800/60 |" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["charts.py", str(run_file), "--out", str(tmp_path / "g")])
    charts.main()
    assert not (tmp_path / "g" / "efficiency.svg").exists()
    assert "efficiency.svg isn't drawn" in capsys.readouterr().out


def _changed(run_file, tmp_path, name, **edits):
    """The run_file run, with some cases' recorded results replaced."""
    lines = [json.loads(line) for line in run_file.read_text().splitlines()]
    lines = [line | edits.get(line["case"], {}) for line in lines]
    path = tmp_path / name
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def test_flips_agree_with_run_flips_and_are_drawn(run_file, tmp_path, monkeypatch, capsys):
    broke = run.run_case(Scripted({"intents": [{"intent": "decline", "reason": "not_supported"}]}), _case("show-tag"), SUITE)
    fixed = run.run_case(Scripted({"intents": [{"intent": "show_transactions", "filter": {"text": "uber"}}]}), _case("show-merchant"), SUITE)
    new = _changed(run_file, tmp_path, "new.jsonl", **{"show-tag": broke, "show-merchant": fixed})
    table = charts.flip_table(charts.load([run_file]), charts.load([new]), SUITE)
    charts.check_flips(table, run_file, new, SUITE)  # raises if they disagree
    assert dict(zip(table["case"], table["flip"])) == {
        "show-tag": "broke", "show-merchant": "fixed", "show-account": "wrong both", "summary-category": "passed both",
    }
    monkeypatch.setattr(sys, "argv", ["charts.py", "--compare", str(run_file), str(new), "--out", str(tmp_path / "f")])
    charts.main()
    assert [p.name for p in (tmp_path / "f").iterdir()] == ["flips.svg"]
    out = capsys.readouterr().out
    assert "| All | 1 | 1 | 1 | 1 |" in out and "Broke: show-tag" in out and "Fixed: show-merchant" in out


def test_flips_that_disagree_with_todays_key_stop_the_drawing(run_file, tmp_path):
    # Recorded as wrong, though today's key passes the plan: the case changed since.
    old = _changed(run_file, tmp_path, "old.jsonl", **{"show-tag": {"outcome": "wrong", "why": "made up"}})
    table = charts.flip_table(charts.load([old]), charts.load([run_file]), SUITE)
    with pytest.raises(SystemExit, match="planner.toml may have changed"):
        charts.check_flips(table, old, run_file, SUITE)
