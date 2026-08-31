"""CLI for the frozen offline inventory-positive V2 campaign."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from .positive_v2_evaluation import (
    INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA,
    InventoryPositiveV2EvaluationError,
    evaluate_inventory_positive_v2,
)

_FROZEN_MODEL_PATHS = (
    "src/mining_automation/perception/inventory/configuration.py",
    "src/mining_automation/perception/inventory/positive_classifier_v2.py",
    "src/mining_automation/perception/inventory/positive_v2_calibration.py",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the frozen inventory-positive V2 model against the reviewed "
            "calibration and held-out sanitized campaigns."
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
        raise InventoryPositiveV2EvaluationError(f"Git command failed: {detail}")
    return completed.stdout.strip()


def _verify_clean_head(expected_head: str) -> str:
    if (
        len(expected_head) != 40
        or any(character not in "0123456789abcdef" for character in expected_head)
    ):
        raise InventoryPositiveV2EvaluationError(
            "--expected-head must be an exact lowercase 40-character Git SHA"
        )
    actual = _git_output("rev-parse", "HEAD")
    if actual != expected_head:
        raise InventoryPositiveV2EvaluationError(
            f"Git HEAD mismatch: expected {expected_head}, got {actual}"
        )
    dirty = _git_output("status", "--porcelain=v1", "--untracked-files=no")
    if dirty:
        raise InventoryPositiveV2EvaluationError(
            "tracked worktree changes prevent exact-head V2 evidence"
        )
    _git_output(
        "merge-base",
        "--is-ancestor",
        INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA,
        actual,
    )
    changed_model_paths = _git_output(
        "diff",
        "--name-only",
        f"{INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA}..{actual}",
        "--",
        *_FROZEN_MODEL_PATHS,
    )
    if changed_model_paths:
        raise InventoryPositiveV2EvaluationError(
            "V2 model files changed after the recorded freeze commit: "
            f"{changed_model_paths}"
        )
    return actual


def _write_report(output: Path, report_json: str) -> tuple[Path, str]:
    try:
        output.mkdir(parents=True, exist_ok=False)
        report_path = output / "inventory-positive-v2-report.json"
        report_path.write_text(report_json, encoding="utf-8", newline="\n")
        digest = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        (output / "inventory-positive-v2-report.sha256").write_text(
            f"{digest}  {report_path.name}\n",
            encoding="ascii",
            newline="\n",
        )
    except OSError as exc:
        raise InventoryPositiveV2EvaluationError(
            f"cannot write V2 report directory {output}: {exc}"
        ) from exc
    return report_path, digest


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        head = _verify_clean_head(args.expected_head)
        report = evaluate_inventory_positive_v2(args.fixture, git_head_sha=head)
        report_path, digest = _write_report(args.output, report.to_json())
    except (InventoryPositiveV2EvaluationError, OSError, ValueError) as exc:
        print(f"inventory positive V2 evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Inventory positive V2: {'PASS' if report.passed else 'FAIL'}")
    print(f"Calibration SHA-256: {report.calibration_evidence_sha256}")
    print(f"Model freeze: {report.model_freeze_git_sha}")
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {digest}")
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
