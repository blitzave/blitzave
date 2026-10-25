import json
import sqlite3


class AccountNotFoundError(Exception):
    """No account exists with the given id."""


class AccountConflictError(Exception):
    """The correction would collide with another account's display_name."""


def list_accounts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.id, a.account_type, a.account_number, a.display_name,
               assets.bank_name, assets.asset_type,
               liabilities.issuer_name, liabilities.liability_type
        FROM accounts a
        LEFT JOIN assets ON assets.account_id = a.id
        LEFT JOIN liabilities ON liabilities.account_id = a.id
        ORDER BY a.id
        """
    ).fetchall()
    return [
        {
            "id": row[0],
            "account_type": row[1],
            "account_number": row[2],
            "display_name": row[3],
            "bank_name": row[4],
            "asset_type": row[5],
            "issuer_name": row[6],
            "liability_type": row[7],
        }
        for row in rows
    ]


def update_account_type(
    conn: sqlite3.Connection,
    account_id: int,
    account_type: str,
    asset_type: str | None,
    liability_type: str | None,
) -> None:
    """Correct a guessed account_type/asset_type/liability_type. Moves the
    account between the assets/liabilities extension tables when
    account_type itself changes, carrying forward the existing bank_name/
    issuer_name if there is one, or falling back to the name SimpleFIN gave
    the account (stashed in accounts.meta at ingest)."""
    row = conn.execute("SELECT meta FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise AccountNotFoundError(f"no account with id {account_id}")
    fallback_name = json.loads(row[0]).get("name", "Unknown")

    try:
        conn.execute(
            "UPDATE accounts SET account_type = ? WHERE id = ?", (account_type, account_id)
        )

        if account_type == "asset":
            existing = conn.execute(
                "SELECT bank_name FROM assets WHERE account_id = ?", (account_id,)
            ).fetchone()
            bank_name = existing[0] if existing else fallback_name
            conn.execute(
                """
                INSERT INTO assets (account_id, bank_name, asset_type) VALUES (?, ?, ?)
                ON CONFLICT (account_id) DO UPDATE SET asset_type = excluded.asset_type
                """,
                (account_id, bank_name, asset_type),
            )
            conn.execute("DELETE FROM liabilities WHERE account_id = ?", (account_id,))
        else:
            existing = conn.execute(
                "SELECT issuer_name FROM liabilities WHERE account_id = ?", (account_id,)
            ).fetchone()
            issuer_name = existing[0] if existing else fallback_name
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
