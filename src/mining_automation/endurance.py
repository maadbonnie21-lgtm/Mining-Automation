"""Five-cycle Varrock East iron endurance orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .full_cycle import (
    FullCycleError,
    _require_banking,
    _require_mining,
    _require_return,
    _require_route_to_bank,
    _run_phase,
)


def _phase(root: Path, cycle: int, index: int, name: str) -> Path:
    return root / f"cycle-{cycle:02d}" / f"{index:02d}-{name}"


def run_endurance(
    *,
    root: Path,
    python: Path,
    hwnd: int,
    title: str,
    sha: str,
    output: Path,
    cycles: int = 5,
) -> dict[str, Any]:
    if type(cycles) is not int or not 3 <= cycles <= 5:
        raise FullCycleError("cycles_must_be_between_three_and_five")
    root, output = root.resolve(), output.resolve()
    if output.exists():
        raise FullCycleError("endurance_output_must_be_new")
    output.mkdir(parents=True, exist_ok=False)
    completed: list[dict[str, Any]] = []
    phase_receipts: list[dict[str, Any]] = []

    def progress(status: str, phase: str, error: str | None = None) -> None:
        payload = {
            "status": status,
            "current_phase": phase,
            "git_sha": sha,
            "cycles_requested": cycles,
            "cycles_completed": len(completed),
            "completed_cycles": completed,
            "phase_receipts": phase_receipts,
            "updated_at_unix": time.time(),
            "error": error,
        }
        tmp = output / "progress.json.tmp"
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, output / "progress.json")

    def run_phase(**kwargs: Any) -> Any:
        progress("RUNNING", kwargs["name"])
        try:
            result = _run_phase(**kwargs)
            if result.payload.get("git_sha") != sha:
                raise FullCycleError("phase_build_does_not_match_frozen_run")
            phase_receipts.append(
                {
                    "name": result.name,
                    "result_path": str(result.result_path.resolve()),
                    "sha256": hashlib.sha256(result.result_path.read_bytes()).hexdigest(),
                }
            )
            return result
        except BaseException as exc:
            progress("STOP", kwargs["name"], f"{type(exc).__name__}:{exc}")
            raise

    for cycle in range(1, cycles + 1):
        print(f"[ENDURANCE] CYCLE {cycle}/{cycles} START", flush=True)
        mining = _phase(output, cycle, 1, "mine-28")
        mine_result = run_phase(
            name=f"cycle-{cycle}-mine-28",
            root=root,
            result_path=mining / "result.json",
            command=[
                str(python),
                "-I",
                str(root / "tools/run_mining_to_full_safe.py"),
                "--live",
                "--hwnd",
                str(hwnd),
                "--authorize-execution-sha",
                sha,
                "--confirm",
                "MINE_TO_FULL_28_FAIL_CLOSED",
                "--title",
                title,
                "--output",
                str(mining),
            ],
        )
        _require_mining(mine_result.payload, label=f"cycle_{cycle}_mining")

        outbound = _phase(output, cycle, 2, "mine-to-bank")
        route_result = run_phase(
            name=f"cycle-{cycle}-mine-to-bank",
            root=root,
            result_path=outbound / "result.json",
            command=[
                str(python),
                "-I",
                str(root / "tools/run_mine_to_bank.py"),
                "--live",
                "--hwnd",
                str(hwnd),
                "--title",
                title,
                "--authorize-execution-sha",
                sha,
                "--confirm",
                "RUN_MINE_TO_BANK_NO_RESIZE",
                "--focus-existing",
                "--output",
                str(outbound),
            ],
        )
        _require_route_to_bank(route_result.payload)

        banking = _phase(output, cycle, 3, "bank-deposit-close")
        bank_result = run_phase(
            name=f"cycle-{cycle}-bank",
            root=root,
            result_path=banking / "result.json",
            command=[
                str(python),
                "-I",
                str(root / "tools/run_bank_iron.py"),
                "--live",
                "--hwnd",
                str(hwnd),
                "--title",
                title,
                "--sha",
                sha,
                "--confirm",
                "DEPOSIT_IRON_AND_CLOSE_BANK",
                "--output",
                str(banking),
            ],
        )
        _require_banking(bank_result.payload)

        returning = _phase(output, cycle, 4, "bank-to-mine")
        return_result = run_phase(
            name=f"cycle-{cycle}-bank-to-mine",
            root=root,
            result_path=returning / "result.json",
            command=[
                str(python),
                "-I",
                str(root / "tools/run_bank_to_mine.py"),
                "--live",
                "--hwnd",
                str(hwnd),
                "--title",
                title,
                "--authorize-execution-sha",
                sha,
                "--confirm",
                "RUN_BANK_TO_MINE_NO_RESIZE",
                "--focus-existing",
                "--output",
                str(returning),
            ],
        )
        _require_return(return_result.payload)

        completed.append(
            {
                "cycle": cycle,
                "mining_result": str(mine_result.result_path),
                "route_result": str(route_result.result_path),
                "bank_result": str(bank_result.result_path),
                "return_result": str(return_result.result_path),
                "mined_ore": 28,
                "deposited_ore": 28,
                "returned_empty": True,
            }
        )
        print(f"[ENDURANCE] CYCLE {cycle}/{cycles} PASS", flush=True)
        progress("RUNNING", f"cycle-{cycle}-complete")

    payload = {
        "status": "PASS",
        "success": True,
        "stop_reason": f"{cycles}_consecutive_cycles_complete",
        "git_sha": sha,
        "cycles_requested": cycles,
        "cycles_completed": len(completed),
        "total_ore_mined": 28 * len(completed),
        "total_ore_deposited": 28 * len(completed),
        "final_inventory": 0,
        "final_location": "mine_start",
        "operator_chose_gameplay_clicks": False,
        "cycles": completed,
        "phase_receipts": phase_receipts,
    }
    (output / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    progress("PASS", "all_requested_cycles_complete")
    return payload
