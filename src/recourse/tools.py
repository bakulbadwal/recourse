"""Strands @tool wrappers over the deterministic core.

The base install of recourse has no strands dependency; this module imports
strands lazily so ``import recourse.tools`` always works. Calling
``get_tools()`` without the [agent] extra installed raises a clear error.

Boundary rule: the model calls these tools to obtain facts. The tools run the
same deterministic code paths as the offline CLI — the model never computes a
hash, amount, or date itself.
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

_STRANDS_HELP = (
    "The Strands Agents SDK is not installed. Install the agent extra:\n"
    "    pip install 'recourse[agent]'\n"
    "The deterministic pipeline (recourse.cli) works without it."
)


def _tool_decorator() -> Callable:
    try:
        from strands import tool
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ImportError(_STRANDS_HELP) from exc
    return tool


# ---------------------------------------------------------------------------
# Plain functions (usable and tested without strands installed).
# ---------------------------------------------------------------------------

def build_case_file(story: str) -> str:
    """Extract an evidence record from a victim's story and return the
    canonical CaseFile as JSON.

    Args:
        story: The victim's account in plain language, including any
            fragments they have (tx hashes, addresses, amounts, dates,
            URLs, emails).
    """
    case = build_casefile(story)
    return json.dumps(case.to_dict(), indent=2)


def draft_ic3_complaint(story: str) -> str:
    """Produce the IC3 complaint DRAFT (markdown) for a victim's story,
    mapped to the real IC3 form's seven steps.

    Args:
        story: The victim's account in plain language.
    """
    return render_ic3_draft(build_casefile(story))


def draft_freeze_letter(story: str) -> str:
    """Produce the exchange freeze-request DRAFT letter (markdown) for a
    victim's story.

    Args:
        story: The victim's account in plain language.
    """
    return render_freeze_letter(build_casefile(story))


def draft_action_plan(story: str) -> str:
    """Produce the ordered action plan (markdown) with urgency rationale and
    official reporting links for a victim's story.

    Args:
        story: The victim's account in plain language.
    """
    return render_action_plan(build_casefile(story))


def list_unverified(story: str) -> str:
    """Produce the explicit 'what we could NOT verify' report (markdown) for
    a victim's story.

    Args:
        story: The victim's account in plain language.
    """
    return render_unverified_report(build_casefile(story))


_PLAIN_FUNCTIONS = (
    build_case_file,
    draft_ic3_complaint,
    draft_freeze_letter,
    draft_action_plan,
    list_unverified,
)


def get_tools() -> list[Any]:
    """Return the deterministic functions wrapped as Strands tools.

    Raises ImportError with install guidance when strands is absent.
    """
    tool = _tool_decorator()
    return [tool(fn) for fn in _PLAIN_FUNCTIONS]
