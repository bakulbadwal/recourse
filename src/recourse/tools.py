"""Strands @tool wrappers over the deterministic core.

The base install of recourse has no strands dependency; this module imports
strands lazily so ``import recourse.tools`` always works. Calling
``get_tools()`` without the [agent] extra installed raises a clear error.

Boundary rule: the model calls these tools to obtain facts. The tools run the
same deterministic code paths as the offline CLI — the model never computes a
hash, amount, or date itself.

How facts enter a case — and how they cannot:
- ``build_case_file`` takes the victim's story, verbatim.
- ``add_detail`` appends the victim's later words to that story and rebuilds.
  A fact the agent learns mid-conversation therefore lands in the case the
  same way every other fact did: as a literal substring of the story, with
  the same provenance guarantee.
- ``set_complainant`` is the ONLY door for identity fields (IC3 Step 2). They
  are never inferred from the story — the email in a scam story is the
  scammer's.
- The drafting tools accept a ``case_id`` and nothing else. There is no story
  parameter for the model to paraphrase, so two drafts cannot disagree about
  which story they were built from.
- ``propose_description`` is the one place the model authors filing content
  (the IC3 Step 5 narrative). The audit runs over it before it is accepted:
  any hash, address, amount, or date that does not trace to the victim's own
  words is rejected, with the violations listed.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .audit import verify_rendered_doc
from .casefile import build_casefile
from .filings import (
    FBI_RECOVERY_FRAUD_URL,
    IC3_DESCRIPTION_CHAR_LIMIT,
    render_action_plan,
    render_freeze_letter,
    render_ic3_draft,
    render_unverified_report,
)
from .models import CaseFile, VictimInfo

_STRANDS_HELP = (
    "The Strands Agents SDK is not installed. Install the agent extra:\n"
    "    pip install 'recourse[agent]'\n"
    "The deterministic pipeline (recourse.cli) works without it."
)

# Case files built during this process, keyed by case_id. Bounded: this is a
# single victim's session, not a datastore.
_CASE_CACHE: "dict[str, CaseFile]" = {}
_CASE_CACHE_MAX = 32

_ADDENDUM_HEADER = "Additional detail from the victim:"


def reset_case_cache() -> None:
    """Clear the per-process case cache (used by tests and between victims)."""
    _CASE_CACHE.clear()


def _remember(case: CaseFile) -> CaseFile:
    if case.case_id not in _CASE_CACHE and len(_CASE_CACHE) >= _CASE_CACHE_MAX:
        _CASE_CACHE.pop(next(iter(_CASE_CACHE)))
    _CASE_CACHE[case.case_id] = case
    return case


def _lookup(case_id: str) -> tuple[CaseFile | None, str]:
    case = _CASE_CACHE.get(case_id.strip())
    if case is None:
        return None, (
            f"ERROR — unknown case_id {case_id!r}. Call build_case_file with the "
            "victim's story first and use the case_id it returns. These tools "
            "accept only a case_id, never a story, so a draft can never be built "
            "from a different story than the case file the victim was shown."
        )
    return case, ""


def _summary(case: CaseFile) -> dict[str, Any]:
    """Compact view for the model: facts and gaps, without echoing the story."""
    return {
        "case_id": case.case_id,
        "evidence": [
            {"kind": e.kind, "value": e.value, "verbatim": e.verbatim}
            for e in case.evidence
        ],
        "transactions": [
            {
                "amount": str(t.amount) if t.amount is not None else None,
                "currency": t.currency,
                "asset": t.asset,
                "date": t.date,
                "tx_hash": t.tx_hash,
                "destination_address": t.destination_address,
                "method": t.method,
            }
            for t in case.transactions
        ],
        "exchanges": case.exchanges,
        "urls": case.urls,
        "emails_in_story": case.emails,
        "business_name_candidates": case.businesses,
        "stated_total_usd": case.stated_total,
        "complainant_missing_fields": case.victim.missing_fields(),
        "description_accepted": bool(case.description),
        "unverified_notes": case.unverified_notes,
    }


# ---------------------------------------------------------------------------
# Plain functions (usable and tested without strands installed).
# ---------------------------------------------------------------------------

def build_case_file(story: str) -> str:
    """Extract an evidence record from a victim's story and return a compact
    case summary as JSON, including the case_id every later tool needs.

    Call this FIRST. Pass the story EXACTLY as the victim wrote it — never
    paraphrased, summarized, or shortened — because every fact is traced back
    to a literal substring of it.

    Args:
        story: The victim's account in plain language, including any
            fragments they have (tx hashes, addresses, amounts, dates,
            URLs, emails), verbatim.
    """
    case = _remember(build_casefile(story))
    return json.dumps(_summary(case), indent=2)


def add_detail(case_id: str, detail: str) -> str:
    """Add something the victim said AFTER the story — a hash they looked up,
    a wire confirmation number, a date, an exchange name — and rebuild the
    case. Returns the NEW case_id (use it from now on) plus what changed.

    Pass the victim's words verbatim. The detail is appended to their story
    so it carries the same provenance as every other fact; do not restate it
    in your own words. Identity fields (name, email, phone, address) do NOT
    go here — use set_complainant.

    Args:
        case_id: The current case_id.
        detail: What the victim just told you, in their exact words.
    """
    old, error = _lookup(case_id)
    if old is None:
        return error
    if not detail.strip():
        return "ERROR — detail is empty; nothing was added."

    story = old.narrative.rstrip("\n") + f"\n\n{_ADDENDUM_HEADER}\n{detail.strip()}\n"
    new = _remember(build_casefile(story, victim=old.victim, created_at=old.created_at))

    before = {(e.kind, e.value) for e in old.evidence}
    found = [
        {"kind": e.kind, "value": e.value}
        for e in new.evidence
        if (e.kind, e.value) not in before
    ]
    return json.dumps(
        {
            "previous_case_id": old.case_id,
            "case_id": new.case_id,
            "new_evidence": found,
            "gaps_closed": [n for n in old.unverified_notes if n not in new.unverified_notes],
            "still_open": new.unverified_notes,
            # An accepted description was audited against the old story; the
            # facts have moved, so it must be proposed again.
            "description_status": (
                "cleared — propose_description again once the facts are settled"
                if old.description
                else "none yet"
            ),
        },
        indent=2,
    )


def set_complainant(
    case_id: str,
    name: str = "",
    email: str = "",
    phone: str = "",
    address: str = "",
    city: str = "",
    country: str = "",
    zip_code: str = "",
    age_range: str = "",
) -> str:
    """Record the victim's own identity details for IC3 Step 2 ("Information
    About You"). Only use values the victim gave you explicitly for this
    purpose — never anything found in the story. Returns the NEW case_id.

    Args:
        case_id: The current case_id.
        name: The victim's full name.
        email: The victim's email address.
        phone: The victim's phone number (digits only).
        address: Street address.
        city: City.
        country: Country.
        zip_code: Postal code.
        age_range: Age range as the IC3 form lists it (e.g. "40-49", "60+").
    """
    old, error = _lookup(case_id)
    if old is None:
        return error
    supplied = {
        k: v.strip()
        for k, v in dict(
            name=name, email=email, phone=phone, address=address, city=city,
            country=country, zip_code=zip_code, age_range=age_range,
        ).items()
        if v and v.strip()
    }
    if not supplied:
        return "ERROR — no complainant fields were supplied; nothing was changed."
    merged = {**old.victim.to_dict(), **supplied}
    new = build_casefile(
        old.narrative, victim=VictimInfo.from_dict(merged), created_at=old.created_at
    )
    # Identity fields are not audited facts, so an accepted description is
    # still valid — carry it over.
    new.description = old.description
    _remember(new)
    return json.dumps(
        {
            "previous_case_id": old.case_id,
            "case_id": new.case_id,
            "complainant_recorded": sorted(supplied),
            "complainant_missing_fields": new.victim.missing_fields(),
        },
        indent=2,
    )


def propose_description(case_id: str, description: str) -> str:
    """Submit a written 'Description of Incident' for IC3 Step 5. It is
    accepted ONLY if the audit can trace every hash, address, amount, and
    date in it back to the victim's own words; otherwise it is rejected with
    the exact violations, and you must fix those and resubmit.

    Write it chronologically and plainly, under 3,500 characters, using only
    figures that appear in tool output. Do not round, convert, or infer any
    number. Until a description is accepted the IC3 draft carries the
    victim's raw story instead.

    Args:
        case_id: The current case_id.
        description: The proposed Step 5 text.
    """
    case, error = _lookup(case_id)
    if case is None:
        return error
    text = description.strip()
    if not text:
        return "REJECTED — the description is empty."
    if len(text) > IC3_DESCRIPTION_CHAR_LIMIT:
        return (
            f"REJECTED — {len(text)} characters; the IC3 form allows "
            f"{IC3_DESCRIPTION_CHAR_LIMIT}. Shorten it and resubmit."
        )
    violations = verify_rendered_doc(case.narrative, case, text, doc_name="description")
    if violations:
        return (
            "REJECTED — the audit found figures that do not trace to the "
            "victim's own words. Remove or correct each of these and resubmit:\n- "
            + "\n- ".join(violations)
        )
    case.description = text
    _remember(case)
    checked = sum(
        1 for _ in re.finditer(r"0x[0-9a-fA-F]{40,64}|\b\d[\d,]*(?:\.\d+)?\b", text)
    )
    return (
        "ACCEPTED — every hash, address, amount, and date in the description "
        f"traces to the victim's story ({checked} figure(s) checked, "
        f"{len(text)} characters). It will appear in IC3 Step 5, marked as "
        "composed by the assistant and audited, for the victim's review."
    )


def draft_ic3_complaint(case_id: str) -> str:
    """Render the IC3 complaint DRAFT (markdown) for a case, mapped to the real
    form's seven steps.

    Args:
        case_id: The current case_id from build_case_file / add_detail /
            set_complainant.
    """
    case, error = _lookup(case_id)
    return error if case is None else render_ic3_draft(case)


def draft_freeze_letter(case_id: str) -> str:
    """Render the exchange fraud-report / records-preservation / freeze-request
    DRAFT letter (markdown) for a case.

    Args:
        case_id: The current case_id.
    """
    case, error = _lookup(case_id)
    return error if case is None else render_freeze_letter(case)


def draft_action_plan(case_id: str) -> str:
    """Render the ordered action plan (markdown) with urgency rationale and
    official reporting links for a case.

    Args:
        case_id: The current case_id.
    """
    case, error = _lookup(case_id)
    return error if case is None else render_action_plan(case)


def list_unverified(case_id: str) -> str:
    """Render the explicit 'what we could NOT verify' report (markdown) for a
    case — also the list of what to ask the victim for next.

    Args:
        case_id: The current case_id.
    """
    case, error = _lookup(case_id)
    return error if case is None else render_unverified_report(case)


# ---------------------------------------------------------------------------
# Recovery-scam screener. Victims are re-targeted within days by "we can get
# your money back" pitches; the FBI has a dedicated form for it. Pure rules,
# and every hit quotes the message verbatim — the model narrates the verdict,
# it does not decide it.
# ---------------------------------------------------------------------------

RECOVERY_SCAM_SIGNALS: tuple[tuple[str, str, str], ...] = (
    (
        "upfront_fee",
        r"\b(?:upfront|up-front|advance|initial|activation|processing|retainer)\s+"
        r"(?:fee|payment|deposit|charge)\b|"
        r"\b(?:fee|deposit|payment|retainer)\b[^.\n]{0,60}\b(?:before|first|in advance|"
        r"to (?:start|begin|release|unlock|proceed))\b|"
        r"\bpay\b[^.\n]{0,40}\b(?:before|first|upfront|up-front|in advance)\b",
        "Asks you to pay before anything is recovered — legitimate recovery never works this way",
    ),
    (
        "guarantee",
        r"\b(?:guarantee[ds]?|100\s?%|assured|certain(?:ly)?|promise[ds]?)\b[^.\n]{0,60}"
        r"\b(?:recover|refund|return|retriev|get (?:it|your (?:money|funds|crypto)) back)",
        "Guarantees recovery — no one can reverse a confirmed blockchain transaction",
    ),
    (
        "recovery_expert",
        r"\b(?:ethical\s+)?hackers?\b|\bblockchain\s+(?:expert|specialist|analyst|forensic)s?\b|"
        r"\b(?:crypto|funds?|asset|wallet)\s+recovery\s+(?:expert|agent|specialist|firm|"
        r"company|service|team)s?\b|\brecovery\s+(?:expert|agent|specialist|firm|company|"
        r"service|team)s?\b",
        "Presents as a 'recovery expert' or hacker",
    ),
    (
        "messaging_app",
        r"\b(?:telegram|whatsapp|signal|instagram|wechat|discord|dm\s+me)\b",
        "Moves the conversation to a messaging app where there is no record",
    ),
    (
        "credentials",
        r"\b(?:seed|recovery|secret)\s+phrase\b|\bprivate\s+keys?\b|\bpassphrase\b|"
        r"\bpasswords?\b|\b2fa\b|\bone[- ]time\s+code\b|\bremote\s+access\b|"
        r"\banydesk\b|\bteamviewer\b|\bscreen[- ]?shar",
        "Asks for keys, passwords, codes, or remote access — this is how the second theft happens",
    ),
    (
        "authority_claim",
        r"\b(?:fbi|ic3|interpol|europol|secret\s+service|homeland\s+security|"
        r"department\s+of\s+justice|doj|treasury|irs|sec|cftc|police|law\s+enforcement)\b"
        r"[^.\n]{0,80}\b(?:fee|payment|pay|deposit|tax|charge|cost)\b",
        "Claims to be law enforcement or a regulator AND wants money — agencies never charge victims",
    ),
    (
        "urgency",
        r"\bact\s+now\b|\bimmediately\b|\bwithin\s+(?:24|48|72)\s+hours\b|"
        r"\blimited\s+time\b|\blast\s+chance\b|\bbefore\s+it'?s\s+too\s+late\b|"
        r"\burgent(?:ly)?\b|\bexpires?\s+(?:today|tonight|soon)\b",
        "Pressures you to act fast",
    ),
    (
        "crypto_or_giftcard_fee",
        r"\b(?:pay|send|deposit|transfer)\b[^.\n]{0,50}\b(?:in|via|using|with|by)\s+"
        r"(?:bitcoin|btc|eth|usdt|usdc|crypto(?:currency)?|gift\s*cards?)\b",
        "Wants the fee paid in crypto or gift cards — untraceable and unrecoverable",
    ),
    (
        "release_fee",
        r"\b(?:tax|clearance|verification|processing|release|unlock|withdrawal|"
        r"anti[- ]money[- ]laundering|aml|kyc)\s+(?:fee|payment|charge|deposit)\b",
        "Invents a 'tax' or 'release fee' to unlock funds — the same trick as the original scam",
    ),
    (
        "unsolicited",
        r"\b(?:saw|found|noticed|came\s+across)\s+your\s+(?:post|comment|case|story|"
        r"name|details|complaint)\b|\bwe\s+(?:noticed|identified|detected)\b|"
        r"\breaching\s+out\s+because\b",
        "Contacted you out of the blue about a loss you posted about",
    ),
)
_SIGNAL_RES = [(sid, re.compile(rx, re.IGNORECASE), why) for sid, rx, why in RECOVERY_SCAM_SIGNALS]
_HARD_SIGNALS = frozenset({"upfront_fee", "credentials", "authority_claim", "release_fee"})


def screen_recovery_offer(message: str) -> str:
    """Screen a message from anyone offering to recover the victim's lost funds
    (a 'recovery service', 'hacker', 'blockchain expert', someone claiming to
    be an agency). Returns a JSON verdict with every matched warning sign
    quoted verbatim from the message, and what the victim should do.

    Run this the moment a victim mentions such an offer — recovery scams
    re-target fresh victims within days, and the second loss is often larger.

    Args:
        message: The offer, email, DM, or call summary in the victim's words
            or pasted verbatim.
    """
    text = message.strip()
    if not text:
        return json.dumps({"verdict": "NO MESSAGE", "signals": []})
    hits: list[dict[str, str]] = []
    for sid, rx, why in _SIGNAL_RES:
        m = rx.search(text)
        if m:
            hits.append({"signal": sid, "why": why, "matched": m.group(0)})
    ids = {h["signal"] for h in hits}
    if ids & _HARD_SIGNALS or len(ids) >= 3:
        verdict = "LIKELY RECOVERY SCAM"
    elif ids:
        verdict = "CAUTION"
    else:
        verdict = "NO KNOWN MARKERS FOUND"
    return json.dumps(
        {
            "verdict": verdict,
            "signal_count": len(ids),
            "signals": hits,
            "always_true": [
                "No one can reverse a confirmed cryptocurrency transaction.",
                "Law enforcement and regulators never charge victims a fee and never "
                "contact victims through messaging apps.",
                "Never share a seed phrase, private key, password, or one-time code, "
                "and never grant remote access to your device.",
                "Legitimate help does not need money first.",
            ],
            "what_to_do": [
                "Do not pay anything and do not share any credential.",
                f"Report the offer to the FBI's recovery-scam form: {FBI_RECOVERY_FRAUD_URL}",
                "Keep the message; add it to your IC3 complaint under Other Information.",
            ],
            "note": (
                "Rule-based screen of the message text only; a message with no known "
                "markers can still be a scam. Verdict: " + verdict
            ),
        },
        indent=2,
    )


_PLAIN_FUNCTIONS = (
    build_case_file,
    add_detail,
    set_complainant,
    propose_description,
    draft_ic3_complaint,
    draft_freeze_letter,
    draft_action_plan,
    list_unverified,
    screen_recovery_offer,
)


def _tool_decorator() -> Callable:
    try:
        from strands import tool
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ImportError(_STRANDS_HELP) from exc
    return tool


def get_tools() -> list[Any]:
    """Return the deterministic functions wrapped as Strands tools.

    Raises ImportError with install guidance when strands is absent.
    """
    tool = _tool_decorator()
    return [tool(fn) for fn in _PLAIN_FUNCTIONS]
