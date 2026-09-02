# Recourse — Demo Video Script (target: under 5 minutes)

## 0:00–0:40 — The problem

- Cold open, talking head or slide: "Last year Americans reported over a billion dollars lost
  to crypto and online scams — and the hours right after the scam are the only hours when a
  freeze request or a complete FBI complaint can still change the outcome."
- "I've spent four years volunteering with cybercrime victims. In those first hours a victim is
  panicked and ashamed, and the paperwork is brutal: the IC3 form can't be saved mid-way,
  exchanges want exact hashes and timestamps, and one wrong digit poisons the whole filing."
- "Recourse is a first-response agent for that moment."

## 0:40–1:10 — Who it's for and what it does

- "You paste your story — plain language plus whatever fragments you have. Recourse produces a
  canonical case file, an IC3 complaint draft mapped to the real form's seven steps, an
  exchange freeze-request letter, an ordered action plan, and an explicit list of what it could
  NOT verify. Every document is a draft for the victim to review — it is not legal advice."

## 1:10–2:40 — Live run (offline pipeline)

- Terminal, fresh venv. Run:
  `python -m recourse intake examples/example_story.txt --out demo-case/`
- Scroll the example story briefly: a fictional romance/investment scam with a BTC leg, an ETH
  leg, and a wire — hashes, addresses, dates mixed into prose.
- Open `demo-case/ic3_draft.md`: point at Step 3 — amounts with no dollar signs or commas
  (that's the real form's rule), each transaction with its hash and destination; point at the
  victim-info fields all reading `[NOT PROVIDED]` — "Recourse never guesses; an email in a
  scam story is usually the scammer's."
- Open `demo-case/freeze_letter.md`: exact transactions, and the conservative wording — a
  freeze is requested, never promised.
- Open `demo-case/action_plan.md`: "Ordered by urgency — exchange fraud team first, because
  funds move in hours; then IC3, FTC, local police. Every link is the official one."
- KEY LINE: "No API key was involved. No model was involved. Same input, same case id, same
  drafts, on any machine."

## 2:40–3:40 — The boundary: the model is not allowed to know numbers

- One architecture slide (the mermaid diagram from the README).
- "Deterministic Python owns every fact — hashes, amounts, dates, field mappings. The Strands
  agent on top wraps those functions as tools: it narrates, asks the victim for missing fields,
  and walks them through the plan. Its prompt forbids stating any number not in tool output —
  but even if it tried, the filings are rendered by code the model never touches."
- Live agent clip (agent mode), three beats, ~40 seconds:
  1. **The interview.** After `build_case_file`, the agent asks for ONE thing — the wire
     confirmation number — and says why ("the only transfer a bank can still try to recall").
     The answer goes in through `add_detail`; the `[NOT PROVIDED]` field flips.
  2. **The audit as a guardrail.** The agent writes the IC3 Step 5 description and submits it via
     `propose_description`. Show a REJECTED response — the audit names the figure that didn't
     trace — then the corrected ACCEPTED one. "The model is allowed to write prose. It is not
     allowed to write a number we can't find in your own words."
  3. **The second scam.** Paste a "we can recover your funds for a small activation fee" DM into
     `screen_recovery_offer`: LIKELY RECOVERY SCAM, every warning sign quoted from the message.
     "Victims get hit again within days. This is the part I wish someone had built for me." 

## 3:40–4:30 — The eval gate, live

- Run: `python evals/run_evals.py` — show the scoreboard going green.
- "Eight golden scenarios — including this exact story — and 140-plus unit tests. Every hash, amount, and date in every rendered filing must exist
  verbatim in the source story. Negative controls — truncated hashes, lookalike strings, a
  64-character puzzle code — must NOT be extracted. And the gate self-tests: filings we
  deliberately tamper with an invented hash must be flagged, or the gate itself fails."
- Briefly show the FAIL output from the self-test description (or flip one expected value to
  show a red run), then the green run again. "This runs in CI on every push, fully offline."

## 4:30–5:00 — Close

- "Recourse does one narrow thing completely: it turns the worst afternoon of someone's
  financial life into a reviewable case file and a next physical step, while acting fast still
  matters."
- "Built on the Strands Agents SDK for the Agents for Humans hackathon, Good Neighbor track.
  All code net-new, Apache-2.0. Every demo person, hash, and domain is fictional."
- End card: repo URL + "DRAFTS for victim review — not legal advice."
