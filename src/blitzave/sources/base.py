from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class AccountRow:
    external_id: str
    account_type: str  # 'asset' | 'liability'
    account_number: str  # last 4 digits when known; see meta['account_number_source']
    bank_name: str | None = None  # set when account_type == 'asset'
    asset_type: str | None = None  # 'checking' | 'savings'
    issuer_name: str | None = None  # set when account_type == 'liability'
    liability_type: str | None = None  # 'credit_card' | 'loan'
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TxRow:
    account_external_id: str
    external_id: str
    amount_cents: int  # negative = money left the account; positive = arrived
    currency: str
    transaction_type: str  # 'debit' | 'credit'
    posted_on: str  # ISO date, local timezone
    description: str
    merchant_name: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BalanceRow:
    account_external_id: str
    as_of: str  # ISO date
    balance_cents: int


@dataclass(frozen=True)
class Fetched:
    accounts: list[AccountRow]
    transactions: list[TxRow]
    balances: list[BalanceRow]


class Source(Protocol):
    name: str

    def fetch(self, since: date) -> Fetched: ...
