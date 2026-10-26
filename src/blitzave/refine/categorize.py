import re

# Payment wallets and card processors that banks glue onto the front of a
# merchant's name. They say how the user paid, not where, so they're dropped
# before matching. Lowercase, because matching happens after lowercasing.
WALLET_PREFIXES = (
    "aplpay",
    "apl pay",
    "appl pay",
    "applepay",
    "apple pay",
    "gpay",
    "google pay",
    "paypal *",
    "paypal*",
    "sq *",
    "sq*",
    "tst*",
)

# A one-letter typo only counts for words at least this long. In a short word
# one letter is the whole difference: "gas" vs "gap" are different words.
TYPO_MIN_LENGTH = 5

# How many words the longer description may have that the shorter one doesn't
# explain. A couple is noise ("REFUND", a city); more means a different
# business: "TRADER JOES" vs "TRADER JOES COFFEE ROASTERS CO".
MAX_EXTRA_WORDS = 2


def description_tokens(text: str) -> list[str]:
    """The words of a description that name the merchant:

        "starbucks #123"         -> ["starbucks"]
        "   STAR BUCKS #163"     -> ["star", "bucks"]
        "AplPay TST* LA LA"      -> ["la", "la"]
        "AIRBNB * HM4K2PQ7"      -> ["airbnb"]

    Lowercase, strip any wallet/processor prefixes (repeatedly — they can
    stack), split into runs of letters and digits, then drop every word with
    a digit in it. That drops store numbers and reference codes, which change
    from one purchase to the next."""
    text = text.lower().lstrip()
    stripped_one = True
    while stripped_one:
        stripped_one = False
        for prefix in WALLET_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
                stripped_one = True
    words = re.findall(r"[a-z0-9]+", text)
    return [w for w in words if not any(c.isdigit() for c in w)]


def match_key(text: str) -> str:
    """The key a rule is stored under: the tokens joined by spaces. A
    description with no words gives "" — callers must not build a rule on
    that."""
    return " ".join(description_tokens(text))


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance: the fewest single-letter inserts, deletes or
    substitutions that turn a into b. Computed one row at a time."""
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(
                min(
                    previous[j] + 1,  # delete char_a
                    current[j - 1] + 1,  # insert char_b
                    previous[j - 1] + (char_a != char_b),  # substitute (free if equal)
                )
            )
        previous = current
    return previous[-1]


def _same_word(a: str, b: str) -> bool:
    """Equal, or one typo apart when both words are long enough to tell."""
    if a == b:
        return True
    long_enough = len(a) >= TYPO_MIN_LENGTH and len(b) >= TYPO_MIN_LENGTH
    return long_enough and _edit_distance(a, b) <= 1


def is_match(key_a: str, key_b: str) -> bool:
    """Do two match keys name the same merchant? Yes or no, no score.

    - Same letters once spaces are ignored: a match ("disney plus" vs
      "disneyplus"). This also keeps older rules, stored without spaces,
      working.
    - Otherwise the first words must agree — the first word is the merchant.
    - Every word of the shorter key must appear in the longer one, each used
      once, allowing a typo in long words.
    - And the longer key may have at most MAX_EXTRA_WORDS words left over.

    An empty key has nothing to compare, so it never matches."""
    if not key_a or not key_b:
        return False
    if key_a.replace(" ", "") == key_b.replace(" ", ""):
        return True

    words_a = key_a.split(" ")
    words_b = key_b.split(" ")
    if not _same_word(words_a[0], words_b[0]):
        return False

    if len(words_a) <= len(words_b):
        shorter, longer = words_a, words_b
    else:
        shorter, longer = words_b, words_a
    used = set()  # positions in `longer` already paired with a word
    for word in shorter:
        for i, other in enumerate(longer):
            if i not in used and _same_word(word, other):
                used.add(i)
                break
        else:
            return False  # this word isn't in the longer key

    return len(longer) - len(used) <= MAX_EXTRA_WORDS
