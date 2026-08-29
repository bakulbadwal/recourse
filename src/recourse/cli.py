"""Offline deterministic CLI.

    python -m recourse intake story.txt --out casedir/
    python -m recourse demo

Writes casefile.json, ic3_draft.md, freeze_letter.md, action_plan.md, and
unverified.md. Pure offline: no model, no network, no API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import resources
from pathlib import Path

from .casefile import build_casefile
from .filings import render_all

_EXAMPLE_STORY = "example_story.txt"


def _write_outputs(story: str, out_dir: Path, created_at: str | None = None) -> list[Path]:
    case = build_casefile(story, created_at=created_at)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    casefile_path = out_dir / "casefile.json"
    casefile_path.write_text(
        json.dumps(case.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    written.append(casefile_path)

    for name, doc in render_all(case).items():
        p = out_dir / name
        p.write_text(doc + "\n", encoding="utf-8")
        written.append(p)
    return written


def _cmd_intake(args: argparse.Namespace) -> int:
    story_path = Path(args.story)
    if not story_path.is_file():
        print(f"error: story file not found: {story_path}", file=sys.stderr)
        return 2
    story = story_path.read_text(encoding="utf-8")
    written = _write_outputs(story, Path(args.out), created_at=args.created_at)
    print("Recourse case written (all documents are DRAFTS for victim review):")
    for p in written:
        print(f"  {p}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    example = resources.files("recourse").joinpath("data", _EXAMPLE_STORY)
    story = example.read_text(encoding="utf-8")
    out_dir = Path(args.out) if args.out else Path("recourse-demo-case")
    written = _write_outputs(story, out_dir)
    print("Demo case (fictional story) written:")
    for p in written:
        print(f"  {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="recourse",
        description=(
            "Recourse — first-response drafts for fraud victims. "
            "All outputs are DRAFTS for the victim's review; not legal advice."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_intake = sub.add_parser("intake", help="Process a victim story file")
    p_intake.add_argument("story", help="Path to a plain-text story file")
    p_intake.add_argument("--out", required=True, help="Output directory")
    p_intake.add_argument(
        "--created-at",
        default=None,
        help="Optional explicit ISO-8601 timestamp recorded on the case file "
        "(the library never reads the clock itself)",
    )
    p_intake.set_defaults(func=_cmd_intake)

    p_demo = sub.add_parser("demo", help="Run the bundled fictional example")
    p_demo.add_argument("--out", default=None, help="Output directory")
    p_demo.set_defaults(func=_cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
