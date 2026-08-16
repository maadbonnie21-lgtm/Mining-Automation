from mining_automation.contracts import RoutineKind, RoutineSegment
from mining_automation.scheduling import RoutineScheduler


def test_scheduler_tracks_segments_and_completion() -> None:
    scheduler = RoutineScheduler(
        [
            RoutineSegment(RoutineKind.ACTIVE, 10.0),
            RoutineSegment(RoutineKind.INACTIVE, 5.0),
            RoutineSegment(RoutineKind.ACTIVE, 20.0),
        ]
    )

    first = scheduler.snapshot(4.0)
    assert first.segment_index == 0
    assert first.kind is RoutineKind.ACTIVE
    assert first.segment_remaining_s == 6.0
    assert first.total_remaining_s == 31.0
    assert first.complete is False

    second = scheduler.snapshot(12.0)
    assert second.segment_index == 1
    assert second.kind is RoutineKind.INACTIVE
    assert second.segment_remaining_s == 3.0

    done = scheduler.snapshot(35.0)
    assert done.complete is True
    assert done.total_remaining_s == 0.0
