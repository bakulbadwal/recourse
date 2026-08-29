"""Anti-invention audit: prove that every hard fact in a rendered filing
traces back to the victim's original story.

This is the enforcement half of the architecture rule "deterministic Python
owns every fact". The audit walks each rendered document, harvests every
hash-like token, wallet address, monetary amount, and ISO date, and verifies
the chain:

    rendered document  ->  CaseFile fact  ->  verbatim substring of the source

Any token that cannot be traced is a violation. The eval gate
(``evals/run_evals.py``) fails the build on any violation.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser

from .intake import (
    BARE_64_HEX_RE,
    BASE58_BTC_RE,
    BECH32_RE,
    CODE_AMOUNT_RE,
    EVM_ADDRESS_RE,
    EVM_TX_HASH_RE,
    ISO_DATE_RE,
    MONTH_NAME_DATE_RE,
    SYMBOL_AMOUNT_RE,
    _NUM,
)
from .models import CaseFile

_HEX_EVIDENCE_KINDS = ("evm_tx_hash", "btc_txid", "evm_address", "btc_address")
_FULL_HEX_RE = re.compile(r"(?:0x)?[0-9a-fA-F]+\Z")

# "Amount: 150000" / "Amount: 2,500.50" style lines in rendered filings.
_LABELED_AMOUNT_RE = re.compile(
    r"[Aa]mount[^:\n]*:\s*\$?(?P<num>" + _NUM + r")\b"
)


def _hex_tokens(doc: str) -> list[str]:
    tokens: list[str] = []
    tokens.extend(m.group(0) for m in EVM_TX_HASH_RE.finditer(doc))
    tokens.extend(m.group(0) for m in EVM_ADDRESS_RE.finditer(doc))
    tokens.extend(m.group(0) for m in BARE_64_HEX_RE.finditer(doc))
    tokens.extend(m.group(0) for m in BECH32_RE.finditer(doc))
    tokens.extend(m.group(0) for m in BASE58_BTC_RE.finditer(doc))
    return tokens


def _doc_amounts(doc: str) -> list[tuple[str, Decimal]]:
    out: list[tuple[str, Decimal]] = []
    for m in SYMBOL_AMOUNT_RE.finditer(doc):
        try:
            out.append((m.group(0), Decimal(m.group("num").replace(",", ""))))
        except InvalidOperation:
            continue
    for m in CODE_AMOUNT_RE.finditer(doc):
        try:
            out.append((m.group(0), Decimal(m.group("num").replace(",", ""))))
        except InvalidOperation:
            continue
    for m in _LABELED_AMOUNT_RE.finditer(doc):
        try:
            out.append((m.group(0), Decimal(m.group("num").replace(",", ""))))
        except InvalidOperation:
            continue
    return out


def _allowed_amounts(case: CaseFile) -> set[Decimal]:
    allowed: set[Decimal] = set()
    for e in case.evidence:
        if e.kind == "amount":
            num, _, _ = e.value.partition(" ")
            allowed.add(Decimal(num))
    txn_amounts = [t.amount for t in case.transactions if t.amount is not None]
    allowed.update(txn_amounts)
    # NOTE: no all-currency sum here. Filings deliberately never add BTC to
    # dollars, so a cross-currency numeric total appearing in a doc is an
    # invention and must NOT be pre-approved. Only the rendered USD total is.
    usd_amounts = [
        t.amount
        for t in case.transactions
        if t.amount is not None and t.currency == "USD"
    ]
    if usd_amounts:
        allowed.add(sum(usd_amounts, start=Decimal(0)))  # rendered USD total
    return allowed


def verify_casefile_provenance(source: str, case: CaseFile) -> list[str]:
    """Every CaseFile fact must carry a verbatim snippet found in the source."""
    violations: list[str] = []
    for e in case.evidence:
        if e.verbatim not in source:
            violations.append(
                f"CaseFile {e.kind} {e.value!r}: verbatim {e.verbatim!r} "
                "not found in source story"
            )
    for i, t in enumerate(case.transactions, 1):
        for label, verbatim in (
            ("amount", t.amount_verbatim),
            ("date", t.date_verbatim),
            ("tx_hash", t.tx_hash_verbatim),
            ("destination_address", t.destination_address_verbatim),
        ):
            if verbatim is not None and verbatim not in source:
                violations.append(
                    f"Transaction {i} {label}: verbatim {verbatim!r} "
                    "not found in source story"
                )
    return violations


def verify_rendered_doc(source: str, case: CaseFile, doc: str, doc_name: str = "doc") -> list[str]:
    """Every hard fact rendered into ``doc`` must trace back to ``source``."""
    violations: list[str] = []

    # Allowed hex-like tokens: whole tokens harvested from the source with the
    # same boundary-aware regexes, plus extracted evidence/transaction values
    # and verbatims. Membership is EXACT (never substring containment), so a
    # fabricated 40-hex address that happens to be a slice of a real 64-hex
    # hash in the source is still flagged. Pure-hex tokens also compare
    # case-insensitively (hex case is not significant); base58/bech32 do not.
    allowed_hex: set[str] = set(_hex_tokens(source))
    for e in case.evidence:
        if e.kind in _HEX_EVIDENCE_KINDS:
            allowed_hex.add(e.value)
            allowed_hex.add(e.verbatim)
    for t in case.transactions:
        for v in (
            t.tx_hash,
            t.tx_hash_verbatim,
            t.destination_address,
            t.destination_address_verbatim,
        ):
            if v:
                allowed_hex.add(v)
    allowed_hex_lower = {a.lower() for a in allowed_hex if _FULL_HEX_RE.fullmatch(a)}

    for token in _hex_tokens(doc):
        if token == case.case_id:
            continue  # the case id is derived (sha256), not a source fact
        if token in allowed_hex:
            continue
        if _FULL_HEX_RE.fullmatch(token) and token.lower() in allowed_hex_lower:
            continue
        violations.append(
            f"{doc_name}: token {token!r} does not appear in the source story"
        )

    allowed_amounts = _allowed_amounts(case)
    for verbatim, value in _doc_amounts(doc):
        if value not in allowed_amounts:
            violations.append(
                f"{doc_name}: amount {verbatim!r} (= {value}) does not match any "
                "extracted amount or their total"
            )

    allowed_dates = {e.value for e in case.evidence if e.kind == "date"}
    allowed_dates.update(t.date for t in case.transactions if t.date is not None)
    for m in ISO_DATE_RE.finditer(doc):
        if m.group(0) not in allowed_dates:
            violations.append(
                f"{doc_name}: date {m.group(0)!r} does not match any extracted date"
            )

    # Month-name dates ("March 15, 1999") rendered into a doc must trace back
    # to an extracted date too — the ISO check alone would miss them.
    for m in MONTH_NAME_DATE_RE.finditer(doc):
        try:
            dt = date_parser.parse(f"{m.group('mon')} {m.group('d')} {m.group('y')}")
        except (ValueError, OverflowError):
            continue
        if dt.date().isoformat() not in allowed_dates:
            violations.append(
                f"{doc_name}: date {m.group(0)!r} does not match any extracted date"
            )

    return violations


def verify_no_invention(source: str, case: CaseFile, docs: dict[str, str]) -> list[str]:
    """Full audit: CaseFile provenance plus every rendered document."""
    violations = verify_casefile_provenance(source, case)
    for name, doc in docs.items():
        violations.extend(verify_rendered_doc(source, case, doc, doc_name=name))
    return violations
