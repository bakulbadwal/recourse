"""Strands @tool wrappers over the deterministic core.

The base install of recourse has no strands dependency; this module imports
strands lazily so ``import recourse.tools`` always works. Calling
``get_tools()`` without the [agent] extra installed raises a clear error.

Boundary rule: the model calls these tools to obtain facts. The tools run the
same deterministic code paths as the offline CLI — the model never computes a
hash, amount, or date itself.

Divergence rule: every tool rebuilds the CaseFile from the story it is handed,
so a model that paraphrases, truncates, or summarizes the story between calls
would silently produce drafts that disagree with each other. Because the
case_id is a sha256 of the case contents, that divergence is detectable — so
the drafting tools cross-check it and refuse rather than emit a draft built
from a different story than the case file the victim was shown.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .casefile import build_casefile
from .filings import (
    render_action_plan,
    render_freeze_letter,
    render_ic3_draft,
    render_unverified_report,
)
from .models import CaseFile

_STRANDS_HELP = (
    "The Strands Agents SDK is not installed. Install the agent extra:\n"
    "    pip install 'recourse[agent]'\n"
    "The deterministic pipeline (recourse.cli) works without it."
)

# Case files built during this process, keyed by case_id. Bounded: this is a
# single victim's session, not a datastore.
_CASE_CACHE: "dict[str, CaseFile]" = {}
_CASE_CACHE_MAX = 32

_REBUILD_INSTRUCTION = (
    "Call build_case_file again with the victim's story copied EXACTLY as they "
    "wrote it (do not paraphrase, summarize, or truncate it), then pass the "
    "case_id from that result to this tool."
)


def reset_case_cache() -> None:
    """Clear the per-process case cache (used by tests and between victims)."""
    _CASE_CACHE.clear()


def _remember(case: CaseFile) -> None:
    if len(_CASE_CACHE) >= _CASE_CACHE_MAX:
        _CASE_CACHE.pop(next(iter(_CASE_CACHE)))
    _CASE_CACHE[case.case_id] = case


def _resolve(story: str, case_id: str) -> tuple[CaseFile | None, str]:
    """Rebuild the case from ``story`` and cross-check it against ``case_id``.

    Returns ``(case, "")`` when the story is consistent, or ``(None, error)``
    when it diverges from the case file this session already built.
    """
    case = build_casefile(story)

    if case_id and case_id != case.case_id:
        return None, (
            "ERROR — story/case mismatch. The story passed to this tool builds "
            f"case {case.case_id}, but you asked for case {case_id}. The drafts "
            "would not agree with each other, so nothing was rendered. "
            + _REBUILD_INSTRUCTION
        )

    if not case_id and _CASE_CACHE and case.case_id not in _CASE_CACHE:
        return None, (
            "ERROR — this story does not match any case file built in this "
            f"session (it builds case {case.case_id}). The story was most "
            "likely altered between tool calls. " + _REBUILD_INSTRUCTION
        )

    _remember(case)
    return case, ""


# ---------------------------------------------------------------------------
# Plain functions (usable and tested without strands installed).
# ---------------------------------------------------------------------------

def build_case_file(story: str) -> str:
    """Extract an evidence record from a victim's story and return the
    canonical CaseFile as JSON. Call this FIRST, before any drafting tool, and
    carry the returned case_id into every later call.

    Args:
        story: The victim's account in plain language, including any
            fragments they have (tx hashes, addresses, amounts, dates,
            URLs, emails). Pass it exactly as the victim wrote it.
    """
    case = build_casefile(story)
    _remember(case)
    return json.dumps(case.to_dict(), indent=2)


def draft_ic3_complaint(story: str, case_id: str = "") -> str:
    """Produce the IC3 complaint DRAFT (markdown) for a victim's story,
    mapped to the real IC3 form's seven steps.

    Args:
        story: The victim's account, exactly as passed to build_case_file.
        case_id: The case_id returned by build_case_file. Always pass it —
            it proves this draft was built from the same story as the others.
    """
    case, error = _resolve(story, case_id)
    return error if case is None else render_ic3_draft(case)


def draft_freeze_letter(story: str, case_id: str = "") -> str:
    """Produce the exchange freeze-request DRAFT letter (markdown) for a
    victim's story.

    Args:
        story: The victim's account, exactly as passed to build_case_file.
        case_id: The case_id returned by build_case_file. Always pass it —
            it proves this draft was built from the same story as the others.
    """
    case, error = _resolve(story, case_id)
    return error if case is None else render_freeze_letter(case)


def draft_action_plan(story: str, case_id: str = "") -> str:
    """Produce the ordered action plan (markdown) with urgency rationale and
    official reporting links for a victim's story.

    Args:
        story: The victim's account, exactly as passed to build_case_file.
        case_id: The case_id returned by build_case_file. Always pass it —
            it proves this plan was built from the same story as the drafts.
    """
    case, error = _resolve(story, case_id)
    return error if case is None else render_action_plan(case)


def list_unverified(story: str, case_id: str = "") -> str:
    """Produce the explicit 'what we could NOT verify' report (markdown) for
    a victim's story.

    Args:
        story: The victim's account, exactly as passed to build_case_file.
        case_id: The case_id returned by build_case_file. Always pass it —
            it proves this report covers the same case file as the drafts.
    """
    case, error = _resolve(story, case_id)
    return error if case is None else render_unverified_report(case)


_PLAIN_FUNCTIONS = (
    build_case_file,
    draft_ic3_complaint,
    draft_freeze_letter,
    draft_action_plan,
    list_unverified,
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
