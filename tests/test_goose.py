"""Goose, end to end through the API, against a scripted model. Each test name
says which of the five guarantees it proves (ARCHITECTURE.md "Goose")."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from blitzave import assistant
from blitzave.__main__ import URL, create_app
from blitzave.sources import models
from blitzave.sources.base import AccountRow, TxRow
from blitzave.store import db, ingest, transactions, transfers, users
from blitzave.words import MONTHS
from blitzave.words import goose as words

TODAY = date(2026, 11, 14)
TRIP = {"start": "2026-05-13", "end": "2026-05-27"}


class FixedDate(date):
    @classmethod
    def today(cls):
        return TODAY


class FakeModel:
    """Plays scripted replies and records every request a model would get."""

    def __init__(self):
        self.replies = []
        self.requests = []

    def chat(self, system, messages, schema=None):
        self.requests.append({"system": system, "messages": messages, "schema": schema})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return models.Reply(reply if isinstance(reply, str) else json.dumps(reply))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLITZAVE_HOME", str(tmp_path))
    monkeypatch.setattr(assistant, "date", FixedDate)


@pytest.fixture
def client():
    with TestClient(create_app(), base_url=URL) as c:
        yield c


@pytest.fixture
def fake(client, monkeypatch):
    """A seeded app with a scripted local model. Groceries every month since
    May (October heavier), a PNW trip in May, and a card payment in the
    middle of it. Every description, merchant and account name carries
    CANARY, so a test can prove none of it ever reaches a model."""
    model = FakeModel()
    monkeypatch.setattr(models, "make_model", lambda *args: model)
    assert client.put("/api/goose/settings", json={"provider": "ollama", "model": "fake"}).status_code == 200
    groceries = client.post("/api/categories", json={"name": "Groceries", "kind": "expense"}).json()
    client.post("/api/categories", json={"name": "Dining", "kind": "expense"})

    conn = db.connect()
    user_id = users.get_or_create_user(conn)
    accounts = {
        "ACT-1": ingest.upsert_account(conn, user_id, AccountRow(
            "ACT-1", "asset", "1111", bank_name="CANARY Credit Union", asset_type="checking",
            meta={"name": "CANARY Checking (1111)"},
        )),
        "ACT-2": ingest.upsert_account(conn, user_id, AccountRow(
            "ACT-2", "liability", "2222", issuer_name="CANARY Bank", liability_type="credit_card",
            meta={"name": "CANARY Platinum (2222)"},
        )),
    }
    rows = [
        ("G5", "ACT-2", -10000, "2026-05-01"),
        ("G6", "ACT-2", -10000, "2026-06-03"),
        ("G7", "ACT-2", -10000, "2026-07-03"),
        ("G8", "ACT-2", -10000, "2026-08-03"),
        ("G9", "ACT-2", -10000, "2026-09-03"),
        ("G10", "ACT-2", -16000, "2026-10-03"),
        ("G11", "ACT-2", -4000, "2026-11-05"),
        ("T1", "ACT-2", -4500, "2026-05-14"),
        ("T2", "ACT-2", -12000, "2026-05-20"),
        ("T3", "ACT-2", -3000, "2026-05-26"),
        ("P1", "ACT-1", -30000, "2026-05-18"),
        ("P2", "ACT-2", 30000, "2026-05-18"),
    ]
    for ext, account, cents, posted in rows:
        ingest.upsert_transaction(conn, user_id, accounts[account], TxRow(
            account, ext, cents, "USD", "debit" if cents < 0 else "credit", posted,
            f"CANARY {ext} Coffee and things", "CANARY Merchant",
        ))
    conn.commit()
    for ext in ("G5", "G6", "G7", "G8", "G9", "G10", "G11"):
        transactions.update_metadata(conn, ext, categories={groceries["id"]: 1.0})
    transfers.create_transfer(conn, user_id, "P1", "P2")  # the card payment
    conn.close()
    model.account_ids = accounts
    return model


def _ask(client, fake, question, *replies):
    fake.replies.extend(replies)
    response = client.post("/api/goose", json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()["cards"]


def _plan(*intents):
    return {"intents": list(intents)}


def _txn(client, ext):
    return next(t for t in client.get("/api/transactions").json()["transactions"] if t["id"] == ext)


def test_goose_waits_until_a_model_is_chosen(client):
    response = client.post("/api/goose", json={"question": "what did I spend?"})
    assert response.status_code == 409


def test_show_lists_the_rows_and_reads_the_question_back_as_chips(client, fake):
    [card] = _ask(client, fake, "show me May 13 to 27", _plan({"intent": "show_transactions", "filter": TRIP}))
    assert card["type"] == "transactions"
    assert card["chips"] == ["May 13, 2026 – May 27, 2026"]
    assert card["text"] == "5 transactions."  # the trip, and both legs of the card payment
    assert card["rows"][0] == {
        "posted_on": "2026-05-26",
        "description": "CANARY T3 Coffee and things",
        "amount_display": "-$30.00",
    }


def test_g4_a_tag_is_written_only_on_apply_and_undo_takes_it_back(client, fake):
    [card] = _ask(client, fake, "tag May 13-27 as PNW", _plan(
        {"intent": "propose_tag", "tag_name": "PNW", "filter": TRIP}
    ))
    assert card["type"] == "proposal"
    assert card["text"] == "Tag these 3 transactions “PNW” (a new tag)?"  # the card payment is left out
    assert words.PAIRS_LEFT_OUT in card["chips"]
    assert client.get("/api/tags").json() == []  # proposing wrote nothing

    url = f"/api/goose/proposals/{card['proposal_id']}"
    applied = client.post(f"{url}/apply", json={"rows": [0, 2]})  # one row left unchecked
    assert applied.json() == {"text": "Tagged 2 transactions “PNW”.", "undo": True}
    [tag] = client.get("/api/tags").json()
    tagged = client.get("/api/transactions", params={"tag_id": tag["id"]}).json()
    assert sorted(t["id"] for t in tagged["transactions"]) == ["T1", "T3"]
    assert client.post(f"{url}/apply", json={"rows": [0]}).status_code == 404  # once only

    assert client.post(f"{url}/undo").json() == {"text": words.UNDONE}
    assert client.get("/api/tags").json() == []  # Apply made the tag, so Undo removes it
    assert client.post(f"{url}/undo").status_code == 404


def test_g4_apply_takes_only_rows_the_preview_showed(client, fake):
    [card] = _ask(client, fake, "tag the trip", _plan(
        {"intent": "propose_tag", "tag_name": "PNW", "filter": TRIP}
    ))
    url = f"/api/goose/proposals/{card['proposal_id']}/apply"
    assert client.post(url, json={"rows": [3]}).status_code == 422
    assert client.post(url, json={"rows": [-1]}).status_code == 422
    assert client.post(url, json={"rows": []}).status_code == 422
    assert client.get("/api/tags").json() == []
    assert client.post(url, json={"rows": [0]}).status_code == 200  # a refused Apply leaves the preview open


def test_g4_filing_under_a_category_and_undoing_it_restore_each_row(client, fake):
    [card] = _ask(client, fake, "the coffee on the 26th was dining", _plan(
        {"intent": "propose_category", "category": "Dining", "filter": {**TRIP, "start": "2026-05-26"}}
    ))
    assert card["text"] == "File this transaction under Dining?"
    url = f"/api/goose/proposals/{card['proposal_id']}"
    assert client.post(f"{url}/apply", json={"rows": [0]}).json()["text"] == "Filed 1 transaction under Dining."
    after = _txn(client, "T3")
    assert after["categories_set_by"] == "user" and len(after["categories"]) == 1

    client.post(f"{url}/undo")
    before = _txn(client, "T3")
    assert (before["categories"], before["categories_set_by"]) == ({}, None)


def test_g3_a_summary_fills_the_writers_slots_with_the_engines_figures(client, fake):
    [card] = _ask(
        client, fake, "what's my grocery spending like",
        _plan({"intent": "spending_summary", "category": "Groceries", "months": 6}),
        "Over {months} months, {label} averaged {average}, and {latest_month} ran {trend}.",
    )
    assert card["type"] == "summary"
    assert card["text"] == "Over 6 months, Groceries averaged $110.00, and October ran higher than usual."
    assert (card["figure"], card["figure_label"]) == ("$110.00", "a month on average")
    assert card["chips"] == ["Category: Groceries", "May – October", words.MONTH_LEFT_OUT]
    assert [m["amount_display"] for m in card["months"]] == ["$100.00"] * 5 + ["$160.00"]


def test_g3_a_writer_that_types_a_number_is_replaced_by_the_engines_sentence(client, fake):
    [card] = _ask(
        client, fake, "groceries?",
        _plan({"intent": "spending_summary", "category": "Groceries"}),
        "{label} averaged $110 a month, which is too much.",
    )
    # The heavier month is reported, never judged (the Prime Directive).
    assert card["text"] == (
        "Over the last 6 months, Groceries averaged $110.00 a month."
        " October came to $160.00, higher than usual."
    )
    assert not assistant.NOT_ALLY.search(card["text"])


def test_g1_no_row_account_detail_or_result_ever_reaches_a_model(client, fake):
    _ask(client, fake, "show the trip", _plan({"intent": "show_transactions", "filter": {**TRIP, "account": "A2"}}))
    _ask(client, fake, "tag the trip", _plan({"intent": "propose_tag", "tag_name": "PNW", "filter": TRIP}))
    [card] = _ask(
        client, fake, "how's my spending", _plan({"intent": "spending_summary"}), "{label} averaged {average}."
    )
    assert card["text"] == "Your spending averaged $142.50."  # the figure is filled in after the model
    sent = json.dumps(fake.requests)
    assert "CANARY" not in sent  # descriptions, merchants, bank and account names
    assert "1111" not in sent and "2222" not in sent  # account numbers
    words_sent = json.dumps([[r["system"], r["messages"]] for r in fake.requests])  # the schema has "$defs"
    assert "$" not in words_sent and "142" not in words_sent  # no amount, not even the summary's own
    assert not any(month in words_sent for month in MONTHS)  # no month a summary picked out
    assert "A2 (credit card)" in fake.requests[0]["system"]  # the alias is all a model knows


def test_g1_the_writer_hears_the_same_thing_whatever_the_figures(client, fake):
    question = "how are groceries going"
    plan = _plan({"intent": "spending_summary", "category": "Groceries"})
    _ask(client, fake, question, plan, "{label} averaged {average}.")
    first = fake.requests[-1]

    conn = db.connect()  # a much heavier October changes every figure the summary has
    groceries = next(c["id"] for c in client.get("/api/categories").json() if c["name"] == "Groceries")
    ingest.upsert_transaction(conn, users.get_or_create_user(conn), fake.account_ids["ACT-2"], TxRow(
        "ACT-2", "G12", -90000, "USD", "debit", "2026-10-20", "CANARY G12", "CANARY Merchant",
    ))
    conn.commit()
    transactions.update_metadata(conn, "G12", categories={groceries: 1.0})
    conn.close()

    [card] = _ask(client, fake, question, plan, "{label} averaged {average}.")
    assert card["text"] == "Groceries averaged $260.00."
    assert fake.requests[-1] == first


def test_g2_the_planner_hears_the_same_thing_whatever_ran_before(client, fake):
    client.post("/api/tags", json={"name": "PNW"})
    show = _plan({"intent": "show_transactions", "filter": {"tag": "PNW"}})
    _ask(client, fake, "show the PNW trip", show)
    first = fake.requests[-1]

    [card] = _ask(client, fake, "tag the trip", _plan(
        {"intent": "propose_tag", "tag_name": "PNW", "filter": TRIP}
    ))
    client.post(f"/api/goose/proposals/{card['proposal_id']}/apply", json={"rows": [0, 1, 2]})
    _ask(client, fake, "what did it cost", _plan({"intent": "spending_summary", "tag": "PNW"}), "No.")
    _ask(client, fake, "show the PNW trip", show)
    assert fake.requests[-1] == first  # no count, row or result came back with it


def test_an_unknown_label_gets_one_repair_that_names_the_field(client, fake):
    [card] = _ask(
        client, fake, "groceries last month",
        _plan({"intent": "spending_summary", "category": "Grocery"}),
        _plan({"intent": "spending_summary", "category": "Groceries", "months": 1}),
        "{label}: {latest}.",
    )
    repair = fake.requests[1]["messages"][-1]["content"]
    assert "category" in repair and "must be one of: Dining, Groceries" in repair
    assert card["text"] == "Groceries: $160.00."


def test_two_misses_end_in_the_engines_own_words(client, fake):
    [card] = _ask(client, fake, "hmm", "not json", '{"intents": []}')
    assert card == {"type": "message", "text": words.NO_PLAN}


def _trace() -> list[dict]:
    return [json.loads(line) for path in (db.home_dir() / "traces").glob("*.jsonl") for line in path.open()]


def test_dev_mode_traces_each_call_with_only_what_the_model_saw(client, fake, monkeypatch):
    monkeypatch.setenv("BLITZAVE_DEV", "1")
    [card] = _ask(
        client, fake, "groceries last month",
        _plan({"intent": "spending_summary", "category": "Grocery"}),
        _plan({"intent": "spending_summary", "category": "Groceries", "months": 1}),
        "{label} came to $5.",
    )
    assert card["text"] == "Groceries came to $160.00 in October."  # the engine's sentence, not the writer's

    [line] = _trace()  # one line per question
    assert line["question"] == "groceries last month"
    plan, repair, write = line["calls"]
    assert (plan["step"], repair["step"], write["step"]) == ("plan", "repair", "write")
    assert plan["verdict"].startswith("doesn't fit: ") and "must be one of: Dining, Groceries" in plan["verdict"]
    assert repair["verdict"] == "fits"
    assert (write["reply"], write["verdict"]) == ("{label} came to $5.", "typed a figure: '$'")
    assert all(isinstance(call["seconds"], float) for call in line["calls"])
    assert plan["messages"] == fake.requests[0]["messages"]  # what was sent, as it was sent

    traced = json.dumps(line)
    assert "CANARY" not in traced and "160" not in traced  # no row, and not the engine's figure
    assert not any(month in traced for month in MONTHS)


def test_dev_trace_keeps_a_call_the_model_never_answered(client, fake, monkeypatch):
    monkeypatch.setenv("BLITZAVE_DEV", "1")
    fake.replies.append(models.ModelError("couldn't reach the model at 127.0.0.1:11434"))
    assert client.post("/api/goose", json={"question": "hmm"}).status_code == 502
    [line] = _trace()
    [call] = line["calls"]
    assert call["error"] == "couldn't reach the model at 127.0.0.1:11434" and "reply" not in call


def test_no_trace_without_dev_mode(client, fake, monkeypatch):
    monkeypatch.delenv("BLITZAVE_DEV", raising=False)
    _ask(client, fake, "should I buy index funds?", _plan({"intent": "decline", "reason": "investment_advice"}))
    assert not (db.home_dir() / "traces").exists()


def test_clarify_and_decline_come_back_in_the_engines_words(client, fake):
    [card] = _ask(client, fake, "should I buy index funds?", _plan(
        {"intent": "decline", "reason": "investment_advice"}
    ))
    assert card == {"type": "message", "text": words.DECLINE["investment_advice"]}
    [card] = _ask(client, fake, "food spending", _plan(
        {"intent": "clarify", "field": "category", "options": ["Groceries", "Dining"]}
    ))
    assert card == {"type": "clarify", "text": "Which category did you mean?", "options": ["Groceries", "Dining"]}


def test_clarify_and_decline_stand_alone():
    labels = {"category": ["Groceries"], "tag": [], "account": []}
    plan = _plan({"intent": "decline", "reason": "not_supported"}, {"intent": "show_transactions", "filter": {}})
    with pytest.raises(ValidationError, match="must be the only intent"):
        assistant.Plan.model_validate(plan, context=labels)


@pytest.mark.parametrize(
    "sentence, expected",
    [
        ("{label} averaged {average}.", "Groceries averaged $110.00."),
        ("{label} came to {total} in all.", "Groceries came to $660.00 in all."),
        ("{label} averaged $110.", None),  # a typed figure
        ("{label} averaged {avg}.", None),  # a slot the engine didn't give
        ("{label} ran high in October.", None),  # a typed month
        ("{label} held for six months.", None),  # a number in words
        ("You overspent on {label}.", None),  # a scorekeeper word
        ("{label} came to {total}, more than {average}.", None),  # a comparison it couldn't see
        ("{label} averaged {average}, as usual.", None),
        ("{label} came to {total}, {trend}.", None),  # a slot the engine didn't give
        ("One. Two. Three.", None),  # more than two sentences
    ],
)
def test_g3_fill_lets_through_only_the_engines_figures(sentence, expected):
    facts = {"label": "Groceries", "average": "$110.00", "total": "$660.00"}
    assert assistant.fill(sentence, facts) == expected


def test_g5_a_remote_model_needs_consent_and_its_key_stays_in_the_keychain(client, fake_keyring):
    claude = {"provider": "anthropic", "model": "claude-opus-5-5", "api_key": "sk-ant-test-123"}
    assert client.put("/api/goose/settings", json=claude).status_code == 422
    assert client.post("/api/goose", json={"question": "hi"}).status_code == 409

    saved = client.put("/api/goose/settings", json={**claude, "remote_ok": True})
    assert saved.status_code == 200
    assert saved.json()["remote"] is True and saved.json()["key_saved"] is True
    assert "sk-ant" not in saved.text + client.get("/api/goose/settings").text
    assert ("blitzave", "anthropic_api_key") in fake_keyring
    for path in db.home_dir().rglob("*"):  # the database, its WAL, any backup
        assert not path.is_file() or b"sk-ant" not in path.read_bytes()
    assert "sk-ant" not in client.get("/api/export").text

    lan = {"provider": "ollama", "model": "qwen3", "base_url": "http://192.168.1.5:11434"}
    assert client.put("/api/goose/settings", json=lan).status_code == 422


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "text/plain"}])
def test_goose_refuses_a_body_that_isnt_sent_as_json(client, headers):
    # A page on another site can send a form or text/plain blind, never
    # application/json, so these bodies must not be read (TODOS.md P1.2).
    response = client.post("/api/goose", content='{"question": "hi"}', headers=headers)
    assert response.status_code == 422


# --- the model list on the settings screen --------------------------------------------


def test_claude_offers_a_short_fixed_list(client):
    assert client.get("/api/goose/models", params={"provider": "anthropic"}).json() == {
        "models": models.CLAUDE_MODELS
    }


def test_ollama_offers_what_it_has_on_this_laptop(client, monkeypatch):
    monkeypatch.setattr(models, "ollama_models", lambda base_url: ["qwen3:8b"])

    listed = client.get(
        "/api/goose/models", params={"provider": "ollama", "base_url": "http://127.0.0.1:11434"}
    )

    assert listed.json() == {"models": ["qwen3:8b"]}


def test_an_ollama_that_cant_be_asked_offers_nothing_and_a_name_is_typed(client, monkeypatch):
    def unreachable(base_url):
        raise models.ModelError("Ollama didn't answer at that address.")

    monkeypatch.setattr(models, "ollama_models", unreachable)

    listed = client.get("/api/goose/models", params={"provider": "ollama"})

    assert listed.status_code == 200
    assert listed.json() == {"models": []}


def test_the_model_list_only_knows_two_providers(client):
    assert client.get("/api/goose/models", params={"provider": "other"}).status_code == 422
