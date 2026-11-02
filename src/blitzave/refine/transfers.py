"""Find the other side of a transaction automatically. Pure: legs in, pairs out.

A pair is a debit and a credit of exactly opposite amount, in the same
currency, posted within WINDOW_DAYS of each other. Two accounts make it a
transfer; one account makes it a refund/credit. Either way both legs leave
activity (AGENTS.md "Match transfers").

A pair is linked only when it's unambiguous. A missed pair stays visible and
the user can link it by hand. A wrong pair hides real spending, which is worse.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from blitzave.refine.categorize import is_match, match_key

# How many days apart the two legs may post. Banks post each leg on their own
# schedule: a Friday card payment can land on Tuesday after a long weekend.
WINDOW_DAYS = 4

# Words that say "money moved between your own accounts". Lowercase; matched
# as substrings of the lowercased description.
TRANSFER_WORDS = (
    "autopay",
    "payment",
    "thank you",
    "transfer",
    "xfer",
)


@dataclass(frozen=True)
class Leg:
    id: int
    account_id: int
    amount_cents: int  # negative = debit, positive = credit
    currency: str
    posted_on: str  # ISO date
    description: str
    no_auto: bool = False  # the user unlinked it once; never link it automatically


def looks_like_transfer(description: str) -> bool:
    text = description.lower()
    return any(word in text for word in TRANSFER_WORDS)


def _days_apart(a: Leg, b: Leg) -> int:
    return abs((date.fromisoformat(a.posted_on) - date.fromisoformat(b.posted_on)).days)


def _only(items: list[Leg]) -> Leg | None:
    return items[0] if len(items) == 1 else None


def _same_merchant(a: Leg, b: Leg) -> bool:
    key_a, key_b = match_key(a.description), match_key(b.description)
    return bool(key_a) and bool(key_b) and is_match(key_a, key_b)


def find_pairs(
    legs: list[Leg], window_days: int = WINDOW_DAYS
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(transfers, refunds), each a list of (debit id, credit id).

    Legs are bucketed by (currency, unsigned amount), so only rows that could
    possibly pair are ever compared. Within a bucket, a leg's candidates are
    the opposite-sign legs within the window. A leg the user unlinked still
    counts as a candidate (it makes things ambiguous) but is never linked.

    Transfers first: the only other-account candidate on both sides, and at
    least one description looks like a transfer.

    Refunds, from what transfers left over, in two levels:
      1. by amount: the only candidate on both sides, in the same account.
         Catches card credits that name neither merchant ("Platinum Walmart+
         Credit" against "Walmart+ Member").
      2. by merchant: when the amount alone is ambiguous, the only
         same-account candidate with the same merchant key, on both sides.
    A leg that looks like a transfer is never a refund: a card payment from
    an account BlitZave can't see would otherwise cancel a purchase.
    """
    buckets: dict[tuple[str, int], tuple[list[Leg], list[Leg]]] = defaultdict(
        lambda: ([], [])
    )
    for leg in sorted(legs, key=lambda x: (x.posted_on, x.id)):
        debits, credits = buckets[(leg.currency, abs(leg.amount_cents))]
        (debits if leg.amount_cents < 0 else credits).append(leg)

    transfers: list[tuple[int, int]] = []
    refunds: list[tuple[int, int]] = []
    for debits, credits in buckets.values():
        if debits and credits:
            bucket_transfers, bucket_refunds = _pairs_in_bucket(debits, credits, window_days)
            transfers += bucket_transfers
            refunds += bucket_refunds
    return transfers, refunds


def _pairs_in_bucket(
    debits: list[Leg], credits: list[Leg], window_days: int
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """find_pairs for one (currency, amount) bucket."""
    near = {
        leg.id: [o for o in (credits if leg.amount_cents < 0 else debits)
                 if _days_apart(leg, o) <= window_days]
        for leg in debits + credits
    }

    def other_account(leg: Leg) -> list[Leg]:
        return [o for o in near[leg.id] if o.account_id != leg.account_id]

    transfers: list[tuple[int, int]] = []
    linked: set[int] = set()
    for d in debits:
        c = _only(other_account(d))
        if c is None or _only(other_account(c)) is not d:
            continue
        if d.no_auto or c.no_auto:
            continue
        if not (looks_like_transfer(d.description) or looks_like_transfer(c.description)):
            continue
        transfers.append((d.id, c.id))
        linked.update((d.id, c.id))

    def left(leg: Leg) -> list[Leg]:
        return [o for o in near[leg.id] if o.id not in linked]

    def same_merchant(leg: Leg) -> list[Leg]:
        return [o for o in left(leg)
                if o.account_id == leg.account_id and _same_merchant(leg, o)]

    refunds: list[tuple[int, int]] = []
    for d in debits:
        if d.id in linked:
            continue
        c = _only(left(d))
        by_amount = c is not None and c.account_id == d.account_id and _only(left(c)) is d
        if not by_amount:
            c = _only(same_merchant(d))
            if c is None or _only(same_merchant(c)) is not d:
                continue
        if d.no_auto or c.no_auto:
            continue
        if looks_like_transfer(d.description) or looks_like_transfer(c.description):
            continue
        refunds.append((d.id, c.id))
    return transfers, refunds
