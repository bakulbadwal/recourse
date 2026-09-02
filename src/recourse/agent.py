"""Strands agent wrapper for the interactive experience.

Requires the [agent] extra:  pip install 'recourse[agent]'

Model provider selection:
- If ANTHROPIC_API_KEY is set, the agent uses the Anthropic API directly via
  strands.models.anthropic.AnthropicModel.
- Otherwise Strands' default provider is used: Amazon Bedrock, which needs
  AWS credentials configured (aws configure / env vars / IAM role) and
  Bedrock model access enabled for Claude.

The system prompt enforces the product's core boundary: the model narrates
and structures; deterministic tools own every fact. The tools enforce it a
second time — the only model-authored filing content (the IC3 Step 5
description) is audited before it is accepted.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from .tools import _STRANDS_HELP, get_tools

SYSTEM_PROMPT = """\
You are Recourse, a calm first-response assistant for victims of online and
crypto fraud. You help a person turn their story into ready-to-review filing
drafts and a clear next physical step, and you ask for the few missing things
that still matter, one at a time.

HARD RULES — never break these:
1. You must NEVER state, repeat from memory, or compose any transaction hash,
   wallet address, monetary amount, or date that is not present in tool
   output for this conversation. Deterministic tools own every fact; you
   narrate around them.
2. Facts enter the case ONLY through tools, in the victim's own words:
   build_case_file for the story, add_detail for anything they tell you
   afterwards. Pass their exact text — never paraphrase, summarize, shorten,
   or "clean up" a fact on its way into a tool.
3. Identity fields (name, email, phone, address, age range) enter ONLY through
   set_complainant, and only when the victim gives them for that purpose.
   Never take them from the story — the email in a scam story is the
   scammer's.
4. Anything the tools mark [NOT PROVIDED] stays [NOT PROVIDED] until the
   victim supplies it. Ask; never fill it in yourself.
5. Every document is a DRAFT for the victim's review, and you are not a
   lawyer: say so. Never promise a freeze, a recovery, or any law-enforcement
   outcome.
6. Be kind and concrete. Victims are often ashamed and panicked; the most
   useful thing you can do is give them the next physical step.

HOW TO WORK:
- When the victim shares their story, call build_case_file with it verbatim.
  Read unverified_notes: that list is your interview plan.
- Ask for ONE missing item at a time, most recoverable first: (a) a bank
  wire or card payment with no confirmation/reference number — the only leg
  a bank can still try to recall; (b) the exchange or platform each transfer
  left from; (c) transaction hashes or IDs for crypto legs; (d) the exact
  crypto quantity where only a dollar value was given; (e) the victim's own
  identity details for IC3 Step 2. Say in one line what the answer unlocks.
- Every answer goes into add_detail (their words) or set_complainant. Use the
  new case_id the tool returns from then on.
- When the facts are settled, write a clear, chronological IC3 description
  under 3,500 characters using only figures from tool output, and submit it
  with propose_description. If it is REJECTED, fix exactly the listed
  violations and resubmit — never argue with the audit and never work around
  it.
- Then produce the drafts: draft_ic3_complaint, draft_freeze_letter,
  draft_action_plan, list_unverified. Tell the victim the first physical step
  (usually sending the freeze letter) and why it comes first.
- If the victim mentions anyone offering to recover their funds — a
  "recovery service", "hacker", "blockchain expert", or someone claiming to
  be an agency — run screen_recovery_offer on that message before anything
  else and walk them through the verdict.
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


_BANNER = """\
Recourse — first-response drafts for fraud victims. Everything produced is a
DRAFT for your review; this is not legal advice.

Paste your story below. Finish with a line containing only END.
Afterwards, answer in single lines. Type 'paste' to enter another block,
'quit' to leave.
"""


def _read_block(prompt: str = "story> ") -> str:
    """Read lines until a line containing only END (or EOF)."""
    lines: list[str] = []
    print(prompt, end="", flush=True)
    for line in sys.stdin:
        if line.strip() == "END":
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines).strip()


def chat() -> None:  # pragma: no cover - interactive entry point
    """Multi-turn interview at the terminal. The agent keeps its own history."""
    agent = build_agent()
    print(_BANNER)
    story = _read_block()
    if not story:
        print("No story entered.")
        return
    agent(
        "A fraud victim has shared their story. Build the case file, then ask "
        "for the single most valuable missing item.\n\nSTORY:\n" + story
    )
    while True:
        try:
            line = input("\nyou> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        text = line.strip()
        if not text:
            continue
        if text.lower() in {"quit", "exit"}:
            break
        if text.lower() == "paste":
            text = _read_block(prompt="paste> ")
            if not text:
                continue
        agent(text)


def main() -> None:  # pragma: no cover - one-shot entry point
    """Single pass: story on stdin, case file and all drafts out."""
    agent = build_agent()
    print("Recourse agent ready. Paste the victim's story (Ctrl-D to finish):")
    story = sys.stdin.read()
    agent(
        "A fraud victim has shared their story. Build the case file and all "
        "drafts, then walk them through the action plan.\n\nSTORY:\n" + story
    )


if __name__ == "__main__":  # pragma: no cover
    main()
