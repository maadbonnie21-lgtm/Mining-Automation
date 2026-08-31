"""CLI for the read-only inventory-positive V3 perimeter forensics."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .positive_v3_perimeter_forensics import (
    InventoryPositiveV3PerimeterForensicError,
    analyze_inventory_positive_v3_perimeter,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Map and classify the exact recurrent slot-1 perimeter pixels from "
            "the frozen 16-case sanitized inventory corpus."
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
        raise InventoryPositiveV3PerimeterForensicError(
            f"Git command failed: {detail}"
        )
    return completed.stdout.strip()


def _verify_clean_head(expected_head: str) -> str:
    if (
        len(expected_head) != 40
        or any(character not in "0123456789abcdef" for character in expected_head)
    ):
        raise InventoryPositiveV3PerimeterForensicError(
            "--expected-head must be an exact lowercase 40-character Git SHA"
        )
    actual = _git_output("rev-parse", "HEAD")
    if actual != expected_head:
        raise InventoryPositiveV3PerimeterForensicError(
            f"Git HEAD mismatch: expected {expected_head}, got {actual}"
        )
    dirty = _git_output("status", "--porcelain=v1")
    if dirty:
        raise InventoryPositiveV3PerimeterForensicError(
            "tracked worktree changes prevent exact-head forensic evidence"
        )
    return actual


def _write_report(output: Path, report_json: str, digest: str) -> Path:
    try:
        output.mkdir(parents=True, exist_ok=False)
        report_path = output / "inventory-positive-v3-perimeter-forensics.json"
        report_path.write_text(report_json, encoding="utf-8", newline="\n")
        (output / "inventory-positive-v3-perimeter-forensics.sha256").write_text(
            f"{digest}  {report_path.name}\n",
            encoding="ascii",
            newline="\n",
        )
    except OSError as exc:
        raise InventoryPositiveV3PerimeterForensicError(
            f"cannot write forensic report directory {output}: {exc}"
        ) from exc
    return report_path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        head = _verify_clean_head(args.expected_head)
        report = analyze_inventory_positive_v3_perimeter(
            args.fixture,
            git_head_sha=head,
        )
        report_path = _write_report(
            args.output,
            report.to_json(),
            report.report_sha256,
        )
    except (InventoryPositiveV3PerimeterForensicError, OSError, ValueError) as exc:
        print(f"inventory V3 perimeter forensics failed: {exc}", file=sys.stderr)
        return 2
    print(f"Inventory V3 perimeter signal: {report.conclusion.value}")
    print("Validation status: independent-campaign-required")
    print("Activation allowed: false")
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {report.report_sha256}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
