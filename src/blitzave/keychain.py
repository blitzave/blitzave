"""Where BlitZave keeps its secrets: the SimpleFIN access URL and a model's
API key.

The OS keychain first, always: macOS Keychain, Windows Credential Locker, or
the desktop's Secret Service on Linux. All three come with the system, and
`keyring` reaches them with nothing else to install.

Some machines have none: a Linux server, WSL, a desktop with no keyring
running. There BlitZave keeps its secrets in one file in its own folder,
`credentials.json`, which only the user can read (0600, inside the 0700 data
folder), the way ssh and gh keep theirs. It says so once at startup. The file
is never in an export or a backup, which copy only the database.

The choice is made once, in main(), by asking the keychain for a name that
isn't there. The rest of the code calls `keyring` as before and never knows
which one answered.
"""

import json
import logging
import os
from pathlib import Path

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

logger = logging.getLogger(__name__)

FILE_NAME = "credentials.json"
# Asked for at startup to see whether the keychain answers. It's never
# saved, so asking finds nothing, and on macOS it raises no prompt.
PROBE = ("blitzave", "keychain-check")


class FileKeyring(KeyringBackend):
    """Secrets in one JSON file that only the user can read:
    {service: {username: secret}}. It needs a path, so keyring never picks it
    on its own (it only builds backends that take no arguments)."""

    priority = 0.1  # below every real keychain; only use_file_if_needed() sets it

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def get_password(self, service: str, username: str) -> str | None:
        return self._read().get(service, {}).get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        secrets = self._read()
        secrets.setdefault(service, {})[username] = password
        self._write(secrets)

    def delete_password(self, service: str, username: str) -> None:
        secrets = self._read()
        if username not in secrets.get(service, {}):
            raise PasswordDeleteError(f"nothing saved for {service}/{username}")
        del secrets[service][username]
        self._write(secrets)

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def _write(self, secrets: dict) -> None:
        # Written whole to a file that's 0600 from the moment it exists, then
        # moved into place, so a crash never leaves half a file, and the
        # secret is never readable by anyone else, not even for a moment.
        temp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(secrets, f)
        os.chmod(temp, 0o600)  # in case an old temp file was already there
        os.replace(temp, self.path)


def use_file_if_needed(home: Path) -> Path | None:
    """Switch to FileKeyring when the OS keychain can't answer. Returns the
    file's path when it did, None when the keychain is in use."""
    try:
        keyring.get_keyring().get_password(*PROBE)
        return None
    except KeyringError as e:
        reason = type(e).__name__  # never the message: it can name the store
    path = home / FILE_NAME
    keyring.set_keyring(FileKeyring(path))
    logger.warning(
        "No system keychain here (%s), so BlitZave keeps your bank connection and "
        "model keys in %s, readable only by you.",
        reason,
        path,
    )
    return path
