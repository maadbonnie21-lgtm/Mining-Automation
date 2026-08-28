"""Explicit coordinate spaces for validation-only RuneLite pointer mapping.

The reviewed camera-plan points are expressed in the DPI-unaware RuneLite
window's *logical client* coordinate space.  Windows pointer APIs consume
*physical screen* coordinates.  The two spaces cannot be mixed safely.

This module contains no Windows calls.  It sequences an injected transform
seam so the ordering and exact reverse round trip are testable on Linux.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "CameraCoordinateMapping",
    "CameraCoordinateTransform",
    "CameraDpiEnvironment",
    "CoordinateMappingError",
    "LogicalClientPoint",
    "LogicalScreenPoint",
    "PhysicalScreenPoint",
    "map_logical_client_point",
    "require_exact_round_trip",
]


class CoordinateMappingError(RuntimeError):
    """A coordinate transform could not prove an exact safe round trip."""


@dataclass(frozen=True, slots=True)
class LogicalClientPoint:
    """A point relative to RuneLite's DPI-virtualized logical client origin."""

    x: int
    y: int

    @property
    def pair(self) -> tuple[int, int]:
        return self.x, self.y


@dataclass(frozen=True, slots=True)
class LogicalScreenPoint:
    """A screen-relative point in the target RuneLite window's logical space."""

    x: int
    y: int

    @property
    def pair(self) -> tuple[int, int]:
        return self.x, self.y


@dataclass(frozen=True, slots=True)
class PhysicalScreenPoint:
    """A physical desktop point suitable for cursor and hit-test APIs."""

    x: int
    y: int

    @property
    def pair(self) -> tuple[int, int]:
        return self.x, self.y


class CameraCoordinateTransform(Protocol):
    """The three explicitly typed transforms required by pointer mapping."""

    def physical_client_origin(self, hwnd: int) -> PhysicalScreenPoint:
        """Return client ``(0, 0)`` in physical screen coordinates."""

    def physical_to_target_logical(
        self,
        hwnd: int,
        point: PhysicalScreenPoint,
    ) -> LogicalScreenPoint:
        """Convert a physical screen point through the target HWND context."""

    def target_logical_to_physical(
        self,
        hwnd: int,
        point: LogicalScreenPoint,
    ) -> PhysicalScreenPoint:
        """Convert a target-logical screen point into physical screen space."""


@dataclass(frozen=True, slots=True)
class CameraCoordinateMapping:
    """Every intermediate value in one forward and reverse pointer mapping."""

    hwnd: int
    logical_client: LogicalClientPoint
    physical_client_origin: PhysicalScreenPoint
    target_logical_screen_origin: LogicalScreenPoint
    target_logical_screen: LogicalScreenPoint
    physical_screen: PhysicalScreenPoint
    reverse_target_logical_screen: LogicalScreenPoint
    reverse_logical_client: LogicalClientPoint

    @property
    def exact_round_trip(self) -> bool:
        return self.reverse_logical_client == self.logical_client


@dataclass(frozen=True, slots=True)
class CameraDpiEnvironment:
    """Native DPI facts that explain one target window's effective transform."""

    caller_thread_context: int
    caller_thread_awareness: str
    caller_process_context: int | None
    caller_process_awareness: str | None
    target_window_context: int
    target_window_awareness: str
    target_window_dpi: int
    target_window_scale: float
    effective_mapping_dpi_x: float
    effective_mapping_dpi_y: float
    effective_mapping_scale_x: float
    effective_mapping_scale_y: float
    physical_client_size: tuple[int, int]
    estimated_target_logical_client_size: tuple[int, int]


def map_logical_client_point(
    hwnd: int,
    point: LogicalClientPoint,
    transform: CameraCoordinateTransform,
) -> CameraCoordinateMapping:
    """Map one target-logical client point and record its exact reverse path.

    ``LogicalToPhysicalPointForPerMonitorDPI`` accepts a *logical screen*
    point, not a client-relative point.  Therefore the target-logical screen
    origin must be established first.  Adding the client delta before that
    step, or passing a physical result into ``ClientToScreen``, mixes spaces.
    """

    physical_origin = transform.physical_client_origin(hwnd)
    logical_origin = transform.physical_to_target_logical(hwnd, physical_origin)
    logical_screen = LogicalScreenPoint(
        logical_origin.x + point.x,
        logical_origin.y + point.y,
    )
    physical_screen = transform.target_logical_to_physical(hwnd, logical_screen)
    reverse_logical_screen = transform.physical_to_target_logical(
        hwnd,
        physical_screen,
    )
    reverse_client = LogicalClientPoint(
        reverse_logical_screen.x - logical_origin.x,
        reverse_logical_screen.y - logical_origin.y,
    )
    return CameraCoordinateMapping(
        hwnd=hwnd,
        logical_client=point,
        physical_client_origin=physical_origin,
        target_logical_screen_origin=logical_origin,
        target_logical_screen=logical_screen,
        physical_screen=physical_screen,
        reverse_target_logical_screen=reverse_logical_screen,
        reverse_logical_client=reverse_client,
    )


def require_exact_round_trip(mapping: CameraCoordinateMapping) -> PhysicalScreenPoint:
    """Return the physical point only after an exact logical-client round trip."""

    if not mapping.exact_round_trip:
        raise CoordinateMappingError(
            "target DPI mapping did not round-trip the reviewed logical client "
            f"point exactly: {mapping.logical_client.pair!r} -> "
            f"{mapping.physical_screen.pair!r} -> "
            f"{mapping.reverse_logical_client.pair!r}"
        )
    return mapping.physical_screen
