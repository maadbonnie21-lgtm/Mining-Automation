from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .contracts import RoutineKind, RoutineSegment


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    segment_index: int
    kind: RoutineKind
    segment_remaining_s: float
    total_remaining_s: float
    complete: bool


class RoutineScheduler:
    def __init__(self, segments: Sequence[RoutineSegment]) -> None:
        if not segments:
            raise ValueError("routine must contain at least one segment")
        self._segments = tuple(segments)
        self._total_duration_s = sum(segment.duration_s for segment in self._segments)

    @property
    def total_duration_s(self) -> float:
        return self._total_duration_s

    def snapshot(self, elapsed_s: float) -> ScheduleSnapshot:
        if elapsed_s < 0:
            raise ValueError("elapsed_s cannot be negative")

        remaining_elapsed = elapsed_s
        for index, segment in enumerate(self._segments):
            if remaining_elapsed < segment.duration_s:
                remaining_segment = segment.duration_s - remaining_elapsed
                return ScheduleSnapshot(
                    segment_index=index,
                    kind=segment.kind,
                    segment_remaining_s=remaining_segment,
                    total_remaining_s=max(0.0, self._total_duration_s - elapsed_s),
                    complete=False,
                )
            remaining_elapsed -= segment.duration_s

        final_index = len(self._segments) - 1
        return ScheduleSnapshot(
            segment_index=final_index,
            kind=self._segments[final_index].kind,
            segment_remaining_s=0.0,
            total_remaining_s=0.0,
            complete=True,
        )
