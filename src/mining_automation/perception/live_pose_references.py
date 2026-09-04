"""Fail-closed contract for the local real-client pose references.

The 2026-09-03 successful RuneLite runs used three local BGRA reference frames.
Those raw frames remain private/local.  This module owns their names, geometry,
size, and invariant values so the live miner can verify the exact host inputs
before perception without embedding raw client pixels in Git.

Verifying references is read-only and grants no perception release or input
authority.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, final

RESOURCE_LANDMARK_DISTANCE_THRESHOLD: Final[float] = 0.12
RESOURCE_LANDMARK_COUNT: Final[int] = 6
RESOURCE_LANDMARK_QUORUM: Final[int] = 5
RESOURCE_REQUIRED_ZONE_COUNT: Final[int] = 3
POSE_FRAME_WIDTH: Final[int] = 1005
POSE_FRAME_HEIGHT: Final[int] = 1078
POSE_BYTES_PER_PIXEL: Final[int] = 4
POSE_FRAME_BYTE_COUNT: Final[int] = (
    POSE_FRAME_WIDTH * POSE_FRAME_HEIGHT * POSE_BYTES_PER_PIXEL
)
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")


@final
@dataclass(frozen=True, slots=True)
class LocalPoseReferenceSpec:
    pose_id: str
    relative_path: str
    width: int = POSE_FRAME_WIDTH
    height: int = POSE_FRAME_HEIGHT
    bytes_per_pixel: int = POSE_BYTES_PER_PIXEL

    def __post_init__(self) -> None:
        if type(self.pose_id) is not str or not self.pose_id:
            raise ValueError("pose_id must be a non-empty exact string")
        if type(self.relative_path) is not str or not self.relative_path:
            raise ValueError("relative_path must be a non-empty exact string")
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("pose reference path must be repository-relative")
        if path.suffix.lower() != ".bgra":
            raise ValueError("pose reference must be a BGRA file")
        if (
            self.width != POSE_FRAME_WIDTH
            or self.height != POSE_FRAME_HEIGHT
            or self.bytes_per_pixel != POSE_BYTES_PER_PIXEL
        ):
            raise ValueError("pose reference geometry must remain exact 1005x1078 BGRA")

    @property
    def expected_byte_count(self) -> int:
        return self.width * self.height * self.bytes_per_pixel


LOCAL_POSE_REFERENCE_SPECS: Final[tuple[LocalPoseReferenceSpec, ...]] = (
    LocalPoseReferenceSpec(
        pose_id="different-rock-clean",
        relative_path="diagnostics/different-rock-ore3-20260903/ore-01-clean.bgra",
    ),
    LocalPoseReferenceSpec(
        pose_id="third-rock-clean",
        relative_path="diagnostics/third-rock-ore4-20260903/ore-01-clean.bgra",
    ),
    LocalPoseReferenceSpec(
        pose_id="third-rock-reacquired",
        relative_path="diagnostics/third-rock-ore4-20260903/ore-04-reacquired.bgra",
    ),
)


@final
@dataclass(frozen=True, slots=True)
class LocalPoseReferenceReceipt:
    pose_id: str
    relative_path: str
    sha256: str
    byte_count: int
    width: int
    height: int
    bytes_per_pixel: int
    raw_pixels_exported: Literal[False] = field(default=False, init=False)
    perception_release_authority: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        spec = next(
            (item for item in LOCAL_POSE_REFERENCE_SPECS if item.pose_id == self.pose_id),
            None,
        )
        if spec is None or self.relative_path != spec.relative_path:
            raise ValueError("pose receipt is not for an exact owned reference")
        if _SHA256_RE.fullmatch(self.sha256) is None:
            raise ValueError("pose receipt SHA-256 must be lowercase and exact")
        if self.byte_count != spec.expected_byte_count:
            raise ValueError("pose receipt byte count does not match exact geometry")
        if (
            self.width != spec.width
            or self.height != spec.height
            or self.bytes_per_pixel != spec.bytes_per_pixel
        ):
            raise ValueError("pose receipt geometry does not match exact reference")
        if (
            self.raw_pixels_exported is not False
            or self.perception_release_authority is not False
            or self.input_authority is not False
        ):
            raise ValueError("pose receipt cannot export pixels or grant authority")


@final
@dataclass(frozen=True, slots=True)
class LocalPoseReferenceManifest:
    receipts: tuple[LocalPoseReferenceReceipt, ...]
    resource_distance_threshold: float = RESOURCE_LANDMARK_DISTANCE_THRESHOLD
    resource_landmark_count: int = RESOURCE_LANDMARK_COUNT
    resource_landmark_quorum: int = RESOURCE_LANDMARK_QUORUM
    resource_required_zone_count: int = RESOURCE_REQUIRED_ZONE_COUNT
    raw_pixels_exported: Literal[False] = field(default=False, init=False)
    perception_release_authority: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.receipts) is not tuple:
            raise ValueError("pose receipts must be an exact tuple")
        if len(self.receipts) != len(LOCAL_POSE_REFERENCE_SPECS):
            raise ValueError("pose manifest must contain every exact local reference")
        if any(type(item) is not LocalPoseReferenceReceipt for item in self.receipts):
            raise ValueError("pose manifest receipts must be exact")
        if tuple(item.pose_id for item in self.receipts) != tuple(
            spec.pose_id for spec in LOCAL_POSE_REFERENCE_SPECS
        ):
            raise ValueError("pose manifest receipt order/identity changed")
        if (
            self.resource_distance_threshold != RESOURCE_LANDMARK_DISTANCE_THRESHOLD
            or self.resource_landmark_count != RESOURCE_LANDMARK_COUNT
            or self.resource_landmark_quorum != RESOURCE_LANDMARK_QUORUM
            or self.resource_required_zone_count != RESOURCE_REQUIRED_ZONE_COUNT
        ):
            raise ValueError("pose manifest weakens frozen Resource invariants")
        if (
            self.raw_pixels_exported is not False
            or self.perception_release_authority is not False
            or self.input_authority is not False
        ):
            raise ValueError("pose manifest cannot export pixels or grant authority")


def local_pose_reference_paths(repository_root: Path) -> tuple[Path, ...]:
    """Return exact local reference paths without reading them."""

    if type(repository_root) is not Path:
        raise TypeError("repository_root must be exact pathlib.Path")
    root = repository_root.resolve()
    return tuple(root / spec.relative_path for spec in LOCAL_POSE_REFERENCE_SPECS)


def verify_local_pose_references(repository_root: Path) -> LocalPoseReferenceManifest:
    """Hash and verify every private local BGRA reference without copying pixels."""

    root = repository_root.resolve()
    receipts: list[LocalPoseReferenceReceipt] = []
    for spec in LOCAL_POSE_REFERENCE_SPECS:
        path = root / spec.relative_path
        if not path.is_file():
            raise FileNotFoundError(
                f"required local pose reference is missing: {spec.relative_path}"
            )
        payload = path.read_bytes()
        if len(payload) != spec.expected_byte_count:
            raise ValueError(
                f"local pose reference has wrong byte count: {spec.relative_path}; "
                f"expected {spec.expected_byte_count}, got {len(payload)}"
            )
        receipts.append(
            LocalPoseReferenceReceipt(
                pose_id=spec.pose_id,
                relative_path=spec.relative_path,
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_count=len(payload),
                width=spec.width,
                height=spec.height,
                bytes_per_pixel=spec.bytes_per_pixel,
            )
        )
    return LocalPoseReferenceManifest(receipts=tuple(receipts))
