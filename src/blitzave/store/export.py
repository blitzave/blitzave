"""Export every table to one JSON document, and import one into an empty
database (P0.1). The user owns their data: this is how it moves to a new
laptop or survives a lost one.

The SimpleFIN credential is never in the database (it's in the OS keychain),
so it's never in an export. The hash of it in users.simplefin_hash is left
out too: an export holds money data, not anything about the connection.
"""

import sqlite3

FORMAT = "blitzave-export"

# Parents before children, so every foreign key and every trigger that looks
# at another table finds what it needs on import.
TABLES = (
    "users",
    "accounts",
    "assets",
    "liabilities",
    "balances",
    "categories",
    "category_rules",
    "tags",
    "transactions",
    "transaction_tags",
    "transfers",
    "job_runs",
)

# Columns an export never carries: the connection's hash, and columns the
# database computes itself (a generated column can't be inserted). The
# round-trip test fails loudly if a new generated column isn't listed here.
LEFT_OUT = {("users", "simplefin_hash"), ("accounts", "display_name")}


class ImportRefusedError(Exception):
    """The document can't go into this database: it isn't empty, the export
    is from another schema version, or a row broke one of the database's
    rules. Nothing was written."""


class NotEmptyError(ImportRefusedError):
    """This database already holds data; import only goes into a fresh one."""


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    # An empty SELECT names the columns without reading a row. (PRAGMA
    # table_info is off limits once migrations are done; see db.py.)
    cursor = conn.execute(f"SELECT * FROM {table} LIMIT 0")
    return [
        column[0] for column in cursor.description if (table, column[0]) not in LEFT_OUT
    ]


def export_all(conn: sqlite3.Connection) -> dict:
    """Every row of every table, as {format, schema_version, tables}. Each
    table is {columns, rows}, rows in id order."""
    tables = {}
    for table in TABLES:
        columns = _columns(conn, table)
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid"
        ).fetchall()
        tables[table] = {"columns": columns, "rows": [list(row) for row in rows]}
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    return {"format": FORMAT, "schema_version": version, "tables": tables}


def is_empty(conn: sqlite3.Connection) -> bool:
    """Nothing but the local user row the app makes on startup."""
    return all(
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None
        for table in TABLES
        if table != "users"
    )


def import_all(conn: sqlite3.Connection, document: dict) -> dict[str, int]:
    """Load an export into this database, all of it or none of it. Returns
    the number of rows per table."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if document.get("format") != FORMAT:
        raise ImportRefusedError("this isn't a BlitZave export")
    if document.get("schema_version") != version:
        raise ImportRefusedError(
            f"this export is from database version {document.get('schema_version')}; "
            f"this BlitZave is on version {version}. Update both to the same version first."
        )
    if not is_empty(conn):
        raise NotEmptyError(
            "this BlitZave already has data. Import only goes into a fresh install."
        )
    tables = document.get("tables", {})
    unknown = set(tables) - set(TABLES)
    if unknown:
        raise ImportRefusedError(f"unknown tables in the export: {', '.join(sorted(unknown))}")

    counts = {}
    try:
        # The startup user row is replaced by the export's own.
        conn.execute("DELETE FROM users")
        for table in TABLES:
            dump = tables.get(table, {"columns": [], "rows": []})
            allowed = set(_columns(conn, table))
            columns = dump["columns"]
            if not set(columns) <= allowed:
                raise ImportRefusedError(
                    f"{table} has columns this version doesn't: "
                    f"{', '.join(sorted(set(columns) - allowed))}"
                )
            if dump["rows"]:
                placeholders = ", ".join("?" for _ in columns)
                conn.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    dump["rows"],
                )
            counts[table] = len(dump["rows"])
    except sqlite3.Error as e:
        conn.rollback()
        raise ImportRefusedError(f"a row was refused: {e}") from e
    except ImportRefusedError:
        conn.rollback()
        raise
    conn.commit()
    return counts
