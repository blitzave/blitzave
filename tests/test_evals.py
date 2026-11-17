"""evals/run.py's cases and scoring, against scripted models. The eval itself
asks a real model and is run by hand; these keep it honest in CI."""

import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest

from blitzave.sources import models

spec = importlib.util.spec_from_file_location("evals_run", Path(__file__).parents[1] / "evals" / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

SUITE = run.load()  # also proves every case's expected plan fits the grammar


@pytest.fixture(autouse=True)
def no_ollama_lookup(monkeypatch):
    """The stamp asks Ollama for a digest; tests never reach a model server."""
    monkeypatch.setattr(run, "ollama_about", lambda base_url, model: {"digest": "500a1f067a9f", "server": "ollama 0.40.5"})


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, system, messages, schema=None):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return models.Reply(reply if isinstance(reply, str) else json.dumps(reply, default=str))


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
        return models.Reply(self.reply)


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


def test_every_line_of_a_run_carries_its_stamp(tmp_path, monkeypatch):
    plan = {"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW"}}]}
    monkeypatch.setitem(models.ADAPTERS, "ollama", type("Right", (Adapter,), {"reply": json.dumps(plan)}))
    monkeypatch.setattr(run, "RUNS", tmp_path)
    run.main(["ollama", "right", "--only", "show-"])
    lines = [json.loads(line) for line in next(tmp_path.iterdir()).read_text().splitlines()]
    assert len(lines) > 1
    stamp = {key: lines[0][key] for key in ["suite", "suite_hash", "prompt", "provider", "model", "settings", "think", "digest", "server"]}
    assert stamp == {
        "suite": "planner", "suite_hash": run.suite_version(), "prompt": run.prompt_version(),
        "provider": "ollama", "model": "right",
        "settings": {"temperature": 0, "num_ctx": 8192, "timeout": models.TIMEOUT_SECONDS},
        "think": "off", "digest": "500a1f067a9f", "server": "ollama 0.40.5",
    }
    assert lines[0]["machine"] and "started" in lines[0] and "git" in lines[0] and "dirty" in lines[0]
    assert all({key: line[key] for key in stamp} == stamp for line in lines)


def test_todays_prompts_have_a_name_in_the_prompt_log():
    log = run.prompt_log()
    assert run.prompt_version() in log, "a prompt or the plan grammar changed: add an entry to evals/prompts.toml"
    assert all({"name", "date", "changed"} <= set(entry) for entry in log.values())


class Counted(Scripted):
    """A scripted model that also reports usage, as the adapters do."""

    def chat(self, system, messages, schema=None):
        return models.Reply(super().chat(system, messages, schema).text, {"tokens_in": 900, "tokens_out": 40})


def test_the_servers_usage_is_kept_with_each_call_and_counted_per_case():
    case = _case("decline-index-funds")
    result = run.run_case(Counted({"intents": run.first_choice(case["expect"])}), case, SUITE)
    assert result["calls"][0]["usage"] == {"tokens_in": 900, "tokens_out": 40}
    assert run.tokens(result) == (900, 40)
    assert run.tally([result])["tokens"] == (900, 40)
    assert run.tokens({"calls": [{"step": "plan", "reply": "{}"}]}) is None  # older runs kept none


def _recorded(case_id: str, plan: dict, writer: str | None = None) -> dict:
    """One line of a run, as run_case() wrote it."""
    model = Scripted(plan, writer) if writer else Scripted(plan)
    return run.run_case(model, _case(case_id), SUITE)


def _with_expect(case_id: str, expect: list[dict]) -> run.Suite:
    """Today's suite, with one case's answer changed since a run."""
    cases = [{**case, "expect": expect} if case["id"] == case_id else case for case in SUITE.cases]
    return dataclasses.replace(SUITE, cases=cases)


def test_a_stored_run_is_scored_again_against_todays_key():
    uber = _recorded("show-merchant", {"intents": [{"intent": "show_transactions", "filter": {"text": "uber"}}]})
    assert uber["outcome"] == "pass"
    edited = _with_expect("show-merchant", [{"intent": "show_transactions", "filter": {"text": "uber", "category": "Transport"}}])
    [now] = run.rescore([uber], edited)
    assert (now["outcome"], now["why"]) == ("wrong", "filter.category is None, expected 'Transport'")
    assert run.rescore([uber], SUITE) == [uber]


def test_rescoring_leaves_out_cases_gone_from_the_key_and_keeps_runs_with_no_plan():
    gone = {**_recorded("show-tag", {"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW"}}]}), "case": "since-deleted"}
    timeout = run.run_case(Scripted(models.ModelError("couldn't reach the model")), _case("show-tag"), SUITE)
    assert run.rescore([gone, timeout], SUITE) == [timeout]


def test_a_writer_sentence_is_judged_again_by_todays_fill():
    summary = {"intents": [{"intent": "spending_summary", "category": "Groceries"}]}
    result = _recorded("summary-category", summary, "{label} averaged {average}.")
    assert result["writer"]["verdict"] == "kept"
    stored = {**result, "writer": {**result["writer"], "reply": "{label} rose to {latest}."}}
    [now] = run.rescore([stored], SUITE)
    assert now["writer"]["verdict"] == "typed a comparison: 'rose'"


