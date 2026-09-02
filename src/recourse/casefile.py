"""CaseFile assembly: dedupe, timeline ordering, verified/unverified split,
and a stable content-derived case id.

Determinism contract:
- Same input text -> same CaseFile -> same case_id, on any machine, any day.
- No wall-clock reads. ``created_at`` is an optional explicit parameter that
  is recorded on the CaseFile but EXCLUDED from the case_id hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from .intake import ExtractionResult, extract
from .models import CaseFile, EvidenceItem, Transaction, VictimInfo

_KIND_ORDER = (
    "evm_tx_hash",
    "btc_txid",
    "evm_address",
    "btc_address",
    "amount",
    "date",
    "url",
    "email",
    "exchange",
    "business",
    "phone",
)


def _dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Drop duplicate (kind, value) pairs, keeping the first occurrence."""
    seen: set[tuple[str, str]] = set()
    out: list[EvidenceItem] = []
    for item in items:
        key = (item.kind, item.value)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _sort_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return sorted(items, key=lambda e: (_KIND_ORDER.index(e.kind), e.value))


def _dedupe_transactions(txns: list[Transaction]) -> list[Transaction]:
    """Merge only true duplicates, never distinct look-alike transfers.

    A transaction hash is globally unique, so hash-bearing transactions dedupe
    on the fact tuple alone (the same transfer restated in two paragraphs is
    one transfer). A HASHLESS transaction additionally keys on its source
    paragraph (``context``): two identical-looking transfers described in
    different paragraphs ("I wired $500" ... "I wired another $500") are
    separate transfers — merging them would understate the loss.
    """
    seen: set[tuple] = set()
    out: list[Transaction] = []
    for t in txns:
        key: tuple = (
            str(t.amount) if t.amount is not None else None,
            t.currency,
            t.date,
            t.tx_hash,
            t.destination_address,
        )
        if t.tx_hash is None:
            key += (t.context,)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _timeline_sort(txns: list[Transaction]) -> list[Transaction]:
    """Dated transactions first, in date order; undated ones after, in
    original (narrative) order — a stable sort keeps ties deterministic."""
    dated = [t for t in txns if t.date is not None]
    undated = [t for t in txns if t.date is None]
    dated.sort(key=lambda t: t.date)  # type: ignore[arg-type,return-value]
    return dated + undated


def compute_case_id(
    narrative: str,
    evidence: list[EvidenceItem],
    transactions: list[Transaction],
    victim: VictimInfo,
) -> str:
    """sha256 over the canonical JSON of normalized contents.

    created_at is deliberately excluded so the id depends only on the facts.
    """
    canonical = {
        "narrative": narrative.strip(),
        "evidence": sorted((e.kind, e.value) for e in evidence),
        "transactions": sorted(
            json.dumps(t.to_dict(), sort_keys=True) for t in transactions
        ),
        "victim": victim.to_dict(),
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _unverified_notes(
    extraction: ExtractionResult,
    evidence: list[EvidenceItem],
    transactions: list[Transaction],
    victim: VictimInfo,
) -> list[str]:
    notes: list[str] = list(extraction.notes)

    kinds_present = {e.kind for e in evidence}
    if not kinds_present & {"evm_tx_hash", "btc_txid"}:
        notes.append("No transaction hashes/IDs were found in the story.")
    if not kinds_present & {"evm_address", "btc_address"}:
        notes.append("No destination wallet addresses were found in the story.")
    if "amount" not in kinds_present:
        notes.append("No monetary amounts were found in the story.")
    if "date" not in kinds_present:
        notes.append("No dates were found in the story.")
    if "exchange" not in kinds_present:
        notes.append("No exchange or platform names were recognized in the story.")

    for i, t in enumerate(transactions, 1):
        missing = []
        if t.amount is None:
            missing.append("amount")
        if t.date is None:
            missing.append("date")
        if t.tx_hash is None:
            missing.append("transaction hash/ID")
        if missing:
            notes.append(f"Transaction {i}: missing {', '.join(missing)}.")
        if t.asset is not None and t.asset != t.currency:
            notes.append(
                f"Transaction {i}: the asset sent was {t.asset}, but you stated its "
                f"value as {t.amount_verbatim or t.amount} {t.currency}. That "
                f"{t.currency} figure is your own estimate at the time — Recourse "
                f"never converts. Confirm the exact {t.asset} quantity from your "
                "wallet or exchange history before filing."
            )

    distinct_totals = sorted({t.verbatim for t in extraction.stated_totals if t.value.endswith(" USD")})
    if len({t.value for t in extraction.stated_totals if t.value.endswith(" USD")}) > 1:
        notes.append(
            "Your story states more than one different total ("
            + "; ".join(distinct_totals)
            + "). Recourse did not choose between them — state the single "
            "total yourself on the IC3 form."
        )

    business_names = [e.value for e in evidence if e.kind == "business"]
    if business_names:
        notes.append(
            "Possible subject business name(s) found in your story: "
            + "; ".join(business_names)
            + ". These are CANDIDATES only — confirm each is the scammer's "
            "business and not your own bank, exchange, or employer before "
            "putting it in the IC3 'Business Name' field."
        )

    missing_victim = victim.missing_fields()
    if missing_victim:
        notes.append(
            "Victim/complainant fields not provided (required by the IC3 form): "
            + ", ".join(missing_victim)
            + "."
        )

    notes.append(
        "Nothing in this case file has been independently verified on-chain or "
        "with any institution; all facts are as reported by the victim."
    )
    return notes


def build_casefile(
    story: str,
    victim: Optional[VictimInfo] = None,
    created_at: Optional[str] = None,
) -> CaseFile:
    """Assemble the canonical CaseFile from a raw story.

    ``created_at``, if supplied, must be an explicit ISO-8601 string chosen by
    the caller. The library itself never consults the clock.
    """
    victim = victim or VictimInfo()
    extraction = extract(story)

    evidence = _sort_evidence(_dedupe_evidence(extraction.evidence))
    transactions = _timeline_sort(_dedupe_transactions(extraction.transactions))

    # A single distinct USD figure the victim stated as their total. Two
    # different stated totals are a contradiction to surface, not to pick from.
    usd_totals = {
        t.value: t for t in extraction.stated_totals if t.value.endswith(" USD")
    }
    stated_total = stated_total_verbatim = None
    if len(usd_totals) == 1:
        (total,) = usd_totals.values()
        stated_total = total.value.partition(" ")[0]
        stated_total_verbatim = total.verbatim

    case_id = compute_case_id(story, evidence, transactions, victim)

    return CaseFile(
        case_id=case_id,
        narrative=story,
        evidence=evidence,
        transactions=transactions,
        exchanges=sorted({e.value for e in evidence if e.kind == "exchange"}),
        urls=sorted({e.value for e in evidence if e.kind == "url"}),
        emails=sorted({e.value for e in evidence if e.kind == "email"}),
        businesses=sorted({e.value for e in evidence if e.kind == "business"}),
        stated_total=stated_total,
        stated_total_verbatim=stated_total_verbatim,
        victim=victim,
        unverified_notes=_unverified_notes(extraction, evidence, transactions, victim),
        created_at=created_at,
    )
