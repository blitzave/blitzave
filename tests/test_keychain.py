"""Where secrets live: the OS keychain when it answers, a file only the user
can read when it doesn't (keychain.py). No test here touches the real
keychain: each one sets a stand-in backend and puts the old one back."""

import json
import logging
import os
import stat
import sys

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import KeyringLocked, PasswordDeleteError

from blitzave import keychain
from blitzave.sources import simplefin

# Both stand-ins take an argument, so keyring never builds one on its own
# when it looks for the best backend (it only builds those that take none).


class WorkingKeychain(KeyringBackend):
    priority = 1

    def __init__(self, saved: dict):
        super().__init__()
        self.saved = saved

    def get_password(self, service, username):
        return self.saved.get((service, username))

    def set_password(self, service, username, password):
        self.saved[(service, username)] = password


class LockedKeychain(KeyringBackend):
    """A Secret Service with no unlocked collection, as on a server over ssh."""

    priority = 1

    def __init__(self, reason: str):
        super().__init__()
        self.reason = reason

    def get_password(self, service, username):
        raise KeyringLocked(self.reason)

    def set_password(self, service, username, password):
        raise KeyringLocked(self.reason)


@pytest.fixture(autouse=True)
def restore_backend():
    before = keyring.get_keyring()
    yield
    keyring.set_keyring(before)


def test_a_keychain_that_answers_is_kept(tmp_path):
    ring = WorkingKeychain({})
    keyring.set_keyring(ring)
    assert keychain.use_file_if_needed(tmp_path) is None
    assert keyring.get_keyring() is ring
    assert not (tmp_path / keychain.FILE_NAME).exists()


@pytest.mark.parametrize("missing", [fail.Keyring(), LockedKeychain("the collection is locked")], ids=["none", "locked"])
def test_no_keychain_means_a_file_only_the_user_can_read(tmp_path, missing, caplog):
    keyring.set_keyring(missing)
    with caplog.at_level(logging.WARNING):
        path = keychain.use_file_if_needed(tmp_path)
    assert path == tmp_path / keychain.FILE_NAME
    assert isinstance(keyring.get_keyring(), keychain.FileKeyring)
    assert str(path) in caplog.text  # said once, in plain words

    keyring.get_keyring().set_password("blitzave", "simplefin_access_url", "https://user:secret@bridge.example")
    assert json.loads(path.read_text()) == {"blitzave": {"simplefin_access_url": "https://user:secret@bridge.example"}}
    if sys.platform != "win32":  # Windows has no mode bits; it always has a keychain anyway
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert "secret" not in caplog.text


def test_the_rest_of_the_app_reads_the_file_without_knowing(tmp_path, monkeypatch):
    # conftest stands in for keyring's own functions; undo that here so the
    # call goes through whichever backend is set, as it does in the app.
    monkeypatch.undo()
    keyring.set_keyring(fail.Keyring())
    keychain.use_file_if_needed(tmp_path)
    simplefin.store_access_url("https://user:secret@bridge.example/simplefin")
    assert simplefin.load_access_url() == "https://user:secret@bridge.example/simplefin"
    assert simplefin.is_connected()


def test_the_file_keeps_each_secret_and_forgets_on_delete(tmp_path):
    ring = keychain.FileKeyring(tmp_path / keychain.FILE_NAME)
    assert ring.get_password("blitzave", "anthropic_api_key") is None
    ring.set_password("blitzave", "anthropic_api_key", "sk-one")
    ring.set_password("blitzave", "simplefin_access_url", "https://a")
    ring.set_password("blitzave", "anthropic_api_key", "sk-two")  # a new key replaces the old
    assert ring.get_password("blitzave", "anthropic_api_key") == "sk-two"
    assert ring.get_password("blitzave", "simplefin_access_url") == "https://a"

    ring.delete_password("blitzave", "anthropic_api_key")
    assert ring.get_password("blitzave", "anthropic_api_key") is None
    with pytest.raises(PasswordDeleteError):
        ring.delete_password("blitzave", "anthropic_api_key")


@pytest.mark.skipif(sys.platform == "win32", reason="no mode bits on Windows")
def test_a_file_someone_loosened_is_tightened_on_the_next_save(tmp_path):
    path = tmp_path / keychain.FILE_NAME
    ring = keychain.FileKeyring(path)
    ring.set_password("blitzave", "anthropic_api_key", "sk-one")
    os.chmod(path, 0o644)
    ring.set_password("blitzave", "anthropic_api_key", "sk-two")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not path.with_name(path.name + ".tmp").exists()
