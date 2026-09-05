"""Offline deterministic CLI.

    python -m recourse intake story.txt --out casedir/
    python -m recourse demo
    python -m recourse serve

Intake/demo write casefile.json and four filing drafts. Serve runs a
loopback-only workbench. All three need no model, external network, or API key.
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

    p_serve = sub.add_parser("serve", help="Open the offline localhost browser workbench")
    p_serve.add_argument("--port", type=_port, default=8765,
                         help="Local port (default: 8765; 0 chooses a free port)")
    p_serve.set_defaults(func=_cmd_serve)

    p_chat = sub.add_parser(
        "chat",
        help="Interactive interview with the Strands agent (needs the [agent] extra "
        "and ANTHROPIC_API_KEY or AWS/Bedrock credentials)",
    )
    p_chat.set_defaults(func=_cmd_chat)

    args = parser.parse_args(argv)
    return args.func(args)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer from 0 to 65535") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be from 0 to 65535")
    return port


def _cmd_serve(args: argparse.Namespace) -> int:
    from .workbench import serve

    return serve(port=args.port)


def _cmd_chat(args: argparse.Namespace) -> int:  # pragma: no cover - interactive
    try:
        from .agent import chat
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    chat()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
