"""The one place Goose reaches a language model (ARCHITECTURE.md "Goose").

Bring your own model: Ollama on this laptop by default, or Claude when the
user opts in. Every adapter does the same small job: send a system prompt and
messages, hold the reply to a JSON schema when one is given, and return the
reply's text. How each vendor enforces the schema stays in here.

A model counts as remote by where its bytes go, never by its vendor: an Ollama
on another machine is remote too. Nothing here logs a prompt, a reply or a key.
"""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx
import keyring

OLLAMA_URL = "http://127.0.0.1:11434"
ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2026-10-21"
# Claude models that can re-run a request their safety checks declined on a
# fallback model Anthropic picks ("fallbacks": "default").
FALLBACK_MODELS = {
    "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
}
KEYRING_SERVICE = "blitzave"
KEYRING_USERNAME = "anthropic_api_key"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
TIMEOUT_SECONDS = 120  # a local model can take this long to load
# Whether an Ollama model that can think does so before it answers. qwen3
# thinks by default, which for a short JSON plan only costs time: on an M1,
# qwen3:8b spent 57 seconds thinking its way to {"ok": true}, and answered in
# 1.5 without. qwen3 takes no level in between ("low" thinks just as long).
# None leaves it to Ollama. A model that can't think is never sent this.
OLLAMA_THINK: bool | None = False


class ModelError(Exception):
    """The model couldn't be reached or gave no usable answer. The message
    never holds a key or a prompt."""


class ChatModel(Protocol):
    def chat(self, system: str, messages: list[dict], schema: dict | None = None) -> str:
        """The reply's text. With a schema, one JSON object that fits it."""
        ...


def is_remote(base_url: str) -> bool:
    return urlsplit(base_url).hostname not in LOOPBACK


def save_api_key(key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)


def has_api_key() -> bool:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) is not None


@dataclass(frozen=True)
class Ollama:
    model: str
    base_url: str = OLLAMA_URL

    def chat(self, system: str, messages: list[dict], schema: dict | None = None) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            # Ollama's default context is small, and a prompt that doesn't fit
            # is cut with only a server log line to show for it.
            "options": {"temperature": 0, "num_ctx": 8192},
            "keep_alive": "10m",  # stays loaded between questions
        }
        if schema is not None:
            body["format"] = schema  # becomes a grammar: only JSON that fits can be sampled
        if OLLAMA_THINK is not None and ollama_thinks(self.base_url, self.model):
            body["think"] = OLLAMA_THINK
        content = (_post(f"{self.base_url}/api/chat", body, {}).get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ModelError("Ollama's answer had no message in it")
        return content


_thinks: dict[tuple[str, str], bool] = {}  # (base_url, model) -> can it think


def ollama_thinks(base_url: str, model: str) -> bool:
    """Whether Ollama lists "thinking" among the model's capabilities. Asked
    once per model. An ask that fails counts as no and isn't remembered, so
    the next question asks again."""
    key = (base_url, model)
    if key not in _thinks:
        try:
            info = _post(f"{base_url}/api/show", {"model": model}, {})
        except ModelError:
            return False
        _thinks[key] = "thinking" in (info.get("capabilities") or [])
    return _thinks[key]


@dataclass(frozen=True)
class Claude:
    model: str
    base_url: str = ANTHROPIC_URL

    def chat(self, system: str, messages: list[dict], schema: dict | None = None) -> str:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if not key:
            raise ModelError("no Claude API key is saved")
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        body = {"model": self.model, "max_tokens": 16000, "system": system, "messages": messages}
        if schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        if self.model in FALLBACK_MODELS:
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        reply = _post(f"{self.base_url}/v1/messages", body, headers)
        if reply.get("stop_reason") == "refusal":
            raise ModelError("Claude declined to answer that")
        blocks = reply.get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


ADAPTERS = {"ollama": Ollama, "anthropic": Claude}


def make_model(provider: str, model: str, base_url: str) -> ChatModel:
    """The factory. A new provider is one class above and one entry in ADAPTERS."""
    return ADAPTERS[provider](model, base_url)


def _post(url: str, body: dict, headers: dict) -> dict:
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as e:
        raise ModelError(f"couldn't reach the model at {urlsplit(url).netloc}") from e
    try:
        reply = response.json()
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        reply = {}
    if response.status_code != 200:
        # Ollama says {"error": "..."}; Claude says {"error": {"message": "..."}}.
        error = reply.get("error")
        detail = error.get("message") if isinstance(error, dict) else error
        raise ModelError(f"the model answered {response.status_code}: {detail or 'no detail'}")
    return reply
