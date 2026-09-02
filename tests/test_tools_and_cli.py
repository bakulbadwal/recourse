"""Tool wrappers (without strands installed) and the offline CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from recourse import tools
from recourse.cli import main as cli_main

STORY = (
    "On 2026-02-14 I sent $8,000 worth of ETH from Coinbase to "
    "0xcd34cd34cd34cd34cd34cd34cd34cd34cd34cd34. The transaction hash was "
    "0xab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12."
)


def _strands_installed() -> bool:
    try:
        import strands  # noqa: F401
        return True
    except ImportError:
        return False


# --- plain tool functions (no strands needed) -----------------------------

def test_build_case_file_returns_json():
    data = json.loads(tools.build_case_file(STORY))
    assert len(data["case_id"]) == 64
    assert data["transactions"][0]["amount"] == "8000"


def test_draft_tools_return_drafts():
    tools.reset_case_cache()
    case_id = json.loads(tools.build_case_file(STORY))["case_id"]
    for fn in (
        tools.draft_ic3_complaint,
        tools.draft_freeze_letter,
        tools.draft_action_plan,
        tools.list_unverified,
    ):
        out = fn(case_id)
        assert "DRAFT" in out


def test_get_tools_without_strands_raises_helpful_error():
    if _strands_installed():  # pragma: no cover - offline CI never hits this
        pytest.skip("strands installed; offline error path not testable")
    with pytest.raises(ImportError) as exc:
        tools.get_tools()
    assert "recourse[agent]" in str(exc.value)


def test_agent_module_imports_without_strands():
    import recourse.agent  # noqa: F401  # must not raise at import time


# --- CLI ------------------------------------------------------------------

EXPECTED_FILES = {
    "casefile.json",
    "ic3_draft.md",
    "freeze_letter.md",
    "action_plan.md",
    "unverified.md",
}


def test_cli_intake_writes_five_files(tmp_path):
    story = tmp_path / "story.txt"
    story.write_text(STORY, encoding="utf-8")
    out = tmp_path / "case"
    rc = cli_main(["intake", str(story), "--out", str(out)])
    assert rc == 0
    assert {p.name for p in out.iterdir()} == EXPECTED_FILES
    data = json.loads((out / "casefile.json").read_text())
    assert data["created_at"] is None  # no wall-clock read


def test_cli_intake_explicit_created_at(tmp_path):
    story = tmp_path / "story.txt"
    story.write_text(STORY, encoding="utf-8")
    out = tmp_path / "case"
    cli_main(["intake", str(story), "--out", str(out),
              "--created-at", "2026-08-25T00:00:00Z"])
    data = json.loads((out / "casefile.json").read_text())
    assert data["created_at"] == "2026-08-25T00:00:00Z"


def test_cli_intake_missing_story_errors(tmp_path):
    rc = cli_main(["intake", str(tmp_path / "nope.txt"), "--out", str(tmp_path)])
    assert rc == 2


def test_cli_demo_runs_bundled_example(tmp_path):
    out = tmp_path / "demo"
    rc = cli_main(["demo", "--out", str(out)])
    assert rc == 0
    assert {p.name for p in out.iterdir()} == EXPECTED_FILES


def test_module_entrypoint(tmp_path):
    story = tmp_path / "story.txt"
    story.write_text(STORY, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "recourse", "intake", str(story),
         "--out", str(tmp_path / "case")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "DRAFTS" in proc.stdout


def test_eval_gate_passes_from_pytest():
    """The eval gate is part of the green bar: run it as a subprocess."""
    gate = Path(__file__).resolve().parents[1] / "evals" / "run_evals.py"
    proc = subprocess.run(
        [sys.executable, str(gate)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RESULT: PASS" in proc.stdout


# --- drafting tools accept only a case_id ---------------------------------

DRAFTERS = (
    tools.draft_ic3_complaint,
    tools.draft_freeze_letter,
    tools.draft_action_plan,
    tools.list_unverified,
)


def _build():
    tools.reset_case_cache()
    return json.loads(tools.build_case_file(STORY))


def test_build_returns_a_compact_summary_not_the_story():
    data = _build()
    assert len(data["case_id"]) == 64
    assert "narrative" not in data
    assert data["transactions"][0]["amount"] == "8000"
    assert data["transactions"][0]["asset"] == "ETH"
    assert "name" in data["complainant_missing_fields"]


def test_known_case_id_renders_every_draft():
    case_id = _build()["case_id"]
    for fn in DRAFTERS:
        assert "DRAFT" in fn(case_id)


def test_unknown_case_id_is_refused_by_every_drafter():
    tools.reset_case_cache()
    for fn in DRAFTERS:
        out = fn("f" * 64)
        assert out.startswith("ERROR — unknown case_id")
        assert "DRAFT" not in out


# --- add_detail: later facts enter through the story ----------------------

def test_add_detail_appends_verbatim_and_rebuilds():
    case_id = _build()["case_id"]
    out = json.loads(tools.add_detail(case_id, "The wire reference was 20260328MMQFMPUS33 on 2026-03-28."))
    assert out["previous_case_id"] == case_id
    assert out["case_id"] != case_id
    assert {"kind": "date", "value": "2026-03-28"} in out["new_evidence"]
    # The detail is now literally part of the story the drafts are built from.
    assert "20260328MMQFMPUS33" in tools.draft_ic3_complaint(out["case_id"])


def test_add_detail_rejects_unknown_case_and_empty_detail():
    case_id = _build()["case_id"]
    assert tools.add_detail("0" * 64, "x").startswith("ERROR — unknown case_id")
    assert tools.add_detail(case_id, "   ").startswith("ERROR — detail is empty")


# --- set_complainant: the only door for identity ---------------------------

def test_set_complainant_fills_ic3_step_2_and_changes_case_id():
    case_id = _build()["case_id"]
    out = json.loads(tools.set_complainant(case_id, name="Dana Whitfield", email="dana@mailbox.example"))
    assert out["case_id"] != case_id
    assert out["complainant_recorded"] == ["email", "name"]
    assert "name" not in out["complainant_missing_fields"]
    doc = tools.draft_ic3_complaint(out["case_id"])
    assert "- Name: Dana Whitfield" in doc
    assert "- Email: dana@mailbox.example" in doc


def test_set_complainant_with_nothing_supplied_is_an_error():
    case_id = _build()["case_id"]
    assert tools.set_complainant(case_id).startswith("ERROR — no complainant fields")


# --- propose_description: model-authored, audit-gated ---------------------

def test_clean_description_is_accepted_and_rendered_into_step_5():
    case_id = _build()["case_id"]
    text = ("On 2026-02-14 I sent $8,000 worth of ETH from Coinbase to "
            "0xcd34cd34cd34cd34cd34cd34cd34cd34cd34cd34.")
    out = tools.propose_description(case_id, text)
    assert out.startswith("ACCEPTED")
    doc = tools.draft_ic3_complaint(case_id)
    assert "composed by the assistant" in doc
    assert text in doc


def test_description_with_an_invented_amount_is_rejected():
    case_id = _build()["case_id"]
    out = tools.propose_description(case_id, "On 2026-02-14 I sent $9,000 worth of ETH.")
    assert out.startswith("REJECTED")
    assert "9,000" in out or "9000" in out
    assert "composed by the assistant" not in tools.draft_ic3_complaint(case_id)


def test_description_with_an_invented_hash_or_date_is_rejected():
    case_id = _build()["case_id"]
    assert tools.propose_description(case_id, "The hash was 0x" + "f" * 64 + ".").startswith("REJECTED")
    assert tools.propose_description(case_id, "It happened on 1999-01-31.").startswith("REJECTED")


def test_description_over_the_ic3_limit_is_rejected():
    case_id = _build()["case_id"]
    assert tools.propose_description(case_id, "x" * 3501).startswith("REJECTED — 3501 characters")


def test_add_detail_clears_an_accepted_description():
    case_id = _build()["case_id"]
    tools.propose_description(case_id, "On 2026-02-14 I sent $8,000 worth of ETH.")
    out = json.loads(tools.add_detail(case_id, "I also wired $500 on 2026-02-20."))
    assert out["description_status"].startswith("cleared")
    assert "composed by the assistant" not in tools.draft_ic3_complaint(out["case_id"])


# --- recovery-scam screener -------------------------------------------------

def test_recovery_scam_pitch_is_flagged_with_verbatim_evidence():
    msg = ("Hi, I saw your post about the Coinbase loss. Our blockchain experts can "
           "guarantee recovery of your funds. There is a small activation fee of "
           "$500 payable in USDT before we start. Message me on Telegram, act now.")
    out = json.loads(tools.screen_recovery_offer(msg))
    assert out["verdict"] == "LIKELY RECOVERY SCAM"
    ids = {s["signal"] for s in out["signals"]}
    assert {"upfront_fee", "guarantee", "recovery_expert", "messaging_app", "urgency"} <= ids
    for s in out["signals"]:
        assert s["matched"] in msg  # every hit is quoted from the message
    assert "cryptorecoveryfraudvictims" in " ".join(out["what_to_do"])


def test_benign_message_has_no_markers():
    out = json.loads(tools.screen_recovery_offer("Your IC3 complaint was received. Thank you."))
    assert out["verdict"] == "NO KNOWN MARKERS FOUND"
    assert out["signals"] == []


def test_single_soft_signal_is_caution_not_scam():
    out = json.loads(tools.screen_recovery_offer("Please reply within 48 hours."))
    assert out["verdict"] == "CAUTION"


def test_demo_story_and_its_golden_eval_copy_are_identical():
    """The story in the video is the story the eval gate verifies."""
    root = Path(__file__).resolve().parents[1]
    packaged = root / "src" / "recourse" / "data" / "example_story.txt"
    golden = root / "evals" / "golden" / "demo_romance_investment.txt"
    example = root / "examples" / "example_story.txt"
    assert packaged.read_text() == golden.read_text() == example.read_text()
