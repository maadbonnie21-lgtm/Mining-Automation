#!/usr/bin/env python3
"""Run the verified bank-to-mine return route with no window/camera mutation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mining_automation.navigation.return_route import (  # noqa: E402
    result_payload,
    run_bank_to_mine,
)

CONFIRM = "RUN_BANK_TO_MINE_NO_RESIZE"


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
    parser.add_argument("--focus-existing", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if not args.live or args.confirm != CONFIRM:
        parser.error(f"Live return requires --confirm {CONFIRM}")
    if args.authorize_execution_sha != head or dirty:
        parser.error("Exact clean committed checkout SHA required")
    if args.output.exists():
        parser.error("Output directory must be new")

    profile = ROOT / "src/mining_automation/navigation/profiles/varrock_east/route.json"
    tracked = [
        Path(__file__),
        ROOT / "src/mining_automation/navigation/return_route.py",
        profile,
        profile.parent / "terrain.npz",
    ]
    for path in tracked:
        git("ls-files", "--error-unmatch", path.relative_to(ROOT).as_posix())

    result = run_bank_to_mine(
        hwnd=args.hwnd,
        title=args.title,
        output=args.output,
        profile=profile,
        focus_existing=args.focus_existing,
    )
    payload = result_payload(result)
    payload["git_sha"] = head
    payload["expected_title"] = args.title
    payload["window_unchanged"] = result.start_window == result.end_window
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "result.json"
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "success": payload["success"],
                "stop_reason": payload["stop_reason"],
                "click_count": payload["click_count"],
                "evidence": str(result_path),
            },
            indent=2,
        )
    )
    return 0 if payload["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
