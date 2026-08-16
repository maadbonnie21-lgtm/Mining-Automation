"""Deterministic, frame-local inventory-region localization.

This milestone deliberately supports only explicitly reviewed frame profiles.
It does not infer a RuneLite sidebar anchor, depend on desktop coordinates, or
ship an unvalidated default.  Unknown frame geometry therefore produces an
explicit zero-confidence localization rather than a fabricated region.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ...capture import Frame
from .geometry import InventoryGridLayout, Region

__all__ = [
    "ExactProfileInventoryLocator",
    "InventoryFrameProfile",
    "InventoryLocalization",
    "InventoryRegionLocator",
]


@dataclass(frozen=True, slots=True)
class InventoryLocalization:
    """Result of trying to localize the inventory in one consumer frame."""

    region: Region | None
    confidence: float
    reason: str
    profile_id: str | None = None

    def __post_init__(self) -> None:
        if self.region is not None and not isinstance(self.region, Region):
            raise TypeError(f"region must be Region or None, got {type(self.region).__name__}")
        if not _is_confidence(self.confidence):
            raise ValueError("confidence must be a finite number between 0.0 and 1.0")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if self.profile_id is not None and (
            not isinstance(self.profile_id, str) or not self.profile_id.strip()
        ):
            raise ValueError("profile_id must be None or a non-empty string")

        confidence = float(self.confidence)
        if self.region is None and confidence != 0.0:
            raise ValueError("an unknown localization must have confidence 0.0")
        if self.region is None and self.profile_id is not None:
            raise ValueError("an unknown localization cannot identify a profile")
        if self.region is not None and confidence == 0.0:
            raise ValueError("a localized region must have confidence greater than 0.0")
        object.__setattr__(self, "confidence", confidence)


@runtime_checkable
class InventoryRegionLocator(Protocol):
    """Platform-neutral seam for obtaining a frame-local inventory region."""

    def locate(self, frame: Frame, /) -> InventoryLocalization:
        """Localize the inventory without guessing when the frame is unsupported."""
        ...


@dataclass(frozen=True, slots=True)
class InventoryFrameProfile:
    """Reviewed mapping from exact frame geometry to an inventory region."""

    profile_id: str
    frame_width: int
    frame_height: int
    region: Region
    layout: InventoryGridLayout

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("profile_id must be a non-empty string")
        _require_positive_int("frame_width", self.frame_width)
        _require_positive_int("frame_height", self.frame_height)
        if not isinstance(self.region, Region):
            raise TypeError(f"region must be Region, got {type(self.region).__name__}")
        if not isinstance(self.layout, InventoryGridLayout):
            raise TypeError(
                f"layout must be InventoryGridLayout, got {type(self.layout).__name__}"
            )
        if self.layout.profile_id != self.profile_id:
            raise ValueError(
                "profile_id must match layout.profile_id: "
                f"{self.profile_id!r} != {self.layout.profile_id!r}"
            )
        if (self.region.width, self.region.height) != (
            self.layout.width,
            self.layout.height,
        ):
            raise ValueError(
                "profile inventory region dimensions must match its layout: "
                f"got {self.region.width}x{self.region.height}, "
                f"expected {self.layout.width}x{self.layout.height}"
            )
        if not self.region.fits(self.frame_width, self.frame_height):
            raise ValueError(
                f"inventory region {self.region.as_tuple()} does not fit frame "
                f"{self.frame_width}x{self.frame_height}"
            )


@dataclass(frozen=True, slots=True, init=False)
class ExactProfileInventoryLocator:
    """Resolve only exact, reviewed frame-size profiles.

    A profile list is required; there is intentionally no baked-in RuneLite
    coordinate.  More robust visual anchor localization can implement the same
    :class:`InventoryRegionLocator` protocol after real fixtures are reviewed.
    """

    _profiles: tuple[InventoryFrameProfile, ...]

    def __init__(self, profiles: Sequence[InventoryFrameProfile]) -> None:
        if not isinstance(profiles, Sequence) or isinstance(profiles, (str, bytes, bytearray)):
            raise TypeError("profiles must be a sequence of InventoryFrameProfile values")
        normalized = tuple(profiles)
        if not normalized:
            raise ValueError("profiles must contain at least one reviewed frame profile")

        seen_geometry: set[tuple[int, int]] = set()
        layouts_by_profile_id: dict[str, InventoryGridLayout] = {}
        for index, profile in enumerate(normalized):
            if not isinstance(profile, InventoryFrameProfile):
                raise TypeError(
                    f"profiles[{index}] must be InventoryFrameProfile, "
                    f"got {type(profile).__name__}"
                )
            geometry = (profile.frame_width, profile.frame_height)
            if geometry in seen_geometry:
                raise ValueError(
                    "frame geometry must identify exactly one inventory profile; "
                    f"duplicate {geometry[0]}x{geometry[1]}"
                )
            seen_geometry.add(geometry)
            prior_layout = layouts_by_profile_id.get(profile.profile_id)
            if prior_layout is not None and prior_layout != profile.layout:
                raise ValueError(
                    f"profiles sharing profile_id {profile.profile_id!r} must use "
                    "an identical inventory grid layout"
                )
            layouts_by_profile_id[profile.profile_id] = profile.layout

        object.__setattr__(self, "_profiles", normalized)

    @property
    def profiles(self) -> tuple[InventoryFrameProfile, ...]:
        """Immutable reviewed profiles, retained in caller order."""
        return self._profiles

    def locate(self, frame: Frame, /) -> InventoryLocalization:
        """Return the exact frame-size match or an explicit unknown result."""
        if not isinstance(frame, Frame):
            raise TypeError(f"frame must be Frame, got {type(frame).__name__}")
        for profile in self._profiles:
            if (frame.width, frame.height) == (profile.frame_width, profile.frame_height):
                return InventoryLocalization(
                    region=profile.region,
                    confidence=1.0,
                    reason=f"matched reviewed inventory profile {profile.profile_id!r}",
                    profile_id=profile.profile_id,
                )
        return InventoryLocalization(
            region=None,
            confidence=0.0,
            reason=(
                "no reviewed inventory profile for frame geometry "
                f"{frame.width}x{frame.height}"
            ),
        )


def _require_positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


def _is_confidence(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= 1
    return (
        isinstance(value, float)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )
