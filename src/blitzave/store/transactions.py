import json
import sqlite3

from blitzave.store.categories import upsert_rule

# Whitelisted sort columns — the API's sort param maps here, so user input
# never reaches the ORDER BY clause as raw SQL.
_SORT_COLUMNS = {
    "date": "a.posted_on",
    "amount": "a.amount_cents",
    "description": "a.description COLLATE NOCASE",
}


class TransactionNotFoundError(Exception):
    """No transaction exists with the given id."""


class AmbiguousTransactionError(Exception):
    """More than one transaction carries this external id (possible across accounts)."""


class UnknownCategoryError(Exception):
    """The split map references a category id that doesn't exist."""


class UnknownTagError(Exception):
    """The tag list references a tag id that doesn't exist."""


def _tags_for(
    conn: sqlite3.Connection, internal_ids: list[int]
) -> dict[int, list[dict]]:
    if not internal_ids:
        return {}
    placeholders = ",".join("?" * len(internal_ids))
    tags_by_txn: dict[int, list[dict]] = {}
    for txn_id, tag_id, tag_name in conn.execute(
        f"""
        SELECT tt.transaction_id, t.id, t.name
        FROM transaction_tags tt JOIN tags t ON t.id = tt.tag_id
        WHERE tt.transaction_id IN ({placeholders})
        ORDER BY t.name COLLATE NOCASE
        """,
        internal_ids,
    ):
        tags_by_txn.setdefault(txn_id, []).append({"id": tag_id, "name": tag_name})
    return tags_by_txn


def _row_to_dict(row: tuple, tags: list[dict]) -> dict:
    return {
        "id": row[1],  # SimpleFIN's own transaction id — never the internal one
        "account_id": row[2],
        "account_display_name": row[3],
        "posted_on": row[4],
        "amount_cents": row[5],
        "currency": row[6],
        "transaction_type": row[7],
        "description": row[8],
        "merchant_name": row[9],
        "categories": json.loads(row[10]),
        "categories_set_by": row[11],
        "tags": tags,
        "transfer_id": row[12],  # set when this row is one leg of a transfer
        "refund_id": row[13],
    }


_SELECT = """
    SELECT a.id, a.external_id, acc.external_id, acc.display_name, a.posted_on,
           a.amount_cents, a.currency, a.transaction_type, a.description,
           a.merchant_name, a.categories, a.categories_set_by,
           (SELECT tr.id FROM transfers tr
            WHERE tr.debit_transaction_id = a.id OR tr.credit_transaction_id = a.id),
           (SELECT r.id FROM refunds r
            WHERE r.debit_transaction_id = a.id OR r.credit_transaction_id = a.id)
"""


