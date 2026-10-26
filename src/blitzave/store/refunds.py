import sqlite3
import time

from blitzave.store.transactions import (
    _SELECT,
    AmbiguousTransactionError,
    TransactionNotFoundError,
    _row_to_dict,
    _tags_for,
)


class RefundNotFoundError(Exception):
    """No refund link exists with the given id."""


class AlreadyAdjustmentLinkedError(Exception):
    """A transaction is already linked as a refund or transfer."""


class InvalidRefundPairError(Exception):
    """A refund must credit a charge in the same account and currency."""


def _lookup(conn: sqlite3.Connection, external_id: str) -> tuple[int, int, int, str, str, int]:
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


def is_refund_linked(conn: sqlite3.Connection, internal_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM refunds WHERE debit_transaction_id = ? OR credit_transaction_id = ?",
        (internal_id, internal_id),
    ).fetchone() is not None


def _check_unlinked(conn: sqlite3.Connection, internal_id: int, external_id: str) -> None:
    if is_refund_linked(conn, internal_id):
        raise AlreadyAdjustmentLinkedError(
            f"transaction {external_id} is already linked as a refund or credit"
        )
    transfer = conn.execute(
        "SELECT 1 FROM transfers WHERE debit_transaction_id = ? OR credit_transaction_id = ?",
        (internal_id, internal_id),
    ).fetchone()
    if transfer is not None:
        raise AlreadyAdjustmentLinkedError(
            f"transaction {external_id} is already part of a transfer"
        )


def list_candidates(conn: sqlite3.Connection, external_id: str) -> list[dict]:
    """Possible pairs for the user to review, never automatic refund decisions.

    Only the exact opposite amount in the same account and currency — the
    other side of this transaction, so a linked pair nets to zero. (The API
    can still link a partial credit; the picker just never offers one.)
    There is no date window or description guess; nearest dates appear
    first. Each transaction may have only one adjustment link.
    """
    internal_id, account_id, amount_cents, posted_on, currency, user_id = _lookup(
        conn, external_id
    )
    _check_unlinked(conn, internal_id, external_id)
    rows = conn.execute(
        f"""
        {_SELECT}
        FROM activity a JOIN accounts acc ON acc.id = a.account_id
        WHERE a.account_id = ? AND a.currency = ? AND a.user_id = ?
          AND a.amount_cents = ?
          AND NOT EXISTS (
            SELECT 1 FROM refunds r
            WHERE r.debit_transaction_id = a.id OR r.credit_transaction_id = a.id
          )
        ORDER BY ABS(julianday(a.posted_on) - julianday(?)), a.id
        """,
        (account_id, currency, user_id, -amount_cents, posted_on),
    ).fetchall()
    tags_by_txn = _tags_for(conn, [r[0] for r in rows])
    return [_row_to_dict(r, tags_by_txn.get(r[0], [])) for r in rows]


_REFUND_SELECT = """
    SELECT r.id, c.amount_cents, MAX(d.posted_on, c.posted_on),
           d.external_id, dacc.display_name, d.posted_on, d.description, d.amount_cents,
           c.external_id, cacc.display_name, c.posted_on, c.description, c.amount_cents
    FROM refunds r
    JOIN transactions d ON d.id = r.debit_transaction_id
    JOIN accounts dacc ON dacc.id = d.account_id
    JOIN transactions c ON c.id = r.credit_transaction_id
    JOIN accounts cacc ON cacc.id = c.account_id
"""


def _leg(row: tuple) -> dict:
    return {
        "id": row[0],
        "account_display_name": row[1],
        "posted_on": row[2],
        "description": row[3],
        "amount_cents": row[4],
    }


def _refund_to_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "amount_cents": row[1],
        "adjustment_date": row[2],
        "debit": _leg(row[3:8]),
        "credit": _leg(row[8:13]),
    }


def list_refunds(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(f"{_REFUND_SELECT} ORDER BY 3 DESC, r.id DESC").fetchall()
    return [_refund_to_dict(row) for row in rows]


def create_refund(
    conn: sqlite3.Connection, user_id: int, external_id_a: str, external_id_b: str
) -> dict:
    """Manually link a charge and credit without changing either bank record.

    Both stay in activity. Categories, including existing user corrections,
    remain untouched; a refund offsets whichever category the user assigns.
    """
    a_id, a_account, a_cents, _, a_currency, a_user = _lookup(conn, external_id_a)
    b_id, b_account, b_cents, _, b_currency, b_user = _lookup(conn, external_id_b)
    _check_unlinked(conn, a_id, external_id_a)
    _check_unlinked(conn, b_id, external_id_b)

    if a_user != user_id or b_user != user_id:
        raise InvalidRefundPairError("both transactions must belong to this user")
    if a_account != b_account:
        raise InvalidRefundPairError("a refund or credit must be in the charge's account")
    if a_currency != b_currency:
        raise InvalidRefundPairError("both transactions must use the same currency")
    if (a_cents < 0) == (b_cents < 0):
        raise InvalidRefundPairError("choose one negative charge and one positive credit")

    debit_id, credit_id = (a_id, b_id) if a_cents < 0 else (b_id, a_id)
    charge_cents = -min(a_cents, b_cents)
    credit_cents = max(a_cents, b_cents)
    if credit_cents > charge_cents:
        raise InvalidRefundPairError("the credit cannot be larger than the charge")

    cursor = conn.execute(
        "INSERT INTO refunds (user_id, debit_transaction_id, credit_transaction_id, created_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, debit_id, credit_id, int(time.time())),
    )
    conn.commit()
    row = conn.execute(f"{_REFUND_SELECT} WHERE r.id = ?", (cursor.lastrowid,)).fetchone()
    return _refund_to_dict(row)


def delete_refund(conn: sqlite3.Connection, refund_id: int) -> None:
    cursor = conn.execute("DELETE FROM refunds WHERE id = ?", (refund_id,))
    if cursor.rowcount == 0:
        raise RefundNotFoundError(f"no refund link with id {refund_id}")
    conn.commit()
