"""Goose's dev-mode trace (ARCHITECTURE.md "Goose"), so a wrong card can be
traced back to what the model was asked and what it said. Off unless
BLITZAVE_DEV=1.

One JSON line per question, in ~/BlitZave/traces/YYYY-MM-DD.jsonl: each model
call's prompt, the model's raw reply, how long it took, what the server said
about the call (tokens, seconds), and what the engine made of it. Only what
already crossed to the model and back: never a row, a count or a figure from
the user's money, because those never reach the model either. Nothing here is
read back into a prompt, and the folder isn't in a backup or an export.

Every question notes its calls in memory, dev mode or not; that costs a dict
per call and no I/O. Only write() touches the disk, and only in dev mode.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from blitzave.sources import models
from blitzave.store import db

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.environ.get("BLITZAVE_DEV") == "1"


@dataclass
class Turn:
    """One question's model calls, in order. evals/run.py reads these too."""

    model: models.ChatModel
    question: str
    calls: list[dict] = field(default_factory=list)

    def chat(self, step: str, system: str, messages: list[dict], schema: dict | None = None) -> str:
        """model.chat()'s text, noted down with the time it took and the
        server's usage. step is "plan", "repair" or "write"."""
        call = {"step": step, "system": system, "messages": list(messages)}
        self.calls.append(call)
        started = time.monotonic()
        try:
            reply = self.model.chat(system, messages, schema)
        except models.ModelError as e:
            call["error"] = str(e)
            raise
        finally:
            call["seconds"] = round(time.monotonic() - started, 2)
        call["reply"] = reply.text
        if reply.usage:
            call["usage"] = reply.usage
        return reply.text

    def verdict(self, text: str) -> None:
        """What the engine made of the last reply."""
        self.calls[-1]["verdict"] = text


def write(turn: Turn) -> None:
    """Add the turn to today's trace, in dev mode only. A trace that can't be
    written is logged and skipped; it never fails a question."""
    if not enabled():
        return
    line = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "provider": type(turn.model).__name__,
        "model": getattr(turn.model, "model", None),
        "question": turn.question,
        "calls": turn.calls,
    }
    folder = db.home_dir() / "traces"
    try:
        folder.mkdir(mode=0o700, exist_ok=True)
        with open(folder / f"{date.today().isoformat()}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except OSError as e:
        logger.warning("couldn't write Goose's dev trace: %s", e)
