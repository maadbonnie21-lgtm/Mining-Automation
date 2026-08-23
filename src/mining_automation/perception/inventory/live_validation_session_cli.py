"""Thin CLI for guided, resumable inventory evidence sessions."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from ...capture import CaptureError
from ...capture.windows import DEFAULT_TITLE_SUBSTRING, WindowsCaptureBackend
from ..cli import load_detector
from ..errors import PerceptionError
from .detector import InventoryDetector
from .live_validation import InventoryValidationCase, InventoryValidationProvenance
from .live_validation_session import (
    DEFAULT_INVENTORY_VALIDATION_CASES,
    OPTIONAL_INVENTORY_VALIDATION_CASES,
    InventoryValidationSessionError,
    InventoryValidationSessionPaused,
    load_inventory_validation_session,
    run_inventory_validation_session,
)

__all__ = ["build_parser", "main"]

InputFunction = Callable[[str], str]

_CASE_INSTRUCTIONS = {
    InventoryValidationCase.EMPTY_REFERENCE: (
        "Open the inventory tab and make the inventory completely empty. "
        "This capture will be retained as the proposed immutable reference."
    ),
    InventoryValidationCase.EMPTY_VALIDATION: (
        "Keep the same client layout and capture a separate empty inventory. "
        "Do not reuse or copy the reference capture."
    ),
    InventoryValidationCase.PARTIAL: (
        "Open the inventory tab with a reviewer-countable partially occupied inventory."
    ),
    InventoryValidationCase.FULL: (
        "Open the inventory tab with all 28 logical slots occupied."
    ),
    InventoryValidationCase.WRONG_TAB: (
        "Select a non-inventory side-panel tab so inventory perception must fail closed."
    ),
    InventoryValidationCase.OBSTRUCTED: (
        "Open the inventory tab and deliberately cover part of it with a normal tooltip, "
        "menu, or other visible obstruction."
    ),
    InventoryValidationCase.HOVER_DRAG: (
        "Open the inventory tab while an item is hovered or being held/dragged by you."
    ),
    InventoryValidationCase.QUANTITY_TEXT: (
        "Open the inventory tab with visible stack-quantity text on one or more items."
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one guided, resumable RuneLite inventory evidence session. "
            "The tool captures only after the operator prepares each requested state."
        )
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("diagnostics/inventory-validation-sessions"),
        help="parent for uniquely allocated session directories",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        help="resume an existing session directory without recapturing completed cases",
    )
    parser.add_argument(
        "--include-case",
        action="append",
        choices=tuple(case.value for case in OPTIONAL_INVENTORY_VALIDATION_CASES),
        default=[],
        help="append an optional case to the six-case default plan; repeat as needed",
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


def main(
    argv: Sequence[str] | None = None,
    *,
    input_function: InputFunction = input,
) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        title = cast(str, arguments.title)
        if not title.strip():
            raise ValueError("--title must be a non-empty window-title substring")
        resume = cast(Path | None, arguments.resume)
        if resume is None:
            optional_cases = tuple(
                InventoryValidationCase(value)
                for value in cast(list[str], arguments.include_case)
            )
            cases = DEFAULT_INVENTORY_VALIDATION_CASES + optional_cases
            provenance = InventoryValidationProvenance(
                capture_build=cast(str | None, arguments.capture_build),
                runelite_build=cast(str | None, arguments.runelite_build),
                notes=tuple(cast(list[str], arguments.notes)),
            )
            output_root = cast(Path, arguments.output_root)
        else:
            if cast(list[str], arguments.include_case):
                raise ValueError("--include-case cannot change a durable resumed plan")
            if (
                arguments.capture_build is not None
                or arguments.runelite_build is not None
                or cast(list[str], arguments.notes)
            ):
                raise ValueError(
                    "build/notes are loaded from the resumed session and cannot be replaced"
                )
            durable = load_inventory_validation_session(resume)
            cases = tuple(item.case for item in durable.records)
            provenance = durable.provenance
            output_root = resume.parent
        detector = _load_reviewed_detector(cast(str | None, arguments.reviewed_detector))
        _validate_resume_detector_mode(resume, detector)
    except (InventoryValidationSessionError, PerceptionError, TypeError, ValueError) as exc:
        print(f"inventory validation session setup error: {exc}", file=sys.stderr)
        return 2

    def backend_factory() -> WindowsCaptureBackend:
        return WindowsCaptureBackend(title_substring=title)

    def ready(
        case: InventoryValidationCase,
        order: int,
        total: int,
        session_directory: Path,
    ) -> None:
        instruction = _CASE_INSTRUCTIONS[case]
        print(f"\n[{order}/{total}] Prepare case: {case.value}")
        print(instruction)
        print(f"Session evidence: {session_directory.resolve()}")
        input_function("Press Enter when RuneLite is ready, or Ctrl+C to pause safely: ")

    try:
        report = run_inventory_validation_session(
            backend_factory=backend_factory,
            output_root=output_root,
            provenance=provenance,
            cases=cases,
            detector=detector,
            ready_callback=ready,
            resume_directory=resume,
        )
    except InventoryValidationSessionPaused as exc:
        print("\nInventory validation session paused safely.", file=sys.stderr)
        print(
            "Resume with:\n"
            f"python tools/validate_inventory_session.py --resume \"{exc.session_directory}\"",
            file=sys.stderr,
        )
        return 130
    except (
        CaptureError,
        InventoryValidationSessionError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"inventory validation session error: {exc}", file=sys.stderr)
        return 1

    print(report.render_text(), end="")
    return report.exit_code


def _load_reviewed_detector(specification: str | None) -> InventoryDetector | None:
    if specification is None:
        return None
    detector = load_detector(specification)
    if not isinstance(detector, InventoryDetector):
        raise InventoryValidationSessionError(
            "--reviewed-detector must resolve to the production InventoryDetector"
        )
    return detector


def _validate_resume_detector_mode(
    resume: Path | None,
    detector: InventoryDetector | None,
) -> None:
    if resume is None:
        return
    durable = load_inventory_validation_session(resume)
    modes = {
        item.detector_mode
        for item in durable.captured_records
        if item.detector_mode is not None
    }
    if modes == {"capture-only"} and detector is not None:
        raise InventoryValidationSessionError(
            "a capture-only session cannot switch detector mode during resume; "
            "finish evidence collection, then evaluate the owned frames separately"
        )
    if modes == {"detector-run"} and detector is None:
        raise InventoryValidationSessionError(
            "a detector-run session must resume with --reviewed-detector"
        )
    if len(modes) > 1:
        raise InventoryValidationSessionError(
            "the durable session already contains mixed detector modes"
        )
