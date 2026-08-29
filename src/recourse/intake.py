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
# $1,234.56 / €500 / £2000
SYMBOL_AMOUNT_RE = re.compile(r"(?P<sym>[$€£])\s?(?P<num>" + _NUM + r")\b")
# 1.5 BTC / 20000 USDT / 500 USD  (code after)
CODE_AMOUNT_RE = re.compile(
    r"\b(?P<num>" + _NUM + r")\s?(?P<code>" + "|".join(CRYPTO_CODES + FIAT_CODES) + r")\b"
)
# USD 500 (code before)
CODE_FIRST_AMOUNT_RE = re.compile(
    r"\b(?P<code>" + "|".join(FIAT_CODES) + r")\s?(?P<num>" + _NUM + r")\b"
)
# 5,000 dollars
WORD_AMOUNT_RE = re.compile(r"\b(?P<num>" + _NUM + r")\s(?:US\s)?dollars?\b", re.IGNORECASE)

_SYMBOL_TO_CODE = {"$": "USD", "€": "EUR", "£": "GBP"}

# Date patterns. Deliberately explicit — no fuzzy parsing of arbitrary text.
ISO_DATE_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
SLASH_DATE_RE = re.compile(r"\b(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4})\b")
MONTH_NAME_DATE_RE = re.compile(
    r"\b(?P<mon>January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})\b",
    re.IGNORECASE,
)


@dataclass
class ExtractionResult:
    """Everything the deterministic pass pulled out of the story."""

    evidence: list[EvidenceItem] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
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

    def _note(m: re.Match) -> None:
        if notes is not None:
            notes.append(
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
        seen_spans.append(m.span())
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

    for m in ISO_DATE_RE.finditer(text):
        try:
            date_parser.isoparse(m.group(0))
        except ValueError:
            continue
        seen_spans.append(m.span())
        items.append(
            EvidenceItem(
                kind="date",
                value=m.group(0),
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    for m in MONTH_NAME_DATE_RE.finditer(text):
        if _overlaps(m.span()):
            continue
        try:
            dt = date_parser.parse(f"{m.group('mon')} {m.group('d')} {m.group('y')}")
        except (ValueError, OverflowError):
            continue
        seen_spans.append(m.span())
        items.append(
            EvidenceItem(
                kind="date",
                value=dt.date().isoformat(),
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

    for m in SLASH_DATE_RE.finditer(text):
        if _overlaps(m.span()):
            continue
        a, b, y = int(m.group("a")), int(m.group("b")), int(m.group("y"))
        if 1 <= a <= 12 and 1 <= b <= 31:
            month, day = a, b  # US month-first reading
        elif a > 12 and 1 <= b <= 12 and 1 <= a <= 31:
            month, day = b, a  # unambiguous day-first
        else:
            continue
        try:
            value = f"{y:04d}-{month:02d}-{day:02d}"
            date_parser.isoparse(value)
        except (ValueError, OverflowError):
            continue
        seen_spans.append(m.span())
        items.append(
            EvidenceItem(
                kind="date",
                value=value,
                verbatim=m.group(0),
                context=_context(text, *m.span()),
            )
        )

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


# Paragraph-level segmentation: victims naturally describe one transfer per
# paragraph, and hard-wrapped lines must not split a sentence's facts apart.
_SEGMENT_SPLIT_RE = re.compile(r"\n\s*\n")


def _segments(text: str) -> list[str]:
    return [s for s in _SEGMENT_SPLIT_RE.split(text) if s and s.strip()]


def _build_transactions(text: str) -> list[Transaction]:
    """Group co-occurring facts into transactions, one paragraph at a time.

    A paragraph that contains an amount OR a tx hash becomes a transaction;
    dates/hashes/addresses in the same paragraph are attached to it in order.
    Facts that co-occur with nothing stay as loose evidence — never invented
    into a transaction.
    """
    txns: list[Transaction] = []
    for seg in _segments(text):
        amounts = extract_amounts(seg)
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
            if amt is not None:
                num, _, code = amt.value.partition(" ")
                amount_dec = Decimal(num)
                currency = code or None
            method = None
            if hsh is not None or (currency in CRYPTO_CODES):
                method = "Cryptocurrency"
            elif re.search(r"\bwire(d|s)?\b|\bwire transfer\b", seg, re.IGNORECASE):
                method = "Wire Transfer"
            txns.append(
                Transaction(
                    amount=amount_dec,
                    amount_verbatim=amt.verbatim if amt else None,
                    currency=currency,
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
    return txns


def extract(text: str) -> ExtractionResult:
    """Run every deterministic extractor over the story."""
    result = ExtractionResult()
    result.evidence.extend(extract_hashes_and_addresses(text))
    result.evidence.extend(extract_amounts(text, notes=result.notes))
    result.evidence.extend(extract_dates(text))
    result.evidence.extend(extract_urls_and_emails(text))
    result.evidence.extend(extract_exchanges(text))
    result.transactions = _build_transactions(text)

    for m in SLASH_DATE_RE.finditer(text):
        a, b = int(m.group("a")), int(m.group("b"))
        if 1 <= a <= 12 and 1 <= b <= 12 and a != b:
            result.notes.append(
                f"Date '{m.group(0)}' is ambiguous (day/month order); "
                f"read as US month/day/year. Victim should confirm."
            )
    return result
