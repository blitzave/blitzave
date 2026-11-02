"""Every matched pair: an exactly opposite debit and credit, one row in
`transfers`. Both legs leave activity, so a pair never counts as spending or
income. Whether a pair is a transfer, a refund or a credit is decided when
it's shown, never stored (refine/transfers.py: kind_of)."""

import sqlite3
import time

# Reuse the transactions store's row shape so a candidate looks exactly like
# a row from GET /api/transactions, and its lookup errors so the API maps
# "not found" and "ambiguous id" the same way everywhere.
from blitzave.store.transactions import (
    _SELECT,
    AmbiguousTransactionError,
    TransactionNotFoundError,
    _row_to_dict,
    _tags_for,
)


class TransferNotFoundError(Exception):
    """No transfer exists with the given id."""


class AlreadyTransferLegError(Exception):
    """The transaction is already one leg of a pair; it can't be in two."""


class InvalidTransferPairError(Exception):
    """The two transactions can't be a pair: the amounts don't cancel, or the
    currencies or users differ."""


def lookup(conn: sqlite3.Connection, external_id: str) -> tuple[int, int, int, str, str, int]:
    """(internal id, account id, amount_cents, posted_on, currency, user id)
    for a SimpleFIN id. SimpleFIN ids are unique only within an account, so
    two matches is an error, not a guess."""
    rows = conn.execute(
        "SELECT id, account_id, amount_cents, posted_on, currency, user_id "
        "FROM transactions WHERE external_id = ?",
        (external_id,),
    ).fetchall()
    if not rows:
        raise TransactionNotFoundError(f"no transaction with id {external_id}")
    if len(rows) > 1:
        raise AmbiguousTransactionError(
            f"transaction id {external_id} exists in more than one account"
        )
    return rows[0]


def is_linked(conn: sqlite3.Connection, internal_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM transfers WHERE ? IN (debit_transaction_id, credit_transaction_id)",
        (internal_id,),
    ).fetchone() is not None


def list_candidates(conn: sqlite3.Connection, external_id: str) -> list[dict]:
    """Every transaction that could be the other leg: the exact opposite
    amount in the same currency (€100 is not the other side of $100), not
    already in a pair, in any account. Each carries `same_account`, so the
    caller can say what linking it would make. No date window — the user is
    choosing by hand, so closest date first is enough."""
    internal_id, account_id, amount_cents, posted_on, currency, user_id = lookup(
        conn, external_id
    )
    if is_linked(conn, internal_id):
        raise AlreadyTransferLegError(f"transaction {external_id} is already linked")
    # activity already leaves out every paired leg, so reading from it is
    # the "not already linked" filter.
    rows = conn.execute(
        f"""
        {_SELECT}, a.account_id = ?
        FROM activity a JOIN accounts acc ON acc.id = a.account_id
        WHERE a.amount_cents = ? AND a.currency = ? AND a.user_id = ? AND a.id <> ?
        ORDER BY ABS(julianday(a.posted_on) - julianday(?)), a.id
        """,
        (account_id, -amount_cents, currency, user_id, internal_id, posted_on),
    ).fetchall()
    tags_by_txn = _tags_for(conn, [r[0] for r in rows])
    return [
        {**_row_to_dict(r, tags_by_txn.get(r[0], [])), "same_account": bool(r[-1])}
        for r in rows
    ]


PAIR_SELECT = """
    SELECT tr.id, tr.transfer_amount_cents, tr.transfer_date, tr.matched_by,
           d.external_id, dacc.display_name, d.posted_on, d.description, d.amount_cents,
           c.external_id, cacc.display_name, c.posted_on, c.description, c.amount_cents,
           d.account_id = c.account_id
    FROM transfers tr
    JOIN transactions d ON d.id = tr.debit_transaction_id
    JOIN accounts dacc ON dacc.id = d.account_id
    JOIN transactions c ON c.id = tr.credit_transaction_id
    JOIN accounts cacc ON cacc.id = c.account_id
"""


def _leg(row: tuple) -> dict:
    return {
        "id": row[0],  # SimpleFIN's own transaction id — never the internal one
        "account_display_name": row[1],
        "posted_on": row[2],
        "description": row[3],
        "amount_cents": row[4],
    }


def pair_to_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "amount_cents": row[1],
        "transfer_date": row[2],
        "matched_by": row[3],
        "debit": _leg(row[4:9]),
        "credit": _leg(row[9:14]),
        "same_account": bool(row[14]),
    }


def list_transfers(conn: sqlite3.Connection) -> list[dict]:
    """Every pair, newest first."""
    rows = conn.execute(f"{PAIR_SELECT} ORDER BY tr.transfer_date DESC, tr.id DESC").fetchall()
    return [pair_to_dict(r) for r in rows]


