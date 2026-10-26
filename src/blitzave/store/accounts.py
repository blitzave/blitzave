import json
import sqlite3


class AccountNotFoundError(Exception):
    """No account exists with the given id."""


class AccountConflictError(Exception):
    """The correction would collide with another account's display_name."""


def list_accounts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.external_id, a.account_type, a.account_number, a.display_name,
               assets.bank_name, assets.asset_type,
               liabilities.issuer_name, liabilities.liability_type,
               b.balance_cents, b.as_of,
               json_extract(a.meta, '$.name')
        FROM accounts a
        LEFT JOIN assets ON assets.account_id = a.id
        LEFT JOIN liabilities ON liabilities.account_id = a.id
        LEFT JOIN balances b ON b.account_id = a.id
            AND b.as_of = (SELECT MAX(as_of) FROM balances b2 WHERE b2.account_id = a.id)
        ORDER BY a.id
        """
    ).fetchall()
    return [
        {
            "id": row[0],  # SimpleFIN's own account id — never an internal sequential one
            "account_type": row[1],
            "account_number": row[2],
            # The human name the bank gave the account ("Platinum Card® (1002)").
            # display_name is the schema's uniqueness key ("liability-1002"); the
            # UI leads with `name` and keeps display_name only as a fallback.
            "name": row[10] or row[3],
            "display_name": row[3],
            "bank_name": row[4],
            "asset_type": row[5],
            "issuer_name": row[6],
            "liability_type": row[7],
            "balance_cents": row[8],
            "balance_as_of": row[9],
        }
        for row in rows
    ]


def update_account_type(
    conn: sqlite3.Connection,
    external_id: str,
    account_type: str,
    asset_type: str | None,
    liability_type: str | None,
) -> None:
    """Correct a guessed account_type/asset_type/liability_type, addressed by
    SimpleFIN's own account id (the public identifier — see list_accounts).
    Moves the account between the assets/liabilities extension tables when
    account_type itself changes. bank_name and issuer_name are the same
    institution seen from two tables, so it travels with the account across
    the move; only an account with neither falls back to the name SimpleFIN
    gave it (stashed in accounts.meta at ingest)."""
    row = conn.execute(
        """
        SELECT a.id, a.meta, assets.bank_name, liabilities.issuer_name
        FROM accounts a
        LEFT JOIN assets ON assets.account_id = a.id
        LEFT JOIN liabilities ON liabilities.account_id = a.id
        WHERE a.source = 'simplefin' AND a.external_id = ?
        """,
        (external_id,),
    ).fetchone()
    if row is None:
        raise AccountNotFoundError(f"no account with id {external_id}")
    account_id, meta_json, bank_name, issuer_name = row
    institution = bank_name or issuer_name or json.loads(meta_json).get("name", "Unknown")

    try:
        conn.execute(
            "UPDATE accounts SET account_type = ? WHERE id = ?", (account_type, account_id)
        )

        if account_type == "asset":
            conn.execute(
                """
                INSERT INTO assets (account_id, bank_name, asset_type) VALUES (?, ?, ?)
                ON CONFLICT (account_id) DO UPDATE SET asset_type = excluded.asset_type
                """,
                (account_id, institution, asset_type),
            )
            conn.execute("DELETE FROM liabilities WHERE account_id = ?", (account_id,))
        else:
            issuer_name = institution
            conn.execute(
                """
                INSERT INTO liabilities (account_id, issuer_name, liability_type) VALUES (?, ?, ?)
                ON CONFLICT (account_id) DO UPDATE SET liability_type = excluded.liability_type
                """,
                (account_id, issuer_name, liability_type),
            )
            conn.execute("DELETE FROM assets WHERE account_id = ?", (account_id,))
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "display_name" in str(e):
            raise AccountConflictError(
                f"another account already has the display_name this correction would produce: {e}"
            ) from e
        raise
    except Exception:
        conn.rollback()
        raise

    conn.commit()
