"""Find the other side of a transaction automatically. Pure: legs in, pairs out.

A pair is a debit and a credit of exactly opposite amount, in the same
currency, posted at most WINDOW_DAYS apart. Every pair is stored the same way
(one `transfers` row) and both legs leave activity. What it's called is
decided only when it's shown (kind_of):

    legs in two accounts               -> transfer
    one account, same merchant         -> refund
    one account, different merchant    -> credit

A pair is linked only when it's unambiguous. A missed pair stays visible and
the user can link it by hand. A wrong pair hides real spending, which is worse.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from blitzave.refine.categorize import is_match, match_key

# How many days apart the two legs may post, whichever comes first. Banks post
# each leg on their own schedule: a Friday card payment can land on Tuesday
# after a long weekend.
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


def same_merchant(description_a: str, description_b: str) -> bool:
    key_a, key_b = match_key(description_a), match_key(description_b)
    return bool(key_a) and bool(key_b) and is_match(key_a, key_b)


def kind_of(same_account: bool, debit_description: str, credit_description: str) -> str:
    """What to call a pair when it's shown. Never stored."""
    if not same_account:
        return "transfer"
    return "refund" if same_merchant(debit_description, credit_description) else "credit"


def _days_apart(a: Leg, b: Leg) -> int:
    return abs((date.fromisoformat(a.posted_on) - date.fromisoformat(b.posted_on)).days)


def _only(items: list[Leg]) -> Leg | None:
    return items[0] if len(items) == 1 else None


def find_pairs(
    legs: list[Leg], window_days: int = WINDOW_DAYS
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(transfers, same-account pairs), each a list of (debit id, credit id).

    Legs are bucketed by (currency, unsigned amount), so only rows that could
    possibly pair are ever compared. Within a bucket, legs are sorted by date
    and each one looks back at most window_days: every pair is compared
    exactly once, from its later leg. A leg's candidates are all the pairs it
    turned up in. A leg the user unlinked still counts as a candidate (it
    makes things ambiguous) but is never linked.

    Transfers first: the only other-account candidate on both sides, and at
    least one description looks like a transfer.

    Then same-account pairs (refunds and credits), from what's left, in two
    levels:
      1. by amount: the only candidate on both sides, in the same account.
         Catches card credits that name no merchant ("Platinum Walmart+
         Credit" against "Walmart+ Member").
      2. by merchant: when the amount alone is ambiguous, the only
         same-account candidate with the same merchant key, on both sides.
    A leg that looks like a transfer is never a refund: a card payment from
    an account BlitZave can't see would otherwise cancel a purchase.
    """
    buckets: dict[tuple[str, int], list[Leg]] = defaultdict(list)
    for leg in legs:
        buckets[(leg.currency, abs(leg.amount_cents))].append(leg)

    transfers: list[tuple[int, int]] = []
    same_account: list[tuple[int, int]] = []
    for bucket in buckets.values():
        bucket_transfers, bucket_same_account = _pairs_in_bucket(bucket, window_days)
        transfers += bucket_transfers
        same_account += bucket_same_account
    return transfers, same_account


def _candidates(bucket: list[Leg], window_days: int) -> dict[int, list[Leg]]:
    """Each leg's opposite-sign legs within the window. Sorted by date, each
    leg looks back only, and stops at the first leg that's too old."""
    ordered = sorted(bucket, key=lambda x: (x.posted_on, x.id))
    near: dict[int, list[Leg]] = {leg.id: [] for leg in ordered}
    for i, leg in enumerate(ordered):
        for j in range(i - 1, -1, -1):
            earlier = ordered[j]
            if _days_apart(leg, earlier) > window_days:
                break
            if (earlier.amount_cents < 0) != (leg.amount_cents < 0):
                near[leg.id].append(earlier)
                near[earlier.id].append(leg)
    return near


def _pairs_in_bucket(
    bucket: list[Leg], window_days: int
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """find_pairs for one (currency, amount) bucket."""
    near = _candidates(bucket, window_days)
    debits = sorted((leg for leg in bucket if leg.amount_cents < 0), key=lambda x: x.id)

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

    def same_account_merchant(leg: Leg) -> list[Leg]:
        return [o for o in left(leg)
                if o.account_id == leg.account_id and same_merchant(leg.description, o.description)]

    same_account: list[tuple[int, int]] = []
    for d in debits:
        if d.id in linked:
            continue
        c = _only(left(d))
        by_amount = c is not None and c.account_id == d.account_id and _only(left(c)) is d
        if not by_amount:
            c = _only(same_account_merchant(d))
            if c is None or _only(same_account_merchant(c)) is not d:
                continue
        if d.no_auto or c.no_auto:
            continue
        if looks_like_transfer(d.description) or looks_like_transfer(c.description):
            continue
        same_account.append((d.id, c.id))
    return transfers, same_account
