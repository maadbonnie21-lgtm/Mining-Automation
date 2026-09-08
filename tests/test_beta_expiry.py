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
        start_iron=start,
        end_iron=end,
        start_gems=0,
        end_gems=0,
        start_gem_item_ids=(),
        end_gem_item_ids=(),
        verified_gems=0,
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


def test_smooth_cursor_expiry_keeps_original_starts_and_all_gem_gains():
    from dataclasses import replace

    backend = SimpleNamespace(_zero_click_expired=False)
    first = replace(
        result(0, 8),
        start_iron=0,
        start_gems=0,
        end_iron=7,
        end_gems=1,
        end_gem_item_ids=("uncut_ruby",),
        verified_ores=7,
        verified_gems=1,
    )
    last = replace(
        result(8, 28, expired=False, success=True),
        start_iron=7,
        start_gems=1,
        start_gem_item_ids=("uncut_ruby",),
        end_iron=27,
        end_gems=1,
        end_gem_item_ids=("uncut_ruby",),
        verified_ores=20,
        verified_gems=0,
    )
    values = iter([first, last])

    def existing(b, c):
        item = next(values)
        b._zero_click_expired = not item.success
        return item

    actual = run_with_fresh_expiry(backend, None, existing)
    assert actual.success and actual.start_inventory == 0
    assert actual.start_iron == actual.start_gems == 0
    assert actual.start_gem_item_ids == ()
    assert actual.verified_ores == actual.end_iron == 27
    assert actual.verified_gems == actual.end_gems == 1
    assert actual.end_inventory == 28
    assert actual.end_gem_item_ids == ("uncut_ruby",)
