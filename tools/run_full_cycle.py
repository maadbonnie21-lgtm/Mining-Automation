#!/usr/bin/env python3
"""Run bank->mine->28->bank->deposit->mine->28 as one exact-build live test."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mining_automation.full_cycle import FullCycleError, run_full_cycle  # noqa: E402

CONFIRM = "RUN_FULL_CYCLE_TWO_LOADS"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--authorize-execution-sha", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if not args.live or args.confirm != CONFIRM:
        parser.error(f"Live full cycle requires --confirm {CONFIRM}")
    if args.authorize_execution_sha != head or dirty:
        parser.error("Exact clean committed checkout SHA required")
    if args.output.exists():
        parser.error("Output directory must be new")

    tracked = [
        "tools/run_full_cycle.py",
        "src/mining_automation/full_cycle.py",
        "tools/run_bank_to_mine.py",
        "src/mining_automation/navigation/return_route.py",
    ]
    for path in tracked:
        git("ls-files", "--error-unmatch", path)
    try:
        payload = run_full_cycle(
            root=ROOT,
            python=Path(sys.executable),
            hwnd=args.hwnd,
            title=args.title,
            sha=head,
            output=args.output,
        )
    except (FullCycleError, KeyboardInterrupt) as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "STOP",
            "success": False,
            "stop_reason": f"{type(exc).__name__}:{exc}",
            "git_sha": head,
            "evidence_origin": "standalone_full_cycle_program",
        }
        (args.output / "result.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
