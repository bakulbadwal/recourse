"""Core data models for Recourse.

Everything here is deterministic, dependency-light, and JSON-round-trippable.
No wall-clock reads happen anywhere in this module: ``CaseFile.created_at``
is an optional, explicitly supplied value.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any, Optional

NOT_PROVIDED = "[NOT PROVIDED]"

# Evidence kinds recognised by the intake extractors.
EVIDENCE_KINDS = frozenset(
    {
        "evm_tx_hash",
        "btc_txid",
        "evm_address",
        "btc_address",
        "amount",
        "date",
        "url",
        "email",
        "exchange",
        "phone",
    }
)


@dataclass(frozen=True)
class EvidenceItem:
    """A single extracted fact with provenance.

    ``value`` is the normalized form (lowercased hex, ISO date, plain decimal
    string). ``verbatim`` is the exact substring of the victim's story the
    fact was extracted from — the provenance anchor the anti-invention eval
    gate checks against.
    """

    kind: str
    value: str
    verbatim: str
    context: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"Unknown evidence kind: {self.kind!r}")
        if not self.value:
            raise ValueError("EvidenceItem.value must be non-empty")
        if not self.verbatim:
            raise ValueError("EvidenceItem.verbatim must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvidenceItem":
        return cls(
            kind=d["kind"],
            value=d["value"],
            verbatim=d["verbatim"],
            context=d.get("context", ""),
        )


@dataclass
class Transaction:
    """One transfer of value, as reconstructed from the story.

    Any field the victim did not provide stays ``None`` and renders as
    [NOT PROVIDED] in filings. ``amount`` is a Decimal serialized as a
    string; ``amount_verbatim`` preserves the exact source token.
    """

    amount: Optional[Decimal] = None
    amount_verbatim: Optional[str] = None
    currency: Optional[str] = None
    date: Optional[str] = None  # ISO YYYY-MM-DD
    date_verbatim: Optional[str] = None
    tx_hash: Optional[str] = None  # normalized (lowercased hex)
    tx_hash_verbatim: Optional[str] = None
    destination_address: Optional[str] = None
    destination_address_verbatim: Optional[str] = None
    method: Optional[str] = None  # e.g. "Cryptocurrency", "Wire Transfer"
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["amount"] = str(self.amount) if self.amount is not None else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transaction":
        amount = d.get("amount")
        return cls(
            amount=Decimal(amount) if amount is not None else None,
            amount_verbatim=d.get("amount_verbatim"),
            currency=d.get("currency"),
            date=d.get("date"),
            date_verbatim=d.get("date_verbatim"),
            tx_hash=d.get("tx_hash"),
            tx_hash_verbatim=d.get("tx_hash_verbatim"),
            destination_address=d.get("destination_address"),
            destination_address_verbatim=d.get("destination_address_verbatim"),
            method=d.get("method"),
            context=d.get("context", ""),
        )


@dataclass
class VictimInfo:
    """Complainant fields required by the IC3 form (Step 2).

    Recourse never guesses these from the story — emails and phone numbers in
    a scam narrative usually belong to the scammer. They are only populated
    when supplied explicitly (e.g. via the agent asking the victim).
    """

    name: Optional[str] = None
    age_range: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    zip_code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VictimInfo":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})

    def missing_fields(self) -> list[str]:
        return [k for k, v in asdict(self).items() if v is None]


@dataclass
class CaseFile:
    """Canonical, deduped, timeline-ordered record of a fraud case.

    ``case_id`` is the sha256 of the normalized contents (see
    ``recourse.casefile.compute_case_id``) — stable across runs for the same
    input. ``created_at`` is optional and must be passed in explicitly; the
    library never reads the wall clock.
    """

    case_id: str
    narrative: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    exchanges: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    victim: VictimInfo = field(default_factory=VictimInfo)
    unverified_notes: list[str] = field(default_factory=list)
    created_at: Optional[str] = None  # explicit, optional ISO timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "narrative": self.narrative,
            "evidence": [e.to_dict() for e in self.evidence],
            "transactions": [t.to_dict() for t in self.transactions],
            "exchanges": list(self.exchanges),
            "urls": list(self.urls),
            "emails": list(self.emails),
            "victim": self.victim.to_dict(),
            "unverified_notes": list(self.unverified_notes),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CaseFile":
        return cls(
            case_id=d["case_id"],
            narrative=d["narrative"],
            evidence=[EvidenceItem.from_dict(e) for e in d.get("evidence", [])],
            transactions=[Transaction.from_dict(t) for t in d.get("transactions", [])],
            exchanges=list(d.get("exchanges", [])),
            urls=list(d.get("urls", [])),
            emails=list(d.get("emails", [])),
            victim=VictimInfo.from_dict(d.get("victim", {})),
            unverified_notes=list(d.get("unverified_notes", [])),
            created_at=d.get("created_at"),
        )

    def evidence_of(self, kind: str) -> list[EvidenceItem]:
        return [e for e in self.evidence if e.kind == kind]
