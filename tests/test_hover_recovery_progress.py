"""Regression tests for recovery across productive mining segments, not live proof."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_mining_to_full_safe as safe  # noqa: E402

from mining_automation.mining_loop_runtime import (  # noqa: E402
    MiningLoopConfig,
    MiningLoopResult,
    MiningLoopStopReason,
)
from mining_automation.mining_slice import MiningOnlyPhase, MiningOnlyStopReason  # noqa: E402

TARGET = "varrock-east-iron-northwest"


def _segment(
    start: int,
    end: int,
    *,
    reason: MiningLoopStopReason,
    start_iron: int | None = None,
    end_iron: int | None = None,
    start_gems: int = 0,
    end_gems: int = 0,
) -> MiningLoopResult:
    start_iron = start if start_iron is None else start_iron
    end_iron = end if end_iron is None else end_iron
    iron_gain = end_iron - start_iron
    gem_gain = end_gems - start_gems
    success = reason is MiningLoopStopReason.INVENTORY_FULL
    return MiningLoopResult(
        success=success,
        phase=MiningOnlyPhase.COMPLETE if success else MiningOnlyPhase.STOPPED,
        stop_reason=reason,
        state_stop_reason=(
            MiningOnlyStopReason.INVENTORY_FULL
            if success else MiningOnlyStopReason.ATTEMPT_RECEIPT_INVALID
        ),
        start_inventory=start,
        end_inventory=end,
        start_iron=start_iron,
        end_iron=end_iron,
        start_gems=start_gems,
        end_gems=end_gems,
        start_gem_item_ids=("uncut_ruby",) * start_gems,
        end_gem_item_ids=("uncut_ruby",) * end_gems,
        verified_ores=iron_gain,
        verified_gems=gem_gain,
        click_count=iron_gain,
        attempt_count=iron_gain,
        target_sequence=(TARGET,) * iron_gain,
        dispatch_ids=tuple(f"dispatch-{ore}" for ore in range(start_iron, end_iron)),
        events=({"kind": "hover_proof", "target_id": TARGET},) if not success else (),
        final_state=None,
        detail="synthetic segment",
    )


def test_hover_recovery_preserves_first_composition_and_aggregates_gems() -> None:
    segments = iter(
        (
            _segment(
                0,
                8,
                reason=MiningLoopStopReason.HOVER_ACTION_UNPROVEN,
                start_iron=0,
                end_iron=7,
                start_gems=0,
                end_gems=1,
            ),
            _segment(
                8,
                28,
                reason=MiningLoopStopReason.INVENTORY_FULL,
                start_iron=7,
                end_iron=27,
                start_gems=1,
                end_gems=1,
            ),
        )
    )
    result = safe._run_with_hover_recovery(
        _backend(),
        MiningLoopConfig(session_id="mixed-recovery", expected_hwnd=42),
        lambda *_: next(segments),
    )
    assert result.success
    assert (result.start_inventory, result.end_inventory) == (0, 28)
    assert (result.start_iron, result.start_gems, result.start_gem_item_ids) == (0, 0, ())
    assert (result.end_iron, result.end_gems, result.end_gem_item_ids) == (
        27,
        1,
        ("uncut_ruby",),
    )
    assert (result.verified_ores, result.verified_gems) == (27, 1)
    assert (result.click_count, result.attempt_count) == (27, 27)


def _backend() -> safe.SafeWindowsMiningToFullBackend:
    # Exercise recovery bookkeeping without constructing a Windows input device.
    backend = object.__new__(safe.SafeWindowsMiningToFullBackend)
    backend._hover_rejections = {}
    backend._last_status = None
    backend.active_registration = {"pose": "previous-pose", "detector": object()}
    return backend


def test_productive_0_to_28_does_not_exhaust_lifetime_hover_misses() -> None:
    segments = []
    for count in range(28):
        segments.append(_segment(count, count, reason=MiningLoopStopReason.HOVER_ACTION_UNPROVEN))
        segments.append(_segment(
            count, count + 1,
            reason=(MiningLoopStopReason.INVENTORY_FULL if count == 27
                    else MiningLoopStopReason.HOVER_ACTION_UNPROVEN),
        ))
    iterator = iter(segments)
    backend = _backend()
    result = safe._run_with_hover_recovery(
        backend, MiningLoopConfig(session_id="recovery", expected_hwnd=42),
        lambda *_: next(iterator),
    )
    assert result.success
    assert (result.start_inventory, result.end_inventory) == (0, 28)
    assert (result.verified_ores, result.click_count, result.attempt_count) == (28, 28, 28)
    assert len(set(result.dispatch_ids)) == 28
    events = [e for e in result.events if e["kind"] == "hover_action_reacquire"]
    assert len(events) == 55  # All recorded; only consecutive no-progress misses are limited.
    assert max(e["misses_since_progress"] for e in events) == 2
    assert not backend._hover_rejections


def test_persistent_hover_failure_still_never_clicks_or_invents_ore() -> None:
    calls = []

    def failed_segment(*_):
        calls.append(True)
        return _segment(16, 16, reason=MiningLoopStopReason.HOVER_ACTION_UNPROVEN)

    result = safe._run_with_hover_recovery(
        _backend(), MiningLoopConfig(session_id="no-progress", expected_hwnd=42,
                                     max_passive_observations=3), failed_segment,
    )
    assert not result.success
    assert len(calls) == 3
    assert result.click_count == result.verified_ores == 0
    assert result.end_inventory == 16
    assert "3 consecutive" in result.detail


def test_rejected_hover_discards_cached_geometry_but_preserves_target_penalty() -> None:
    backend = _backend()
    backend.note_hover_rejection(TARGET)
    assert backend.active_registration == {"pose": None, "detector": None}
    assert backend._hover_rejections == {TARGET: 1}


def test_verified_gain_clears_old_hover_penalties() -> None:
    backend = _backend()
    backend.note_hover_rejection(TARGET)
    backend.note_verified_progress()
    assert not backend._hover_rejections


@pytest.mark.parametrize("reason", [
    MiningLoopStopReason.HOVER_PROOF_MISMATCH,
    MiningLoopStopReason.REACQUISITION_BLOCKED,
    MiningLoopStopReason.BACKEND_ERROR,
])
def test_other_failures_are_not_relabelled_as_recoverable(reason) -> None:
    calls = []

    def terminal(*_):
        calls.append(True)
        return _segment(16, 16, reason=reason)

    result = safe._run_with_hover_recovery(
        _backend(), MiningLoopConfig(session_id="terminal", expected_hwnd=42), terminal,
    )
    assert not result.success
    assert result.stop_reason is reason
    assert len(calls) == 1
    assert result.click_count == 0


def test_visible_status_reports_changes_without_repeating_identical_waits(capsys) -> None:
    backend = _backend()
    observation = SimpleNamespace(
        state=SimpleNamespace(
            inventory=SimpleNamespace(occupied_slots=16), status="ready",
            stop_reason=MiningOnlyStopReason.NONE,
        ), pose_id="at_center",
    )
    backend._report_observation(observation)
    backend._report_observation(observation)
    output = capsys.readouterr().out
    assert output.count("Inventory 16/28") == 1
    observation.state.inventory.occupied_slots = None
    observation.state.status = "blocked"
    observation.state.stop_reason = MiningOnlyStopReason.RESOURCE_VIEW_NOT_SUPPORTED
    backend._report_observation(observation)
    output = capsys.readouterr().out
    assert "resource_view_not_supported" in output
    assert "Inventory" not in output
