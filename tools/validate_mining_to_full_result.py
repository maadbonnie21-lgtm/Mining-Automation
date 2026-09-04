#!/usr/bin/env python3
"""Validate one real mining-to-full result without granting authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.mining_full_proof import (
    MiningFullProofError,
    validate_mining_to_full_result,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="real run result.json")
    parser.add_argument(
        "--expected-git-sha",
        required=True,
        help="exact reviewed execution SHA expected in the result",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="optional new path for the deny-only validation receipt",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw = args.result.read_bytes()
        payload = json.loads(raw)
        receipt = validate_mining_to_full_result(
            payload,
            expected_git_sha=args.expected_git_sha,
            source_result_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except (OSError, json.JSONDecodeError, MiningFullProofError) as exc:
        print(f"MINING_TO_FULL_PROOF_REJECTED: {exc}", file=sys.stderr)
        return 2

    result = asdict(receipt)
    result.update(
        {
            "validation_status": "real_mining_to_full_proof_accepted",
            "input_authority": False,
            "navigation_authority": False,
            "banking_authority": False,
            "release_authority": False,
        }
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.receipt is not None:
        if args.receipt.exists():
            print(
                f"MINING_TO_FULL_PROOF_REJECTED: receipt already exists: {args.receipt}",
                file=sys.stderr,
            )
            return 2
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(encoded, encoding="utf-8")
        print(f"VALIDATION_RECEIPT={args.receipt}")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
