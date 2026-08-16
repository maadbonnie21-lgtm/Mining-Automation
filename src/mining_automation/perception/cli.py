"""Command-line entry point for display-free detector regression evaluation."""

from __future__ import annotations

import argparse
import importlib
import inspect
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from .detector import Detector, validate_detector
from .errors import DetectorContractError, PerceptionError
from .evaluation import evaluate_dataset
from .replay import load_replay_dataset

__all__ = ["build_parser", "load_detector", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate detector objects against a versioned replay manifest.",
    )
    parser.add_argument("--manifest", required=True, type=Path, help="Path to fixture manifest JSON")
    parser.add_argument(
        "--detector",
        required=True,
        action="append",
        dest="detectors",
        metavar="MODULE:ATTRIBUTE",
        help=(
            "Importable detector instance, no-argument class, or no-argument factory; "
            "repeat for an ensemble"
        ),
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optional path for deterministic machine-readable JSON",
    )
    return parser


def load_detector(specification: str) -> Detector:
    """Load and validate one detector import specification."""
    module_name, separator, attribute_name = specification.partition(":")
    if not separator or not module_name or not attribute_name:
        raise DetectorContractError(
            f"detector specification must be MODULE:ATTRIBUTE, got {specification!r}"
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise DetectorContractError(f"could not import detector module {module_name!r}") from exc
    try:
        candidate: object = getattr(module, attribute_name)
    except AttributeError as exc:
        raise DetectorContractError(
            f"detector module {module_name!r} has no attribute {attribute_name!r}"
        ) from exc
    except Exception as exc:
        raise DetectorContractError(
            f"could not read detector attribute {attribute_name!r} "
            f"from module {module_name!r}"
        ) from exc

    try:
        if inspect.isclass(candidate):
            candidate = candidate()
        elif callable(candidate) and not hasattr(candidate, "detect"):
            candidate = candidate()
    except Exception as exc:
        raise DetectorContractError(f"could not construct detector {specification!r}") from exc

    detector = cast(Detector, candidate)
    validate_detector(detector)
    return detector


def main(argv: Sequence[str] | None = None) -> int:
    """Run the evaluator; return 0 pass, 1 regression failure, or 2 setup error."""
    arguments = build_parser().parse_args(argv)
    try:
        dataset = load_replay_dataset(arguments.manifest)
        detectors = tuple(load_detector(specification) for specification in arguments.detectors)
        report = evaluate_dataset(dataset, detectors)
        if arguments.json_report is not None:
            arguments.json_report.write_text(report.to_json(), encoding="utf-8")
    except (PerceptionError, OSError, ValueError) as exc:
        print(f"perception evaluation error: {exc}", file=sys.stderr)
        return 2

    print(report.render_text(), end="")
    return 0 if report.passed else 1
