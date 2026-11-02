import shutil
import sqlite3
from datetime import date

import pytest

from blitzave import backup
from blitzave.store import db

REAL_MIGRATIONS = db.MIGRATIONS_DIR
LATEST = int(max(REAL_MIGRATIONS.glob("*.sql")).name.split("_", 1)[0])


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("BLITZAVE_HOME", str(home))
    return home


def make_old_install(home, monkeypatch, tmp_path):
    """A database one migration behind the code, holding one user row."""
    old = tmp_path / "old_migrations"
    old.mkdir()
    for m in sorted(REAL_MIGRATIONS.glob("*.sql"))[:-1]:
        shutil.copy(m, old)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", old)
    conn = db.connect()
    conn.execute("INSERT INTO users (id, username, created_at) VALUES (1, 'rohit', 0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "MIGRATIONS_DIR", REAL_MIGRATIONS)


def snapshots(home):
    return sorted((home / "backups").glob("blitzave-v*.db"))


def test_fresh_install_takes_no_snapshot(home):
    db.connect().close()
    assert snapshots(home) == []


def test_up_to_date_start_takes_no_snapshot(home):
    db.connect().close()
    db.connect().close()
    assert snapshots(home) == []


def test_update_snapshots_the_old_database_before_migrating(
    home, monkeypatch, tmp_path
):
    make_old_install(home, monkeypatch, tmp_path)

    conn = db.connect()  # the update: one migration pending
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    conn.close()

    [snap] = snapshots(home)
    assert snap.name.startswith(f"blitzave-v{LATEST - 1:03d}-")
    copy = sqlite3.connect(snap)
    assert copy.execute("PRAGMA user_version").fetchone()[0] == LATEST - 1
    assert copy.execute("SELECT username FROM users").fetchall() == [("rohit",)]
    copy.close()


def test_failed_snapshot_means_no_migration(home, monkeypatch, tmp_path):
    make_old_install(home, monkeypatch, tmp_path)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "snapshot_before_migration", broken)
    with pytest.raises(OSError):
        db.connect()

    conn = sqlite3.connect(home / "blitzave.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST - 1
    conn.close()


def test_keeps_only_the_last_three(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    backups_dir = tmp_path / "backups"
    for version in range(1, 6):
        backup.snapshot_before_migration(conn, version, backups_dir, date(2026, 11, 1))
    conn.close()

    names = [p.name for p in sorted(backups_dir.glob("*.db"))]
    assert names == [
        "blitzave-v003-2026-11-01.db",
        "blitzave-v004-2026-11-01.db",
        "blitzave-v005-2026-11-01.db",
    ]


def test_retry_keeps_the_first_snapshot(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    backups_dir = tmp_path / "backups"
    day = date(2026, 11, 1)
    first = backup.snapshot_before_migration(conn, 6, backups_dir, day)
    before = first.read_bytes()

    conn.execute("CREATE TABLE half_done (x INTEGER)")  # a failed migration's leftovers
    conn.commit()
    again = backup.snapshot_before_migration(conn, 6, backups_dir, day)

    assert again == first
    assert again.read_bytes() == before
    conn.close()
