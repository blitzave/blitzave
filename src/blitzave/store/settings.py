"""Settings that Live and Demo share, in one small JSON file in Live's folder:
today, which model Goose uses ({"goose": {...}}). Each profile has its own
database, and Demo's starts over, so these can't live in either one.

Never a secret: a Claude API key stays in the keychain.

A file, not a table (maintainer call, 2026-11-20): every table lives in one
profile's database. In Live's, saving a model from Demo on a fresh install
would create a Live database, and the next start would open an empty Live.
In each profile's, Demo's copy would vanish every time it starts over.
"""

import json
import os
from pathlib import Path

FILE_NAME = "settings.json"


def read(folder: Path) -> dict:
    try:
        return json.loads((folder / FILE_NAME).read_text())
    except FileNotFoundError:
        return {}


def update(folder: Path, changes: dict) -> None:
    """Replace the top-level keys in changes, keeping the rest. Written whole
    to a 0600 temp file and moved into place, so a crash never leaves half a
    file (as keychain.FileKeyring does)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FILE_NAME
    temp = path.with_name(FILE_NAME + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(read(folder) | changes, f)
    os.chmod(temp, 0o600)  # in case an old temp file was already there
    os.replace(temp, path)
