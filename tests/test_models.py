"""The model adapters' wire formats, with httpx stubbed: CI never reaches a model."""

import httpx
import pytest

from blitzave.sources import models

SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
MESSAGES = [{"role": "user", "content": "hi"}]


@pytest.fixture
def sent(monkeypatch):
    """Records each POST and answers with whatever the test puts in reply."""
    calls = {"reply": httpx.Response(200, json={})}

    def fake_post(url, json, headers, timeout):
        calls.update(url=url, body=json, headers=headers)
        return calls["reply"]

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


@pytest.mark.parametrize(
    "url, remote",
    [
        ("http://127.0.0.1:11434", False),
        ("http://localhost:11434", False),
        ("http://[::1]:11434", False),
        ("http://192.168.1.5:11434", True),  # another machine on the network
        ("https://api.anthropic.com", True),
    ],
)
def test_g5_remote_is_decided_by_where_the_bytes_go(url, remote):
    assert models.is_remote(url) is remote


def test_ollama_holds_the_plan_to_the_schema_with_its_grammar(sent):
    sent["reply"] = httpx.Response(200, json={"message": {"content": '{"intents": []}'}})
    reply = models.make_model("ollama", "qwen3:8b", models.OLLAMA_URL).chat("rules", MESSAGES, SCHEMA)
    assert reply == '{"intents": []}'
    assert sent["url"] == "http://127.0.0.1:11434/api/chat"
    assert sent["body"]["format"] == SCHEMA
    assert sent["body"]["messages"] == [{"role": "system", "content": "rules"}, *MESSAGES]
    assert sent["body"]["options"]["num_ctx"] == 8192


def test_claude_holds_the_plan_to_the_schema_with_structured_outputs(sent, fake_keyring):
    models.save_api_key("sk-ant-test")
    sent["reply"] = httpx.Response(200, json={
        "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"intents": []}'}],
    })
    reply = models.make_model("anthropic", "claude-opus-5-5", models.ANTHROPIC_URL).chat("rules", MESSAGES, SCHEMA)
    assert reply == '{"intents": []}'
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["headers"]["x-api-key"] == "sk-ant-test"
    assert sent["headers"]["anthropic-version"] == models.ANTHROPIC_VERSION
    assert sent["body"]["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert sent["body"]["fallbacks"] == "default"
    assert "temperature" not in sent["body"]


def test_claude_writes_plain_text_without_a_schema(sent, fake_keyring):
    models.save_api_key("sk-ant-test")
    sent["reply"] = httpx.Response(200, json={"content": [{"type": "text", "text": "Calm words."}]})
    assert models.Claude("claude-haiku-5-5").chat("rules", MESSAGES) == "Calm words."
    assert "output_config" not in sent["body"]
    assert "fallbacks" not in sent["body"]  # Haiku has no server-side fallback


def test_a_refusal_or_an_error_is_a_model_error_that_never_shows_the_key(sent, fake_keyring):
    models.save_api_key("sk-ant-test")
    claude = models.Claude("claude-opus-5-5")
    sent["reply"] = httpx.Response(200, json={"stop_reason": "refusal", "content": []})
    with pytest.raises(models.ModelError, match="declined"):
        claude.chat("rules", MESSAGES)
    sent["reply"] = httpx.Response(401, json={"error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    with pytest.raises(models.ModelError) as error:
        claude.chat("rules", MESSAGES)
    assert "401" in str(error.value) and "sk-ant" not in str(error.value)


def test_claude_needs_a_saved_key():
    with pytest.raises(models.ModelError, match="no Claude API key"):
        models.Claude("claude-opus-5-5").chat("rules", MESSAGES)


def test_an_unreachable_ollama_is_a_model_error(monkeypatch):
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", refuse)
    with pytest.raises(models.ModelError, match="couldn't reach the model at 127.0.0.1:11434"):
        models.Ollama("qwen3:8b").chat("rules", MESSAGES)
