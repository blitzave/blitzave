"""The automatic matcher, as a pure function: legs in, (transfers, refunds) out."""

from blitzave.refine.transfers import (
    WINDOW_DAYS,
    Leg,
    find_pairs,
    kind_of,
    looks_like_transfer,
)

CHECKING, CARD, SAVINGS = 1, 2, 3


def leg(id, account, cents, posted, description="Synthetic", no_auto=False, currency="USD"):
    return Leg(id, account, cents, currency, posted, description, no_auto)


def test_a_card_payment_is_one_transfer():
    legs = [
        leg(1, CHECKING, -50000, "2026-10-03", "CHASE CARD AUTOPAY"),
        leg(2, CARD, 50000, "2026-10-04", "PAYMENT THANK YOU"),
    ]
    assert find_pairs(legs) == ([(1, 2)], [])


def test_a_payment_across_a_long_weekend_still_matches():
    # Friday out of checking, Tuesday onto the card: 4 days.
    legs = [
        leg(1, CHECKING, -50000, "2026-10-02", "ONLINE TRANSFER TO CARD"),
        leg(2, CARD, 50000, "2026-10-06", "PAYMENT RECEIVED"),
    ]
    assert find_pairs(legs) == ([(1, 2)], [])


def test_one_day_past_the_window_is_left_for_the_user():
    legs = [
        leg(1, CHECKING, -50000, "2026-10-01", "TRANSFER"),
        leg(2, SAVINGS, 50000, f"2026-10-{1 + WINDOW_DAYS + 1:02d}", "TRANSFER"),
    ]
    assert find_pairs(legs) == ([], [])


def test_a_transfer_needs_a_transfer_like_description():
    # A $50 Venmo to a friend and an unrelated $50 credit on another account.
    legs = [
        leg(1, CHECKING, -5000, "2026-10-03", "VENMO SARAH"),
        leg(2, SAVINGS, 5000, "2026-10-04", "INTEREST ADJ"),
    ]
    assert find_pairs(legs) == ([], [])


def test_two_debits_for_one_credit_is_ambiguous():
    legs = [
        leg(1, CHECKING, -10000, "2026-10-03", "TRANSFER TO SAVINGS"),
        leg(4, CARD, -10000, "2026-10-03", "TRANSFER"),
        leg(2, SAVINGS, 10000, "2026-10-04", "TRANSFER FROM CHECKING"),
    ]
    assert find_pairs(legs) == ([], [])


def test_amounts_must_match_to_the_cent_and_currency():
    legs = [
        leg(1, CHECKING, -10000, "2026-10-03", "TRANSFER"),
        leg(2, SAVINGS, 10001, "2026-10-03", "TRANSFER"),
        leg(3, SAVINGS, 10000, "2026-10-03", "TRANSFER", currency="EUR"),
    ]
    assert find_pairs(legs) == ([], [])


def test_an_unlinked_pair_is_never_linked_again():
    legs = [
        leg(1, CHECKING, -50000, "2026-10-03", "AUTOPAY", no_auto=True),
        leg(2, CARD, 50000, "2026-10-04", "PAYMENT THANK YOU", no_auto=True),
    ]
    assert find_pairs(legs) == ([], [])


def test_an_unlinked_leg_still_makes_a_new_pair_ambiguous():
    legs = [
        leg(1, CHECKING, -50000, "2026-10-03", "AUTOPAY", no_auto=True),
        leg(3, CHECKING, -50000, "2026-10-03", "AUTOPAY"),
        leg(2, CARD, 50000, "2026-10-04", "PAYMENT THANK YOU"),
    ]
    assert find_pairs(legs) == ([], [])


def test_a_card_credit_that_names_no_merchant_is_a_refund_by_amount():
    legs = [
        leg(1, CARD, -1402, "2026-10-20", "Walmart+ Member 10/28009666546 AR"),
        leg(2, CARD, 1402, "2026-10-21", "Platinum Walmart+ Credit"),
    ]
    assert find_pairs(legs) == ([], [(1, 2)])


