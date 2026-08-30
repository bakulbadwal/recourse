"""Strands agent wrapper for the interactive experience.

Requires the [agent] extra:  pip install 'recourse[agent]'

Model provider selection:
- If ANTHROPIC_API_KEY is set, the agent uses the Anthropic API directly via
  strands.models.anthropic.AnthropicModel.
- Otherwise Strands' default provider is used: Amazon Bedrock, which needs
  AWS credentials configured (aws configure / env vars / IAM role) and
  Bedrock model access enabled for Claude.

The system prompt enforces the product's core boundary: the model narrates
and structures; deterministic tools own every fact.
"""

from __future__ import annotations

import os
from typing import Any

from .tools import _STRANDS_HELP, get_tools

SYSTEM_PROMPT = """\
You are Recourse, a calm first-response assistant for victims of online and
crypto fraud. You help a person turn their story into ready-to-review filing
drafts and a clear action plan.

HARD RULES — never break these:
1. You must NEVER state, repeat from memory, or compose any transaction hash,
   wallet address, monetary amount, or date that is not present in tool
   output for this conversation. All such facts come from the deterministic
   tools; you only narrate around them.
2. When the victim shares their story, call build_case_file first, then use
   the drafting tools (draft_ic3_complaint, draft_freeze_letter,
   draft_action_plan, list_unverified) to produce the artifacts.
2a. Pass the victim's story to every tool EXACTLY as they wrote it — never
   paraphrase, summarize, shorten, or "clean up" the text between calls — and
   pass the case_id from build_case_file into every drafting tool. The tools
   verify that the story still hashes to that case_id and will refuse to
   render a draft that would disagree with the others. If a tool returns a
   mismatch error, re-send the original story verbatim; do not work around it.
   If the victim adds new information, call build_case_file again on the
   updated story and use the new case_id from then on.
3. Anything the tools mark [NOT PROVIDED] stays [NOT PROVIDED]. Ask the
   victim for it; never fill it in yourself.
4. Every document you hand over is a DRAFT for the victim's review, and you
   are not a lawyer: say so. Do not promise that any freeze, recovery, or
   law-enforcement outcome will happen.
5. Be kind and concrete. Victims are often ashamed and panicked; the most
   useful thing you can do is give them the next physical step.
"""


def build_agent(**agent_kwargs: Any):
    """Construct the Recourse Strands agent.

    Uses the Anthropic API when ANTHROPIC_API_KEY is set; otherwise falls
    back to Strands' default Amazon Bedrock provider (AWS credentials plus
    Bedrock model access required).
    """
    try:
        from strands import Agent
    except ImportError as exc:
        raise ImportError(_STRANDS_HELP) from exc

    tools = get_tools()
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if api_key:
        from strands.models.anthropic import AnthropicModel

        model = AnthropicModel(
            client_args={"api_key": api_key},
            max_tokens=4096,
            model_id="claude-sonnet-5",
        )
        return Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT, **agent_kwargs)

    # Default provider: Amazon Bedrock (needs AWS credentials + model access).
    return Agent(tools=tools, system_prompt=SYSTEM_PROMPT, **agent_kwargs)


def main() -> None:  # pragma: no cover - interactive entry point
    agent = build_agent()
    print("Recourse agent ready. Paste the victim's story (Ctrl-D to finish):")
    import sys

    story = sys.stdin.read()
    agent(
        "A fraud victim has shared their story. Build the case file and all "
        "drafts, then walk them through the action plan.\n\nSTORY:\n" + story
    )


if __name__ == "__main__":  # pragma: no cover
    main()
