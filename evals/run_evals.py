#!/usr/bin/env python3
"""Recourse eval gate — the anti-invention enforcement layer.

For every golden scenario this gate checks:

  1. DETERMINISM   — building the case twice yields the same case_id.
  2. EXTRACTION    — extracted evidence matches the hand-verified expected
                     values EXACTLY, kind by kind (a kind absent from the
                     expected file must be empty). This is precision AND
                     recall: extras fail, misses fail.
  3. TRANSACTIONS  — reconstructed transactions match expected field-for-field.
  4. PROVENANCE    — every hash/address/amount/date appearing in every
                     rendered filing exists verbatim in the source story
                     (recourse.audit). The model never had a chance to invent
                     anything, but this proves the templates didn't either.
  5. NEGATIVE      — lookalike/truncated strings that must NOT be extracted
                     are absent from the case file and from the drafts that
                     do not quote the narrative.
  6. SELF-TEST     — the gate proves it can actually fail: filings tampered
                     with an invented hash, amount, and date MUST be flagged.

Exit code is non-zero on any failure. Requires only the base (offline)
install — strands is never imported here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from recourse.audit import verify_no_invention, verify_rendered_doc
from recourse.casefile import build_casefile
from recourse.filings import render_all

EVALS_DIR = Path(__file__).resolve().parent
GOLDEN = EVALS_DIR / "golden"
EXPECTED = EVALS_DIR / "expected"

# Drafts that never quote the victim's narrative — negative-control strings
# must not appear in these at all.
NON_QUOTING_DOCS = ("freeze_letter.md", "action_plan.md", "unverified.md")

TXN_FIELDS = (
    "amount",
    "currency",
    "asset",
    "date",
    "tx_hash",
    "destination_address",
    "method",
)


def run_scenario(expected_path: Path) -> list[str]:
    """Return a list of failure strings (empty = pass)."""
    failures: list[str] = []
    spec = json.loads(expected_path.read_text(encoding="utf-8"))
    story = (GOLDEN / spec["story"]).read_text(encoding="utf-8")

    case = build_casefile(story)

    # 1. Determinism: same input -> same case id.
    if build_casefile(story).case_id != case.case_id:
        failures.append("determinism: case_id differs between two identical builds")

    # 2. Extraction exact-match, kind by kind.
    actual_by_kind: dict[str, list[str]] = {}
    for e in case.evidence:
        actual_by_kind.setdefault(e.kind, []).append(e.value)
    expected_by_kind = {k: sorted(v) for k, v in spec["evidence"].items()}
    for kind in sorted(set(actual_by_kind) | set(expected_by_kind)):
        got = sorted(actual_by_kind.get(kind, []))
        want = expected_by_kind.get(kind, [])
        if got != want:
            missing = [v for v in want if v not in got]
            extra = [v for v in got if v not in want]
            if missing:
                failures.append(f"extraction[{kind}]: missing {missing}")
            if extra:
                failures.append(f"extraction[{kind}]: invented/extra {extra}")

    # 3. Transactions field-for-field, in timeline order.
    want_txns = spec.get("transactions", [])
    got_txns = [
        {
            f: (str(t.amount) if f == "amount" and t.amount is not None else getattr(t, f))
            for f in TXN_FIELDS
        }
        for t in case.transactions
    ]
    if len(got_txns) != len(want_txns):
        failures.append(
            f"transactions: expected {len(want_txns)}, got {len(got_txns)}"
        )
    else:
        for i, (got, want) in enumerate(zip(got_txns, want_txns), 1):
            for f in TXN_FIELDS:
                if got[f] != want[f]:
                    failures.append(
                        f"transaction {i}.{f}: expected {want[f]!r}, got {got[f]!r}"
                    )

    # 4. Anti-invention provenance audit over every rendered filing.
    docs = render_all(case)
    for violation in verify_no_invention(story, case, docs):
        failures.append(f"provenance: {violation}")

    # 5. Negative controls.
    values_everywhere = {e.value for e in case.evidence} | {e.verbatim for e in case.evidence}
    for t in case.transactions:
        for f in TXN_FIELDS:
            v = getattr(t, f)
            if v is not None:
                values_everywhere.add(str(v))
    for forbidden in spec.get("forbidden", []):
        if forbidden in values_everywhere:
            failures.append(f"negative-control: {forbidden!r} was extracted")
        for doc_name in NON_QUOTING_DOCS:
            if forbidden in docs[doc_name]:
                failures.append(
                    f"negative-control: {forbidden!r} leaked into {doc_name}"
                )

    # 6. Unverified-notes expectations.
    joined_notes = "\n".join(case.unverified_notes)
    for fragment in spec.get("notes_contain", []):
        if fragment not in joined_notes:
            failures.append(f"notes: expected fragment {fragment!r} not found")

    return failures


def run_gate_self_test() -> list[str]:
    """Prove the gate can fail: tampered filings MUST produce violations."""
    failures: list[str] = []
    story = (GOLDEN / "eth_investment_scam.txt").read_text(encoding="utf-8")
    case = build_casefile(story)
    clean_doc = render_all(case)["freeze_letter.md"]

    tamperings = {
        "invented hash": clean_doc
        + "\nTx hash: 0x" + "f" * 64,
        "invented amount": clean_doc + "\nAmount: $99,999",
        "invented date": clean_doc + "\nDate: 1999-01-31",
        "invented address": clean_doc
        + "\nDestination: 0x" + "a1" * 20,
    }
    for label, tampered in tamperings.items():
        if not verify_rendered_doc(story, case, tampered, doc_name="tampered"):
            failures.append(f"self-test: {label} was NOT flagged by the audit")

    if verify_rendered_doc(story, case, clean_doc, doc_name="clean"):
        failures.append("self-test: clean document was wrongly flagged")
    return failures


def main() -> int:
    scenarios = sorted(EXPECTED.glob("*.json"))
    if not scenarios:
        print("No expected/*.json scenarios found — refusing to pass an empty gate.")
        return 1

    print("Recourse eval gate — anti-invention scoreboard")
    print("=" * 62)
    total_failures = 0
    for path in scenarios:
        failures = run_scenario(path)
        status = "PASS" if not failures else "FAIL"
        print(f"  [{status}] {path.stem}")
        for f in failures:
            print(f"         - {f}")
        total_failures += len(failures)

    self_test_failures = run_gate_self_test()
    status = "PASS" if not self_test_failures else "FAIL"
    print(f"  [{status}] gate-self-test (tampered filings must be flagged)")
    for f in self_test_failures:
        print(f"         - {f}")
    total_failures += len(self_test_failures)

    print("=" * 62)
    n = len(scenarios) + 1
    if total_failures:
        print(f"RESULT: FAIL — {total_failures} failure(s) across {n} checks")
        return 1
    print(f"RESULT: PASS — {n}/{n} checks green, zero invented facts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
