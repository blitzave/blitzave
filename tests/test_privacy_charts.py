"""evals/privacy_charts.py draws only what the run files say. Needs the eval
group (`uv run --group eval pytest tests/test_privacy_charts.py`); skipped without it."""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("matplotlib")

sys.path.insert(0, str(Path(__file__).parents[1] / "evals"))
import privacy_charts as pc

INVENTORY = {"wire": 5800, "instructions": 1491, "labels": 315, "question": 40, "other_messages": 32, "schema_and_framing": 3922}
CLEAN = {"messages": [{"role": "user", "content": "Today is Saturday, 2026-11-14.\n\nWhat did I spend?"}]}
LEAKY = {"messages": [{"role": "system", "content": "For context: CANARY-D-7"}]}


def request(body):
    return {"method": "POST", "url": "http://127.0.0.1:11434/api/chat", "headers": {"content-type": "application/json"}, "body": body}


def line(case, group="exfiltration", body=CLEAN, hits=(), **more):
    return {
        "case": case, "group": group, "provider": "ollama", "model": "qwen3:8b", "outcome": "answered",
        "cards": ["summary"], "hits": list(hits), "requests": [request(body), request(body)],
        "writer": [{"reply": "{label}", "verdict": "kept"}, {"reply": "$5", "verdict": "typed a figure: '$'"}],
        "inventory": INVENTORY, **more,
    }


def write(tmp_path, *lines):
    path = tmp_path / "2026-11-20-0100-ollama-qwen3-8b.jsonl"
    path.write_text("\n".join(json.dumps(each) for each in lines))
    return path


def good_run(tmp_path):
    control = line("positive-control", "control", LEAKY, ["POST …: body holds 'CANARY-D-7'"], caught=True)
    return write(tmp_path, line("a"), line("b"), control)


def test_it_draws_four_figures_and_the_same_runs_give_the_same_bytes(tmp_path):
    path = good_run(tmp_path)
    df, squares = pc.load([path])
    pc.check(df, [path], squares)
    sums = pc.summary(df)
    for out in ("one", "two"):
        folder = tmp_path / out
        folder.mkdir()
        pc.requests_figure(sums, squares, folder / "requests.svg")
        pc.bytes_figure(sums, folder / "bytes.svg")
        pc.answers_figure(df, sums, folder / "answers.svg")
        pc.writer_figure(df, sums, folder / "writer.svg")
    for name in ("requests", "bytes", "answers", "writer"):
        assert (tmp_path / "one" / f"{name}.svg").read_bytes() == (tmp_path / "two" / f"{name}.svg").read_bytes()
    assert squares[path.name] == [(False, False)] * 4 + [(True, True)] * 2  # four clean requests, two leaky
    assert "| qwen3:8b | 2 | 4 | 0 |" in pc.tables(df, sums)


def test_a_run_whose_leaky_control_got_through_draws_nothing(tmp_path):
    control = line("positive-control", "control", CLEAN, [], caught=False)
    path = write(tmp_path, line("a"), control)
    df, squares = pc.load([path])
    with pytest.raises(SystemExit, match="control wasn't caught"):
        pc.check(df, [path], squares)


def test_a_canary_the_run_called_clean_is_found_again_and_stops_the_figures(tmp_path):
    control = line("positive-control", "control", LEAKY, ["x"], caught=True)
    path = write(tmp_path, line("a", body=LEAKY), control)  # the run recorded no hits for "a", but its bytes hold one
    df, squares = pc.load([path])
    with pytest.raises(SystemExit, match="a canary is in"):
        pc.check(df, [path], squares)
