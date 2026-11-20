import os
import tempfile

# No test may touch the real ~/BlitZave. Ever. Set before any test module
# imports blitzave, so even a default worked out at import time (jobs.LIVE)
# lands in a throwaway folder; each test then names its own.
os.environ["BLITZAVE_HOME"] = tempfile.mkdtemp(prefix="blitzave-tests-")

import keyring
import pytest

from blitzave import jobs


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    """No test may touch the real OS keychain. Ever."""
    store: dict[tuple[str, str], str] = {}

    def fake_set_password(service: str, username: str, password: str) -> None:
        store[(service, username)] = password

    def fake_get_password(service: str, username: str) -> str | None:
        return store.get((service, username))

    monkeypatch.setattr(keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    return store


@pytest.fixture(autouse=True)
def profile_as_it_was(monkeypatch):
    """Opening a profile changes process-wide state (BLITZAVE_HOME and which
    profile is showing). Registered here first, so every test leaves both as
    it found them."""
    monkeypatch.setenv("BLITZAVE_HOME", os.environ["BLITZAVE_HOME"])
    for name in ("PROFILE", "LIVE", "DEMO"):
        monkeypatch.setattr(jobs, name, getattr(jobs, name))