def list_transactions(
    conn: sqlite3.Connection,
    *,
    account_external_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    q: str | None = None,
    category_id: int | None = None,
    tag_id: int | None = None,
    sort: str = "date",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    group_refunds: bool = False,
) -> tuple[int, list[dict]]:
    """Reads transactions, not the activity view, so each account shows its
    real debits and credits — a card payment still appears in checking.
    Transfer legs are labelled with transfer_id; totals and analysis still
    read activity, so a leg never counts as spending.

    With group_refunds, each refund pair comes back as one refund_group item
    (see _list_grouped)."""
    where = []
    params: list = []
    if account_external_id:
        where.append("acc.external_id = ?")
        params.append(account_external_id)
    if start:
        where.append("a.posted_on >= ?")
        params.append(start)
    if end:
        where.append("a.posted_on <= ?")
        params.append(end)
    if q:
        where.append("(a.description LIKE ? OR COALESCE(a.merchant_name, '') LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    if category_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM json_each(a.categories) je WHERE CAST(je.key AS INTEGER) = ?)"
        )
        params.append(category_id)
        # A leg doesn't count toward its category's total, so it mustn't be
        # listed under that category either.
        where.append(
            "NOT EXISTS (SELECT 1 FROM transfers tr "
            "WHERE tr.debit_transaction_id = a.id OR tr.credit_transaction_id = a.id)"
        )
    if tag_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM transaction_tags tt WHERE tt.transaction_id = a.id AND tt.tag_id = ?)"
        )
        params.append(tag_id)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    base = f"FROM transactions a JOIN accounts acc ON acc.id = a.account_id {where_sql}"
    direction = "ASC" if order == "asc" else "DESC"

    if group_refunds:
        return _list_grouped(conn, base, params, sort, direction, limit, offset)

    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]

    sort_column = _SORT_COLUMNS[sort]
    rows = conn.execute(
        f"{_SELECT} {base} ORDER BY {sort_column} {direction}, a.id {direction} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()

    tags_by_txn = _tags_for(conn, [r[0] for r in rows])
    return total, [_row_to_dict(r, tags_by_txn.get(r[0], [])) for r in rows]


_GROUP_SORT_COLUMNS = {
    "date": "s_date",
    "amount": "s_amount",
    "description": "s_description COLLATE NOCASE",
}


def _list_grouped(
    conn: sqlite3.Connection,
    base: str,
    params: list,
    sort: str,
    direction: str,
    limit: int,
    offset: int,
) -> tuple[int, list[dict]]:
    """Collapse each refund pair into one item BEFORE sorting and paging.
    Grouping on the client can't work: sorted by amount, -$14.02 and +$14.02
    land far apart and can fall on different pages. A pair is listed when
    either half matches the filters, and sorts by its later date, its net
    amount, and its combined description. Read-only: nothing is written."""
    items_sql = f"""
        WITH matched AS (
            SELECT a.id AS txn_id,
                   (SELECT r.id FROM refunds r
                    WHERE r.debit_transaction_id = a.id
                       OR r.credit_transaction_id = a.id) AS refund_id
            {base}
        ),
        items AS (
            SELECT DISTINCT
                   CASE WHEN refund_id IS NULL THEN txn_id END AS txn_id,
                   refund_id
            FROM matched
        ),
        sortable AS (
            SELECT i.txn_id, i.refund_id,
                   COALESCE(t.posted_on, MAX(d.posted_on, c.posted_on)) AS s_date,
                   COALESCE(t.amount_cents, d.amount_cents + c.amount_cents) AS s_amount,
                   COALESCE(t.description, d.description || ' + ' || c.description)
                       AS s_description,
                   COALESCE(t.id, d.id) AS s_tiebreak
            FROM items i
            LEFT JOIN transactions t ON t.id = i.txn_id
            LEFT JOIN refunds r ON r.id = i.refund_id
            LEFT JOIN transactions d ON d.id = r.debit_transaction_id
            LEFT JOIN transactions c ON c.id = r.credit_transaction_id
        )
    """
    total = conn.execute(f"{items_sql} SELECT COUNT(*) FROM items", params).fetchone()[0]
    sort_column = _GROUP_SORT_COLUMNS[sort]
    page = conn.execute(
        f"""{items_sql}
        SELECT txn_id, refund_id FROM sortable
        ORDER BY {sort_column} {direction}, s_tiebreak {direction}
        LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    ).fetchall()

    refund_ids = [refund_id for _, refund_id in page if refund_id is not None]
    legs_by_refund = {}
    if refund_ids:
        placeholders = ",".join("?" * len(refund_ids))
        legs_by_refund = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT id, debit_transaction_id, credit_transaction_id "
                f"FROM refunds WHERE id IN ({placeholders})",
                refund_ids,
            )
        }

    internal_ids = [txn_id for txn_id, _ in page if txn_id is not None]
    for debit_id, credit_id in legs_by_refund.values():
        internal_ids.extend((debit_id, credit_id))
    rows_by_id = {}
    if internal_ids:
        placeholders = ",".join("?" * len(internal_ids))
        raw = conn.execute(
            f"{_SELECT} FROM transactions a JOIN accounts acc ON acc.id = a.account_id "
            f"WHERE a.id IN ({placeholders})",
            internal_ids,
        ).fetchall()
        tags_by_txn = _tags_for(conn, [r[0] for r in raw])
        rows_by_id = {r[0]: _row_to_dict(r, tags_by_txn.get(r[0], [])) for r in raw}

    items = []
    for txn_id, refund_id in page:
        if refund_id is None:
            items.append({"kind": "transaction", **rows_by_id[txn_id]})
        else:
            debit_id, credit_id = legs_by_refund[refund_id]
            items.append(_refund_group(refund_id, rows_by_id[debit_id], rows_by_id[credit_id]))
    return total, items


def _refund_group(refund_id: int, debit: dict, credit: dict) -> dict:
    """One list item for a linked charge and its refund/credit. Both bank
    records stay intact and editable inside `legs`; the group only nets them
    for display (zero for a full refund)."""
    return {
        "kind": "refund_group",
        "id": f"refund-{refund_id}",
        "refund_id": refund_id,
        "account_id": debit["account_id"],
        "account_display_name": debit["account_display_name"],
        "posted_on": max(debit["posted_on"], credit["posted_on"]),
        "amount_cents": debit["amount_cents"] + credit["amount_cents"],
        "currency": debit["currency"],
        "description": f"{debit['description']} + {credit['description']}",
        "legs": [debit, credit],
    }


def get_transaction(conn: sqlite3.Connection, external_id: str) -> dict:
    rows = conn.execute(
        f"{_SELECT} FROM transactions a JOIN accounts acc ON acc.id = a.account_id "
        "WHERE a.external_id = ?",
        (external_id,),
    ).fetchall()
    if not rows:
        raise TransactionNotFoundError(f"no transaction with id {external_id}")
    if len(rows) > 1:
        raise AmbiguousTransactionError(
            f"transaction id {external_id} exists in more than one account"
        )
    row = rows[0]
    tags_by_txn = _tags_for(conn, [row[0]])
    return _row_to_dict(row, tags_by_txn.get(row[0], []))


def activity_descriptions(conn: sqlite3.Connection) -> list[dict]:
    """Every activity row's internal id, description and who set its
    categories — what rule matching needs. Matching itself happens outside
    store/, because the key is computed in Python (refine/categorize.py).
    Activity only: transfer legs never get a category."""
    rows = conn.execute(
        "SELECT id, description, categories_set_by FROM activity"
    ).fetchall()
    return [{"id": r[0], "description": r[1], "categories_set_by": r[2]} for r in rows]


def _check_ids_exist(conn: sqlite3.Connection, table: str, ids: list[int]) -> list[int]:
    placeholders = ",".join("?" * len(ids))
    known = {
        r[0]
        for r in conn.execute(
            f"SELECT id FROM {table} WHERE id IN ({placeholders})", ids
        )
    }
    return [i for i in ids if i not in known]


def update_metadata(
    conn: sqlite3.Connection,
    external_id: str,
    *,
    categories: dict[int, float] | None = None,
    tags: list[int] | None = None,
    rule_key: str | None = None,
    matching_ids: list[int] | None = None,
) -> int:
    """The only mutable parts of a transaction are its metadata — categories
    (with split fractions) and tags. Amount, date, description, and account
    are the bank's record and are never editable through any path.

    "Apply to all" passes rule_key (match_key of the description) and
    matching_ids (the activity rows the caller found with that key). The
    single category then becomes a rule under that key, and every OTHER
    matching row is recategorized now — even ones the user set by hand,
    because they just said "all". Everything lands in one commit. Returns how
    many other transactions were recategorized (0 without a rule_key)."""
    rows = conn.execute(
        "SELECT id, user_id, description FROM transactions WHERE external_id = ?",
        (external_id,),
    ).fetchall()
    if not rows:
        raise TransactionNotFoundError(f"no transaction with id {external_id}")
    if len(rows) > 1:
        raise AmbiguousTransactionError(
            f"transaction id {external_id} exists in more than one account"
        )
    txn_id, user_id, description = rows[0]

    if categories is not None:
        if categories:
            missing = _check_ids_exist(conn, "categories", list(categories))
            if missing:
                raise UnknownCategoryError(f"unknown category ids: {missing}")
        conn.execute(
            "UPDATE transactions SET categories = ?, categories_set_by = 'user' WHERE id = ?",
            (json.dumps({str(k): v for k, v in categories.items()}), txn_id),
        )

    applied_to_count = 0
    if rule_key is not None:
        # The API only allows apply to all with exactly one category.
        category_id = next(iter(categories))
        upsert_rule(conn, user_id, rule_key, description, category_id)
        for other_id in matching_ids or []:
            cursor = conn.execute(
                """
                UPDATE transactions SET categories = ?, categories_set_by = 'rule'
                WHERE id = ? AND id <> ? AND user_id = ?
                  AND id IN (SELECT id FROM activity)
                """,
                (json.dumps({str(category_id): 1.0}), other_id, txn_id, user_id),
            )
            applied_to_count += cursor.rowcount

    if tags is not None:
        unique_tags = sorted(set(tags))
        if unique_tags:
            missing = _check_ids_exist(conn, "tags", unique_tags)
            if missing:
                raise UnknownTagError(f"unknown tag ids: {missing}")
        conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (txn_id,))
        for tag_id in unique_tags:
            conn.execute(
                "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                (txn_id, tag_id),
            )

    conn.commit()
    return applied_to_count
