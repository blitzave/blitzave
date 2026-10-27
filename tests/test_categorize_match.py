import pytest

from blitzave.refine.categorize import description_tokens, is_match, match_key


def _same(a: str, b: str) -> bool:
    return is_match(match_key(a), match_key(b))


# --- tokens and keys ----------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "key"),
    [
        ("starbucks #123", "starbucks"),
        ("STARBUCKS #123", "starbucks"),
        ("   STAR BUCKS #163", "star bucks"),
    ],
)
def test_the_maintainers_starbucks_examples(description, key):
    assert match_key(description) == key
    assert _same(description, "STARBUCKS #555")


@pytest.mark.parametrize(
    "description",
    [
        "AplPay STARBUCKS #123",
        "APPLE PAY Starbucks #9",
        "Appl Pay starbucks",
        "  applepay STARBUCKS",
        "GPAY STARBUCKS #1",
        "PAYPAL *STARBUCKS",
        "PAYPAL*STARBUCKS",
        "SQ *STARBUCKS",
        "SQ*STARBUCKS",
    ],
)
def test_wallet_prefixes_are_dropped(description):
    assert description_tokens(description) == ["starbucks"]


def test_stacked_prefixes_are_all_dropped():
    assert match_key("AplPay TST* LA LA LASOUTHLAKE TX") == "la la lasouthlake tx"


def test_a_prefix_word_in_the_middle_is_kept():
    # Only a leading prefix says how the user paid; elsewhere it's the merchant's name.
    assert match_key("SHOP GPAY") == "shop gpay"


def test_words_with_digits_go():
    assert match_key("COSTCO WHSE #0669") == "costco whse"
    assert match_key("AIRBNB * HM4K2PQ7") == "airbnb"  # a reference code


def test_no_words_gives_an_empty_key():
    assert match_key("#0669 - 12/31 $4.50") == ""


# --- is_match -----------------------------------------------------------------

# The bench the matching rules were chosen on (26 real-looking pairs).
SAME = [
    ("STARBUCKS #123", "starbucks #123"),
    ("STARBUCKS #123", "   STAR BUCKS #163"),
    ("STARBUCKS #123", "AplPay STARBUCKS #555"),
    ("STARBUCKS #123", "STARBUCK #123"),
    ("AIRBNB * HM4K2PQ7", "AIRBNB * HMXZ93ND"),
    ("DISNEY PLUS 888-905-7888", "DISNEYPLUS 8889057888"),
    ("WALMART.COM 8009256278 AR", "WALMART.COM 8009256278"),
    ("SQ *BLUE BOTTLE COFFEE", "BLUE BOTTLE COFFE 0042"),
    ("COSTCO WHSE #0669", "COSTCO WHSE #1042"),
    ("AMAZON MKTPL*2K4LP9 AMZN.COM/BILL WA", "AMAZON MKTPL*7Q1MM3 AMZN.COM/BILL WA"),
    ("UBER   *TRIP HELP.UBER.COM", "UBER *TRIP HELP.UBER.COM"),
    ("NETFLIX.COM", "NETFLIX.COM 866-579-7172"),
]
DIFF = [
    ("COSTCO WHSE #0669", "COSTCO GAS #0669"),
    ("STARBUCKS #123", "STARBRIGHT CAFE"),
    ("ZELLE PAYMENT TO ALICE", "ZELLE PAYMENT TO BOB"),
    ("TRADER JOES #552", "TRADER JOES COFFEE ROASTERS CO"),
    ("AMAZON MKTPL*2K4LP9 AMZN.COM/BILL WA", "AMAZON WEB SERVICES AWS.AMAZON.CO WA"),
    ("UBER *TRIP HELP.UBER.COM", "UBER *EATS HELP.UBER.COM"),
    ("SHELL OIL 5744", "SHELL ONLINE PAYMENT"),
    ("ATM WITHDRAWAL CHASE", "ATM WITHDRAWAL WELLS FARGO"),
    ("DISNEY PLUS", "DISNEY STORE"),
    ("WHOLEFDS MKT 10234", "WHOLE FOODS PARKING"),
    ("CAPITAL ONE AUTOPAY", "CAPITAL ONE MOBILE PMT"),
    ("LA LA LASOUTHLAKE TX", "LA LA GRAPEVINE TX"),
    # One-word names must match exactly, so "starbucks store" is a different
    # key until the user applies a category to it separately. (Was a SAME pair
    # before single-word keys stopped allowing extra words.)
    ("STARBUCKS #123", "STARBUCKS STORE 04412"),
    # Same rule: "airbnb" is one word, so "airbnb refund" is a separate key.
    ("AIRBNB * HM4K2PQ7", "AIRBNB * HM4K2PQ7 REFUND"),
]


@pytest.mark.parametrize(("a", "b"), SAME)
def test_same_merchant_matches(a, b):
    assert _same(a, b)
    assert _same(b, a)  # order doesn't matter


@pytest.mark.parametrize(("a", "b"), DIFF)
def test_different_merchant_does_not_match(a, b):
    assert not _same(a, b)
    assert not _same(b, a)


def test_reference_codes_are_ignored():
    assert _same("AIRBNB * HM4K2PQ7", "AIRBNB * HMXZ93ND")


def test_more_than_two_extra_words_is_a_different_business():
    assert not _same("TRADER JOES", "TRADER JOES COFFEE ROASTERS CO")  # three extra
    assert _same("TRADER JOES", "TRADER JOES COFFEE ROASTERS")  # two extra is fine


@pytest.mark.parametrize(
    ("one_word", "longer"),
    [("uber", "uber eats"), ("costco", "costco gas"), ("home", "home depot"), ("starbucks", "starbucks store")],
)
def test_a_single_word_name_allows_no_extra_words(one_word, longer):
    assert not is_match(one_word, longer)
    assert not is_match(longer, one_word)


def test_a_single_word_still_matches_its_typo_and_its_spaced_spelling():
    assert is_match("starbucks", "starbuck")
    assert is_match("disneyplus", "disney plus")


def test_two_word_names_still_allow_two_extra_words():
    assert is_match("trader joes", "trader joes coffee roasters")
    assert not is_match("trader joes", "trader joes coffee roasters co")


def test_a_one_letter_typo_in_a_long_word_matches():
    assert is_match("starbucks", "starbuck")


def test_short_words_must_be_exact():
    # Below TYPO_MIN_LENGTH letters, one letter is the whole difference.
    assert not is_match("gas", "gap")
    assert not is_match("shell gas", "shell gap")


def test_legacy_keys_without_spaces_still_match():
    # Rules saved before tokens existed stored the letters with no spaces.
    assert is_match("starbucks", match_key("STARBUCKS #123"))
    assert is_match("bluebottlecoffee", match_key("BLUE BOTTLE COFFEE"))


def test_empty_keys_never_match():
    assert not is_match("", "")
    assert not is_match("", "starbucks")
