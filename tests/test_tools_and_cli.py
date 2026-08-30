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
    for fn in (
        tools.draft_ic3_complaint,
        tools.draft_freeze_letter,
        tools.draft_action_plan,
        tools.list_unverified,
    ):
        out = fn(STORY)
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


# --- case_id cross-check (tool-to-tool divergence) ------------------------

ALTERED_STORY = STORY.replace("$8,000", "$9,000")


def test_matching_case_id_renders_the_draft():
    tools.reset_case_cache()
    case_id = json.loads(tools.build_case_file(STORY))["case_id"]
    out = tools.draft_ic3_complaint(STORY, case_id)
    assert "IC3 Complaint — DRAFT" in out


def test_mismatched_case_id_refuses_to_render():
    tools.reset_case_cache()
    case_id = json.loads(tools.build_case_file(STORY))["case_id"]
    out = tools.draft_freeze_letter(ALTERED_STORY, case_id)
    assert out.startswith("ERROR — story/case mismatch")
    assert "DRAFT" not in out


def test_altered_story_refused_even_without_an_explicit_case_id():
    tools.reset_case_cache()
    tools.build_case_file(STORY)
    out = tools.draft_action_plan(ALTERED_STORY)
    assert out.startswith("ERROR — this story does not match")


def test_standalone_use_without_a_prior_build_is_allowed():
    tools.reset_case_cache()
    assert "DRAFT" in tools.list_unverified(STORY)


def test_every_drafting_tool_enforces_the_cross_check():
    tools.reset_case_cache()
    case_id = json.loads(tools.build_case_file(STORY))["case_id"]
    for fn in (
        tools.draft_ic3_complaint,
        tools.draft_freeze_letter,
        tools.draft_action_plan,
        tools.list_unverified,
    ):
        assert fn(ALTERED_STORY, case_id).startswith("ERROR")
        assert "DRAFT" in fn(STORY, case_id)


def test_demo_story_and_its_golden_eval_copy_are_identical():
    """The story in the video is the story the eval gate verifies."""
    root = Path(__file__).resolve().parents[1]
    packaged = root / "src" / "recourse" / "data" / "example_story.txt"
    golden = root / "evals" / "golden" / "demo_romance_investment.txt"
    example = root / "examples" / "example_story.txt"
    assert packaged.read_text() == golden.read_text() == example.read_text()
