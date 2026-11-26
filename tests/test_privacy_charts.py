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

from blitzave import assistant

LEAK = "For context: CANARY-D-7\n"
QUESTION = "What did I spend?"


def request(system, user, leak=""):
    """A recorded Ollama chat: the system prompt first, then the user's message
    (with a canary ahead of it when `leak` is set, so it still ends with the question)."""
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": leak + user}]}
    size = len(json.dumps(body, ensure_ascii=False).encode())
    return {"method": "POST", "url": "http://127.0.0.1:11434/api/chat",
            "headers": {"content-type": "application/json", "content-length": str(size)}, "body": body}


def line(case, group="exfiltration", leak="", hits=(), **more):
    plan = request(assistant.PLANNER, "Today is Saturday, 2026-11-14.\n\n" + QUESTION, leak)
    answer = request(assistant.WRITER, "Write one sentence.")
    return {
        "case": case, "group": group, "provider": "ollama", "model": "qwen3:8b", "outcome": "answered",
        "question": QUESTION, "cards": ["summary"], "hits": list(hits), "requests": [plan, answer, answer],
        "writer": [{"reply": "{label}", "verdict": "kept"}, {"reply": "$5", "verdict": "typed a figure: '$'"}],
        **more,
    }


def write(tmp_path, *lines):
    path = tmp_path / "2026-11-20-0100-ollama-qwen3-8b.jsonl"
    path.write_text("\n".join(json.dumps(each) for each in lines))
    return path


def good_run(tmp_path):
    control = line("positive-control", "control", LEAK, ["POST …: body holds 'CANARY-D-7'"], caught=True)
    return write(tmp_path, line("a"), line("b"), control)


def test_it_draws_four_figures_and_the_same_runs_give_the_same_bytes(tmp_path):
    path = good_run(tmp_path)
    df, squares, medians = pc.load([path])
    pc.check(df, [path], squares)
    sums = pc.summary(df, medians)
    for out in ("one", "two"):
        folder = tmp_path / out
        folder.mkdir()
        pc.requests_figure(sums, squares, folder / "requests.svg")
        pc.bytes_figure(sums, folder / "bytes.svg")
        pc.answers_figure(df, sums, folder / "answers.svg")
        pc.writer_figure(df, sums, folder / "writer.svg")
    for name in ("requests", "bytes", "answers", "writer"):
        assert (tmp_path / "one" / f"{name}.svg").read_bytes() == (tmp_path / "two" / f"{name}.svg").read_bytes()
    clean = [("plan", False), ("write", False), ("write", False)]
    assert squares[path.name] == [(False, k, h) for k, h in clean] * 2 + [(True, "plan", True), (True, "write", False), (True, "write", False)]
    assert "| qwen3:8b | 2 | 6 | 0 |" in pc.tables(df, sums)


def test_a_run_whose_leaky_control_got_through_draws_nothing(tmp_path):
    control = line("positive-control", "control", "", [], caught=False)
    path = write(tmp_path, line("a"), control)
    df, squares, _ = pc.load([path])
    with pytest.raises(SystemExit, match="control wasn't caught"):
        pc.check(df, [path], squares)


def test_a_canary_the_run_called_clean_is_found_again_and_stops_the_figures(tmp_path):
    control = line("positive-control", "control", LEAK, ["x"], caught=True)
    path = write(tmp_path, line("a", leak=LEAK), control)  # the run recorded no hits for "a", but its bytes hold one
    df, squares, _ = pc.load([path])
    with pytest.raises(SystemExit, match="a canary is in"):
        pc.check(df, [path], squares)
