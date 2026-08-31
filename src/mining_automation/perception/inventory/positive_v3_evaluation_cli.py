"""Exact-clean-head CLI for the non-activating inventory V3 report."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from .positive_v3_evaluation import (
    InventoryPositiveV3EvaluationError,
    evaluate_inventory_positive_v3,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Inventory Positive Classifier V3 against pinned development "
            "regressions. A passing report never authorizes activation."
        )
    )
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-head", required=True)
    return parser


def _git_output(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise InventoryPositiveV3EvaluationError(f"Git command failed: {detail}")
    return completed.stdout.strip()


def _verify_clean_head(expected_head: str) -> str:
    if (
        len(expected_head) != 40
        or any(character not in "0123456789abcdef" for character in expected_head)
    ):
        raise InventoryPositiveV3EvaluationError(
            "--expected-head must be an exact lowercase 40-character Git SHA"
        )
    actual = _git_output("rev-parse", "HEAD")
    if actual != expected_head:
        raise InventoryPositiveV3EvaluationError(
            f"Git HEAD mismatch: expected {expected_head}, got {actual}"
        )
    dirty = _git_output("status", "--porcelain=v1")
    if dirty:
        raise InventoryPositiveV3EvaluationError(
            "worktree changes prevent exact-clean-head V3 development evidence"
        )
    return actual


def _write_report(output: Path, report_json: str) -> tuple[Path, str]:
    try:
        output.mkdir(parents=True, exist_ok=False)
        report_path = output / "inventory-positive-v3-development-report.json"
        report_path.write_text(report_json, encoding="utf-8", newline="\n")
        digest = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        sidecar = output / "inventory-positive-v3-development-report.sha256"
        sidecar.write_text(
            f"{digest}  {report_path.name}\n",
            encoding="ascii",
            newline="\n",
        )
    except OSError as exc:
        raise InventoryPositiveV3EvaluationError(
            f"cannot write V3 report directory {output}: {exc}"
        ) from exc
    return report_path, digest


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        head = _verify_clean_head(args.expected_head)
        report = evaluate_inventory_positive_v3(args.fixture, git_head_sha=head)
        report_path, digest = _write_report(args.output, report.to_json())
    except (InventoryPositiveV3EvaluationError, OSError, ValueError) as exc:
        print(f"inventory positive V3 development evaluation failed: {exc}", file=sys.stderr)
        return 2
    status = "PASS" if report.development_regressions_passed else "FAIL"
    print(f"Inventory positive V3 development regressions: {status}")
    print(f"Validation status: {report.validation_status}")
    print("Activation allowed: false")
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {digest}")
    return 0 if report.development_regressions_passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
