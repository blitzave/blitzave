import json
import sqlite3

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


def _tags_for(conn: sqlite3.Connection, internal_ids: list[int]) -> dict[int, list[dict]]:
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
    }


_SELECT = """
    SELECT a.id, a.external_id, acc.external_id, acc.display_name, a.posted_on,
           a.amount_cents, a.currency, a.transaction_type, a.description,
           a.merchant_name, a.categories, a.categories_set_by
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
) -> tuple[int, list[dict]]:
    """Reads the activity view, never transactions directly — transfer legs
    (once the matcher exists) must never show up in a spending list."""
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
    if tag_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM transaction_tags tt WHERE tt.transaction_id = a.id AND tt.tag_id = ?)"
        )
        params.append(tag_id)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    base = f"FROM activity a JOIN accounts acc ON acc.id = a.account_id {where_sql}"

    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]

    sort_column = _SORT_COLUMNS[sort]
    direction = "ASC" if order == "asc" else "DESC"
    rows = conn.execute(
        f"{_SELECT} {base} ORDER BY {sort_column} {direction}, a.id {direction} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()

    tags_by_txn = _tags_for(conn, [r[0] for r in rows])
    return total, [_row_to_dict(r, tags_by_txn.get(r[0], [])) for r in rows]


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


def _check_ids_exist(conn: sqlite3.Connection, table: str, ids: list[int]) -> list[int]:
    placeholders = ",".join("?" * len(ids))
    known = {
        r[0]
        for r in conn.execute(f"SELECT id FROM {table} WHERE id IN ({placeholders})", ids)
    }
    return [i for i in ids if i not in known]


def update_metadata(
    conn: sqlite3.Connection,
    external_id: str,
    *,
    categories: dict[int, float] | None = None,
    tags: list[int] | None = None,
) -> None:
    """The only mutable parts of a transaction are its metadata — categories
    (with split fractions) and tags. Amount, date, description, and account
    are the bank's record and are never editable through any path."""
    rows = conn.execute(
        "SELECT id FROM transactions WHERE external_id = ?", (external_id,)
    ).fetchall()
    if not rows:
        raise TransactionNotFoundError(f"no transaction with id {external_id}")
    if len(rows) > 1:
        raise AmbiguousTransactionError(
            f"transaction id {external_id} exists in more than one account"
        )
    txn_id = rows[0][0]

    if categories is not None:
        if categories:
            missing = _check_ids_exist(conn, "categories", list(categories))
            if missing:
                raise UnknownCategoryError(f"unknown category ids: {missing}")
        conn.execute(
            "UPDATE transactions SET categories = ?, categories_set_by = 'user' WHERE id = ?",
            (json.dumps({str(k): v for k, v in categories.items()}), txn_id),
        )

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
