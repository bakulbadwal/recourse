# Recourse

**A first-response agent for fraud victims.** Paste your story — plain language plus whatever
fragments you have (transaction hashes, wallet addresses, amounts, dates, URLs, emails) — and
Recourse turns it into a reviewable evidence record and ready-to-review filing drafts, in minutes,
while acting fast still matters.

Built for the AWS **"Agents for Humans"** hackathon, **Good Neighbor track**, with the
[Strands Agents SDK](https://strandsagents.com). Born out of the maintainer's four years of
volunteer work with cybercrime victims: the hours right after a scam are when a freeze request or
a complete IC3 complaint can still change the outcome — and they are exactly the hours when a
panicked victim can't produce one.

> **Every document Recourse produces is a DRAFT for the victim to review before filing.
> Recourse is not a lawyer and nothing it outputs is legal advice.**

## What it does

From one pasted story, Recourse produces:

1. **`casefile.json`** — a canonical CaseFile: deduped, timeline-ordered evidence with explicit
   verified-vs-`[NOT PROVIDED]` fields and a stable case id (sha256 of normalized contents).
2. **`ic3_draft.md`** — an FBI IC3 complaint draft mapped to the real form's seven steps
   (complaint type, victim info, financial transactions, subjects, description, other info,
   signature), respecting the form's actual mechanics (10-transaction cap, 3,500-character
   description limit, amounts with no `$` or commas, no file uploads).
3. **`freeze_letter.md`** — an exchange fraud-report / freeze-request letter with exact
   transaction details, conservatively worded (a freeze is requested, never promised).
4. **`action_plan.md`** — an ordered plan with urgency rationale and official links:
   exchange fraud team first (funds move fast), then IC3, FTC, local police, plus situational
   channels (SEC, CFTC, elder-fraud hotline) and a recovery-scam warning.
5. **`unverified.md`** — an explicit list of everything that could **not** be verified or was
   not provided. Nothing is ever guessed.

## Quickstart — offline mode (no API key, no network, no model)

```bash
git clone https://github.com/bakulbadwal/recourse && cd recourse
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Run the bundled fictional example:
python -m recourse demo --out demo-case/

# Run on your own story:
python -m recourse intake my_story.txt --out my-case/
```

That is the entire product working end to end: five files, deterministic, reproducible —
same story in, same case id and same drafts out, on any machine, any day.

## Quickstart — agent mode (optional, interactive)

```bash
pip install -e ".[agent]"        # pulls in strands-agents[anthropic]

# Anthropic API — or set nothing and use AWS credentials for Amazon Bedrock (the Strands default):
export ANTHROPIC_API_KEY=sk-ant-...
python -m recourse chat                                              # interactive interview
python -c "from recourse.agent import main; main()" < my_story.txt   # one-shot
```

The agent runs an **interview**, not a one-shot conversion — a victim in the first hours has
fragments and shame, not a clean story. Nine Strands `@tool`s, each guarding one boundary:

| Tool | What it does | Boundary it enforces |
|---|---|---|
| `build_case_file` | Extracts the evidence record from the story, verbatim | Every fact is a literal substring of the story |
| `add_detail` | Appends what the victim says later and rebuilds the case | Later facts enter the same way, with the same provenance |
| `set_complainant` | Records the victim's own identity for IC3 Step 2 | The only door for identity — never inferred from the story |
| `propose_description` | Accepts a model-written IC3 Step 5 narrative **only if the audit passes** | The one place the model authors filing content, gated by `audit.py` |
| `draft_ic3_complaint`, `draft_freeze_letter`, `draft_action_plan`, `list_unverified` | Render the drafts | Take a `case_id` only — there is no story parameter to paraphrase |
| `screen_recovery_offer` | Rule-based screen of "we can get your money back" pitches | Every warning sign is quoted verbatim from the message |

The system prompt makes the model ask for **one missing item at a time, most recoverable first**
— a bank wire with no reference number before anything else, because it is the only leg a bank
can still try to recall — and forbids stating any figure not present in tool output. With no
`ANTHROPIC_API_KEY`, Strands defaults to Amazon Bedrock (AWS credentials + Bedrock model access
for Claude required); both provider paths are exercised in `tests/`.

**The audit is a runtime guardrail, not only a CI check.** `propose_description` runs the same
anti-invention audit over the model's own text before accepting it: a hash, address, amount, or
date that does not trace to the victim's words is rejected with the exact violations listed, and
the model has to fix them. Until a description is accepted, the IC3 draft carries the victim's raw
story instead.

## Architecture: the model is not allowed to know numbers

```mermaid
flowchart LR
    A[Victim's story\nplain text] --> B[intake.py\nregex/parser extractors]
    B --> C[casefile.py\ndedupe · timeline · sha256 case id]
    C --> D[filings.py\ndeterministic templates]
    D --> E[ic3_draft.md\nfreeze_letter.md\naction_plan.md\nunverified.md]
    C --> F[audit.py\nanti-invention audit]
    E --> F
    F --> G[evals/run_evals.py\nCI gate — exit 1 on any\nuntraceable fact]
    subgraph OPTIONAL agent layer
        H[Strands Agent\nnarrates & asks questions] -. calls @tools .-> B
        H -. calls @tools .-> D
    end
```

