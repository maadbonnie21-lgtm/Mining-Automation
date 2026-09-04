"""Validation-only gameplay-chrome veto before Issue #31 camera input.

This module does not identify a world scene and cannot authorize perception
success.  It checks only fixed client chrome that is expected to be present
while the reviewed RuneLite client is showing gameplay.  A rejected or
ambiguous result means camera input must stop.

The fixed regions deliberately exclude world pixels, resource candidates, and
the production scene landmarks.  They are input-readiness evidence only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ..capture import Frame, PixelFormat
from .camera_plan import EXPECTED_CLIENT_HEIGHT, EXPECTED_CLIENT_WIDTH

__all__ = [
    "CLIENT_INPUT_READINESS_ID",
    "CLIENT_INPUT_READINESS_VERSION",
    "GAMEPLAY_CHROME_POLICIES",
    "ClientInputReadiness",
    "ClientReadinessAnchorEvaluation",
    "ClientReadinessAnchorPolicy",
    "ClientReadinessReason",
    "evaluate_client_input_readiness",
]

CLIENT_INPUT_READINESS_ID: Final[str] = "issue31-gameplay-chrome-readiness"
CLIENT_INPUT_READINESS_VERSION: Final[str] = "1.0.0"
_EDGE_LUMA_DELTA: Final[int] = 20
_DARK_LUMA_MAXIMUM: Final[int] = 16


class ClientReadinessReason(StrEnum):
    """Why fixed client chrome did or did not permit a camera attempt."""

    READY = "ready"
    UNSUPPORTED_FRAME = "unsupported_frame"
    GAMEPLAY_CHROME_MISMATCH = "gameplay_chrome_mismatch"


@dataclass(frozen=True, slots=True)
class ClientReadinessAnchorPolicy:
    """Frozen scalar policy for one fixed gameplay-chrome region."""

    anchor_id: str
    region: tuple[int, int, int, int]
    minimum_luma_stddev: float | None = None
    minimum_edge_density: float | None = None
    maximum_dark_fraction: float | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.anchor_id, str)
            or not self.anchor_id
            or self.anchor_id != self.anchor_id.strip()
        ):
            raise ValueError("readiness anchor id must be a non-empty trimmed string")
        if (
            not isinstance(self.region, tuple)
            or len(self.region) != 4
            or any(isinstance(item, bool) or not isinstance(item, int) for item in self.region)
        ):
            raise ValueError("readiness anchor region must contain four integers")
        x, y, width, height = self.region
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("readiness anchor region must have positive area")
        if x + width > EXPECTED_CLIENT_WIDTH or y + height > EXPECTED_CLIENT_HEIGHT:
            raise ValueError("readiness anchor region must fit the reviewed client")
        if all(
            threshold is None
            for threshold in (
                self.minimum_luma_stddev,
                self.minimum_edge_density,
                self.maximum_dark_fraction,
            )
        ):
            raise ValueError("readiness anchor policy requires at least one threshold")
        if self.minimum_luma_stddev is not None:
            if (
                isinstance(self.minimum_luma_stddev, bool)
                or not isinstance(self.minimum_luma_stddev, (int, float))
                or not math.isfinite(self.minimum_luma_stddev)
                or self.minimum_luma_stddev < 0.0
            ):
                raise ValueError("minimum_luma_stddev must be finite and non-negative")
        for name, threshold in (
            ("minimum_edge_density", self.minimum_edge_density),
            ("maximum_dark_fraction", self.maximum_dark_fraction),
        ):
            if threshold is not None and (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not math.isfinite(threshold)
                or not 0.0 <= threshold <= 1.0
            ):
                raise ValueError(f"{name} must be finite and in [0, 1]")


GAMEPLAY_CHROME_POLICIES: Final[tuple[ClientReadinessAnchorPolicy, ...]] = (
    ClientReadinessAnchorPolicy(
        "compass-chrome",
        (588, 34, 40, 40),
        minimum_luma_stddev=30.0,
        minimum_edge_density=0.20,
    ),
    ClientReadinessAnchorPolicy(
        "minimap-chrome",
        # Starts immediately to the right of the 40x40 compass anchor and below
        # its top row, so all three readiness anchors are pixel-disjoint.
        (628, 74, 139, 180),
        minimum_luma_stddev=25.0,
        minimum_edge_density=0.10,
    ),
    ClientReadinessAnchorPolicy(
        "chat-tab-chrome",
        # The visible chat tabs begin below the south-path landmark, whose
        # reviewed world region ends at y=832.  Keeping a two-pixel gap makes
        # the veto structurally independent of every production landmark.
        (0, 834, 520, 16),
        minimum_edge_density=0.05,
        maximum_dark_fraction=0.20,
    ),
)


@dataclass(frozen=True, slots=True)
class ClientReadinessAnchorEvaluation:
    """Observed scalar evidence for one fixed client-chrome anchor."""

    policy: ClientReadinessAnchorPolicy
    luma_stddev: float
    edge_density: float
    dark_fraction: float
    matched: bool

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ClientReadinessAnchorPolicy):
            raise ValueError("anchor evaluation requires a readiness policy")
        if (
            isinstance(self.luma_stddev, bool)
            or not isinstance(self.luma_stddev, (int, float))
            or not math.isfinite(self.luma_stddev)
            or self.luma_stddev < 0.0
        ):
            raise ValueError("anchor luma_stddev must be finite and non-negative")
        for name, value in (
            ("edge_density", self.edge_density),
            ("dark_fraction", self.dark_fraction),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"anchor {name} must be finite and in [0, 1]")
        if not isinstance(self.matched, bool):
            raise ValueError("anchor matched must be a boolean")


@dataclass(frozen=True, slots=True)
class ClientInputReadiness:
    """Veto-only result; it can never establish scene or resource success."""

    evaluator_id: str
    evaluator_version: str
    reason: ClientReadinessReason
    detail: str
    anchors: tuple[ClientReadinessAnchorEvaluation, ...]
    safe_to_attempt_camera_input: bool
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.safe_to_attempt_camera_input is not (
            self.reason is ClientReadinessReason.READY
        ):
            raise ValueError("readiness boolean must agree with its reason")
        if self.safe_to_attempt_camera_input and not self.anchors:
            raise ValueError("a ready result requires fixed client-chrome evidence")
        if self.safe_to_attempt_camera_input and not all(
            anchor.matched for anchor in self.anchors
        ):
            raise ValueError("a ready result requires every chrome anchor")


def evaluate_client_input_readiness(frame: Frame) -> ClientInputReadiness:
    """Return whether fixed gameplay chrome permits one camera-input attempt.

    Passing this gate does not prove a live network connection, the Varrock
    scene, or any production resource state.  It only avoids known login and
    disconnect canvases before the validation harness sends input.
    """

    if (
        frame.width != EXPECTED_CLIENT_WIDTH
        or frame.height != EXPECTED_CLIENT_HEIGHT
        or frame.pixel_format is not PixelFormat.BGRA8888
    ):
        return ClientInputReadiness(
            evaluator_id=CLIENT_INPUT_READINESS_ID,
            evaluator_version=CLIENT_INPUT_READINESS_VERSION,
            reason=ClientReadinessReason.UNSUPPORTED_FRAME,
            detail=(
                "Input readiness requires the exact reviewed 1005x1078 "
                "BGRA8888 client frame."
            ),
            anchors=(),
            safe_to_attempt_camera_input=False,
        )

    anchors = tuple(_evaluate_anchor(frame, policy) for policy in GAMEPLAY_CHROME_POLICIES)
    matched = all(anchor.matched for anchor in anchors)
    if matched:
        reason = ClientReadinessReason.READY
        detail = (
            "All fixed gameplay-chrome anchors match; this is input-readiness "
            "evidence only and cannot validate the world scene."
        )
    else:
        reason = ClientReadinessReason.GAMEPLAY_CHROME_MISMATCH
        failed = ", ".join(anchor.policy.anchor_id for anchor in anchors if not anchor.matched)
        detail = (
            f"Fixed gameplay chrome is missing or ambiguous at: {failed}; stop "
            "before camera input."
        )
    return ClientInputReadiness(
        evaluator_id=CLIENT_INPUT_READINESS_ID,
        evaluator_version=CLIENT_INPUT_READINESS_VERSION,
        reason=reason,
        detail=detail,
        anchors=anchors,
        safe_to_attempt_camera_input=matched,
    )


def _evaluate_anchor(
    frame: Frame,
    policy: ClientReadinessAnchorPolicy,
) -> ClientReadinessAnchorEvaluation:
    luminances, width, height = _region_luminances(frame, policy.region)
    count = len(luminances)
    mean = sum(luminances) / count
    variance = max(0.0, sum(value * value for value in luminances) / count - mean * mean)
    stddev = math.sqrt(variance)
    dark_fraction = sum(value <= _DARK_LUMA_MAXIMUM for value in luminances) / count

    edge_count = 0
    edge_pairs = 0
    for y in range(height):
        row = y * width
        for x in range(width):
            value = luminances[row + x]
            if x + 1 < width:
                edge_pairs += 1
                edge_count += abs(value - luminances[row + x + 1]) >= _EDGE_LUMA_DELTA
            if y + 1 < height:
                edge_pairs += 1
                edge_count += abs(value - luminances[row + width + x]) >= _EDGE_LUMA_DELTA
    edge_density = edge_count / edge_pairs

    matched = (
        (
            policy.minimum_luma_stddev is None
            or stddev >= policy.minimum_luma_stddev
        )
        and (
            policy.minimum_edge_density is None
            or edge_density >= policy.minimum_edge_density
        )
        and (
            policy.maximum_dark_fraction is None
            or dark_fraction <= policy.maximum_dark_fraction
        )
    )
    return ClientReadinessAnchorEvaluation(
        policy=policy,
        luma_stddev=stddev,
        edge_density=edge_density,
        dark_fraction=dark_fraction,
        matched=matched,
    )


def _region_luminances(
    frame: Frame,
    region: tuple[int, int, int, int],
) -> tuple[tuple[int, ...], int, int]:
    x, y, width, height = region
    stride = frame.width * 4
    payload = frame.payload
    values: list[int] = []
    for row in range(y, y + height):
        offset = row * stride + x * 4
        for column in range(width):
            pixel = offset + column * 4
            blue = payload[pixel]
            green = payload[pixel + 1]
            red = payload[pixel + 2]
            values.append((77 * red + 150 * green + 29 * blue + 128) >> 8)
    return tuple(values), width, height
