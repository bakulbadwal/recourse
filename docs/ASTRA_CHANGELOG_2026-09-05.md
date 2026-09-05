# Recourse local workbench — September 5, 2026

Baseline: `7e1878aa44f21386555f745756d7dc6a4a186c64`. Work performed on
`astra/portfolio-2026-09-05`, after the original project implementation. This is an additional
workflow, not a claim about what an earlier hackathon submission or video contained.

## Before and after

Before, `python -m recourse serve --help` failed with “invalid choice: 'serve'”. Users needed
the CLI to create five files, then inspect those files in other applications, or configure
the optional Strands interview.

After, `python -m recourse serve` starts a local browser workbench. Users paste a story or
load the existing fictional example, inspect extracted values beside their exact source
quotes, select a quote in the original story, review reconstructed transfers and missing
information, preview all four drafts and the CaseFile JSON, and download the five-file ZIP.
Changing the story marks the old build as stale and disables downloads until it is rebuilt.
An audit failure is visible and blocks export.

The visual design uses system fonts, a paper-and-ink palette, readable document surfaces,
an explicit draft-only state, and an original CSS document illustration. It adapts to narrow
screens. Keyboard support includes Cmd/Ctrl+Enter, arrow/Home/End tab navigation, a skip
link, visible focus, and live status/error messages. No UI framework, stylesheet library,
font CDN, model, or new dependency was added.

## Files and rationale

| File | Change | Why |
| --- | --- | --- |
| `src/recourse/workbench.py` | Stateless stdlib loopback server, reviewed JSON response, deterministic ZIP export, request boundaries | Make the existing engine inspectable without configuration or a case database |
| `src/recourse/web/index.html` | Accessible story, evidence, review-gap, and draft workflow | Provide a clear path from fragments to reviewable output |
| `src/recourse/web/workbench.css` | Desktop/mobile layout and document surfaces | Keep the interface calm and readable while long identifiers wrap |
| `src/recourse/web/workbench.js` | Local requests, literal-text rendering, source selection, keyboard tabs, stale-state and download handling | Preserve provenance and keep old builds from being mistaken for current results |
| `src/recourse/cli.py` | Lazy `serve` command and validated port option | Preserve existing `intake`, `demo`, and `chat` entry points |
| `pyproject.toml` | Include UI assets in package data | Ensure the interface ships with a normal install |
| `tests/test_workbench.py` | Source/bundle and real HTTP behavior tests | Exercise the new trust boundaries and deterministic output |
| `README.md`, `ARCHITECTURE.md` | Usage, data flow, privacy boundary, and limitations | Make the new workflow reproducible and avoid equating source checks with verification |
| `docs/workbench-desktop.png` | Genuine local browser capture of the entry screen | Show the actual interface without inventing a product screenshot |

No changes were made to the extractors, CaseFile assembly, audit rules, deterministic filing
templates, optional agent, or existing golden cases.

## Verification performed

- The initial behavior test import failed because the workbench module did not yet exist.
  This established the missing surface before implementation.
- `.venv/bin/python -m pytest -q` — **208 passed, 1 skipped** after independent review.
  The skip is the existing “strands installed; offline error path not testable” case.
  HTTP tests used ephemeral loopback ports; the macOS sandbox required the normal local
  execution escalation for socket binding. No dependency installation was performed.
- Independent review reproduced a default-port authority mismatch: browsers omit `:80`
  from Host and Origin. Both loopback names now accept that normalized authority when
  serving port 80, with matching-origin requirements preserved; two regression cases
  exercise the actual HTTP handler on an ephemeral test socket.
- `.venv/bin/python evals/run_evals.py` — **9/9 checks passed**, covering all eight golden
  scenarios and the tampered-filing gate self-test.
- `.venv/bin/python -m recourse serve --help` — exits successfully and documents `--port`.
- `node --check src/recourse/web/workbench.js` and `git diff --check` — passed.
- Real Codex in-app browser, with the server running on `http://127.0.0.1:8765/`:
  fictional example → **14 evidence items, 3 transfers, 5 review notes**; filtering “12500”
  finds one item and “Locate quote” selects exactly `$12,500`; arrow keys navigate the
  evidence, gaps, and drafts panels; Cmd/Ctrl+Enter builds; literal HTML stays text and
  produces no image elements; stale results disable both downloads; clearing removes the
  story and rendered record. The mobile page measured **390px content width at a 390px
  viewport**, with no horizontal overflow. No browser warning/error logs were observed.

The HTTP tests cover source equivalence, empty extraction, independent successive requests,
the maximum story size, deterministic five-file ZIP contents, markup in stories, exact
packaged routes, HEAD, unknown methods, Host/Origin restrictions, missing/duplicate/invalid
length headers, oversized requests before body reads, unsupported encodings, malformed and
duplicate-key JSON, Unicode errors, generic exception responses, no logs, port ownership,
and failed-audit export blocking.

## Limits and observations

- The browser displayed “Bundle download started” with no error after the download click,
  but its download-event observer timed out. A completed browser-managed file save was
  **not confirmed**. Actual ZIP bytes, filenames, content, and repeatability are verified
  by the HTTP tests. Browser download handling remains a manual check for the maintainer.
- The browser is stateless: no saved-case list, document upload, attachments, identity form,
  direct filing, or model-generated prose. Identity is completed in downloaded drafts.
- Source checks preserve the engine's existing coverage and limitations. Passing is not
  external verification, proof of recovery prospects, or confirmation of transaction
  groupings. The UI calls out these distinctions.
- The local server is for one user's local workflow. It has no authentication service,
  public-host deployment support, or persistent store. Loopback does not isolate it from
  other software already running on the same computer. Browser/OS behavior and exported
  files remain outside the application's no-persistence promise.
- Testing used the existing Python 3.14 environment. The existing CI matrix covers Python
  3.11–3.13; those interpreter versions were not installed or run during this change.

Run it again with:

```bash
.venv/bin/python -m recourse serve --port 8765
```