**Deterministic Python owns every fact.** Extraction is pure regex/parsing — no model is
involved. Hashes, addresses, amounts, dates, and field mappings are computed and rendered by
code. The (optional) model layer narrates, structures the conversation, and asks the victim for
missing fields; its system prompt forbids stating any hash/amount/date not present in tool
output, and even if it tried, the filings themselves are rendered by templates the model never
touches.

A filing draft with one wrong hash is worse than no draft at all — this boundary is the product.

### The anti-invention eval gate

```bash
pip install -e ".[dev]"
pytest -q                    # 140+ unit tests
python evals/run_evals.py    # the gate; non-zero exit on any failure
```

For eight golden scenarios (multi-chain crypto, romance/pig-butchering scams, a no-crypto wire
fraud, ambiguous date formats, a negative-control story full of lookalike strings, and the exact
story used in the demo video), the gate checks that:

- every hash/address/amount/date in every rendered filing exists **verbatim in the source story**
  (or is a declared derivation, like the loss total or the sha256 case id);
- extraction matches hand-verified expected values **exactly** — extras fail, misses fail;
- truncated hashes, hex strings with invalid characters, 39-character addresses, and
  contextless 64-hex strings are **not** extracted (negative controls);
- the same input yields the same case id (determinism);
- and, as a self-test, filings deliberately tampered with an invented hash/amount/date/address
  **are flagged** — proving the gate can actually fail.

## Honest limitations

- Extraction is deliberately conservative: unusual phrasings of amounts/dates, and Solana/Tron/
  Monero-style identifiers, may be missed. Missed facts land in `unverified.md` for the victim
  to add by hand; that trade is intentional — a miss is recoverable, a fabrication is not.
- Base58/bech32 matching is heuristic (context-gated, no checksum validation); a syntactically
  valid but mistyped address will pass through exactly as the victim wrote it.
- Ambiguous slash dates (03/04/2026) are read as US month/day/year, flagged for confirmation.
- Recourse verifies nothing externally — no on-chain lookups, no exchange contact. It structures
  what the victim reports; investigators verify.
- A leg the victim describes in dollars but paid in crypto ("$12,500 worth of ETH") keeps both
  labels — `currency: USD`, `asset: ETH` — and says so in `unverified.md`. Recourse never
  converts, so the dollar figure stays the victim's own stated value.
- Subject business names are offered as **candidates to confirm**, never asserted: a scam story
  names the victim's own bank as readily as the scammer's shell company. `LP` and `Co.` are
  deliberately not recognized as corporate suffixes — they collide with initials and prose.
- Spoken multipliers are normalized, never truncated: "$2.5 million" becomes `2500000` with a
  confirm-this note (reading it as $2.50 would be a six-orders-of-magnitude fabrication). A
  multiplier on a crypto quantity ("5k BTC") is dropped with a note instead.
- An amount a paragraph describes as the *total* of its other amounts is quoted on the IC3
  total-loss line and reconciled against the itemized sum — never counted as a transfer. Two
  competing totals in one paragraph are left alone and flagged. Anything money-shaped that no rule
  could read is listed in `unverified.md`: a miss is never silent.
- The audit over a model-written description checks digits, hashes, addresses, and dates. It
  cannot catch a number written out in words ("twelve thousand dollars") or two real figures
  swapped between transactions; the victim's review is still the last line.
- US-centric: the filing map targets IC3/FTC. The freeze letter and case file are
  jurisdiction-neutral.
- The IC3 step structure and required fields follow the DOJ/OVC walkthrough and IC3's FAQ;
  quoted labels are exact where those sources quote them, descriptive otherwise.

## Hackathon statements

- **Track:** Good Neighbor — an agent that does real work for real people at one of the worst
  moments of their financial lives, end to end: story in, filed-ready drafts and an ordered plan
  out.
- **AI-assistance disclosure:** built during the submission window with AI coding assistants,
  per hackathon rules; all code net-new for this project.
- **Example data:** every story in `examples/` and `evals/golden/` is synthetic — fictional
  people, invented hashes/addresses, `.example` domains.
- **License:** Apache-2.0 (see `LICENSE`).

## Repository layout

```
src/recourse/
  intake.py     deterministic extractors (regex/parsers only)
  casefile.py   canonical CaseFile: dedupe, timeline, sha256 case id
  filings.py    IC3 draft, freeze letter, action plan, unverified report
  audit.py      anti-invention provenance audit
  tools.py      Strands @tool wrappers (lazy import; base install needs no strands)
  agent.py      build_agent(): Anthropic provider or Bedrock default
  cli.py        offline pipeline: python -m recourse intake|demo
evals/          golden stories + expected extractions + the gate
tests/          unit tests (pytest)
```
