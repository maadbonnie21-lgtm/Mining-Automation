"""Thin command-line wiring for passive Windows inventory validation."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from ...capture import CaptureError
from ...capture.windows import DEFAULT_TITLE_SUBSTRING, WindowsCaptureBackend
from ..cli import load_detector
from ..errors import PerceptionError
from .detector import InventoryDetector
from .live_validation import (
    InventoryLiveValidationError,
    InventoryValidationCase,
    InventoryValidationProvenance,
    run_inventory_live_validation,
)

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture one RuneLite frame for unverified inventory validation evidence."
        ),
    )
    parser.add_argument(
        "--case",
        required=True,
        choices=tuple(case.value for case in InventoryValidationCase),
        help="operator-selected case label; recorded as unverified provenance",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("diagnostics/inventory-live"),
        help="parent for uniquely allocated evidence directories",
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE_SUBSTRING,
        help="case-insensitive RuneLite window-title substring",
    )
    parser.add_argument("--capture-build", help="capture/harness build or commit identity")
    parser.add_argument("--runelite-build", help="RuneLite build/version when known")
    parser.add_argument(
        "--note",
        action="append",
        dest="notes",
        default=[],
        help="operator build/environment note; repeat as needed",
    )
    parser.add_argument(
        "--reviewed-detector",
        metavar="MODULE:ATTRIBUTE",
        help=(
            "optional no-argument InventoryDetector instance/factory composed from "
            "an explicitly reviewed live profile and empty reference"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        detector = _load_reviewed_detector(
            cast(str | None, arguments.reviewed_detector)
        )
        provenance = InventoryValidationProvenance(
            capture_build=cast(str | None, arguments.capture_build),
            runelite_build=cast(str | None, arguments.runelite_build),
            notes=tuple(cast(list[str], arguments.notes)),
        )
        title = cast(str, arguments.title)
        if not title.strip():
            raise ValueError("--title must be a non-empty window-title substring")
        backend = WindowsCaptureBackend(title_substring=title)
    except (PerceptionError, RuntimeError, TypeError, ValueError) as exc:
        print(f"inventory validation setup error: {exc}", file=sys.stderr)
        return 2

    try:
        report = run_inventory_live_validation(
            backend=backend,
            case=InventoryValidationCase(cast(str, arguments.case)),
            output_root=cast(Path, arguments.output_root),
            provenance=provenance,
            detector=detector,
        )
    except (CaptureError, InventoryLiveValidationError, OSError, TypeError, ValueError) as exc:
        print(f"inventory validation error: {exc}", file=sys.stderr)
        return 1

    print(report.render_text(), end="")
    return report.exit_code


def _load_reviewed_detector(specification: str | None) -> InventoryDetector | None:
    if specification is None:
        return None
    detector = load_detector(specification)
    if not isinstance(detector, InventoryDetector):
        raise InventoryLiveValidationError(
            "--reviewed-detector must resolve to the production InventoryDetector"
        )
    return detector