def test_the_report_names_cases_that_score_differently_today(tmp_path):
    uber = _recorded("show-merchant", {"intents": [{"intent": "show_transactions", "filter": {"text": "uber"}}]})
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({"provider": "ollama", "model": "m", "prompt": run.prompt_version(), **uber}) + "\n")
    edited = _with_expect("show-merchant", [{"intent": "show_transactions", "filter": {"text": "lyft"}}])
    text = run.report([path], edited)
    assert "| 0/1 (0%) |" in text
    assert "- m (ollama): 1 passed at run time, 0 today; scored differently: show-merchant" in text
    assert f"v1 ({run.prompt_version()})" in text


def _run_file(path: Path, stamp: dict, results: list[dict]) -> Path:
    path.write_text("".join(json.dumps(stamp | result) + "\n" for result in results))
    return path


def test_compare_lists_what_a_change_fixed_broke_and_moved(tmp_path):
    right = {"intents": [{"intent": "show_transactions", "filter": {"text": "uber"}}]}
    this_month = {"intents": [{"intent": "show_transactions", "filter": {"text": "uber", "start": "2026-11-01"}}]}
    tag = {"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW"}}]}
    tag_and_dates = {"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW", "end": "2026-10-31"}}]}
    tag_and_coffee = {"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW", "category": "Coffee"}}]}
    decline = {"intents": [{"intent": "decline", "reason": "investment_advice"}]}
    stamp = {"provider": "ollama", "model": "qwen3:8b", "prompt": "cbdc6dc4", "think": "off"}
    old = _run_file(tmp_path / "old.jsonl", stamp, [
        _recorded("show-merchant", this_month), _recorded("show-tag", tag_and_dates), _recorded("decline-index-funds", decline),
    ])
    new = _run_file(tmp_path / "new.jsonl", stamp | {"prompt": "0badc0de"}, [
        _recorded("show-merchant", right), _recorded("show-tag", tag_and_coffee), _recorded("decline-index-funds", tag),
    ])
    text = run.compare(old, new)
    assert "- prompt: v1 (cbdc6dc4) → 0badc0de (not in prompts.toml)" in text
    assert "Passed 1 → 1 of the 3 cases both asked: fixed 1, broke 1, 1 still wrong for another reason." in text
    assert "Broke (passed before, not now). Each needs a sentence in the PR:\n- decline-index-funds: wrong: intents" in text
    assert "Fixed:\n- show-merchant" in text
    assert "- show-tag: wrong: filter.end is 2026-10-31, expected None → wrong: filter.category is 'Coffee', expected None" in text


def test_two_runs_that_differ_in_nothing_say_so(tmp_path):
    stamp = {"provider": "ollama", "model": "qwen3:8b", "prompt": "cbdc6dc4"}
    results = [_recorded("show-tag", {"intents": [{"intent": "show_transactions", "filter": {"tag": "PNW"}}]})]
    text = run.compare(_run_file(tmp_path / "a.jsonl", stamp, results), _run_file(tmp_path / "b.jsonl", stamp, results))
    assert "Nothing recorded differs between the runs" in text
    assert "fixed 0, broke 0, 0 still wrong" in text


def test_p90_of_one_case_is_that_case():
    assert run._p90([4.2]) == 4.2
    assert run._p90([float(n) for n in range(1, 11)]) == pytest.approx(9.1)


def test_stage_seconds_split_a_cases_planning_time_and_leave_the_rest_as_other():
    timed = {"seconds": 3.0, "calls": [
        {"step": "plan", "usage": {"load_seconds": 0.5, "prompt_seconds": 0.4, "output_seconds": 1.0}},
        {"step": "repair", "usage": {"prompt_seconds": 0.1, "output_seconds": 0.5}},
        {"step": "write", "usage": {"output_seconds": 9.0}},  # the writer isn't planning time
    ]}
    split = run.stage_seconds(timed)
    assert split == pytest.approx({"load_seconds": 0.5, "prompt_seconds": 0.5, "output_seconds": 1.5, "other_seconds": 0.5})
    assert run.stage_seconds({"seconds": 1.7, "calls": [{"step": "plan", "usage": {"tokens_in": 2000}}]}) is None


def test_flips_sort_every_shared_case_into_one_list():
    def result(case, outcome, why=None):
        return {"case": case, "outcome": outcome, **({"why": why} if why else {})}

    old = {r["case"]: r for r in [
        result("show-tag", "pass"), result("show-merchant", "wrong", "a"), result("show-account", "wrong", "b"),
        result("show-since", "pass"), result("show-yesterday", "wrong", "c"),
    ]}
    new = {r["case"]: r for r in [
        result("show-tag", "wrong", "d"), result("show-merchant", "pass"), result("show-account", "wrong", "e"),
        result("show-since", "pass"), result("show-yesterday", "wrong", "c"), result("show-typos", "pass"),
    ]}
    found = run.flips(old, new, SUITE)
    assert found["broke"] == ["show-tag"] and found["fixed"] == ["show-merchant"]
    assert found["passed both"] == ["show-since"]
    assert found["wrong both"] == ["show-account", "show-yesterday"] and found["moved"] == ["show-account"]
    assert found["only second"] == ["show-typos"]
    assert sorted(case for flip in run.FLIPS for case in found[flip]) == sorted(found["both"])
