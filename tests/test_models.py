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
    assert reply.text == '{"intents": []}'
    assert sent["url"] == "http://127.0.0.1:11434/api/chat"
    assert sent["body"]["format"] == SCHEMA
    assert sent["body"]["messages"] == [{"role": "system", "content": "rules"}, *MESSAGES]
    assert sent["body"]["options"] == {"temperature": 0, "num_ctx": 8192}


def test_ollamas_counts_and_timings_are_kept_as_tokens_and_seconds(sent):
    sent["reply"] = httpx.Response(200, json={
        "message": {"content": "{}"},
        "prompt_eval_count": 412, "eval_count": 38,
        "load_duration": 2_500_000_000, "prompt_eval_duration": 310_000_000, "eval_duration": 1_234_567_890,
    })
    reply = models.Ollama("qwen3:8b").chat("rules", MESSAGES, SCHEMA)
    assert reply.usage == {
        "tokens_in": 412, "tokens_out": 38, "load_seconds": 2.5, "prompt_seconds": 0.31, "output_seconds": 1.235,
    }


def test_what_ollama_leaves_out_is_left_out(sent):
    sent["reply"] = httpx.Response(200, json={"message": {"content": "{}"}, "eval_count": 5})
    assert models.Ollama("qwen3:8b").chat("rules", MESSAGES, SCHEMA).usage == {"tokens_out": 5}


@pytest.fixture
def ollama(monkeypatch):
    """A fake Ollama: /api/show reports the capabilities the test sets, and
    /api/chat answers. Records every request, in order."""
    state = {"capabilities": ["completion", "thinking"], "show": 200, "requests": []}

    def fake_post(url, json, headers, timeout):
        state["requests"].append((url.rsplit("/", 1)[-1], json))
        if url.endswith("/api/show"):
            return httpx.Response(state["show"], json={"capabilities": state["capabilities"]})
        return httpx.Response(200, json={"message": {"content": "{}"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(models, "_thinks", {})
    return state


def _chats(state):
    return [body for path, body in state["requests"] if path == "chat"]


def test_a_model_that_can_think_is_asked_not_to_and_ollama_is_asked_once(ollama):
    qwen = models.Ollama("qwen3:8b")
    qwen.chat("rules", MESSAGES, SCHEMA)
    qwen.chat("rules", MESSAGES)
    assert [path for path, _ in ollama["requests"]] == ["show", "chat", "chat"]
    assert all(body["think"] is False for body in _chats(ollama))


def test_a_model_that_cant_think_is_never_sent_think(ollama):
    ollama["capabilities"] = ["completion"]
    models.Ollama("llama3.2").chat("rules", MESSAGES)
    assert "think" not in _chats(ollama)[0]


def test_think_can_be_left_to_ollama(ollama, monkeypatch):
    monkeypatch.setattr(models, "OLLAMA_THINK", None)
    models.Ollama("qwen3:8b").chat("rules", MESSAGES)
    assert [path for path, _ in ollama["requests"]] == ["chat"]  # not even asked
    assert "think" not in _chats(ollama)[0]


def test_a_failed_ask_sends_no_think_and_is_asked_again_next_time(ollama):
    ollama["show"] = 500
    models.Ollama("qwen3:8b").chat("rules", MESSAGES)
    assert "think" not in _chats(ollama)[0]
    ollama["show"] = 200
    models.Ollama("qwen3:8b").chat("rules", MESSAGES)
    assert _chats(ollama)[1]["think"] is False


def test_claude_only_ever_sends_the_key_to_anthropic(sent, fake_keyring):
    # Whatever address a settings row holds (an imported one, say), the key
    # goes to Anthropic and nowhere else.
    models.save_api_key("sk-ant-test")
    sent["reply"] = httpx.Response(200, json={
        "stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]})
    models.make_model("anthropic", "claude-haiku-5-5", "https://evil.example").chat("rules", MESSAGES)
    assert sent["url"] == "https://api.anthropic.com/v1/messages"


def test_claude_holds_the_plan_to_the_schema_with_structured_outputs(sent, fake_keyring):
    models.save_api_key("sk-ant-test")
    sent["reply"] = httpx.Response(200, json={
        "model": "claude-opus-5-5",
        "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"intents": []}'}],
        "usage": {"input_tokens": 1890, "output_tokens": 61},
    })
    reply = models.make_model("anthropic", "claude-opus-5-5", models.ANTHROPIC_URL).chat("rules", MESSAGES, SCHEMA)
    assert reply.text == '{"intents": []}'
    assert reply.usage == {"tokens_in": 1890, "tokens_out": 61, "served_by": "claude-opus-5-5"}
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["headers"]["x-api-key"] == "sk-ant-test"
    assert sent["headers"]["anthropic-version"] == models.ANTHROPIC_VERSION
    assert sent["body"]["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert sent["body"]["fallbacks"] == "default"
    assert "temperature" not in sent["body"]


def test_claude_writes_plain_text_without_a_schema(sent, fake_keyring):
    models.save_api_key("sk-ant-test")
    sent["reply"] = httpx.Response(200, json={"content": [{"type": "text", "text": "Calm words."}]})
    assert models.Claude("claude-haiku-5-5").chat("rules", MESSAGES) == models.Reply("Calm words.")
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


# --- the names the settings screen offers --------------------------------------------


def _tags(monkeypatch, response: httpx.Response | Exception) -> list[str]:
    asked = []

    def fake_get(url, timeout):
        asked.append(url)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx, "get", fake_get)
    return asked


def test_ollama_is_asked_what_it_has(monkeypatch):
    tags = {"models": [{"name": "qwen3:8b"}, {"name": "gemma3:12b"}]}
    asked = _tags(monkeypatch, httpx.Response(200, json=tags, request=httpx.Request("GET", "x")))

    assert models.ollama_models("http://127.0.0.1:11434/") == ["gemma3:12b", "qwen3:8b"]
    assert asked == ["http://127.0.0.1:11434/api/tags"]


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectError("refused"),
        httpx.Response(500, request=httpx.Request("GET", "x")),
        httpx.Response(200, text="not json", request=httpx.Request("GET", "x")),
        httpx.Response(200, json={"no": "models"}, request=httpx.Request("GET", "x")),
    ],
    ids=["not running", "server error", "not json", "unexpected shape"],
)
def test_an_ollama_that_cant_say_is_a_model_error(monkeypatch, answer):
    _tags(monkeypatch, answer)

    with pytest.raises(models.ModelError, match="didn't answer"):
        models.ollama_models("http://127.0.0.1:11434")


def test_an_ollama_on_another_machine_is_never_asked(monkeypatch):
    asked = _tags(monkeypatch, httpx.ConnectError("should not be called"))

    with pytest.raises(models.ModelError, match="on this laptop"):
        models.ollama_models("http://192.168.1.20:11434")
    assert asked == []
