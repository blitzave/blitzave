import os
from dataclasses import dataclass
from datetime import date, timedelta
from importlib import metadata
from pathlib import Path
from types import ModuleType

from blitzave import demo
from blitzave.refine import transfers as match
from blitzave.refine.categorize import is_match, match_key
from blitzave.sources import github, simplefin
from blitzave.sources.base import Fetched
from blitzave.store import categories, db, ingest, transactions, transfers, users

SYNC_JOB = "ingest"
MATCH_JOB = "match_transfers"  # its watermark only records that the one full pass ran
WATERMARK_OVERLAP_DAYS = 7  # banks post late; re-fetching the overlap is safe, upserts are idempotent
INITIAL_LOOKBACK_DAYS = 90  # how far back the very first sync reaches, before any watermark exists

DEMO_ONLY = "This demo can't hold your data. Stop it and run `blitzave` instead."


@dataclass(frozen=True)
class Profile:
    """Which data the app is showing: Live, the user's own money, or Demo, a
    made-up household (blitzave.demo). Same engine, same pages; these fields
    are the differences. Demo also starts over every time it opens: see
    open_profile."""

    name: str  # "live" or "demo"
    home: Path  # its data folder; BLITZAVE_HOME points here while it's open
    source: ModuleType | None  # its bank (sources.simplefin); None: nothing to sync


# start() makes the real ones. Until then (in tests, say) Live is showing, in
# whatever folder BLITZAVE_HOME names, and there's no Demo to switch to.
# Nothing moves BLITZAVE_HOME before start(): open_live() does nothing when
# Live is already showing, and there's no DEMO to open.
PROFILE = Profile("live", db.home_path(), simplefin)  # the one showing
LIVE: Profile | None = PROFILE  # None: `blitzave --demo`, which has no Live
DEMO: Profile | None = None


class ConnectError(Exception):
    """Connecting to SimpleFIN failed for a reason the user can fix."""


class SyncError(Exception):
    """Syncing failed for a reason the user can fix (not connected, revoked access, ...)."""


class UpdateCheckError(Exception):
    """The opt-in update check couldn't get an answer from GitHub."""


class DemoOnlyError(Exception):
    """`blitzave --demo` was asked to become the real setup; it never does."""


def simplefin_connected() -> bool:
    """Whether a SimpleFIN token is saved, whichever profile is showing."""
    return simplefin.is_connected()


def start(demo_only: bool = False) -> Profile:
    """Open the profile to show at startup. Live when there's a token or a
    Live database: anyone with real data never sees made-up numbers, even if
    their keychain entry went missing. Demo otherwise, which is a fresh
    install. `blitzave --demo` is Demo in a temporary folder with a keychain
    in memory, and no Live to switch to; it never reads the real keychain."""
    global LIVE, DEMO
    if demo_only:
        LIVE = None
        DEMO = Profile("demo", demo.start_throwaway(), None)
    else:
        home = db.home_path()
        LIVE = Profile("live", home, simplefin)
        DEMO = Profile("demo", home / "demo", None)
    first = LIVE if live_has_data() else DEMO
    open_profile(first)
    return first


def live_has_data() -> bool:
    """Whether Live has anything to show: a saved token or a database. False
    for `blitzave --demo`, which has no Live and never reads the keychain."""
    if LIVE is None:
        return False
    return (LIVE.home / "blitzave.db").exists() or simplefin.is_connected()


def open_profile(profile: Profile) -> None:
    """Show this profile from now on. Nothing is deleted, except Demo's old
    household: Demo starts over every time it opens, as of today."""
    global PROFILE
    os.environ["BLITZAVE_HOME"] = str(profile.home)  # db reads it on every connect
    PROFILE = profile
    if profile is DEMO:
        start_demo_over(demo.build(date.today()))