def test_an_ambiguous_amount_falls_back_to_the_merchant():
    # Two $42.10 charges; only one is from the store that refunded.
    legs = [
        leg(1, CARD, -4210, "2026-10-02", "COSTCO WHSE #0669"),
        leg(3, CARD, -4210, "2026-10-02", "TARGET 00012345"),
        leg(2, CARD, 4210, "2026-10-04", "COSTCO WHSE #0669"),
    ]
    assert find_pairs(legs) == ([], [(1, 2)])


def test_two_charges_from_the_same_merchant_stay_ambiguous():
    legs = [
        leg(1, CARD, -4210, "2026-10-02", "COSTCO WHSE #0669"),
        leg(3, CARD, -4210, "2026-10-03", "COSTCO WHSE #0669"),
        leg(2, CARD, 4210, "2026-10-04", "COSTCO WHSE #0669"),
    ]
    assert find_pairs(legs) == ([], [])


def test_a_refund_never_pairs_across_accounts():
    # A $50 card refund and an unrelated $50 checking debit: not a transfer
    # (no transfer words), and not a refund (different accounts).
    legs = [
        leg(1, CHECKING, -5000, "2026-10-03", "VENMO SARAH"),
        leg(2, CARD, 5000, "2026-10-04", "AMAZON MKTPL REFUND"),
    ]
    assert find_pairs(legs) == ([], [])


def test_a_card_payment_from_an_unseen_account_never_cancels_a_purchase():
    legs = [
        leg(1, CARD, -5000, "2026-10-03", "SHELL OIL 1234"),
        leg(2, CARD, 5000, "2026-10-04", "PAYMENT THANK YOU"),
    ]
    assert find_pairs(legs) == ([], [])


def test_the_transfer_is_taken_first_and_the_purchase_stays_spending():
    legs = [
        leg(1, CARD, -5000, "2026-10-03", "SHELL OIL 1234"),
        leg(2, CARD, 5000, "2026-10-04", "PAYMENT THANK YOU"),
        leg(3, CHECKING, -5000, "2026-10-04", "CARD AUTOPAY"),
    ]
    assert find_pairs(legs) == ([(3, 2)], [])


def test_transfer_words():
    for text in ("PAYMENT THANK YOU", "Online Transfer to SAV", "CHASE AUTOPAY", "XFER"):
        assert looks_like_transfer(text)
    for text in ("Platinum Walmart+ Credit", "COSTCO WHSE #0669", "ACME CORP PAYROLL"):
        assert not looks_like_transfer(text)


def test_a_later_rival_still_makes_a_pair_ambiguous():
    # Each leg only looks back, but the debit still learns about both credits.
    legs = [
        leg(1, CHECKING, -10000, "2026-10-01", "TRANSFER TO SAVINGS"),
        leg(2, SAVINGS, 10000, "2026-10-02", "TRANSFER FROM CHECKING"),
        leg(3, SAVINGS, 10000, "2026-10-05", "TRANSFER FROM CHECKING"),
    ]
    assert find_pairs(legs) == ([], [])


def test_each_pair_is_compared_once(monkeypatch):
    from blitzave.refine import transfers as match

    calls = []
    real = match._days_apart
    monkeypatch.setattr(match, "_days_apart", lambda a, b: calls.append((a.id, b.id)) or real(a, b))
    legs = [
        leg(1, CHECKING, -50000, "2026-10-03", "AUTOPAY"),
        leg(2, CARD, 50000, "2026-10-04", "PAYMENT THANK YOU"),
        leg(3, CARD, -1402, "2026-10-04", "Walmart+ Member"),  # another bucket: never compared
    ]
    assert find_pairs(legs) == ([(1, 2)], [])
    assert calls == [(2, 1)]


def test_kind_is_decided_when_shown():
    assert kind_of(False, "CHASE CARD AUTOPAY", "PAYMENT THANK YOU") == "transfer"
    assert kind_of(True, "COSTCO WHSE #0669", "COSTCO WHSE #0669") == "refund"
    assert kind_of(True, "Walmart+ Member 10/28009666546 AR", "Platinum Walmart+ Credit") == "credit"