def get_transfer(conn: sqlite3.Connection, transfer_id: int) -> dict:
    """One pair with both legs, so the user can see what a row is linked to."""
    row = conn.execute(f"{PAIR_SELECT} WHERE tr.id = ?", (transfer_id,)).fetchone()
    if row is None:
        raise TransferNotFoundError(f"no transfer with id {transfer_id}")
    return pair_to_dict(row)


def insert_pair(
    conn: sqlite3.Connection, user_id: int, debit_id: int, credit_id: int, matched_by: str
) -> int:
    """One pair row; the caller commits. Banks post the two legs on different
    days, and the money isn't fully moved until the later leg lands, so that's
    the pair's date. The transfers_must_balance trigger is the backstop."""
    cursor = conn.execute(
        """
        INSERT INTO transfers
          (user_id, debit_transaction_id, credit_transaction_id,
           transfer_amount_cents, transfer_date, description, matched_by, created_at)
        SELECT ?, d.id, c.id, -d.amount_cents, MAX(d.posted_on, c.posted_on), NULL, ?, ?
        FROM transactions d, transactions c
        WHERE d.id = ? AND c.id = ?
        """,
        (user_id, matched_by, int(time.time()), debit_id, credit_id),
    )
    return cursor.lastrowid


def create_transfer(
    conn: sqlite3.Connection, user_id: int, external_id_a: str, external_id_b: str
) -> dict:
    """Link two transactions that cancel exactly, in one account or two. The
    caller may pass the legs in either order; the negative one is the debit.
    Categories on the legs are left alone: the legs drop out of activity, so
    they stop counting, and unlinking brings them back exactly as they were.
    Partial credits aren't supported for now (maintainer call, 2026-11-02)."""
    a_id, _, a_cents, _, a_currency, a_user = lookup(conn, external_id_a)
    b_id, _, b_cents, _, b_currency, b_user = lookup(conn, external_id_b)
    for internal_id, external_id in ((a_id, external_id_a), (b_id, external_id_b)):
        if is_linked(conn, internal_id):
            raise AlreadyTransferLegError(f"transaction {external_id} is already linked")

    if a_id == b_id:
        raise InvalidTransferPairError("a transaction can't be its own other side")
    if a_user != user_id or b_user != user_id:
        raise InvalidTransferPairError("both transactions must belong to this user")
    if a_currency != b_currency:
        raise InvalidTransferPairError("both transactions must use the same currency")
    if a_cents + b_cents != 0:
        raise InvalidTransferPairError("the two amounts don't cancel out to exactly zero")

    debit_id, credit_id = (a_id, b_id) if a_cents < 0 else (b_id, a_id)
    pair_id = insert_pair(conn, user_id, debit_id, credit_id, "user")
    conn.commit()
    return pair_to_dict(conn.execute(f"{PAIR_SELECT} WHERE tr.id = ?", (pair_id,)).fetchone())


def create_rule_pairs(conn: sqlite3.Connection, user_id: int, pairs: list[tuple[int, int]]) -> int:
    """Link the (debit id, credit id) pairs the sync's matcher found, in one commit."""
    for debit_id, credit_id in pairs:
        insert_pair(conn, user_id, debit_id, credit_id, "rule")
    conn.commit()
    return len(pairs)


def delete_transfer(conn: sqlite3.Connection, transfer_id: int) -> None:
    """Unlink a pair. Both legs return to activity with their categories
    intact. "Unlinking sticks" (AGENTS.md): each leg gets
    meta.no_auto_transfer, so the sync's matcher never links it again."""
    row = conn.execute(
        "SELECT debit_transaction_id, credit_transaction_id FROM transfers WHERE id = ?",
        (transfer_id,),
    ).fetchone()
    if row is None:
        raise TransferNotFoundError(f"no transfer with id {transfer_id}")
    conn.execute("DELETE FROM transfers WHERE id = ?", (transfer_id,))
    conn.execute(
        "UPDATE transactions SET meta = json_set(meta, '$.no_auto_transfer', json('true')) "
        "WHERE id IN (?, ?)",
        row,
    )
    conn.commit()


def unlinked_legs(conn: sqlite3.Connection, start: str | None = None) -> list[dict]:
    """Every activity row (so not yet in a pair) posted on or after start, or
    all of them when start is None: what the matcher needs."""
    where, params = ("WHERE posted_on >= ?", (start,)) if start else ("", ())
    rows = conn.execute(
        f"""
        SELECT id, account_id, amount_cents, currency, posted_on, description,
               COALESCE(json_extract(meta, '$.no_auto_transfer'), 0)
        FROM activity {where}
        """,
        params,
    ).fetchall()
    return [
        {
            "id": r[0], "account_id": r[1], "amount_cents": r[2], "currency": r[3],
            "posted_on": r[4], "description": r[5], "no_auto": bool(r[6]),
        }
        for r in rows
    ]
