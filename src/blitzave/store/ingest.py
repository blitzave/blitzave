import json
import logging
import sqlite3
import time

from blitzave.sources.base import AccountRow, BalanceRow, TxRow

logger = logging.getLogger(__name__)


def upsert_account(conn: sqlite3.Connection, user_id: int, row: AccountRow) -> int:
    """Insert the account if it's new. For an existing one, only the
    institution name is refreshed — account_type and asset_type/
    liability_type are the user's to correct (PATCH /api/accounts/{id}), so a
    resync never touches them."""
    existing = conn.execute(
        "SELECT id FROM accounts WHERE source = 'simplefin' AND external_id = ?",
        (row.external_id,),
    ).fetchone()
    if existing is not None:
        _refresh_institution(conn, existing[0], row.bank_name or row.issuer_name)
        return existing[0]

    cursor = conn.execute(
        """
        INSERT INTO accounts (user_id, account_type, account_number, source, external_id, meta)
        VALUES (?, ?, ?, 'simplefin', ?, ?)
        """,
        (user_id, row.account_type, row.account_number, row.external_id, json.dumps(row.meta)),
    )
    account_id = cursor.lastrowid

    if row.account_type == "asset":
        conn.execute(
            "INSERT INTO assets (account_id, bank_name, asset_type) VALUES (?, ?, ?)",
            (account_id, row.bank_name, row.asset_type),
        )
    else:
        conn.execute(
            "INSERT INTO liabilities (account_id, issuer_name, liability_type) VALUES (?, ?, ?)",
            (account_id, row.issuer_name, row.liability_type),
        )
    conn.commit()
    return account_id


def _refresh_institution(conn: sqlite3.Connection, account_id: int, institution: str | None) -> None:
    """The institution is the bank's data, not a user correction, so every
    sync may refresh it. This also repairs rows an early parser stored with
    the account's own name as the issuer. "Unknown" is the parser's
    placeholder for a missing org, never better than what's stored."""
    if not institution or institution == "Unknown":
        return
    conn.execute(
        "UPDATE assets SET bank_name = ? WHERE account_id = ?", (institution, account_id)
    )
    conn.execute(
        "UPDATE liabilities SET issuer_name = ? WHERE account_id = ?", (institution, account_id)
    )
    conn.commit()


def upsert_transaction(conn: sqlite3.Connection, user_id: int, account_id: int, row: TxRow) -> None:
    """Insert the transaction if it's new. Never change an existing one's
    amount — if the bank now reports a different figure for a known id, log
    it loudly and leave the stored row alone (a silent change could unbalance
    a transfer)."""
    existing = conn.execute(
        "SELECT amount_cents FROM transactions WHERE account_id = ? AND external_id = ?",
        (account_id, row.external_id),
    ).fetchone()
    if existing is not None:
        if existing[0] != row.amount_cents:
            logger.warning(
                "transaction external_id=%s on account_id=%s: bank now reports "
                "amount_cents=%s but the stored row has amount_cents=%s — leaving "
                "the stored row unchanged",
                row.external_id,
                account_id,
                row.amount_cents,
                existing[0],
            )
        return

    conn.execute(
        """
        INSERT INTO transactions
            (user_id, account_id, external_id, amount_cents, currency, transaction_type,
             posted_on, description, merchant_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            account_id,
            row.external_id,
            row.amount_cents,
            row.currency,
            row.transaction_type,
            row.posted_on,
            row.description,
            row.merchant_name,
            int(time.time()),
        ),
    )
    conn.commit()


def upsert_balance(conn: sqlite3.Connection, account_id: int, row: BalanceRow) -> None:
    """Balances are a daily snapshot, not an append-only ledger like
    transactions — the latest figure for a given day should win if synced
    more than once that day."""
    conn.execute(
        """
        INSERT INTO balances (account_id, as_of, balance_cents)
        VALUES (?, ?, ?)
        ON CONFLICT (account_id, as_of) DO UPDATE SET balance_cents = excluded.balance_cents
        """,
        (account_id, row.as_of, row.balance_cents),
    )
    conn.commit()


def get_watermark(conn: sqlite3.Connection, job: str) -> str | None:
    row = conn.execute("SELECT watermark FROM job_runs WHERE job = ?", (job,)).fetchone()
    return row[0] if row else None


def set_watermark(conn: sqlite3.Connection, job: str, watermark: str) -> None:
    conn.execute(
        """
        INSERT INTO job_runs (job, last_ok_at, watermark)
        VALUES (?, ?, ?)
        ON CONFLICT (job) DO UPDATE SET last_ok_at = excluded.last_ok_at, watermark = excluded.watermark
        """,
        (job, int(time.time()), watermark),
    )
    conn.commit()
