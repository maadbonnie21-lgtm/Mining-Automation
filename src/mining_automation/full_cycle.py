"""Fail-closed two-load Varrock East iron mining cycle orchestrator."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FullCycleError(RuntimeError):
    """A required live phase failed or did not prove its expected outcome."""


@dataclass(frozen=True)
class PhaseResult:
    name: str
    result_path: Path
    payload: dict[str, Any]


def _load_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FullCycleError(f"missing_result:{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FullCycleError(f"invalid_result:{path}")
    return payload


def _run_phase(
    *,
    name: str,
    command: list[str],
    result_path: Path,
    root: Path,
) -> PhaseResult:
    print(f"[FULL-CYCLE] START {name}", flush=True)
    completed = subprocess.run(command, cwd=root)
    payload = _load_result(result_path)
    print(
        f"[FULL-CYCLE] END {name}: rc={completed.returncode} "
        f"status={payload.get('status')} success={payload.get('success')}",
        flush=True,
    )
    if completed.returncode != 0 or payload.get("success") is not True:
        raise FullCycleError(
            f"phase_failed:{name}:{payload.get('stop_reason') or payload.get('reason')}"
        )
    return PhaseResult(name, result_path, payload)


def _require_mining(payload: dict[str, Any], *, label: str) -> None:
    if payload.get("start_inventory") != 0 or payload.get("end_inventory") != 28:
        raise FullCycleError(f"{label}_inventory_transition_unproven")
    if payload.get("stop_reason") != "inventory_full":
        raise FullCycleError(f"{label}_did_not_stop_at_full_inventory")
    start_iron = payload.get("start_iron")
    start_gems = payload.get("start_gems")
    end_iron = payload.get("end_iron")
    end_gems = payload.get("end_gems")
    gem_ids = payload.get("end_gem_item_ids")
    if (
        start_iron != 0
        or start_gems != 0
        or type(end_iron) is not int
        or type(end_gems) is not int
        or end_iron < 0
        or end_gems < 0
        or end_iron + end_gems != 28
        or type(gem_ids) is not list
        or len(gem_ids) != end_gems
        or any(item_id != "uncut_ruby" for item_id in gem_ids)
        or payload.get("verified_ores") != end_iron
        or payload.get("verified_gems") != end_gems
    ):
        raise FullCycleError(f"{label}_item_composition_unproven")


def _require_route_to_bank(payload: dict[str, Any]) -> None:
    checkpoints = payload.get("completed_checkpoints") or []
    if not checkpoints or checkpoints[-1] != "bank_interior_endpoint":
        raise FullCycleError("mine_to_bank_endpoint_unproven")
    if payload.get("bank_interface_opened") is True or payload.get("item_actions") not in (0, None):
        raise FullCycleError("navigation_crossed_banking_boundary")


def _require_banking(
    payload: dict[str, Any],
    *,
    mining_payload: dict[str, Any],
) -> None:
    expected_iron = mining_payload.get("end_iron")
    expected_gems = mining_payload.get("end_gems")
    expected_gem_ids = mining_payload.get("end_gem_item_ids")
    if (
        payload.get("before_occupied_count") != 28
        or payload.get("before_ore_count") != expected_iron
        or payload.get("before_gem_count") != expected_gems
        or payload.get("before_gem_item_ids") != expected_gem_ids
        or payload.get("deposited_ore_count") != expected_iron
        or payload.get("deposited_gem_count") != expected_gems
        or payload.get("deposited_gem_item_ids") != expected_gem_ids
        or payload.get("after_occupied_count") != 0
        or payload.get("after_ore_count") != 0
        or payload.get("after_gem_count") != 0
    ):
        raise FullCycleError("banking_full_mining_load_to_empty_unproven")
    if payload.get("deposit_verified") is not True:
        raise FullCycleError("deposit_not_verified")
    if payload.get("bank_closed_verified") is not True:
        raise FullCycleError("bank_close_not_verified")


def _require_return(payload: dict[str, Any]) -> None:
    checkpoints = payload.get("completed_checkpoints") or []
    if not checkpoints or checkpoints[-1] != "mine_start":
        raise FullCycleError("bank_to_mine_endpoint_unproven")
    if payload.get("window_unchanged") is not True:
        raise FullCycleError("return_route_window_changed")


def run_full_cycle(
    *,
    root: Path,
    python: Path,
    hwnd: int,
    title: str,
    sha: str,
    output: Path,
    prior_return: Path | None = None,
) -> dict[str, Any]:
    """Run bank->mine->28->bank->deposit->mine->28 as one fail-closed sequence."""
    if output.exists():
        raise FullCycleError("full_cycle_output_must_be_new")
    output.mkdir(parents=True, exist_ok=False)
    phases: list[PhaseResult] = []

    def phase_dir(index: int, name: str) -> Path:
        return output / f"{index:02d}-{name}"

    if prior_return is not None:
        prior_payload = _load_result(prior_return)
        _require_return(prior_payload)
        if prior_payload.get("operator_chose_walking_clicks") is not False:
            raise FullCycleError("prior_return_was_not_program_owned")
        phases.append(PhaseResult("return-to-mine", prior_return, prior_payload))
    else:
        return1 = phase_dir(0, "return-to-mine")
        phases.append(
            _run_phase(
                name="return-to-mine",
                root=root,
                result_path=return1 / "result.json",
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
                    str(return1),
                ],
            )
        )
        _require_return(phases[-1].payload)

    mine1 = phase_dir(1, "mine-first-28")
    phases.append(
        _run_phase(
            name="mine-first-28",
            root=root,
            result_path=mine1 / "result.json",
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
                str(mine1),
            ],
        )
    )
    _require_mining(phases[-1].payload, label="first_mining")
    first_mining_payload = phases[-1].payload

    outbound = phase_dir(2, "mine-to-bank")
    phases.append(
        _run_phase(
            name="mine-to-bank",
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
    )
    _require_route_to_bank(phases[-1].payload)

    banking = phase_dir(3, "bank-deposit-close")
    phases.append(
        _run_phase(
            name="bank-deposit-close",
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
    )
    _require_banking(phases[-1].payload, mining_payload=first_mining_payload)
    banking_payload = phases[-1].payload
    return2 = phase_dir(4, "return-to-mine-second")
    phases.append(
        _run_phase(
            name="return-to-mine-second",
            root=root,
            result_path=return2 / "result.json",
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
                str(return2),
            ],
        )
    )
    _require_return(phases[-1].payload)

    mine2 = phase_dir(5, "mine-second-28")
    phases.append(
        _run_phase(
            name="mine-second-28",
            root=root,
            result_path=mine2 / "result.json",
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
                str(mine2),
            ],
        )
    )
    _require_mining(phases[-1].payload, label="second_mining")
    second_mining_payload = phases[-1].payload
    payload = {
        "status": "PASS",
        "success": True,
        "stop_reason": "second_inventory_full_verified",
        "git_sha": sha,
        "hwnd": hwnd,
        "title": title,
        "phase_count": len(phases),
        "phases": [
            {
                "name": phase.name,
                "result_path": str(phase.result_path),
                "status": phase.payload.get("status"),
                "success": phase.payload.get("success"),
                "stop_reason": phase.payload.get("stop_reason") or phase.payload.get("reason"),
            }
            for phase in phases
        ],
        "final_inventory": 28,
        "final_iron": second_mining_payload["end_iron"],
        "final_gems": second_mining_payload["end_gems"],
        "final_gem_item_ids": second_mining_payload["end_gem_item_ids"],
        "first_load_iron_mined": first_mining_payload["end_iron"],
        "first_load_gems_mined": first_mining_payload["end_gems"],
        "first_load_gem_item_ids": first_mining_payload["end_gem_item_ids"],
        "first_load_iron_deposited": banking_payload["deposited_ore_count"],
        "first_load_gems_deposited": banking_payload["deposited_gem_count"],
        "operator_chose_gameplay_clicks": False,
        "evidence_origin": "standalone_full_cycle_program",
    }
    (output / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("[FULL-CYCLE] PASS second inventory 28/28 verified", flush=True)
    return payload
