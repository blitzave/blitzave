from datetime import date, timedelta

from blitzave.refine.categorize import is_match, match_key
from blitzave.sources import simplefin
from blitzave.store import categories, db, ingest, transactions, users

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


def _categorize(conn, user_id: int) -> int:
    """Give each uncategorized activity row a rule's category: the rule with
    exactly the row's key if there is one, otherwise the oldest rule that
    matches (rules come oldest first). No match: it stays uncategorized."""
    rules = categories.list_rules(conn, user_id)
    assignments = {}
    for row in transactions.activity_descriptions(conn):
        if row["categories_set_by"] is not None:
            continue  # already decided — by the user, or by an earlier run
        key = match_key(row["description"])
        chosen = None
        for rule in rules:
            if rule["match_key"] == key:
                chosen = rule
                break
        if chosen is None:
            for rule in rules:
                if is_match(key, rule["match_key"]):
                    chosen = rule
                    break
        if chosen is not None:
            assignments[row["id"]] = chosen["category_id"]
    return categories.apply_rules(conn, assignments)


def sync() -> dict:
    """Run the pipeline steps that exist so far: ingest, then categorize.
    match_transfers, detect, and snapshot_backup join as their own tasks get
    built (see AGENTS.md "Refinement: the sync pipeline")."""
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

        # match_transfers belongs here, before categorize, once it's built.
        categorized = _categorize(conn, user_id)

        ingest.set_watermark(conn, SYNC_JOB, today.isoformat())

        return {
            "accounts": len(fetched.accounts),
            "transactions": len(fetched.transactions),
            "balances": len(fetched.balances),
            "categorized": categorized,
            "since": since.isoformat(),
        }
    finally:
        conn.close()
