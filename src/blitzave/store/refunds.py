"""Refunds and credits: pairs whose two legs share an account. They live in
`transfers` like every other pair (store/transfers.py); this module is the
same-account view behind /api/refunds, so the UI's contract is unchanged."""

import sqlite3

from blitzave.store import transfers
from blitzave.store.transfers import (
    AlreadyAdjustmentLinkedError,
    AlreadyTransferLegError,
)


class RefundNotFoundError(Exception):
    """No refund link exists with the given id."""


class InvalidRefundPairError(Exception):
    """A refund must exactly cancel a charge in the same account and currency."""


def _check_unlinked(conn: sqlite3.Connection, internal_id: int, external_id: str) -> None:
    # Either kind of link is "already linked" here, with one error type.
    try:
        transfers.check_unlinked(conn, internal_id, external_id)
    except AlreadyTransferLegError as e:
        raise AlreadyAdjustmentLinkedError(str(e)) from e


def list_candidates(conn: sqlite3.Connection, external_id: str) -> list[dict]:
    """Possible pairs for the user to review: the exact opposite amount in
    the same account and currency, nearest dates first."""
    internal_id = transfers.lookup(conn, external_id)[0]
    _check_unlinked(conn, internal_id, external_id)
    return transfers.candidates(conn, external_id, same_account=True)


def _refund_to_dict(pair: dict) -> dict:
    # /api/refunds has always called the pair's date adjustment_date.
    return {
        "id": pair["id"],
        "amount_cents": pair["amount_cents"],
        "adjustment_date": pair["transfer_date"],
        "debit": pair["debit"],
        "credit": pair["credit"],
        "matched_by": pair["matched_by"],
    }


def list_refunds(conn: sqlite3.Connection) -> list[dict]:
    return [_refund_to_dict(p) for p in transfers.list_pairs(conn, same_account=True)]


def create_refund(
    conn: sqlite3.Connection, user_id: int, external_id_a: str, external_id_b: str
) -> dict:
    """Manually link a charge and the credit that exactly cancels it, without
    changing either bank record. Like a transfer's legs, both leave activity,
    so the pair counts as nothing. Categories are left alone, so unlinking
    brings both rows back exactly as they were. Partial credits aren't
    supported for now (maintainer call, 2026-11-02)."""
    a_id, a_account, a_cents, _, a_currency, a_user = transfers.lookup(conn, external_id_a)
    b_id, b_account, b_cents, _, b_currency, b_user = transfers.lookup(conn, external_id_b)
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
    if a_cents + b_cents != 0:
        raise InvalidRefundPairError(
            "the credit must exactly cancel the charge (partial credits aren't supported yet)"
        )

    debit_id, credit_id = (a_id, b_id) if a_cents < 0 else (b_id, a_id)
    pair_id = transfers.insert_pair(conn, user_id, debit_id, credit_id, "user")
    conn.commit()
    row = conn.execute(f"{transfers.PAIR_SELECT} WHERE tr.id = ?", (pair_id,)).fetchone()
    return _refund_to_dict(transfers.pair_to_dict(row))


def delete_refund(conn: sqlite3.Connection, refund_id: int) -> None:
    if not transfers.unlink(conn, refund_id, same_account=True):
        raise RefundNotFoundError(f"no refund link with id {refund_id}")
