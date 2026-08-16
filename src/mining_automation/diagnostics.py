from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    event_type: str
    message: str
    timestamp_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    data: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventSink:
    """Minimal sink contract; durable sinks/replay are later milestones."""

    def emit(self, event: DiagnosticEvent) -> None:
        raise NotImplementedError


class InMemoryEventSink(EventSink):
    def __init__(self) -> None:
        self.events: list[DiagnosticEvent] = []

    def emit(self, event: DiagnosticEvent) -> None:
        self.events.append(event)
