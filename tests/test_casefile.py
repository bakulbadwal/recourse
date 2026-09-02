"""CaseFile determinism, dedupe, timeline, and round-trip tests."""

import json

from recourse.casefile import build_casefile
from recourse.models import CaseFile, VictimInfo

STORY = (
    "On 2026-02-14 I sent $8,000 worth of ETH from Coinbase to "
    "0xcd34cd34cd34cd34cd34cd34cd34cd34cd34cd34. The transaction hash was "
    "0xab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12.\n\n"
    "On 2026-01-03 I had wired $2,000 first. The site was "
    "https://fake.example.org and he emailed from scam@mail.example."
)


def test_same_input_same_case_id():
    a = build_casefile(STORY)
    b = build_casefile(STORY)
    assert a.case_id == b.case_id
    assert len(a.case_id) == 64  # sha256 hex


def test_different_input_different_case_id():
    assert build_casefile(STORY).case_id != build_casefile(STORY + " x").case_id


def test_created_at_excluded_from_case_id():
    a = build_casefile(STORY, created_at="2026-08-25T00:00:00Z")
    b = build_casefile(STORY, created_at="2027-01-01T12:34:56Z")
    assert a.case_id == b.case_id
    assert a.created_at == "2026-08-25T00:00:00Z"


def test_no_wall_clock_read():
    case = build_casefile(STORY)
    assert case.created_at is None  # never auto-populated


def test_duplicate_evidence_deduped():
    doubled = STORY + "\n\nAgain: the hash was " + \
        "0xab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12."
    case = build_casefile(doubled)
    hashes = [e for e in case.evidence if e.kind == "evm_tx_hash"]
    assert len(hashes) == 1


def test_timeline_orders_dated_transactions():
    case = build_casefile(STORY)
    dates = [t.date for t in case.transactions if t.date]
    assert dates == sorted(dates)
    assert dates[0] == "2026-01-03"  # narrative order was reversed


def test_undated_transactions_sort_after_dated():
    story = (
        "I sent 1,000 USDT at some point, no idea when.\n\n"
        "On 2026-03-01 I sent 500 USDT."
    )
    case = build_casefile(story)
    assert case.transactions[0].date == "2026-03-01"
    assert case.transactions[-1].date is None


def test_exchanges_urls_emails_collected():
    case = build_casefile(STORY)
    assert case.exchanges == ["coinbase"]
    assert case.urls == ["https://fake.example.org"]
    assert case.emails == ["scam@mail.example"]


def test_unverified_notes_flag_missing_victim_fields():
    case = build_casefile(STORY)
    joined = "\n".join(case.unverified_notes)
    assert "Victim/complainant fields not provided" in joined
    assert "independently verified" in joined


def test_victim_info_included_in_case_id():
    a = build_casefile(STORY)
    b = build_casefile(STORY, victim=VictimInfo(name="Pat Doe"))
    assert a.case_id != b.case_id


def test_casefile_json_round_trip():
    case = build_casefile(STORY, created_at="2026-08-25T00:00:00Z")
    blob = json.dumps(case.to_dict())
    restored = CaseFile.from_dict(json.loads(blob))
    assert restored.case_id == case.case_id
    assert restored.to_dict() == case.to_dict()


def test_business_candidates_collected_on_the_casefile():
    case = build_casefile('he called it "Golden Harbor Trading LLC" and vanished')
    assert case.businesses == ["Golden Harbor Trading LLC"]


def test_business_candidates_survive_the_json_round_trip():
    case = build_casefile('he called it "Golden Harbor Trading LLC" and vanished')
    restored = CaseFile.from_dict(json.loads(json.dumps(case.to_dict())))
    assert restored.businesses == case.businesses


def test_asset_is_part_of_the_case_id():
    a = build_casefile("On 2026-02-14 I sent $8,000 worth of ETH.")
    b = build_casefile("On 2026-02-14 I sent $8,000 to him.")
    assert a.case_id != b.case_id


def test_stated_total_lands_on_the_casefile():
    case = build_casefile("I lost $10,000 total: $4,000 on 2026-04-01 and $6,000 on 2026-04-02.")
    assert case.stated_total == "10000"
    assert case.stated_total_verbatim == "$10,000"


def test_description_is_excluded_from_the_case_id_but_round_trips():
    case = build_casefile(STORY)
    original = case.case_id
    case.description = "A composed description."
    assert case.case_id == original
    restored = CaseFile.from_dict(json.loads(json.dumps(case.to_dict())))
    assert restored.description == "A composed description."
