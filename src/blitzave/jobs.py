from datetime import date, timedelta

from blitzave.sources import simplefin
from blitzave.store import db, ingest, users

SYNC_JOB = "ingest"
WATERMARK_OVERLAP_DAYS = 7  # banks post late; re-fetching the overlap is safe, upserts are idempotent
INITIAL_LOOKBACK_DAYS = 90  # how far back the very first sync reaches, before any watermark exists


class ConnectError(Exception):
    """Connecting to SimpleFIN failed for a reason the user can fix."""


class SyncError(Exception):
    """Syncing failed for a reason the user can fix (not connected, revoked access, ...)."""


def simplefin_connected() -> bool:
    return simplefin.is_connected()


def connect_simplefin(setup_token: str) -> None:
    try:
        simplefin_hash = simplefin.connect(setup_token)
    except simplefin.SimpleFinError as e:
        raise ConnectError(str(e)) from e

    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        users.set_simplefin_hash(conn, user_id, simplefin_hash)
    finally:
        conn.close()


def _sync_since(conn, today: date) -> date:
    watermark = ingest.get_watermark(conn, SYNC_JOB)
    if watermark is None:
        return today - timedelta(days=INITIAL_LOOKBACK_DAYS)
    return date.fromisoformat(watermark) - timedelta(days=WATERMARK_OVERLAP_DAYS)


def sync() -> dict:
    """Run the one pipeline step that exists so far: ingest. match_transfers,
    categorize, detect, and snapshot_backup join PIPELINE as their own tasks
    get built (see AGENTS.md "Refinement: the sync pipeline")."""
    if not simplefin.is_connected():
        raise SyncError("not connected to SimpleFIN yet — POST /api/connect first")

    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        today = date.today()
        since = _sync_since(conn, today)

        try:
            fetched = simplefin.fetch(since)
        except simplefin.SimpleFinError as e:
            raise SyncError(str(e)) from e

        account_ids_by_external_id = {}
        for account_row in fetched.accounts:
            account_ids_by_external_id[account_row.external_id] = ingest.upsert_account(
                conn, user_id, account_row
            )

        for tx_row in fetched.transactions:
            account_id = account_ids_by_external_id.get(tx_row.account_external_id)
            if account_id is None:
                continue  # the account itself didn't parse into a row; skip its transactions too
            ingest.upsert_transaction(conn, user_id, account_id, tx_row)

        for balance_row in fetched.balances:
            account_id = account_ids_by_external_id.get(balance_row.account_external_id)
            if account_id is None:
                continue
            ingest.upsert_balance(conn, account_id, balance_row)

        ingest.set_watermark(conn, SYNC_JOB, today.isoformat())

        return {
            "accounts": len(fetched.accounts),
            "transactions": len(fetched.transactions),
            "balances": len(fetched.balances),
            "since": since.isoformat(),
        }
    finally:
        conn.close()
