"""Small detector test doubles for replay/CLI integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..capture import Frame
from ..contracts import Observation
from .detector import DetectorMetadata

__all__ = ["EmptyDetector", "build_empty_detector", "empty_detector"]


@dataclass(frozen=True, slots=True)
class EmptyDetector:
    """A conforming detector that intentionally produces no observations."""

    metadata: DetectorMetadata = field(
        default_factory=lambda: DetectorMetadata("empty", "1.0.0")
    )

    def detect(self, frame: Frame) -> tuple[Observation, ...]:
        del frame
        return ()


def build_empty_detector() -> EmptyDetector:
    """Return an empty detector through a zero-argument CLI factory."""
    return EmptyDetector()


empty_detector = EmptyDetector()
