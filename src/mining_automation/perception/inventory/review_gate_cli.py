"""Thin CLI for the privacy-safe inventory review/replay gate."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from .review_gate import (
    InventoryReviewGateError,
    prepare_inventory_review_package,
    run_inventory_review_replay_gate,
)

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create privacy-safe inventory review material or replay explicit "
            "reviewer truth through the unchanged production detector."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="create a blank review package")
    _add_common(prepare)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="derive a non-activating candidate and run detector replay",
    )
    _add_common(evaluate)
    evaluate.add_argument("--package", type=Path, required=True)
    evaluate.add_argument("--review", type=Path, required=True)
    evaluate.add_argument("--fixture-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        expected_head = str(args.expected_head)
        _require_clean_exact_head(expected_head)
        sessions = tuple(args.session)
        output = Path(args.output)
        if args.command == "prepare":
            package = prepare_inventory_review_package(
                sessions,
                output,
                generator_head_sha=expected_head,
            )
            manifest_sha = _sha256_file(package.manifest_path)
            print("INVENTORY REVIEW PACKAGE READY -- REVIEW TRUTH IS BLANK")
            print(f"Manifest: {package.manifest_path.resolve()}")
            print(f"Manifest SHA-256: {manifest_sha}")
            print(f"Review template: {package.template_path.resolve()}")
            print("Operator labels were not copied into reviewer truth.")
            return 0
        report = run_inventory_review_replay_gate(
            sessions,
            Path(args.package),
            Path(args.review),
            output,
            expected_head_sha=expected_head,
            fixture_output_directory=(
                None if args.fixture_output is None else Path(args.fixture_output)
            ),
        )
        print("INVENTORY REVIEW/REPLAY GATE " + ("PASS" if report.passed else "BLOCKED"))
        print(f"Report: {report.report_path.resolve()}")
        print(f"Report SHA-256: {_sha256_file(report.report_path)}")
        print("Candidate activation_allowed=false")
        return 0 if report.passed else 1
    except (InventoryReviewGateError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"inventory review/replay error: {exc}", file=sys.stderr)
        return 2


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--session",
        type=Path,
        action="append",
        required=True,
        help="owned inventory validation session; repeat for additional batches",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)


def _require_clean_exact_head(expected_head: str) -> None:
    if len(expected_head) != 40 or any(
        character not in "0123456789abcdef" for character in expected_head
    ):
        raise ValueError("--expected-head must be an exact lowercase 40-character SHA")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != expected_head:
        raise InventoryReviewGateError(
            f"git HEAD {head!r} does not equal --expected-head {expected_head!r}"
        )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise InventoryReviewGateError(
            "worktree/index must be clean; ignored diagnostics may remain private"
        )


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
