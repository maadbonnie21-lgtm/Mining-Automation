#!/usr/bin/env python3
"""Run the current P0 startup resolver in strictly read-only mode.

This command is intentionally separate from the live mining entry point. It
captures and evaluates the exact RuneLite client, prints the PREP receipt path,
and sends no focus, resize, camera, mining, navigation, or banking input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import runelite_prep as base  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="RuneLite")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    forwarded = ["--title", args.title]
    if args.output is not None:
        forwarded.extend(("--output", str(args.output)))
    return base.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
