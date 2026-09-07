#!/usr/bin/env python3
"""Run five complete mine->bank->deposit->mine cycles from a verified mine start."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mining_automation.endurance import run_endurance  # noqa: E402
from mining_automation.full_cycle import FullCycleError  # noqa: E402

CONFIRM = "RUN_FIVE_COMPLETE_CYCLES"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
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
        parser.error(f"Live endurance requires --confirm {CONFIRM}")
    if args.authorize_execution_sha != head or dirty:
        parser.error("Exact clean committed checkout SHA required")
    if args.output.exists():
        parser.error("Output directory must be new")

    try:
        payload = run_endurance(
            root=ROOT,
            python=Path(sys.executable),
            hwnd=args.hwnd,
            title=args.title,
            sha=head,
            output=args.output,
            cycles=5,
        )
    except (FullCycleError, KeyboardInterrupt) as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        progress = args.output / "progress.json"
        completed = []
        if progress.is_file():
            completed = json.loads(progress.read_text(encoding="utf-8")).get("completed_cycles", [])
        payload = {
            "status": "STOP",
            "success": False,
            "stop_reason": f"{type(exc).__name__}:{exc}",
            "git_sha": head,
            "cycles_requested": 5,
            "cycles_completed": len(completed),
            "completed_cycles": completed,
            "operator_chose_gameplay_clicks": False,
        }
        (args.output / "result.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(payload, indent=2), flush=True)
    return 0 if payload.get("success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
