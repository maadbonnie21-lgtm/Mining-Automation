from __future__ import annotations

from types import SimpleNamespace

import pytest

from mining_automation.beta_interaction import InputExpired
from mining_automation.beta_mining import run_with_fresh_expiry, zero_click_expiry
from mining_automation.mining_loop_runtime import MiningLoopResult, MiningLoopStopReason
from mining_automation.mining_slice import MiningOnlyPhase, MiningOnlyStopReason


def result(start=0, end=0, *, expired=True, success=False):
    return MiningLoopResult(
        success=success,
        phase=MiningOnlyPhase.COMPLETE if success else MiningOnlyPhase.STOPPED,
        stop_reason=MiningLoopStopReason.INVENTORY_FULL
        if success
        else MiningLoopStopReason.BACKEND_ERROR,
        state_stop_reason=MiningOnlyStopReason.INVENTORY_FULL
        if success
        else MiningOnlyStopReason.PUBLICATION_BLOCKED,
        start_inventory=start,
        end_inventory=end,
        verified_ores=end - start,
        click_count=end - start,
        attempt_count=end - start + int(expired),
        target_sequence=tuple("iron" for _ in range(end - start)),
        dispatch_ids=tuple(f"id-{i}" for i in range(start, end)),
        events=(),
        final_state=None,
        detail="backend raised InputExpired: source_expired_before_down"
        if expired
        else "other_backend_error",
    )


def test_expiry_marker_discards_old_point_and_registration():
    class Backend:
        _selected = "old_sample"
        active_registration = {"pose": "old", "detector": "old"}

        @zero_click_expiry
        def hover(self):
            raise InputExpired("stale")

    backend = Backend()
    with pytest.raises(InputExpired):
        backend.hover()
    assert backend._zero_click_expired
    assert backend._selected is None
    assert backend.active_registration == {"pose": None, "detector": None}


def test_only_two_fresh_reacquisitions_without_progress():
    backend = SimpleNamespace(_zero_click_expired=False)
    calls = []

    def existing(b, c):
        calls.append("fresh_existing_loop")
        b._zero_click_expired = True
        return result()

    stopped = run_with_fresh_expiry(backend, None, existing)
    assert len(calls) == 3
    assert not stopped.success and stopped.click_count == 0
    assert len(stopped.events) == 2
    assert "reacquisitions=2" in stopped.detail


def test_reacquisition_preserves_prior_actual_gain_and_dispatch_counts():
    backend = SimpleNamespace(_zero_click_expired=False)
    sequence = iter([result(0, 5), result(5, 28, expired=False, success=True)])

    def existing(b, c):
        value = next(sequence)
        b._zero_click_expired = not value.success
        return value

    complete = run_with_fresh_expiry(backend, None, existing)
    assert complete.success
    assert complete.start_inventory == 0 and complete.end_inventory == 28
    assert complete.verified_ores == complete.click_count == 28
    assert len(set(complete.dispatch_ids)) == 28
    assert len(complete.events) == 1


@pytest.mark.parametrize("marked,expired", [(False, True), (True, False), (False, False)])
def test_generic_failure_never_gets_automatic_restart(marked, expired):
    backend = SimpleNamespace(_zero_click_expired=marked)
    calls = []

    def existing(b, c):
        calls.append("once")
        return result(expired=expired)

    stopped = run_with_fresh_expiry(backend, None, existing)
    assert calls == ["once"]
    assert not stopped.success
    assert not stopped.events


def test_verified_progress_resets_only_the_expiry_retry_budget():
    backend = SimpleNamespace(_zero_click_expired=False)
    values = iter(
        [result(0, 0), result(0, 1), result(1, 1), result(1, 28, expired=False, success=True)]
    )

    def existing(b, c):
        value = next(values)
        b._zero_click_expired = not value.success
        return value

    complete = run_with_fresh_expiry(backend, None, existing)
    assert complete.success and complete.verified_ores == 28
    assert len(complete.events) == 3