def start_demo_over(household: Fetched) -> None:
    """Demo's database, made again: the household, then what a few months of
    use would have taught it (demo.set_up). Only ever Demo's own folder."""
    if PROFILE is not DEMO or (LIVE is not None and DEMO.home == LIVE.home):
        raise RuntimeError("only Demo's own database ever starts over")
    for name in ("blitzave.db", "blitzave.db-wal", "blitzave.db-shm"):
        (DEMO.home / name).unlink(missing_ok=True)
    conn = db.connect()
    try:
        _ingest(conn, users.get_or_create_user(conn), household)
    finally:
        conn.close()
    demo.set_up()


def open_live() -> None:
    """The user is bringing in their own data (a bank, or an export), and it
    belongs in Live. `blitzave --demo` has no Live, so it refuses."""
    if LIVE is None:
        raise DemoOnlyError(DEMO_ONLY)
    if PROFILE is not LIVE:
        open_profile(LIVE)


def connect_simplefin(setup_token: str) -> None:
    if LIVE is None:
        # Refused before claiming: a setup token works only once.
        raise ConnectError("This demo can't connect a bank. Stop it and run `blitzave` instead.")
    try:
        # Claims, then keeps the access URL in the keychain, which Demo and
        # Live share. A bad token raises here, so Demo carries on as it was.
        simplefin_hash = simplefin.connect(setup_token)
    except simplefin.SimpleFinError as e:
        raise ConnectError(str(e)) from e
    open_live()  # the bank's data is Live's

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


def _match_pairs(conn, user_id: int, fetched: Fetched) -> dict:
    """Link the other side of each new transaction. Every pair is one
    `transfers` row, whatever it's called when shown: two accounts make a
    transfer, one account a refund or credit.

    The first run looks at all history once. After that, only the fetched
    range matters, widened by two windows: a fetched row's candidate can sit
    one window earlier, and that candidate's own rivals one window before it."""
    if ingest.get_watermark(conn, MATCH_JOB) is None:
        start = None
    elif fetched.transactions:
        earliest = min(date.fromisoformat(t.posted_on) for t in fetched.transactions)
        start = (earliest - timedelta(days=2 * match.WINDOW_DAYS)).isoformat()
    else:
        return {"transfers_matched": 0, "refunds_matched": 0}

    legs = [match.Leg(**row) for row in transfers.unlinked_legs(conn, start)]
    transfer_pairs, same_account_pairs = match.find_pairs(legs)
    transfers.create_rule_pairs(conn, user_id, transfer_pairs + same_account_pairs)
    ingest.set_watermark(conn, MATCH_JOB, date.today().isoformat())
    return {
        "transfers_matched": len(transfer_pairs),
        "refunds_matched": len(same_account_pairs),  # refunds and credits
    }


def _fetch(since: date, until: date | None = None) -> Fetched:
    source = PROFILE.source
    if source is None:
        raise SyncError("The demo has no bank to sync.")
    if not source.is_connected():
        raise SyncError("not connected to SimpleFIN yet — POST /api/connect first")
    try:
        return source.fetch(since, until)
    except simplefin.SimpleFinError as e:
        raise SyncError(str(e)) from e


def _ingest(conn, user_id: int, fetched: Fetched) -> dict:
    """Upsert what SimpleFIN returned, link matched pairs, then
    categorize what's left. Idempotent, so
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

    # Before categorize, so linked legs never get a category.
    matched = _match_pairs(conn, user_id, fetched)
    categorized = _categorize(conn, user_id)

    return {
        "accounts": len(fetched.accounts),
        "transactions": len(fetched.transactions),
        "balances": len(fetched.balances),
        **matched,
        "categorized": categorized,
    }


def sync() -> dict:
    """Run the pipeline steps that exist so far: ingest, match_transfers,
    categorize. detect joins when the detectors are built (see AGENTS.md
    "Refinement: the sync pipeline")."""
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
        # The release's notes page, so the UI can say what changed.
        "release_url": github.RELEASE_PAGE_URL.format(version=latest) if newer else None,
    }
