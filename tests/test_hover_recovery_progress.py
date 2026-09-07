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


def _segment(start: int, end: int, *, reason: MiningLoopStopReason) -> MiningLoopResult:
    gain = end - start
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
        verified_ores=gain,
        click_count=gain,
        attempt_count=gain,
        target_sequence=(TARGET,) * gain,
        dispatch_ids=tuple(f"dispatch-{ore}" for ore in range(start, end)),
        events=({"kind": "hover_proof", "target_id": TARGET},) if not success else (),
        final_state=None,
        detail="synthetic segment",
    )


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
