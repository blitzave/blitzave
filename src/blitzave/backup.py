import sqlite3
from datetime import date
from pathlib import Path

# Pre-migration snapshots only (maintainer call, 2026-11-01): a normal start
# changes no schema, so the copy that matters is the one taken right before
# an update's migrations touch the user's only database.
KEEP = 3


def snapshot_before_migration(
    conn: sqlite3.Connection, version: int, backups_dir: Path, today: date | None = None
) -> Path:
    """Copy the database, as it is at `version`, into backups_dir.

    If a snapshot for this version and day already exists (a migration failed
    and the app was started again), keep it: it's the copy taken before the
    failed attempt touched anything."""
    backups_dir.mkdir(mode=0o700, exist_ok=True)
    day = (today or date.today()).isoformat()
    target = backups_dir / f"blitzave-v{version:03d}-{day}.db"
    if not target.exists():
        conn.execute("VACUUM INTO ?", (str(target),))
    prune(backups_dir)
    return target


def prune(backups_dir: Path, keep: int = KEEP) -> None:
    # Zero-padded version, then ISO date: name order is age order.
    snapshots = sorted(backups_dir.glob("blitzave-v*.db"))
    for old in snapshots[:-keep]:
        old.unlink()
