"""evals/run.py's cases and scoring, against scripted models. The eval itself
asks a real model and is run by hand; these keep it honest in CI."""

import importlib.util
import json
from pathlib import Path

import pytest

from blitzave.sources import models

spec = importlib.util.spec_from_file_location("evals_run", Path(__file__).parents[1] / "evals" / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

SUITE = run.load()  # also proves every case's expected plan fits the grammar


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, system, messages, schema=None):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, default=str)


def _case(case_id: str) -> dict:
    return next(case for case in SUITE.cases if case["id"] == case_id)


@pytest.mark.parametrize("case", SUITE.cases, ids=lambda case: case["id"])
def test_a_planner_that_answers_as_expected_passes_every_case(case):
    model = Scripted({"intents": run.first_choice(case["expect"])}, "{label} averaged {average}.")
    result = run.run_case(model, case, SUITE)
    assert result["outcome"] == "pass", result.get("why")
    assert result["repaired"] is False
    if case["group"] == "summary":
        assert result["writer"]["verdict"] == "kept"


def test_a_filter_nobody_asked_for_fails_the_case():
    plan = {"intents": [{"intent": "show_transactions", "filter": {"text": "Uber", "category": "Transport"}}]}
    result = run.run_case(Scripted(plan), _case("show-merchant"), SUITE)
    assert (result["outcome"], result["why"]) == ("wrong", "filter.category is 'Transport', expected None")


def test_an_end_of_today_counts_as_no_end_and_months_are_the_models_pick():
    show = {"intents": [{"intent": "show_transactions", "filter": {"category": "Coffee", "start": "2026-09-01", "end": "2026-11-15"}}]}
    assert run.run_case(Scripted(show), _case("show-since"), SUITE)["outcome"] == "pass"
    summary = {"intents": [{"intent": "spending_summary", "category": "Groceries", "months": 3}]}
    assert run.run_case(Scripted(summary, "No."), _case("summary-category"), SUITE)["outcome"] == "pass"


def test_clarify_passes_when_the_expected_labels_are_among_the_options():
    extra = {"intents": [{"intent": "clarify", "field": "category", "options": ["Groceries", "Dining", "Coffee"]}]}
    assert run.run_case(Scripted(extra), _case("clarify-food"), SUITE)["outcome"] == "pass"
    short = {"intents": [{"intent": "clarify", "field": "category", "options": ["Groceries", "Coffee"]}]}
    assert run.run_case(Scripted(short), _case("clarify-food"), SUITE)["outcome"] == "wrong"


def test_a_repair_that_fits_passes_and_is_counted():
    result = run.run_case(
        Scripted('{"intents": []}', {"intents": [{"intent": "decline", "reason": "investment_advice"}]}),
        _case("decline-index-funds"),
        SUITE,
    )
    assert (result["outcome"], result["repaired"]) == ("pass", True)
    assert [call["step"] for call in result["calls"]] == ["plan", "repair"]
    assert all("system" not in call for call in result["calls"])


def test_a_slow_model_is_an_error_not_a_wrong_plan():
    timeout = models.ModelError("couldn't reach the model at 127.0.0.1:11434")
    result = run.run_case(Scripted(timeout), _case("show-tag"), SUITE)
    assert (result["outcome"], result["why"]) == ("error", "couldn't reach the model at 127.0.0.1:11434")


def test_empty_replies_are_the_servers_failure_not_a_plan_the_model_missed():
    result = run.run_case(Scripted("", ""), _case("decline-index-funds"), SUITE)
    assert (result["outcome"], result["why"]) == ("error", run.EMPTY)


class Adapter:
    """Stands in for models.Ollama in run.main(), with scripted replies."""

    reply = ""

    def __init__(self, model, base_url="http://127.0.0.1:11434"):
        self.model, self.base_url = model, base_url

    def chat(self, system, messages, schema=None):
        return self.reply


def test_a_server_that_sends_back_nothing_stops_the_run_and_leaves_it_partial(tmp_path, monkeypatch):
    monkeypatch.setitem(models.ADAPTERS, "ollama", Adapter)
    monkeypatch.setattr(run, "RUNS", tmp_path)
    with pytest.raises(SystemExit, match="model server looks unwell"):
        run.main(["ollama", "silent"])
    [partial] = tmp_path.iterdir()
    assert partial.name.endswith(".jsonl.partial")  # so runs/*.jsonl never picks it up
    assert [json.loads(line)["outcome"] for line in partial.read_text().splitlines()] == ["error"] * 3


def test_a_finished_run_loses_its_partial_suffix(tmp_path, monkeypatch, capsys):
    plan = {"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW"}}]}
    monkeypatch.setitem(models.ADAPTERS, "ollama", type("Right", (Adapter,), {"reply": json.dumps(plan)}))
    monkeypatch.setattr(run, "RUNS", tmp_path)
    run.main(["ollama", "right", "--only", "show-tag"])
    [done] = tmp_path.iterdir()
    assert done.suffix == ".jsonl" and json.loads(done.read_text())["outcome"] == "pass"


def test_the_writers_refusals_are_reported_by_rule():
    summary = {"intents": [{"intent": "spending_summary", "category": "Groceries"}]}
    result = run.run_case(Scripted(summary, "{label} rose to {latest}."), _case("summary-category"), SUITE)
    assert result["writer"]["verdict"] == "typed a comparison: 'rose'"
