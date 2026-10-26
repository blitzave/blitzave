import sqlite3
import time

from blitzave.store.refunds import AlreadyAdjustmentLinkedError, is_refund_linked

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
    """The transaction is already one leg of a transfer; it can't be in two."""


class InvalidTransferPairError(Exception):
    """The two transactions can't be a transfer: same account, or the amounts don't cancel."""


def _lookup(conn: sqlite3.Connection, external_id: str) -> tuple[int, int, int, str, str]:
    """(internal id, account id, amount_cents, posted_on, currency) for a
    SimpleFIN id. SimpleFIN ids are unique only within an account, so two
    matches is an error, not a guess."""
    rows = conn.execute(
        "SELECT id, account_id, amount_cents, posted_on, currency "
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


def _is_transfer_leg(conn: sqlite3.Connection, internal_id: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM transfers WHERE debit_transaction_id = ? OR credit_transaction_id = ?",
            (internal_id, internal_id),
        ).fetchone()
        is not None
    )


def list_candidates(conn: sqlite3.Connection, external_id: str) -> list[dict]:
    """Every transaction that could be the other leg: the exact opposite amount
    in the same currency (€100 is not the other side of $100), in a different
    account, not already in a transfer. No date window — the user is
    choosing by hand, so closest date first is enough."""
    internal_id, account_id, amount_cents, posted_on, currency = _lookup(conn, external_id)
    if is_refund_linked(conn, internal_id):
        raise AlreadyAdjustmentLinkedError(
            f"transaction {external_id} is already linked as a refund or credit"
        )
    if _is_transfer_leg(conn, internal_id):
        raise AlreadyTransferLegError(f"transaction {external_id} is already part of a transfer")

    # activity already leaves out transfer legs, so reading from it is the
    # "not already linked" filter.
    rows = conn.execute(
        f"""
        {_SELECT}
        FROM activity a JOIN accounts acc ON acc.id = a.account_id
        WHERE a.amount_cents = ? AND a.currency = ? AND a.account_id <> ?
          AND NOT EXISTS (
            SELECT 1 FROM refunds r
            WHERE r.debit_transaction_id = a.id OR r.credit_transaction_id = a.id
          )
        ORDER BY ABS(julianday(a.posted_on) - julianday(?)), a.id
        """,
        (-amount_cents, currency, account_id, posted_on),
    ).fetchall()
    tags_by_txn = _tags_for(conn, [r[0] for r in rows])
    return [_row_to_dict(r, tags_by_txn.get(r[0], [])) for r in rows]


_TRANSFER_SELECT = """
    SELECT tr.id, tr.transfer_amount_cents, tr.transfer_date, tr.matched_by,
           d.external_id, dacc.display_name, d.posted_on, d.description, d.amount_cents,
           c.external_id, cacc.display_name, c.posted_on, c.description, c.amount_cents
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


def _transfer_to_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "amount_cents": row[1],
        "transfer_date": row[2],
        "matched_by": row[3],
        "debit": _leg(row[4:9]),
        "credit": _leg(row[9:14]),
    }


def list_transfers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"{_TRANSFER_SELECT} ORDER BY tr.transfer_date DESC, tr.id DESC"
    ).fetchall()
    return [_transfer_to_dict(r) for r in rows]


def create_transfer(
    conn: sqlite3.Connection, user_id: int, external_id_a: str, external_id_b: str
) -> dict:
    """Link two transactions as one transfer. The caller may pass the legs in
    either order; the negative one is the debit. Categories on the legs are
    left alone: the legs drop out of activity, so they stop counting, and
    unlinking brings them back exactly as they were."""
    a_id, a_account, a_cents, a_posted, a_currency = _lookup(conn, external_id_a)
    b_id, b_account, b_cents, b_posted, b_currency = _lookup(conn, external_id_b)

    for internal_id, external_id in ((a_id, external_id_a), (b_id, external_id_b)):
        if is_refund_linked(conn, internal_id):
            raise AlreadyAdjustmentLinkedError(
                f"transaction {external_id} is already linked as a refund or credit"
            )
        if _is_transfer_leg(conn, internal_id):
            raise AlreadyTransferLegError(
                f"transaction {external_id} is already part of a transfer"
            )

    # Checked here for a clear message; the transfers_must_balance trigger
    # is the backstop if this ever drifts.
    if a_account == b_account:
        raise InvalidTransferPairError("both transactions are in the same account")
    if a_currency != b_currency:
        raise InvalidTransferPairError("both transactions must use the same currency")
    if a_cents + b_cents != 0:
        raise InvalidTransferPairError("the two amounts don't cancel out to exactly zero")

    if a_cents < 0:
        debit_id, credit_id = a_id, b_id
    else:
        debit_id, credit_id = b_id, a_id

    # Banks post the two legs on different days. The money isn't fully moved
    # until the later leg lands, so that's the transfer's date. ISO dates
    # compare correctly as strings.
    transfer_date = max(a_posted, b_posted)

    cursor = conn.execute(
        """
        INSERT INTO transfers
          (user_id, debit_transaction_id, credit_transaction_id,
           transfer_amount_cents, transfer_date, description, matched_by, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, 'user', ?)
        """,
        (user_id, debit_id, credit_id, abs(a_cents), transfer_date, int(time.time())),
    )
    conn.commit()
    row = conn.execute(f"{_TRANSFER_SELECT} WHERE tr.id = ?", (cursor.lastrowid,)).fetchone()
    return _transfer_to_dict(row)


def delete_transfer(conn: sqlite3.Connection, transfer_id: int) -> None:
    """Unlink a transfer. Both legs return to activity with their categories
    intact. "Unlinking sticks" (AGENTS.md): each leg gets
    meta.no_auto_transfer so a future automatic matcher never re-pairs them."""
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
