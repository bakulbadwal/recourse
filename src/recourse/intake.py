"""Deterministic evidence extraction from a victim's plain-language story.

Design rules:
- Pure regex/parser logic. NO model is involved anywhere in this module.
- Conservative by construction: a missed fact is recoverable (the victim can
  add it); a fabricated fact poisons a federal filing. Ambiguous candidates
  are dropped, not guessed.
- Every extracted fact carries its exact ``verbatim`` source substring so the
  anti-invention eval gate can trace it back to the input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser

from .models import EvidenceItem, Transaction

# ---------------------------------------------------------------------------
# Regexes. Hex patterns use lookarounds (not \b) so a 64-hex hash can never be
# partially matched as a 40-hex address, and a truncated/overlong hex run never
# matches at all.
# ---------------------------------------------------------------------------

_HEX_BOUNDARY_L = r"(?<![0-9a-fA-Fx])"
_HEX_BOUNDARY_R = r"(?![0-9a-fA-F])"

EVM_TX_HASH_RE = re.compile(_HEX_BOUNDARY_L + r"0x[0-9a-fA-F]{64}" + _HEX_BOUNDARY_R)
EVM_ADDRESS_RE = re.compile(_HEX_BOUNDARY_L + r"0x[0-9a-fA-F]{40}" + _HEX_BOUNDARY_R)
# Bare 64-hex (no 0x): candidate BTC txid — only accepted with nearby context.
BARE_64_HEX_RE = re.compile(_HEX_BOUNDARY_L + r"[0-9a-fA-F]{64}" + _HEX_BOUNDARY_R)
# Bech32 (bc1...) — distinctive prefix + charset; total length 14-74 chars.
BECH32_RE = re.compile(r"\bbc1[02-9ac-hj-np-z]{11,71}\b")
# Base58 legacy BTC address — high false-positive risk, so context-gated.
BASE58_BTC_RE = re.compile(r"\b[13][1-9A-HJ-NP-Za-km-z]{25,34}\b")

URL_RE = re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Context keywords that gate the riskier extractors.
BTC_TXID_CONTEXT = ("txid", "tx id", "transaction", "hash", "btc", "bitcoin")
BTC_ADDR_CONTEXT = ("address", "wallet", "btc", "bitcoin", "sent", "deposit")

# Known exchange / platform names (lowercase). Matched on word boundaries.
EXCHANGES = (
    "coinbase",
    "kraken",
    "binance.us",
    "binance",
    "gemini",
    "crypto.com",
    "bitstamp",
    "bitfinex",
    "kucoin",
    "okx",
    "bybit",
    "robinhood",
    "etoro",
    "bitflyer",
    "gate.io",
    "htx",
    "mexc",
    "uphold",
    "paxos",
    "cash app",
    "cashapp",
)
_EXCHANGE_RES = [
    (name, re.compile(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", re.IGNORECASE))
    for name in EXCHANGES
]
# Canonical evidence values stay lowercase (stable keys for the eval gate);
# letters and plans address the company the way it writes its own name.
EXCHANGE_DISPLAY = {
    "coinbase": "Coinbase", "kraken": "Kraken", "binance.us": "Binance.US",
    "binance": "Binance", "gemini": "Gemini", "crypto.com": "Crypto.com",
    "bitstamp": "Bitstamp", "bitfinex": "Bitfinex", "kucoin": "KuCoin",
    "okx": "OKX", "bybit": "Bybit", "robinhood": "Robinhood", "etoro": "eToro",
    "bitflyer": "bitFlyer", "gate.io": "Gate.io", "htx": "HTX", "mexc": "MEXC",
    "uphold": "Uphold", "paxos": "Paxos", "cash app": "Cash App",
    "cashapp": "Cash App",
}


def display_exchange(name: str) -> str:
    return EXCHANGE_DISPLAY.get(name, name)

CRYPTO_CODES = (
    "BTC",
    "ETH",
    "USDT",
    "USDC",
    "SOL",
    "XRP",
    "DOGE",
    "LTC",
    "ADA",
    "TRX",
    "DAI",
    "BNB",
)
FIAT_CODES = ("USD", "EUR", "GBP", "CAD", "AUD")

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# Spoken multipliers. "$2.5 million" is a $2,500,000 loss; reading it as $2.50
# is not conservative — it is a six-orders-of-magnitude fabrication in a
# federal filing. Word forms need a space ("2.5 million"); letter forms must be
# attached ("$5k", "$1.5M"). A bare "b" is never accepted (too ambiguous).
# ``_MULT`` captures (one use per regex); ``_MULT_NC`` is the non-capturing
# twin for patterns that embed the amount shape more than once.
_MULT_WORDS = r"(?i:thousand|grand|million|mil|billion)"
_MULT_LETTERS = r"[kK]|[mM]{1,2}|bn|BN"
_MULT = r"(?:\s+(?P<mult_w>" + _MULT_WORDS + r")|(?P<mult_s>" + _MULT_LETTERS + r"))?"
_MULT_NC = r"(?:\s+(?:" + _MULT_WORDS + r")|(?:" + _MULT_LETTERS + r"))?"
_MULTIPLIERS = {
    "thousand": 1_000, "grand": 1_000, "k": 1_000,
    "million": 1_000_000, "mil": 1_000_000, "m": 1_000_000, "mm": 1_000_000,
    "billion": 1_000_000_000, "bn": 1_000_000_000,
}

# $1,234.56 / €500 / £2000 / $2.5 million / $5k
SYMBOL_AMOUNT_RE = re.compile(r"(?P<sym>[$€£])\s?(?P<num>" + _NUM + r")" + _MULT + r"\b")
# 1.5 BTC / 20000 USDT / 500 USD  (code after)
CODE_AMOUNT_RE = re.compile(
    r"\b(?P<num>" + _NUM + r")" + _MULT + r"\s?(?P<code>"
    + "|".join(CRYPTO_CODES + FIAT_CODES) + r")\b"
)
# USD 500 (code before)
CODE_FIRST_AMOUNT_RE = re.compile(
    r"\b(?P<code>" + "|".join(FIAT_CODES) + r")\s?(?P<num>" + _NUM + r")" + _MULT + r"\b"
)
# 5,000 dollars / 5k dollars
WORD_AMOUNT_RE = re.compile(
    r"\b(?P<num>" + _NUM + r")" + _MULT + r"\s(?:US\s)?dollars?\b", re.IGNORECASE
)
# "5 grand" — US idiom for thousands of dollars; a number immediately before
# "grand" is the money sense ("grand larceny" has no number in front of it).
GRAND_AMOUNT_RE = re.compile(r"\b(?P<num>" + _NUM + r")\s+(?P<mult_w>grand)\b", re.IGNORECASE)
# 0.5 bitcoin / 2 ether — spelled-out asset names, mapped to their tickers.
CRYPTO_WORDS = {
    "bitcoin": "BTC", "bitcoins": "BTC",
    "ether": "ETH", "ethereum": "ETH",
    "tether": "USDT",
    "solana": "SOL",
    "dogecoin": "DOGE",
    "litecoin": "LTC",
    "ripple": "XRP",
}
WORD_CRYPTO_AMOUNT_RE = re.compile(
    r"\b(?P<num>" + _NUM + r")\s(?P<word>" + "|".join(CRYPTO_WORDS) + r")\b", re.IGNORECASE
)
# Something that looks like money but was not read by any rule above — surfaced
# as a note so a miss is never silent.
MONEY_LIKE_RE = re.compile(
    r"[$€£]\s?\d[\d,.]*\S*|\b\d[\d,.]*\s?(?:" + _MULT_WORDS + r"|[kK])\b"
)

_SYMBOL_TO_CODE = {"$": "USD", "€": "EUR", "£": "GBP"}

# Every fiat amount shape above, non-capturing, so the asset qualifier's
# ``amt`` group reproduces the extractor's verbatim token exactly.
_FIAT_AMOUNT_ALT = (
    r"[$€£]\s?(?:" + _NUM + r")" + _MULT_NC
    + r"|\b(?:" + "|".join(FIAT_CODES) + r")\s?(?:" + _NUM + r")" + _MULT_NC
    + r"|\b(?:" + _NUM + r")" + _MULT_NC + r"\s?(?:" + "|".join(FIAT_CODES) + r")"
    + r"|\b(?:" + _NUM + r")" + _MULT_NC + r"\s(?i:(?:US\s)?dollars?)"
)

# "$12,500 worth of ETH" — the NUMBER is denominated in dollars, but the ASSET
# that actually moved is ETH. Recording only the currency labels a crypto leg
# "USD", which reads as a bank transfer to an exchange's fraud desk sitting next
# to an 0x transaction hash. The qualifier is captured as a separate label; it
# never changes the amount, and no conversion is ever performed. The ticker is
# matched case-sensitively so the stored label is a verbatim slice of the story.
ASSET_QUALIFIER_RE = re.compile(
    r"(?P<amt>" + _FIAT_AMOUNT_ALT + r")'?"
    r"\s+(?:worth\s+of|worth\s+in|worth|of|in)\s+"
    r"(?P<code>" + "|".join(CRYPTO_CODES) + r")\b"
)

# An amount described as a total of several transfers is not itself a
# transfer. Only consulted when a paragraph states two or more amounts.
_TOTAL_CONTEXT_RE = re.compile(
    r"\b(?:total(?:l?ing|led)?|altogether|all\s+together|in\s+all|in\s+total|"
    r"overall|came\s+to|comes\s+to|adds?\s+up\s+to|added\s+up\s+to|combined|sum)\b",
    re.IGNORECASE,
)

# Subject business names for the IC3 form's Step 4 "Business Name" field.
# Deliberately narrow: 1-4 capitalized words immediately followed by a corporate
# suffix. "LP" and "Co." are excluded — they collide with initials and ordinary
# prose, and naming an innocent company as a fraud subject is the exact class of
# error this project refuses to make. Words are separated by a single space or a
# single newline so a match can never span a paragraph break.
CORPORATE_SUFFIXES = (
    "LLC",
    "L.L.C.",
    "Incorporated",
    "Inc.",
    "Inc",
    "Limited",
    "Ltd.",
    "Ltd",
    "Corporation",
    "Corp.",
    "Corp",
    "LLP",
    "PLC",
    "GmbH",
    "S.A.",
)
BUSINESS_NAME_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&'’\-\.]*[ \n]){1,4}"
    r"(?:" + "|".join(re.escape(s) for s in CORPORATE_SUFFIXES) + r")"
    r"(?![A-Za-z0-9])"
)

# Date patterns. Deliberately explicit — no fuzzy parsing of arbitrary text.
_MONTHS = (
    r"January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
ISO_DATE_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
# 2026/03/28 — year first, so unambiguous.
YEAR_FIRST_SLASH_DATE_RE = re.compile(r"\b(?P<y>\d{4})/(?P<m>\d{1,2})/(?P<d>\d{1,2})\b")
# 3/28/2026 and 03-28-2026 — both read under the same day/month rules.
SLASH_DATE_RE = re.compile(r"\b(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4})\b")
DASH_DATE_RE = re.compile(r"\b(?P<a>\d{1,2})-(?P<b>\d{1,2})-(?P<y>\d{4})\b")
# March 3, 2026 / Mar. 3rd 2026
MONTH_NAME_DATE_RE = re.compile(
    r"\b(?P<mon>" + _MONTHS + r")"
    r"\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})\b",
    re.IGNORECASE,
)
# 3 March 2026 / 3rd of March, 2026 — the day-first form most of the world
# writes, and what a non-US victim or a bank statement will say.
DAY_MONTH_NAME_DATE_RE = re.compile(
    r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<mon>" + _MONTHS + r")"
    r"\.?,?\s+(?P<y>\d{4})\b",
    re.IGNORECASE,
)


@dataclass
class ExtractionResult:
    """Everything the deterministic pass pulled out of the story."""

    evidence: list[EvidenceItem] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    # Amounts the story describes as a total of several transfers. Evidence,
    # but never transactions (see _build_transactions).
    stated_totals: list[EvidenceItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def values_of(self, kind: str) -> list[str]:
        return [e.value for e in self.evidence if e.kind == kind]


def _context(text: str, start: int, end: int, radius: int = 80) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def _has_context(text: str, start: int, end: int, keywords: tuple[str, ...], radius: int = 120) -> bool:
    window = text[max(0, start - radius) : min(len(text), end + radius)].lower()
    return any(k in window for k in keywords)


def _normalize_amount(num: str) -> Decimal | None:
    try:
        return Decimal(num.replace(",", ""))
    except InvalidOperation:
        return None


# European thousands-separator shape: 1.500 / 12.345.678 (dot groups of three,
# integer part not starting with 0). Under the US reading this silently turns
# 1500 EUR into 1.5 EUR, so it is ambiguous and dropped, never guessed.
_EURO_GROUPED_NUM_RE = re.compile(r"[1-9]\d{0,2}(?:\.\d{3})+\Z")


def _is_partial_number(text: str, start: int, end: int) -> bool:
    """True when the matched digits are a slice of a larger number token.

    Guards against comma-decimal (European) inputs like '1.000,50' or '$2,50',
    where the US-shaped regex matches a fragment from the middle of the number
    and would extract a fabricated amount.
    """
    if start > 0:
        prev = text[start - 1]
        if prev.isdigit():
            return True
        if prev in ".," and start > 1 and text[start - 2].isdigit():
            return True
    if end < len(text):
        nxt = text[end]
        if nxt.isdigit():
            return True
        if nxt in ".," and end + 1 < len(text) and text[end + 1].isdigit():
            return True
    return False


def extract_hashes_and_addresses(text: str) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    evm_hash_spans: list[tuple[int, int]] = []

    for m in EVM_TX_HASH_RE.finditer(text):
        evm_hash_spans.append(m.span())
        items.append(
            EvidenceItem(
                kind="evm_tx_hash",
                value=m.group(0).lower(),
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    for m in EVM_ADDRESS_RE.finditer(text):
        items.append(
            EvidenceItem(
                kind="evm_address",
                value=m.group(0).lower(),
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    for m in BARE_64_HEX_RE.finditer(text):
        # Skip anything inside an EVM hash span (defensive; lookbehind on 'x'
        # already prevents this) and require nearby transaction context.
        if any(s <= m.start() and m.end() <= e for s, e in evm_hash_spans):
            continue
        if not _has_context(text, *m.span(), BTC_TXID_CONTEXT):
            continue
        items.append(
            EvidenceItem(
                kind="btc_txid",
                value=m.group(0).lower(),
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    for m in BECH32_RE.finditer(text):
        items.append(
            EvidenceItem(
                kind="btc_address",
                value=m.group(0),  # bech32 is case-sensitive-lower already
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    for m in BASE58_BTC_RE.finditer(text):
        if not _has_context(text, *m.span(), BTC_ADDR_CONTEXT):
            continue
        items.append(
            EvidenceItem(
                kind="btc_address",
                value=m.group(0),  # base58 is case-sensitive: never lowercase
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    return items


def extract_amounts(text: str, notes: list[str] | None = None) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    seen_spans: list[tuple[int, int]] = []

    def _overlaps(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= s or span[0] >= e) for s, e in seen_spans)

    handled_spans: list[tuple[int, int]] = []  # extracted OR already noted

    def _handled(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= s or span[0] >= e) for s, e in handled_spans)

    def _say(msg: str) -> None:
        if notes is not None:
            notes.append(msg)

    def _note(m: re.Match) -> None:
        handled_spans.append(m.span())
        _say(
            f"Amount near '{m.group(0)}' looks like a European/comma-decimal "
            "number; ambiguous, so it was NOT extracted. Victim should "
            "restate it in plain US format (e.g. 1500.50)."
        )

    def _add(m: re.Match, num: str, code: str) -> None:
        if _overlaps(m.span()):
            return
        # Reject matches whose digits are a slice of a larger number token
        # (comma-decimal inputs like '1.000,50 EUR' or '$2,50').
        if _is_partial_number(text, *m.span("num")):
            _note(m)
            return
        # Reject European dot-grouped shapes ('€1.500'): ambiguous magnitude.
        if _EURO_GROUPED_NUM_RE.fullmatch(num):
            _note(m)
            return
        dec = _normalize_amount(num)
        if dec is None:
            return
        groups = m.groupdict()
        mult = (groups.get("mult_w") or groups.get("mult_s") or "").lower()
        if mult:
            if code in CRYPTO_CODES:
                # "5k BTC" — a multiplier on a crypto quantity reads as a typo;
                # the honest move is to ask, not to guess a magnitude.
                handled_spans.append(m.span())
                _say(
                    f"'{m.group(0)}' pairs a multiplier with a crypto quantity; "
                    "ambiguous, so it was NOT extracted. Victim should restate "
                    "the exact quantity."
                )
                return
            dec = dec * _MULTIPLIERS[mult]
            if dec == dec.to_integral_value():
                dec = Decimal(int(dec))
            _say(
                f"'{m.group(0)}' was read as {dec} {code}. Confirm the figure "
                "before filing."
            )
        seen_spans.append(m.span())
        handled_spans.append(m.span())
        items.append(
            EvidenceItem(
                kind="amount",
                value=f"{dec} {code}",
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    for m in SYMBOL_AMOUNT_RE.finditer(text):
        _add(m, m.group("num"), _SYMBOL_TO_CODE[m.group("sym")])
    for m in CODE_AMOUNT_RE.finditer(text):
        _add(m, m.group("num"), m.group("code").upper())
    for m in CODE_FIRST_AMOUNT_RE.finditer(text):
        _add(m, m.group("num"), m.group("code").upper())
    for m in WORD_AMOUNT_RE.finditer(text):
        _add(m, m.group("num"), "USD")
    for m in GRAND_AMOUNT_RE.finditer(text):
        _add(m, m.group("num"), "USD")
    for m in WORD_CRYPTO_AMOUNT_RE.finditer(text):
        _add(m, m.group("num"), CRYPTO_WORDS[m.group("word").lower()])

    # Anything money-shaped that no rule read is reported, never dropped in
    # silence: a miss the victim can see is recoverable, a silent one is not.
    for m in MONEY_LIKE_RE.finditer(text):
        if _handled(m.span()):
            continue
        handled_spans.append(m.span())
        _say(
            f"'{m.group(0).strip().rstrip('.,;:')}' looks like an amount but "
            "could not be read, so it was NOT extracted. Victim should restate "
            "it plainly (e.g. 5000 USD)."
        )

    return items


def extract_dates(text: str) -> list[EvidenceItem]:
    """Extract dates from explicit patterns only.

    Ambiguity policy (documented, deterministic): D/D/YYYY slash dates are
    read as US month/day/year. When the first number cannot be a month
    (e.g. 25/03/2026) it is read day-first. A slash date that is ambiguous
    gets a note in its context but is still extracted with the US reading.
    """
    items: list[EvidenceItem] = []
    seen_spans: list[tuple[int, int]] = []

    def _overlaps(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= s or span[0] >= e) for s, e in seen_spans)

    def _emit(m: re.Match, value: str) -> None:
        try:
            date_parser.isoparse(value)
        except (ValueError, OverflowError):
            return
        seen_spans.append(m.span())
        items.append(
            EvidenceItem(
                kind="date",
                value=value,
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    def _month_name(m: re.Match) -> None:
        if _overlaps(m.span()):
            return
        try:
            dt = date_parser.parse(f"{m.group('mon')} {m.group('d')} {m.group('y')}")
        except (ValueError, OverflowError):
            return
        _emit(m, dt.date().isoformat())

    def _numeric_ambiguous(m: re.Match) -> None:
        """3/28/2026 and 03-28-2026: US month-first unless that is impossible."""
        if _overlaps(m.span()):
            return
        a, b, y = int(m.group("a")), int(m.group("b")), int(m.group("y"))
        if 1 <= a <= 12 and 1 <= b <= 31:
            month, day = a, b  # US month-first reading
        elif a > 12 and 1 <= b <= 12 and 1 <= a <= 31:
            month, day = b, a  # unambiguous day-first
        else:
            return
        _emit(m, f"{y:04d}-{month:02d}-{day:02d}")

    # Unambiguous forms first so they claim their spans before the ambiguous
    # numeric readers run.
    for m in ISO_DATE_RE.finditer(text):
        _emit(m, m.group(0))
    for m in YEAR_FIRST_SLASH_DATE_RE.finditer(text):
        if not _overlaps(m.span()):
            _emit(m, f"{int(m.group('y')):04d}-{int(m.group('m')):02d}-{int(m.group('d')):02d}")
    for m in MONTH_NAME_DATE_RE.finditer(text):
        _month_name(m)
    for m in DAY_MONTH_NAME_DATE_RE.finditer(text):
        _month_name(m)
    for m in SLASH_DATE_RE.finditer(text):
        _numeric_ambiguous(m)
    for m in DASH_DATE_RE.finditer(text):
        _numeric_ambiguous(m)

    return items


def extract_urls_and_emails(text: str) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?")
        items.append(
            EvidenceItem(
                kind="url", value=url, verbatim=url, context=_context(text, *m.span())
            )
        )
    for m in EMAIL_RE.finditer(text):
        items.append(
            EvidenceItem(
                kind="email",
                value=m.group(0).lower(),
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )
    return items


def extract_exchanges(text: str) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    claimed: list[tuple[int, int]] = []
    # EXCHANGES is ordered so longer names (binance.us) come before prefixes
    # (binance); span-claiming prevents double counting.
    for name, rx in _EXCHANGE_RES:
        for m in rx.finditer(text):
            if any(not (m.end() <= s or m.start() >= e) for s, e in claimed):
                continue
            claimed.append(m.span())
            items.append(
                EvidenceItem(
                    kind="exchange",
                    value=name,
                    verbatim=m.group(0),
                    context=_context(text, *m.span()),
                )
            )
    return items


def extract_business_names(text: str) -> list[EvidenceItem]:
    """Extract candidate subject business names (IC3 Step 4 'Business Name').

    These are CANDIDATES, not confirmed subjects: a story can also name the
    victim's own bank or exchange. Filings render them with an explicit
    confirm-this instruction rather than asserting them. Names that contain a
    recognized exchange, or that sit inside a URL or email address, are dropped.
    """
    items: list[EvidenceItem] = []
    skip_spans = [m.span() for m in URL_RE.finditer(text)]
    skip_spans += [m.span() for m in EMAIL_RE.finditer(text)]
    exchange_names = {name.lower() for name in EXCHANGES}

    for m in BUSINESS_NAME_RE.finditer(text):
        if any(not (m.end() <= s or m.start() >= e) for s, e in skip_spans):
            continue
        verbatim = m.group(0)
        value = " ".join(verbatim.split())
        lowered = value.lower()
        if any(name in lowered for name in exchange_names):
            continue
        items.append(
            EvidenceItem(
                kind="business",
                value=value,
                verbatim=verbatim,
                context=_context(text, *m.span()),
            )
        )
    return items


def _asset_qualifiers(text: str) -> dict[str, str]:
    """Map an amount's verbatim token to the crypto asset it was stated in.

    '$12,500 worth of ETH' -> {'$12,500': 'ETH'}. A token qualified by two
    different assets in one segment is ambiguous and is dropped rather than
    guessed.
    """
    found: dict[str, set[str]] = {}
    for m in ASSET_QUALIFIER_RE.finditer(text):
        found.setdefault(m.group("amt"), set()).add(m.group("code").upper())
    return {amt: next(iter(codes)) for amt, codes in found.items() if len(codes) == 1}


# Paragraph-level segmentation: victims naturally describe one transfer per
# paragraph, and hard-wrapped lines must not split a sentence's facts apart.
_SEGMENT_SPLIT_RE = re.compile(r"\n\s*\n")


def _segments(text: str) -> list[str]:
    return [s for s in _SEGMENT_SPLIT_RE.split(text) if s and s.strip()]


# "$10,000 total" / "$10,000 in total" / "$10,000 altogether": a total-word
# immediately AFTER an amount binds to that amount.
_TOTAL_AFTER_RE = re.compile(
    r"^\s*(?:in\s+)?(?:total|altogether|all\s+together|combined|overall)\b",
    re.IGNORECASE,
)


def _amount_spans(seg: str, amounts: list[EvidenceItem]) -> list[tuple[int, int, EvidenceItem]]:
    """Locate each amount in its paragraph, in text order.

    Duplicate tokens ("$500 ... $500") map to successive occurrences.
    """
    cursors: dict[str, int] = {}
    spans: list[tuple[int, int, EvidenceItem]] = []
    for a in amounts:
        start = seg.find(a.verbatim, cursors.get(a.verbatim, 0))
        if start == -1:
            start = seg.find(a.verbatim)
        end = start + len(a.verbatim)
        cursors[a.verbatim] = end
        spans.append((start, end, a))
    spans.sort(key=lambda s: s[0])
    return spans


def _stated_totals_in(seg: str, amounts: list[EvidenceItem]) -> list[EvidenceItem]:
    """Amounts this paragraph describes as the sum of its other amounts.

    A total-word binds to the amount it sits right after ("$10,000 total: …")
    or, failing that, to the amount it precedes within a short window ("the
    total came to $10,000"). Windows never cross a neighboring amount, and a
    stretch of text claimed by one amount's after-window is not re-read as the
    next amount's before-window — otherwise "$10,000 total: $4,000" would flag
    both.
    """
    spans = _amount_spans(seg, amounts)
    flagged: list[EvidenceItem] = []
    claimed_until = 0
    for i, (start, end, item) in enumerate(spans):
        prev_end = spans[i - 1][1] if i > 0 else 0
        next_start = spans[i + 1][0] if i + 1 < len(spans) else len(seg)
        after = seg[end:min(next_start, end + 24)]
        before = seg[max(prev_end, claimed_until, start - 48):start]
        m_after = _TOTAL_AFTER_RE.match(after)
        if m_after:
            flagged.append(item)
            claimed_until = end + m_after.end()
        elif _TOTAL_CONTEXT_RE.search(before):
            flagged.append(item)
    return flagged


def _build_transactions(
    text: str, notes: list[str] | None = None
) -> tuple[list[Transaction], list[EvidenceItem]]:
    """Group co-occurring facts into transactions, one paragraph at a time.

    A paragraph that contains an amount OR a tx hash becomes a transaction;
    dates/hashes/addresses in the same paragraph are attached to it in order.
    Facts that co-occur with nothing stay as loose evidence — never invented
    into a transaction.

    Returns ``(transactions, stated_totals)``. An amount a paragraph describes
    as the total of several transfers ("altogether $10,000 — four transfers of
    $2,500") is a stated total, not a transfer. It stays evidence and is
    returned separately so the IC3 total-loss line can quote it, but it never
    becomes a transaction: that would add the total to its own parts.
    """
    txns: list[Transaction] = []
    stated_totals: list[EvidenceItem] = []
    for seg in _segments(text):
        amounts = extract_amounts(seg)
        if len(amounts) >= 2:
            totals = _stated_totals_in(seg, amounts)
            if len(totals) == 1:
                # Exactly one total among several amounts: quote it, don't
                # count it. Two flagged totals is ambiguous — leave every
                # amount as a transfer and say so.
                (total,) = totals
                stated_totals.append(total)
                amounts = [a for a in amounts if a is not total]
                if notes is not None:
                    notes.append(
                        f"'{total.verbatim}' reads as a stated TOTAL, not a "
                        "separate transfer, so it was not added to the itemized "
                        "loss. Confirm it equals the sum of the transfers you "
                        "listed."
                    )
            elif len(totals) > 1 and notes is not None:
                notes.append(
                    "More than one amount in one paragraph reads as a total ("
                    + "; ".join(t.verbatim for t in totals)
                    + "); all were kept as transfers. Check the itemized list "
                    "for double counting."
                )
        qualifiers = _asset_qualifiers(seg)
        dates = extract_dates(seg)
        hash_items = [
            e
            for e in extract_hashes_and_addresses(seg)
            if e.kind in ("evm_tx_hash", "btc_txid")
        ]
        addr_items = [
            e
            for e in extract_hashes_and_addresses(seg)
            if e.kind in ("evm_address", "btc_address")
        ]
        if not amounts and not hash_items:
            continue

        n = max(len(amounts), len(hash_items), 1)
        for i in range(n):
            # Amounts are paired index-wise and NEVER replicated: a single
            # stated amount alongside several hashes may be a total ("$3,000
            # across three transfers"), so copying it onto every hash would
            # fabricate a larger loss. Unmatched hashes get amount=None.
            amt = amounts[i] if i < len(amounts) else None
            hsh = hash_items[i] if i < len(hash_items) else None
            date = dates[i] if i < len(dates) else (dates[0] if dates else None)
            addr = addr_items[i] if i < len(addr_items) else (
                addr_items[0] if addr_items else None
            )
            amount_dec = None
            currency = None
            asset = None
            if amt is not None:
                num, _, code = amt.value.partition(" ")
                amount_dec = Decimal(num)
                currency = code or None
                asset = qualifiers.get(amt.verbatim)
            method = None
            if hsh is not None or (currency in CRYPTO_CODES) or asset is not None:
                method = "Cryptocurrency"
            elif re.search(r"\bwire(d|s)?\b|\bwire transfer\b", seg, re.IGNORECASE):
                method = "Wire Transfer"
            txns.append(
                Transaction(
                    amount=amount_dec,
                    amount_verbatim=amt.verbatim if amt else None,
                    currency=currency,
                    asset=asset,
                    date=date.value if date else None,
                    date_verbatim=date.verbatim if date else None,
                    tx_hash=hsh.value if hsh else None,
                    tx_hash_verbatim=hsh.verbatim if hsh else None,
                    destination_address=addr.value if addr else None,
                    destination_address_verbatim=addr.verbatim if addr else None,
                    method=method,
                    context=seg.strip(),
                )
            )
    return txns, stated_totals


def extract(text: str) -> ExtractionResult:
    """Run every deterministic extractor over the story."""
    result = ExtractionResult()
    result.evidence.extend(extract_hashes_and_addresses(text))
    result.evidence.extend(extract_amounts(text, notes=result.notes))
    result.evidence.extend(extract_dates(text))
    result.evidence.extend(extract_urls_and_emails(text))
    result.evidence.extend(extract_exchanges(text))
    result.evidence.extend(extract_business_names(text))
    result.transactions, result.stated_totals = _build_transactions(
        text, notes=result.notes
    )

    for rx in (SLASH_DATE_RE, DASH_DATE_RE):
        for m in rx.finditer(text):
            a, b = int(m.group("a")), int(m.group("b"))
            if 1 <= a <= 12 and 1 <= b <= 12 and a != b:
                result.notes.append(
                    f"Date '{m.group(0)}' is ambiguous (day/month order); "
                    f"read as US month/day/year. Victim should confirm."
                )
    return result
