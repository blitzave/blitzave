from datetime import date, timedelta
from importlib import metadata

from blitzave.refine.categorize import is_match, match_key
from blitzave.sources import github, simplefin
from blitzave.sources.base import Fetched
from blitzave.store import categories, db, ingest, transactions, users

SYNC_JOB = "ingest"
WATERMARK_OVERLAP_DAYS = 7  # banks post late; re-fetching the overlap is safe, upserts are idempotent
INITIAL_LOOKBACK_DAYS = 90  # how far back the very first sync reaches, before any watermark exists


class ConnectError(Exception):
    """Connecting to SimpleFIN failed for a reason the user can fix."""


class SyncError(Exception):
    """Syncing failed for a reason the user can fix (not connected, revoked access, ...)."""


class UpdateCheckError(Exception):
    """The opt-in update check couldn't get an answer from GitHub."""


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


def _fetch(since: date, until: date | None = None) -> Fetched:
    if not simplefin.is_connected():
        raise SyncError("not connected to SimpleFIN yet — POST /api/connect first")
    try:
        return simplefin.fetch(since, until)
    except simplefin.SimpleFinError as e:
        raise SyncError(str(e)) from e


def _ingest(conn, user_id: int, fetched: Fetched) -> dict:
    """Upsert what SimpleFIN returned, then categorize. Idempotent, so
    fetching an overlapping range again never duplicates anything."""
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

    return {
        "accounts": len(fetched.accounts),
        "transactions": len(fetched.transactions),
        "balances": len(fetched.balances),
        "categorized": categorized,
    }


def sync() -> dict:
    """Run the pipeline steps that exist so far: ingest, then categorize.
    match_transfers, detect, and snapshot_backup join as their own tasks get
    built (see AGENTS.md "Refinement: the sync pipeline")."""
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        today = date.today()
        since = _sync_since(conn, today)
        fetched = _fetch(since)
        result = _ingest(conn, user_id, fetched)
        ingest.set_watermark(conn, SYNC_JOB, today.isoformat())
        return {**result, "since": since.isoformat()}
    finally:
        conn.close()


def sync_month(year: int, month: int) -> dict:
    """Re-fetch one calendar month: the "Sync October 2026" button. Safe to
    repeat, since ingest is idempotent. Leaves the watermark alone: it marks
    how far the recent sync has reached, and an old month says nothing about
    that."""
    first_day = date(year, month, 1)
    next_first_day = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    fetched = _fetch(first_day, next_first_day)
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        result = _ingest(conn, user_id, fetched)
        return {**result, "month": f"{year:04d}-{month:02d}"}
    finally:
        conn.close()


def check_for_update(today: date) -> None:
    """The opt-in update check: at most once a day, ask GitHub for the latest
    release and remember it. Off until the user says yes, so a fresh install
    never touches the network for this. The day is recorded before asking,
    so even a failed attempt counts as that day's one request."""
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        meta = users.get_meta(conn, user_id)
        if meta.get("update_check") is not True:
            return
        if meta.get("update_checked_on") == today.isoformat():
            return
        users.update_meta(conn, user_id, {"update_checked_on": today.isoformat()})
        try:
            latest = github.fetch_latest_version()
        except github.UpdateCheckError as e:
            raise UpdateCheckError(str(e)) from e
        users.update_meta(conn, user_id, {"latest_version": latest})
    finally:
        conn.close()


def set_update_check(enabled: bool) -> None:
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        users.update_meta(conn, user_id, {"update_check": enabled})
    finally:
        conn.close()


def update_status() -> dict:
    """What the UI needs: whether the user has answered (None until then),
    the running version, and a newer release if one is known. A newer
    release only counts while checks are on."""
    conn = db.connect()
    try:
        meta = users.get_meta(conn, users.get_or_create_user(conn))
    finally:
        conn.close()

    enabled = meta.get("update_check")
    installed = metadata.version("blitzave")
    latest = meta.get("latest_version")
    newer = enabled is True and latest is not None and github.is_newer(latest, installed)
    return {
        "enabled": enabled,
        "installed_version": installed,
        "available_version": latest if newer else None,
    }
