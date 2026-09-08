"""Orchestrator tests; these are NOT live cycle proof."""

import json
from pathlib import Path

import pytest

from mining_automation import endurance
from mining_automation.full_cycle import FullCycleError, PhaseResult


def install_fake(monkeypatch, output, fail=None, wrong_sha=None):
    called = []

    def phase(**kw):
        name = kw["name"]
        status = json.loads((output / "progress.json").read_text())
        assert status["status"] == "RUNNING" and status["current_phase"] == name
        called.append(name)
        if name == fail:
            raise FullCycleError("simulated_live_stop")
        value = {"success": True, "status": "PASS", "git_sha": "a" * 40}
        mixed = name.startswith("cycle-2-")
        iron_count = 27 if mixed else 28
        gem_count = 1 if mixed else 0
        gem_ids = ["uncut_ruby"] if mixed else []
        if name.endswith("-mine-28"):
            value.update(
                start_inventory=0,
                end_inventory=28,
                start_iron=0,
                start_gems=0,
                end_iron=iron_count,
                end_gems=gem_count,
                end_gem_item_ids=gem_ids,
                verified_ores=iron_count,
                verified_gems=gem_count,
                stop_reason="inventory_full",
            )
        elif name.endswith("-mine-to-bank"):
            value.update(completed_checkpoints=["bank_interior_endpoint"], item_actions=0)
        elif name.endswith("-bank-to-mine"):
            value.update(completed_checkpoints=["mine_start"], window_unchanged=True)
        else:
            value.update(
                before_occupied_count=28,
                before_ore_count=iron_count,
                before_gem_count=gem_count,
                before_gem_item_ids=gem_ids,
                deposited_ore_count=iron_count,
                deposited_gem_count=gem_count,
                deposited_gem_item_ids=gem_ids,
                after_occupied_count=0,
                after_ore_count=0,
                after_gem_count=0,
                deposit_verified=True,
                bank_closed_verified=True,
            )
        if name == wrong_sha:
            value["git_sha"] = "b" * 40
        path = kw["result_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return PhaseResult(name, path, value)

    monkeypatch.setattr(endurance, "_run_phase", phase)
    return called


def run(tmp_path, cycles=3):
    return endurance.run_endurance(
        root=tmp_path,
        python=Path("python"),
        hwnd=42,
        title="RuneLite - Test",
        sha="a" * 40,
        output=tmp_path / "session",
        cycles=cycles,
    )


def test_three_cycles_require_all_twelve_fresh_phases(tmp_path, monkeypatch):
    calls = install_fake(monkeypatch, tmp_path / "session")
    result = run(tmp_path)
    assert result["success"] and result["cycles_completed"] == 3
    assert result["total_ore_mined"] == result["total_ore_deposited"] == 83
    assert result["total_gems_mined"] == result["total_gems_deposited"] == 1
    assert len(calls) == len(set(calls)) == 12
    assert len(result["phase_receipts"]) == 12
    assert result["stop_reason"] == "3_consecutive_cycles_complete"
    status = json.loads((tmp_path / "session/progress.json").read_text())
    assert status["status"] == "PASS" and status["cycles_completed"] == 3


def test_second_cycle_failure_preserves_one_complete_cycle(tmp_path, monkeypatch):
    calls = install_fake(monkeypatch, tmp_path / "session", fail="cycle-2-bank")
    with pytest.raises(FullCycleError, match="simulated_live_stop"):
        run(tmp_path)
    state = json.loads((tmp_path / "session/progress.json").read_text())
    assert state["status"] == "STOP" and state["cycles_completed"] == 1
    assert calls[-1] == "cycle-2-bank" and len(calls) == 7
    assert not (tmp_path / "session/result.json").exists()


def test_phase_from_different_build_stops_streak(tmp_path, monkeypatch):
    calls = install_fake(monkeypatch, tmp_path / "session", wrong_sha="cycle-1-mine-to-bank")
    with pytest.raises(FullCycleError, match="frozen_run"):
        run(tmp_path)
    state = json.loads((tmp_path / "session/progress.json").read_text())
    assert state["cycles_completed"] == 0 and state["status"] == "STOP"
    assert len(calls) == 2


@pytest.mark.parametrize("count", [0, 1, 2, 6, True])
def test_out_of_scope_count_never_launches(tmp_path, count):
    with pytest.raises(FullCycleError, match="cycles_must_be"):
        run(tmp_path, count)
