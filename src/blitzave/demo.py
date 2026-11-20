"""Demo mode: BlitZave on a made-up household, to try before connecting a bank.

Everything here is fictional: the banks, the employer, the merchants and every
amount. Demo mode never touches the user's real setup:

- the data folder is a new temporary directory (never ~/BlitZave),
- the keychain is a dict in memory (keyring's own set_keyring, no patching),
- the bank is FictionalBank below, swapped in as jobs.SOURCE,
- there's no update check.

Two ways in: `blitzave --demo` is a demo and nothing else. Plain `blitzave` on
a fresh install is one too, until the user connects their bank: then leave()
puts their real keychain and data folder back (jobs.connect_simplefin).

Dates are relative to today, so Home always has something true to say.
"""

import os
import random
import shutil
import tempfile
import warnings
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import keyring
import keyring.errors
from fastapi import FastAPI
from keyring.backend import KeyringBackend

from blitzave.sources.base import AccountRow, BalanceRow, Fetched, TxRow
from blitzave.store import categories, db

PORT = 8791  # so a demo can run beside a real BlitZave on 8742


class DemoSetupError(Exception):
    """Setting up the made-up household through the API was refused."""


class MemoryKeyring(KeyringBackend):
    """A keychain that's a dict: whatever is saved in demo mode (a Goose API
    key, say) is forgotten when the process ends."""

    # keyring only builds backends it can construct with no arguments, and
    # one with no priority isn't viable, so it never picks this on its own
    # (on a Linux machine with no keychain, say). Only start() does.

    def __init__(self, secrets: dict[tuple[str, str], str]):
        super().__init__()
        self._secrets = secrets

    def get_password(self, service: str, username: str) -> str | None:
        return self._secrets.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._secrets[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self._secrets.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError("nothing saved under that name")


class FictionalBank:
    """What jobs.SOURCE needs (is_connected, fetch), answered from made-up data."""

    def __init__(self, fetched: Fetched):
        self.fetched = fetched

    def is_connected(self) -> bool:
        return True

    def fetch(self, since: date, until: date | None = None) -> Fetched:
        return self.fetched


@dataclass
class Demo:
    """A running demo: its temporary data folder, and what to put back if
    the user connects their real bank."""

    home: Path
    real_keyring: KeyringBackend | None  # None for --demo, which never turns real
    home_before: str | None  # BLITZAVE_HOME as it was, usually unset

    @property
    def can_connect(self) -> bool:
        return self.real_keyring is not None

    def leave(self) -> None:
        """Back to the user's real keychain and data folder. The demo's
        folder, and every secret typed into it, are gone."""
        keyring.set_keyring(self.real_keyring)
        if self.home_before is None:
            os.environ.pop("BLITZAVE_HOME", None)
        else:
            os.environ["BLITZAVE_HOME"] = self.home_before
        shutil.rmtree(self.home, ignore_errors=True)


def start(real_keyring: KeyringBackend | None = None) -> Demo:
    """Enter demo mode. Call it before anything opens the database: db reads
    BLITZAVE_HOME on every connect. Pass the keychain to go back to when the
    user connects their bank, or nothing for a demo that never turns real.
    The caller removes the folder on exit."""
    started = Demo(
        home=Path(tempfile.mkdtemp(prefix="blitzave-demo-")),
        real_keyring=real_keyring,
        home_before=os.environ.get("BLITZAVE_HOME"),
    )
    os.environ["BLITZAVE_HOME"] = str(started.home)
    keyring.set_keyring(MemoryKeyring({}))
    return started


# --- The fictional household -----------------------------------------------------

CHK, BUF, EMG, GOALS, NORTH, CEDAR = (
    "demo-chk-4821",
    "demo-sav-3307",
    "demo-sav-6150",
    "demo-sav-7742",
    "demo-cc-9012",
    "demo-cc-5560",
)
ACCOUNTS = [
    AccountRow(
        CHK,
        "asset",
        "4821",
        bank_name="Juniper Credit Union",
        asset_type="checking",
        meta={"name": "Everyday Checking ...4821", "account_number_source": "name"},
    ),
    AccountRow(
        BUF,
        "asset",
        "3307",
        bank_name="Juniper Credit Union",
        asset_type="savings",
        meta={"name": "Bills Savings ...3307", "account_number_source": "name"},
    ),
    AccountRow(
        EMG,
        "asset",
        "6150",
        bank_name="Harbor Bank",
        asset_type="savings",
        meta={"name": "Emergency Savings ...6150", "account_number_source": "name"},
    ),
    AccountRow(
        GOALS,
        "asset",
        "7742",
        bank_name="Harbor Bank",
        asset_type="savings",
        meta={"name": "Goals Savings ...7742", "account_number_source": "name"},
    ),
    AccountRow(
        NORTH,
        "liability",
        "9012",
        issuer_name="Northwind Bank",
        liability_type="credit_card",
        meta={"name": "Northwind Rewards ...9012", "account_number_source": "name"},
    ),
    AccountRow(
        CEDAR,
        "liability",
        "5560",
        issuer_name="Cedar Financial",
        liability_type="credit_card",
        meta={"name": "Cedar Everyday Card ...5560", "account_number_source": "name"},
    ),
]
OPENING = {
    CHK: 2_400_00,
    BUF: 1_200_00,
    EMG: 5_000_00,
    GOALS: 300_00,
    NORTH: -1_180_00,
    CEDAR: -420_00,
}

# What moves into the shared goals account each month, oldest first. Lighter in
# month 5, heavier in month 6: real life, and what makes the pace a range.
GOALS_DEPOSITS = [600_00, 600_00, 700_00, 700_00, 500_00, 800_00, 700_00, 700_00]


def first_month(today: date) -> date:
    """The first of the month about seven months back: where the data starts."""
    return (today.replace(day=1) - timedelta(days=200)).replace(day=1)


def _months(start: date, today: date) -> list[date]:
    months, day = [], start
    while day <= today:
        months.append(day)
        day = (day.replace(day=28) + timedelta(days=5)).replace(day=1)
    return months


def build(today: date) -> Fetched:
    """About seven months of the household's banking, up to yesterday. Seeded,
    so the same day always builds the same data."""
    start = first_month(today)
    rng = random.Random(7)
    txs: list[TxRow] = []
    n = {"i": 0}

    def tx(account: str, day: date, cents: int, description: str) -> None:
        if day >= today or day < start:
            return
        n["i"] += 1
        txs.append(
            TxRow(
                account,
                f"demo-tx-{n['i']:05d}",
                cents,
                "USD",
                "debit" if cents < 0 else "credit",
                day.isoformat(),
                description,
            )
        )

    def move(day: date, cents: int, src: str, src_name: str, dst: str, dst_name: str) -> None:
        tx(src, day, -cents, f"Online Transfer to {dst_name}")
        tx(dst, day, cents, f"Online Transfer from {src_name}")

    card_spend: dict[tuple[str, str], int] = {}

    def charge(card: str, day: date, cents: int, description: str) -> None:
        if start <= day < today:
            key = (card, day.isoformat()[:7])
            card_spend[key] = card_spend.get(key, 0) + cents
        tx(card, day, -cents, description)

    def cents(low: float, high: float) -> int:
        return int(rng.uniform(low, high) * 100)

    for m, first in enumerate(_months(start, today)):
        days = monthrange(first.year, first.month)[1]

        def d(day: int) -> date:
            return first.replace(day=min(day, days))  # noqa: B023

        # Paydays, and savings first, the same day.
        for payday in (1, 15):
            tx(CHK, d(payday), 2_600_00, "LUMEN DESIGN CO PAYROLL")
        move(d(1), 400_00, CHK, "Everyday Checking ...4821", EMG, "Emergency Savings ...6150")
        goals_in = GOALS_DEPOSITS[m] if m < len(GOALS_DEPOSITS) else 700_00
        move(d(1), goals_in, CHK, "Everyday Checking ...4821", GOALS, "Goals Savings ...7742")
        move(d(15), 300_00, CHK, "Everyday Checking ...4821", BUF, "Bills Savings ...3307")
        tx(CHK, d(3), -1_650_00, "MAPLE COURT APTS RENT")

        # Bills, from the buffer.
        tx(BUF, d(18), -cents(92, 148), "BRIGHTWATER ENERGY")
        tx(BUF, d(21), -64_00, "SKYLINE MOBILE")
        tx(BUF, d(9), -49_00, "IRONLEAF GYM MEMBERSHIP")

        # Interest on the goal accounts: money that arrived, but not something you did.
        tx(EMG, d(days), 1_500 + m * 120, "Interest Paid")
        tx(GOALS, d(days), 300 + m * 110, "Interest Paid")

        # Everyday spending on the cards.
        for week in range(5):
            charge(NORTH, d(2 + week * 7), cents(72, 138), "GREENLEAF MARKET #0412")
        charge(NORTH, d(5), 98_00, "METRO TRANSIT MONTHLY PASS")
        charge(NORTH, d(11), 1_549 if m < 5 else 1_799, "STREAMLY MONTHLY")
        charge(NORTH, d(14), 999, "CLOUDNOTE PRO")
        charge(NORTH, d(17), cents(38, 52), "FUELWAY GAS #221")
        for _ in range(rng.randint(1, 3)):
            charge(NORTH, d(rng.randint(2, 27)), cents(18, 96), "PARCEL MARKET")
        for day in (4, 9, 12, 16, 20, 25):
            charge(
                CEDAR,
                d(day + rng.randint(0, 2)),
                cents(14, 48),
                rng.choice(
                    [
                        "HARBOR COFFEE CO",
                        "APLPAY HARBOR COFFEE CO",
                        "SUNSET NOODLE BAR",
                        "PINE STREET PIZZA",
                    ]
                ),
            )
        charge(CEDAR, d(23), cents(42, 88), "LANTERN BISTRO")
        charge(CEDAR, d(6), cents(9, 24), "BOOKSHOP CORNER")

        # Every few months, something lumpier.
        if m in (0, 6):
            charge(NORTH, d(6), 174_00, "OAKSHIELD RENTERS INSURANCE")
        if m in (2, 5, 6):
            charge(CEDAR, d(13), cents(70, 160), "MAKERS GIFT SHOP")

        # Last month's card statements, paid from checking.
        for card, name, thanks, payday in (
            (NORTH, "NORTHWIND CARD PAYMENT", "PAYMENT THANK YOU", 22),
            (CEDAR, "CEDAR FINANCIAL PAYMENT", "AUTOPAY PAYMENT RECEIVED", 24),
        ):
            prev = (first - timedelta(days=1)).isoformat()[:7]
            owed = card_spend.get((card, prev), -OPENING[card])
            tx(CHK, d(payday), -owed, name)
            tx(card, d(payday), owed, thanks)

    # A return, refunded to the same card.
    returned = today - timedelta(days=40)
    charge(NORTH, returned - timedelta(days=6), 64_00, "PARCEL MARKET")
    tx(NORTH, returned, 64_00, "PARCEL MARKET RETURN")

    # The PS5: bought on the card, then $500 moved out of the goals account to
    # cover it. Savings asks "Was that the PS5?" about that $500.
    bought = today - timedelta(days=19)
    charge(NORTH, bought, 499_99, "PIXEL PLAZA ELECTRONICS")
    move(
        bought + timedelta(days=1),
        500_00,
        GOALS,
        "Goals Savings ...7742",
        CHK,
        "Everyday Checking ...4821",
    )

    # Balances on every day something moved, from the opening balances.
    running = dict(OPENING)
    by_day: dict[str, dict[str, int]] = {}
    for row in sorted(txs, key=lambda r: r.posted_on):
        running[row.account_external_id] += row.amount_cents
        by_day[row.posted_on] = dict(running)
    by_day[today.isoformat()] = dict(running)
    balances = [
        BalanceRow(account, day, amounts[account])
        for day, amounts in sorted(by_day.items())
        for account in amounts
    ]
    balances.insert(0, BalanceRow(GOALS, start.isoformat(), OPENING[GOALS]))
    balances.insert(0, BalanceRow(EMG, start.isoformat(), OPENING[EMG]))
    return Fetched(accounts=ACCOUNTS, transactions=txs, balances=balances)


# --- A few months of use, through the API ----------------------------------------

EXPENSES = [
    # name, monthly amount (or (amount, every n months)), merchants it learns
    ("Groceries", "550", ["GREENLEAF MARKET #0412"]),
    (
        "Dining out",
        "260",
        ["HARBOR COFFEE CO", "SUNSET NOODLE BAR", "PINE STREET PIZZA", "LANTERN BISTRO"],
    ),
    ("Transport", "160", ["METRO TRANSIT MONTHLY PASS", "FUELWAY GAS #221"]),
    ("Subscriptions", "40", ["STREAMLY MONTHLY", "CLOUDNOTE PRO"]),
    ("Bills", "290", ["BRIGHTWATER ENERGY", "SKYLINE MOBILE", "IRONLEAF GYM MEMBERSHIP"]),
    ("Rent", "1650", ["MAPLE COURT APTS RENT"]),
    ("Shopping", "180", ["PARCEL MARKET", "PIXEL PLAZA ELECTRONICS"]),
    ("Gifts", ("300", 3), ["MAKERS GIFT SHOP"]),
    ("Insurance", ("180", 6), ["OAKSHIELD RENTERS INSURANCE"]),
]


def set_up(app: FastAPI) -> None:
    """Make the household look like a few months of use: goal accounts,
    categories the user taught, budgets and goals. Everything goes through the
    API in-process, so each write is validated exactly as a user's would be."""
    with warnings.catch_warnings():
        # Starlette wants httpx2 for its TestClient. httpx still works, and
        # the warning would otherwise greet every demo user.
        warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
        from fastapi.testclient import TestClient

    today = date.today()
    # base_url: the app refuses any Host but its own (DNS rebinding).
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:  # the lifespan syncs

        def ok(response):
            if response.status_code >= 300:
                raise DemoSetupError(
                    f"{response.request.method} {response.request.url.path}: "
                    f"{response.status_code} {response.text}"
                )
            return response.json() if response.content else None

        for account in (EMG, GOALS):
            ok(
                client.patch(
                    f"/api/accounts/{account}",
                    json={"account_type": "asset", "asset_type": "targeted"},
                )
            )

        rows = ok(client.get("/api/transactions", params={"limit": 500}))["transactions"]
        first_by_description = {}
        for row in rows:
            first_by_description.setdefault(row["description"], row["id"])

        def teach(merchant: str, category_id: int) -> None:
            if merchant in first_by_description:
                ok(
                    client.patch(
                        f"/api/transactions/{first_by_description[merchant]}",
                        json={"categories": {category_id: 1.0}, "apply_to_all": True},
                    )
                )

        pay = ok(client.post("/api/categories", json={"name": "Paycheck", "kind": "income"}))
        teach("LUMEN DESIGN CO PAYROLL", pay["id"])

        for name, amount, merchants in EXPENSES:
            body = {"name": name, "kind": "expense"}
            if isinstance(amount, tuple):
                body |= {
                    "budget_amount": amount[0],
                    "schedule": "recurring",
                    "period_months": amount[1],
                }
            else:
                body |= {"budget_amount": amount}
            created = ok(client.post("/api/categories", json=body))
            for merchant in merchants:
                teach(merchant, created["id"])

        def goal(name, amount, level, account, **plan):
            return ok(
                client.post(
                    "/api/categories",
                    json={
                        "name": name,
                        "kind": "savings",
                        "budget_amount": amount,
                        "schedule": "once",
                        "goal_level": level,
                        "account_id": account,
                        **plan,
                    },
                )
            )

        goal("Emergency fund", "10000", "critical", EMG)
        goal("PS5", "500", "regular", GOALS)
        goal("Japan trip", "8500", "regular", GOALS,
             target_on=date(today.year + 1, 6, 1).isoformat())
        goal("New bike", "1200", "regular", GOALS,
             target_on=date(today.year + 1, 9, 1).isoformat())
        coat = goal("Winter coat", "350", "regular", GOALS,
                    target_on=(today - timedelta(days=45)).isoformat())
        ok(
            client.patch(
                f"/api/categories/{coat['id']}",
                json={"completed_on": (today - timedelta(days=50)).isoformat()},
            )
        )

    # Budgets "set" when the data starts, so closed rounds have results to show.
    # The API dates a plan the day it's set; a few months of real use is what
    # this stands in for.
    conn = db.connect()
    try:
        categories.backdate_plans(conn, first_month(today).isoformat())
    finally:
        conn.close()
