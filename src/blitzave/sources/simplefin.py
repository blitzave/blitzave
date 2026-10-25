import base64
import binascii
import hashlib
import re
from datetime import date, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
import keyring

from blitzave.api.money import parse_amount
from blitzave.sources.base import AccountRow, BalanceRow, Fetched, TxRow

KEYRING_SERVICE = "blitzave"
KEYRING_USERNAME = "simplefin_access_url"

# Satisfies sources.base.Source's shape (name + fetch(since)) by convention —
# there's only one source so far, so jobs.py calls this module directly
# rather than through the Protocol. Formalize when a second source exists.
name = "simplefin"

_LAST_FOUR_DIGITS = re.compile(r"(\d{4})(?!.*\d)")


class SimpleFinError(Exception):
    """Base error for anything that goes wrong talking to SimpleFIN."""


class ClaimAlreadyUsedError(SimpleFinError):
    """The setup token was already claimed, or was never valid."""


class NotConnectedError(SimpleFinError):
    """No SimpleFIN access URL is stored yet; call connect() first."""


class AccessRevokedError(SimpleFinError):
    """SimpleFIN access was revoked (403 on /accounts); the user must reconnect."""


class SubscriptionRequiredError(SimpleFinError):
    """The user's SimpleFIN subscription needs payment (402)."""


def claim_access_url(setup_token: str) -> str:
    """Exchange a one-time setup token for a SimpleFIN access URL.

    The setup token is Base64 of the claim URL. Claiming it is a one-time
    action: a second claim with the same token returns 403.
    """
    try:
        claim_url = base64.b64decode(setup_token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as e:
        raise SimpleFinError(f"setup token is not valid base64: {e}") from e

    try:
        response = httpx.post(claim_url)
    except httpx.HTTPError as e:
        raise SimpleFinError(f"could not reach SimpleFIN: {e}") from e

    if response.status_code == 403:
        raise ClaimAlreadyUsedError(
            "This setup token was already used or is invalid. Disable it in "
            "SimpleFIN Bridge and create a new one."
        )
    if response.status_code != 200:
        raise SimpleFinError(
            f"SimpleFIN claim failed with status {response.status_code}: {response.text}"
        )

    return response.text.strip()


def access_url_hash(access_url: str) -> str:
    """A non-secret fingerprint of the access URL, safe to store in the database."""
    return hashlib.sha256(access_url.encode("utf-8")).hexdigest()


def store_access_url(access_url: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, access_url)


def load_access_url() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)


def is_connected() -> bool:
    return load_access_url() is not None


def connect(setup_token: str) -> str:
    """Claim a setup token, store the access URL in the keychain, and return
    its hash (the only part of this that's safe to persist elsewhere)."""
    access_url = claim_access_url(setup_token)
    store_access_url(access_url)
    return access_url_hash(access_url)


def _split_credentials(access_url: str) -> tuple[str, str, str]:
    """Pull HTTP Basic credentials out of the access URL so the request URL
    itself (which might end up in an error message or a log line) never
    carries them — only the Authorization header does."""
    parts = urlsplit(access_url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    base_url = urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
    return base_url, parts.username or "", parts.password or ""


_SKIP_KEYWORDS = (
    "invest",
    "401k",
    "401(k)",
    "ira",
    "brokerage",
    "roth",
    "retirement",
    "loan",
    "mortgage",
)


def _guess_account_kind(name: str) -> tuple[str, str] | None:
    """SimpleFIN gives an account name, not a type. Guess from the name; the
    user corrects it with PATCH /api/accounts/{id}. Returns None for
    accounts that aren't supported in the MVP (investment, loan, unknown) —
    those get no accounts row at all, per AGENTS.md's "Supported accounts
    only" note."""
    lowered = name.lower()
    if any(kw in lowered for kw in _SKIP_KEYWORDS):
        return None
    if any(kw in lowered for kw in ("credit", "card", "visa", "mastercard", "amex", "americanexpress", "capitalone", "discover")):
        return "liability", "credit_card"
    if "saving" in lowered:
        return "asset", "savings"
    return "asset", "checking"


def _account_number(external_id: str, name: str) -> tuple[str, str]:
    """SimpleFIN doesn't expose an account number field at all — only an
    opaque id and a human name. Most banks' names happen to end in the real
    last 4 digits (e.g. "Checking ...1234"); fall back to the id's last 4
    characters when the name doesn't have them, so display_name stays
    unique. The fallback is flagged in meta so it's never mistaken for a
    real account number later."""
    match = _LAST_FOUR_DIGITS.search(name)
    if match:
        return match.group(1), "name"
    return external_id[-4:], "simplefin_id_fallback"


def _local_date(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds).date().isoformat()


def parse_fetched(payload: dict) -> Fetched:
    account_rows: list[AccountRow] = []
    tx_rows: list[TxRow] = []
    balance_rows: list[BalanceRow] = []

    for account in payload.get("accounts", []):
        external_id = account["id"]
        name = account.get("name", "")
        kind = _guess_account_kind(name)
        if kind is None:
            continue  # investment/loan/unknown — not ingested in the MVP
        account_type, subtype = kind
        account_number, account_number_source = _account_number(external_id, name)
        org_name = (account.get("org") or {}).get("name") or "Unknown"

        account_rows.append(
            AccountRow(
                external_id=external_id,
                account_type=account_type,
                account_number=account_number,
                bank_name=org_name if account_type == "asset" else None,
                asset_type=subtype if account_type == "asset" else None,
                issuer_name=org_name if account_type == "liability" else None,
                liability_type=subtype if account_type == "liability" else None,
                meta={"name": name, "account_number_source": account_number_source},
            )
        )

        currency = account.get("currency") or "USD"
        for txn in account.get("transactions", []):
            if txn.get("pending"):
                continue
            amount_cents = parse_amount(txn["amount"])
            if amount_cents == 0:
                continue  # transactions table requires a nonzero signed amount
            tx_rows.append(
                TxRow(
                    account_external_id=external_id,
                    external_id=txn["id"],
                    amount_cents=amount_cents,
                    currency=currency,
                    transaction_type="debit" if amount_cents < 0 else "credit",
                    posted_on=_local_date(txn["posted"]),
                    description=txn.get("description") or "",
                    merchant_name=txn.get("payee"),
                )
            )

        if "balance" in account and "balance-date" in account:
            balance_rows.append(
                BalanceRow(
                    account_external_id=external_id,
                    as_of=_local_date(account["balance-date"]),
                    balance_cents=parse_amount(account["balance"]),
                )
            )

    return Fetched(accounts=account_rows, transactions=tx_rows, balances=balance_rows)


def fetch(since: date) -> Fetched:
    access_url = load_access_url()
    if access_url is None:
        raise NotConnectedError("not connected to SimpleFIN yet")

    base_url, username, password = _split_credentials(access_url)
    start_date = int(datetime(since.year, since.month, since.day).timestamp())

    try:
        response = httpx.get(
            f"{base_url}/accounts",
            params={"start-date": start_date},
            auth=(username, password),
        )
    except httpx.HTTPError as e:
        raise SimpleFinError(f"could not reach SimpleFIN: {e}") from e

    if response.status_code == 403:
        raise AccessRevokedError(
            "SimpleFIN access was revoked. Reconnect with a new setup token."
        )
    if response.status_code == 402:
        raise SubscriptionRequiredError(
            "Your SimpleFIN subscription needs payment."
        )
    if response.status_code != 200:
        raise SimpleFinError(
            f"SimpleFIN fetch failed with status {response.status_code}: {response.text}"
        )

    return parse_fetched(response.json())
