#!/usr/bin/env python3
"""Promote reviewed resource drafts into a replay-schema manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.perception.resource_fixtures import (  # noqa: E402
    build_replay_manifest,
    load_resource_fixture_draft,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--draft-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        draft_paths = sorted(args.draft_dir.glob("*.json"))
        if not draft_paths:
            raise ValueError(f"no JSON drafts found in {args.draft_dir}")
        drafts = [load_resource_fixture_draft(path) for path in draft_paths]
        manifest = build_replay_manifest(drafts, args.output)
    except (OSError, ValueError) as exc:
        print(f"Could not build resource replay manifest: {exc}", file=sys.stderr)
        return 2
    print(
        f"Wrote {len(manifest['cases'])} reviewed resource case(s) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
