"""Command-line entry point for display-free navigation replay."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .replay import NavigationManifestError, load_navigation_replay, run_navigation_replay

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a synthetic fixed-route checkpoint replay.",
    )
    parser.add_argument("--manifest", required=True, type=Path, help="Synthetic replay JSON")
    parser.add_argument("--json-report", type=Path, help="Optional deterministic JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 for pass, 1 for expectation mismatch, or 2 for setup error."""

    arguments = build_parser().parse_args(argv)
    try:
        report = run_navigation_replay(load_navigation_replay(arguments.manifest))
        if arguments.json_report is not None:
            arguments.json_report.write_text(report.to_json(), encoding="utf-8")
    except (NavigationManifestError, OSError, ValueError) as exc:
        print(f"navigation replay error: {exc}", file=sys.stderr)
        return 2
    print(report.render_text(), end="")
    return 0 if report.passed else 1
