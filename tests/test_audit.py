"""Anti-invention audit: the gate must pass clean output and fail tampering."""

from recourse.audit import (
    verify_casefile_provenance,
    verify_no_invention,
    verify_rendered_doc,
)
from recourse.casefile import build_casefile
from recourse.filings import render_all
from recourse.models import EvidenceItem

STORY = (
    "On 2026-02-14 I sent $8,000 worth of ETH from Coinbase to "
    "0xcd34cd34cd34cd34cd34cd34cd34cd34cd34cd34. The transaction hash was "
    "0xab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12."
)


def test_clean_pipeline_has_zero_violations():
    case = build_casefile(STORY)
    assert verify_no_invention(STORY, case, render_all(case)) == []


def test_invented_hash_in_doc_fails_the_gate():
    case = build_casefile(STORY)
    doc = render_all(case)["freeze_letter.md"] + "\nTx hash: 0x" + "f" * 64
    violations = verify_rendered_doc(STORY, case, doc)
    assert any("0x" + "f" * 64 in v for v in violations)


def test_invented_amount_in_doc_fails_the_gate():
    case = build_casefile(STORY)
    doc = render_all(case)["freeze_letter.md"] + "\nAmount: $99,999"
    assert verify_rendered_doc(STORY, case, doc)


def test_invented_date_in_doc_fails_the_gate():
    case = build_casefile(STORY)
    doc = render_all(case)["freeze_letter.md"] + "\nDate: 1999-01-31"
    assert verify_rendered_doc(STORY, case, doc)


def test_invented_address_in_doc_fails_the_gate():
    case = build_casefile(STORY)
    doc = render_all(case)["freeze_letter.md"] + "\nDestination: 0x" + "a1" * 20
    assert verify_rendered_doc(STORY, case, doc)


def test_lookalike_hash_fails_even_when_one_char_off():
    case = build_casefile(STORY)
    lookalike = "0xab12" + "ab12" * 15  # same length, different content
    lookalike = lookalike[:-1] + "3"  # flip last char vs the real hash
    doc = render_all(case)["freeze_letter.md"] + f"\nTx hash: {lookalike}"
    assert verify_rendered_doc(STORY, case, doc)


def test_case_id_is_exempt_from_hex_audit():
    # The case id is a derived sha256, printed in every doc; it must not be
    # flagged as an invented 64-hex token.
    case = build_casefile(STORY)
    doc = render_all(case)["unverified.md"]
    assert case.case_id in doc
    assert verify_rendered_doc(STORY, case, doc) == []


def test_tampered_casefile_evidence_fails_provenance():
    case = build_casefile(STORY)
    case.evidence.append(
        EvidenceItem(kind="amount", value="777 USD", verbatim="$777")
    )
    violations = verify_casefile_provenance(STORY, case)
    assert any("'$777'" in v for v in violations)


def test_tampered_transaction_verbatim_fails_provenance():
    case = build_casefile(STORY)
    case.transactions[0].date_verbatim = "2031-12-31"
    assert verify_casefile_provenance(STORY, case)


def test_rendered_total_is_allowed():
    story = "On 2026-03-01 I wired $1,000.\n\nOn 2026-03-02 I wired $2,500."
    case = build_casefile(story)
    # ic3 draft renders the derived total 3500 — allowed, not an invention
    doc = render_all(case)["ic3_draft.md"]
    assert "3500" in doc
    assert verify_rendered_doc(story, case, doc) == []
