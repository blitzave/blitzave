import os
import sqlite3
from pathlib import Path

from blitzave import backup

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def home_dir() -> Path:
    override = os.environ.get("BLITZAVE_HOME")
    path = Path(override) if override else Path.home() / "BlitZave"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def db_path() -> Path:
    return home_dir() / "blitzave.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path())
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn, backups_dir=home_dir() / "backups")
    return conn


def migrate(conn: sqlite3.Connection, backups_dir: Path | None = None) -> None:
    """Apply pending migrations. Up to date (every normal start) runs no DDL.

    With backups_dir, a database that already holds data is snapshotted
    before the first pending migration. If the snapshot fails, nothing is
    migrated: an update never touches the only copy."""
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [
        (int(m.name.split("_", 1)[0]), m) for m in sorted(MIGRATIONS_DIR.glob("*.sql"))
    ]
    pending = [(v, m) for v, m in pending if v > current_version]
    if pending and current_version > 0 and backups_dir is not None:
        backup.snapshot_before_migration(conn, current_version, backups_dir)
    for version, migration in pending:
        conn.executescript(migration.read_text())
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
