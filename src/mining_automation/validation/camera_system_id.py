"""Fixed, development-only camera system identification for Issue #31.

The runner performs one hard-coded A/B/A experiment at a time. It measures
two no-input baselines, executes a four-logical-pixel middle-drag pulse, then
executes the exact opposite pulse only after a new guarded input seam. The
unchanged production evaluator remains the only scene-acceptance authority;
wide landmark registration is retained only as calibration evidence.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from typing import Final

from ..capture import Frame
from ..perception.production_profiles import load_varrock_east_iron_profile
from ..perception.resource import ResourceVisualState
from ..perception.scene_landmarks import MacroZone
from ..perception.wide_scene_registration import (
    DEFAULT_WIDE_COARSE_STEP,
    DEFAULT_WIDE_REFINEMENT_RADIUS,
    MAXIMUM_WIDE_REGISTRATION_RADIUS,
    WideLandmarkSearch,
)
from .camera_arm_guard import (
    CameraArmGuardDisposition,
    CameraArmGuardResult,
    evaluate_camera_arm_guard,
)
from .camera_evaluation import CameraEvaluation, evaluate_varrock_east_camera
from .camera_guidance import WorldCameraGuidance, evaluate_varrock_east_camera_guidance
from .camera_plan import (
    REVIEWED_CAMERA_DRAG_POINT,
    CameraControl,
    CameraDragAxis,
    CameraInputReceipt,
    CameraMiddleDrag,
    CameraPlan,
    CameraPlanReceipt,
    CameraPlanRunner,
    CameraPreflightReceipt,
    Sleeper,
)
from .camera_servo import (
    MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
    CameraServoArmAgeEvidence,
    CameraServoArmAgeStatus,
    CameraServoExceptionEvidence,
    CameraServoFrameEvidence,
)
from .camera_session import (
    CameraArtifactRecorder,
    CameraFrameArtifact,
    CameraFrameSource,
    record_frame_digest,
)
from .client_readiness import evaluate_client_input_readiness

__all__ = [
    "CAMERA_SYSTEM_ID_DRAG_PIXELS",
    "CAMERA_SYSTEM_ID_ID",
    "CAMERA_SYSTEM_ID_SETTLE_SECONDS",
    "CAMERA_SYSTEM_ID_VERSION",
    "CameraSystemIdAxis",
    "CameraSystemIdAxisResult",
    "CameraSystemIdComparison",
    "CameraSystemIdConclusion",
    "CameraSystemIdInputState",
    "CameraSystemIdLandmarkComparison",
    "CameraSystemIdObservation",
    "CameraSystemIdResult",
    "CameraSystemIdStepResult",
    "CameraSystemIdStepTerminalReason",
    "run_fixed_camera_system_identification",
]

CAMERA_SYSTEM_ID_ID: Final[str] = "issue31-fixed-camera-system-identification"
CAMERA_SYSTEM_ID_VERSION: Final[str] = "1.0.0"
CAMERA_SYSTEM_ID_DRAG_PIXELS: Final[int] = 4
CAMERA_SYSTEM_ID_SETTLE_SECONDS: Final[float] = 1.0

_REQUIRED_LANDMARKS: Final[int] = 3
_REQUIRED_ZONES: Final[int] = 3
_PROFILE = load_varrock_east_iron_profile()
_FROZEN_LANDMARKS: Final[dict[str, tuple[MacroZone, float]]] = {
    "west-ridge": (MacroZone.NORTH_WEST, 0.12),
    "west-lower-ridge": (MacroZone.NORTH_WEST, 0.12),
    "south-path": (MacroZone.SOUTH_WEST, 0.12),
    "south-central-edge": (MacroZone.SOUTH_WEST, 0.12),
    "north-east-wall": (MacroZone.NORTH_EAST, 0.12),
    "east-bank-edge": (MacroZone.NORTH_EAST, 0.12),
}
_FROZEN_LANDMARK_IDS: Final[tuple[str, ...]] = tuple(_FROZEN_LANDMARKS)
_FROZEN_ZONES: Final[tuple[MacroZone, ...]] = (
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_EAST,
    MacroZone.SOUTH_WEST,
)
_OBSERVED_PROFILE_LANDMARKS = {
    landmark.landmark_id: (
        landmark.zone(_PROFILE.frame_width, _PROFILE.frame_height),
        landmark.maximum_distance,
    )
    for landmark in _PROFILE.scene_landmarks
}
if (
    _OBSERVED_PROFILE_LANDMARKS != _FROZEN_LANDMARKS
    or tuple(_OBSERVED_PROFILE_LANDMARKS) != _FROZEN_LANDMARK_IDS
):
    raise RuntimeError("camera system-ID frozen landmark profile changed")


class CameraSystemIdAxis(StrEnum):
    """The two independently measured camera-drag axes."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class CameraSystemIdConclusion(StrEnum):
    """The only conclusive outcomes from the lead-approved experiment."""

    CONTROL_DERIVATIVE_USABLE = "control derivative usable"
    LANDMARK_CONTROLLER_RETIRED = "landmark controller retired"


class CameraSystemIdInputState(StrEnum):
    """Honest side-effect state for one fixed drag request."""

    NONE = "none"
    PARTIAL_OR_UNKNOWN = "partial_or_unknown"
    COMPLETE = "complete"


class CameraSystemIdStepTerminalReason(StrEnum):
    """Stable terminal reason for one guarded fixed drag."""

    COMPLETE = "complete"
    PRODUCTION_PASS = "production_pass"
    READINESS_LOST = "readiness_lost"
    PRODUCTION_REJECTION_NOT_FAIL_CLOSED = "production_rejection_not_fail_closed"
    NON_FRESH_OBSERVATION = "non_fresh_observation"
    WORLD_CHANGED = "world_changed"
    ARM_FRESHNESS_EXPIRED = "arm_freshness_expired"
    CLOCK_ERROR = "clock_error"
    OBSERVATION_EXCEPTION = "observation_exception"
    INPUT_EXCEPTION = "input_exception"
    SETTLE_EXCEPTION = "settle_exception"


@dataclass(frozen=True, slots=True)
class CameraSystemIdObservation:
    """One private frame with production and diagnostic-only evidence."""

    frame: Frame
    evidence: CameraServoFrameEvidence
    guidance: WorldCameraGuidance

    def __post_init__(self) -> None:
        artifact = self.evidence.artifact
        if artifact.frame_id != self.frame.frame_id:
            raise ValueError("observation evidence must bind the exact frame")
        if (
            artifact.width != self.frame.width
            or artifact.height != self.frame.height
            or artifact.pixel_format != self.frame.pixel_format.value
            or self.evidence.captured_monotonic_s
            != self.frame.captured_monotonic_s
        ):
            raise ValueError("observation evidence metadata must bind the exact frame")
        if artifact.raw_sha256 != hashlib.sha256(self.frame.payload).hexdigest():
            raise ValueError("observation evidence must bind the exact frame payload")
        if self.guidance.analysis is None:
            raise ValueError("system identification requires wide diagnostic evidence")
        if (
            self.guidance.can_accept
            or self.guidance.can_validate_scene
            or self.guidance.can_expose_resources
        ):
            raise ValueError("diagnostic guidance cannot acquire scene authority")


@dataclass(frozen=True, slots=True)
class CameraSystemIdStepResult:
    """Complete evidence for zero input or one fixed four-pixel drag."""

    axis: CameraSystemIdAxis
    direction: int
    decision: CameraSystemIdObservation
    plan: CameraPlan
    arm: CameraServoFrameEvidence | None
    arm_observation: CameraSystemIdObservation | None
    arm_guard: CameraArmGuardResult | None
    preflight: CameraPreflightReceipt | None
    commit: CameraServoFrameEvidence | None
    commit_observation: CameraSystemIdObservation | None
    commit_guard: CameraArmGuardResult | None
    decision_commit_guard: CameraArmGuardResult | None
    arm_age: CameraServoArmAgeEvidence | None
    receipt: CameraPlanReceipt | None
    post: CameraSystemIdObservation | None
    terminal_reason: CameraSystemIdStepTerminalReason
    detail: str
    input_state: CameraSystemIdInputState = CameraSystemIdInputState.NONE
    input_start_clock_s: float | None = None
    input_receipt_clock_s: float | None = None
    input_delivery_duration_s: float | None = None
    exception: CameraServoExceptionEvidence | None = None

    def __post_init__(self) -> None:
        if isinstance(self.direction, bool) or self.direction not in (-1, 1):
            raise ValueError("probe direction must be exactly -1 or +1")
        expected = _fixed_plan(self.axis, self.direction)
        if self.plan != expected:
            raise ValueError("probe step must retain the exact fixed drag")
        if self.receipt is not None and self.receipt.plan is not self.plan:
            raise ValueError("probe receipt must bind the exact plan object")
        if self.input_state is CameraSystemIdInputState.COMPLETE:
            if self.receipt is None:
                raise ValueError("complete input state requires a complete receipt")
        elif self.receipt is not None:
            raise ValueError("a complete receipt requires complete input state")
        if self.input_state is CameraSystemIdInputState.NONE and any(
            item is not None
            for item in (
                self.input_start_clock_s,
                self.input_receipt_clock_s,
                self.input_delivery_duration_s,
            )
        ):
            raise ValueError("zero-input evidence cannot retain input timing")
        if self.post is not None and self.receipt is None:
            raise ValueError("post-input evidence requires a complete receipt")
        for label, evidence, observation in (
            ("arm", self.arm, self.arm_observation),
            ("commit", self.commit, self.commit_observation),
        ):
            if observation is not None and evidence is None:
                raise ValueError(f"{label} diagnostic requires {label} evidence")
            if observation is not None and observation.evidence is not evidence:
                raise ValueError(f"{label} diagnostic must bind exact {label} evidence")
        if self.terminal_reason is CameraSystemIdStepTerminalReason.COMPLETE:
            if self.input_state is not CameraSystemIdInputState.COMPLETE:
                raise ValueError("complete step requires complete input")
            if self.post is None:
                raise ValueError("complete step requires a fresh post observation")
            if self.arm_observation is None or self.commit_observation is None:
                raise ValueError("complete step requires exact arm/commit diagnostics")
            if not (
                _strictly_newer(self.decision.frame, self.arm_observation.frame)
                and _strictly_newer(
                    self.arm_observation.frame,
                    self.commit_observation.frame,
                )
                and _strictly_newer(self.commit_observation.frame, self.post.frame)
            ):
                raise ValueError("complete step frame chronology is invalid")
            if (
                self.arm_guard is None
                or self.commit_guard is None
                or self.decision_commit_guard is None
                or self.arm_guard.disposition is not CameraArmGuardDisposition.RETAIN
                or self.commit_guard.disposition is not CameraArmGuardDisposition.RETAIN
                or self.decision_commit_guard.disposition
                is not CameraArmGuardDisposition.RETAIN
                or not _guard_binds(
                    self.arm_guard,
                    before=self.decision.evidence,
                    after=self.arm_observation.evidence,
                )
                or not _guard_binds(
                    self.commit_guard,
                    before=self.arm_observation.evidence,
                    after=self.commit_observation.evidence,
                )
                or not _guard_binds(
                    self.decision_commit_guard,
                    before=self.decision.evidence,
                    after=self.commit_observation.evidence,
                )
            ):
                raise ValueError("complete step guards do not bind the exact frame chain")

    @property
    def completed(self) -> bool:
        return self.terminal_reason is CameraSystemIdStepTerminalReason.COMPLETE


@dataclass(frozen=True, slots=True)
class CameraSystemIdLandmarkComparison:
    """Strict-match A/B/A evidence measured at the physical input seams.

    Derived quantities intentionally remain properties rather than constructor
    arguments.  Canonical evidence therefore cannot attach forged deltas or
    verdict booleans to otherwise plausible landmark searches.
    """

    landmark_id: str
    zone: MacroZone
    axis: CameraSystemIdAxis
    search_radius: int
    baseline_one: WideLandmarkSearch
    baseline_two: WideLandmarkSearch
    positive_arm: WideLandmarkSearch
    positive_commit: WideLandmarkSearch
    positive: WideLandmarkSearch
    return_arm: WideLandmarkSearch
    return_commit: WideLandmarkSearch
    returned: WideLandmarkSearch

    def __post_init__(self) -> None:
        if not isinstance(self.axis, CameraSystemIdAxis):
            raise ValueError("landmark comparison axis is invalid")
        expected = _FROZEN_LANDMARKS.get(self.landmark_id)
        if expected is None:
            raise ValueError("comparison landmark is not in the frozen profile")
        expected_zone, expected_threshold = expected
        if self.zone is not expected_zone:
            raise ValueError("comparison landmark macro zone changed")
        if self.search_radius != MAXIMUM_WIDE_REGISTRATION_RADIUS:
            raise ValueError("comparison search radius changed")
        searches = self.searches
        for search in searches:
            if any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (search.offset_x, search.offset_y)
            ):
                raise ValueError("landmark offsets must be integers")
            if (
                search.landmark_id != self.landmark_id
                or search.zone is not expected_zone
                or search.maximum_distance != expected_threshold
            ):
                raise ValueError("strict landmark identity changed across A/B/A")
            if (
                isinstance(search.distance, bool)
                or not isinstance(search.distance, (int, float))
                or not math.isfinite(search.distance)
                or search.distance < 0.0
                or search.matched is not (search.distance <= search.maximum_distance)
            ):
                raise ValueError("landmark match flag does not match descriptor distance")
            if (
                isinstance(search.searched_offsets, bool)
                or not isinstance(search.searched_offsets, int)
                or search.searched_offsets <= 0
            ):
                raise ValueError("landmark search count must be positive")

    @property
    def searches(self) -> tuple[WideLandmarkSearch, ...]:
        return (
            self.baseline_one,
            self.baseline_two,
            self.positive_arm,
            self.positive_commit,
            self.positive,
            self.return_arm,
            self.return_commit,
            self.returned,
        )

    @property
    def strictly_matched(self) -> bool:
        return all(
            search.matched
            and abs(search.offset_x) < self.search_radius
            and abs(search.offset_y) < self.search_radius
            for search in self.searches
        )

    @staticmethod
    def _delta(
        before: WideLandmarkSearch,
        after: WideLandmarkSearch,
    ) -> tuple[int, int]:
        return after.offset_x - before.offset_x, after.offset_y - before.offset_y

    @property
    def no_input_deltas(self) -> tuple[tuple[int, int], ...]:
        a_pose = (
            self.baseline_one,
            self.baseline_two,
            self.positive_arm,
            self.positive_commit,
        )
        b_pose = (self.positive, self.return_arm, self.return_commit)
        return tuple(
            self._delta(before, after)
            for sequence in (a_pose, b_pose)
            for before, after in combinations(sequence, 2)
        )

    @property
    def no_input_descriptor_deltas(self) -> tuple[float, ...]:
        a_pose = (
            self.baseline_one,
            self.baseline_two,
            self.positive_arm,
            self.positive_commit,
        )
        b_pose = (self.positive, self.return_arm, self.return_commit)
        return tuple(
            abs(after.distance - before.distance)
            for sequence in (a_pose, b_pose)
            for before, after in combinations(sequence, 2)
        )

    @property
    def baseline_delta_x(self) -> int:
        return self.baseline_two.offset_x - self.baseline_one.offset_x

    @property
    def baseline_delta_y(self) -> int:
        return self.baseline_two.offset_y - self.baseline_one.offset_y

    @property
    def positive_delta_x(self) -> int:
        return self.positive.offset_x - self.positive_commit.offset_x

    @property
    def positive_delta_y(self) -> int:
        return self.positive.offset_y - self.positive_commit.offset_y

    @property
    def return_delta_x(self) -> int:
        return self.returned.offset_x - self.return_commit.offset_x

    @property
    def return_delta_y(self) -> int:
        return self.returned.offset_y - self.return_commit.offset_y

    @property
    def return_residual_x(self) -> int:
        return self.returned.offset_x - self.positive_commit.offset_x

    @property
    def return_residual_y(self) -> int:
        return self.returned.offset_y - self.positive_commit.offset_y

    @property
    def baseline_jitter_px(self) -> float:
        return max(_vector_length(x, y) for x, y in self.no_input_deltas)

    @property
    def positive_magnitude_px(self) -> float:
        return _vector_length(self.positive_delta_x, self.positive_delta_y)

    @property
    def return_magnitude_px(self) -> float:
        return _vector_length(self.return_delta_x, self.return_delta_y)

    @property
    def return_residual_px(self) -> float:
        return _vector_length(self.return_residual_x, self.return_residual_y)

    @property
    def tested_axis_baseline_jitter(self) -> int:
        component = 0 if self.axis is CameraSystemIdAxis.HORIZONTAL else 1
        return max(abs(delta[component]) for delta in self.no_input_deltas)

    @property
    def tested_axis_positive_delta(self) -> int:
        return (
            self.positive_delta_x
            if self.axis is CameraSystemIdAxis.HORIZONTAL
            else self.positive_delta_y
        )

    @property
    def tested_axis_return_delta(self) -> int:
        return (
            self.return_delta_x
            if self.axis is CameraSystemIdAxis.HORIZONTAL
            else self.return_delta_y
        )

    @property
    def descriptor_jitter(self) -> float:
        return max(self.no_input_descriptor_deltas)

    @property
    def minimum_descriptor_margin(self) -> float:
        return min(search.maximum_distance - search.distance for search in self.searches)

    @property
    def descriptor_stable(self) -> bool:
        return self.minimum_descriptor_margin > self.descriptor_jitter

    @property
    def above_baseline_jitter(self) -> bool:
        return (
            self.positive_magnitude_px > self.baseline_jitter_px
            and self.return_magnitude_px > self.baseline_jitter_px
            and abs(self.tested_axis_positive_delta)
            > self.tested_axis_baseline_jitter
            and abs(self.tested_axis_return_delta)
            > self.tested_axis_baseline_jitter
        )

    @property
    def opposite_return(self) -> bool:
        return self.tested_axis_positive_delta * self.tested_axis_return_delta < 0

    @property
    def opposite_vector_return(self) -> bool:
        return (
            self.positive_delta_x * self.return_delta_x
            + self.positive_delta_y * self.return_delta_y
            < 0
        )

    @property
    def closed_inside_baseline_envelope(self) -> bool:
        a_pose = (
            self.baseline_one,
            self.baseline_two,
            self.positive_arm,
            self.positive_commit,
        )
        return (
            min(item.offset_x for item in a_pose)
            <= self.returned.offset_x
            <= max(item.offset_x for item in a_pose)
            and min(item.offset_y for item in a_pose)
            <= self.returned.offset_y
            <= max(item.offset_y for item in a_pose)
        )

    @property
    def qualified(self) -> bool:
        return (
            self.strictly_matched
            and self.above_baseline_jitter
            and self.opposite_return
            and self.opposite_vector_return
            and self.closed_inside_baseline_envelope
            and self.descriptor_stable
        )


@dataclass(frozen=True, slots=True)
class CameraSystemIdComparison:
    """Conservative distributed verdict for one fixed-axis A/B/A."""

    axis: CameraSystemIdAxis
    landmarks: tuple[CameraSystemIdLandmarkComparison, ...]
    common_matched_zones: tuple[MacroZone, ...]
    qualified_landmark_ids: tuple[str, ...]
    qualified_zones: tuple[MacroZone, ...]
    coherent_forward_sign: int | None
    required_landmarks: int
    required_zones: int
    derivative_usable: bool
    detail: str

    def __post_init__(self) -> None:
        if any(item.axis is not self.axis for item in self.landmarks):
            raise ValueError("comparison landmarks must retain the fixed axis")
        landmark_ids = tuple(item.landmark_id for item in self.landmarks)
        if len(set(landmark_ids)) != len(landmark_ids):
            raise ValueError("system-ID comparison contains duplicate landmark IDs")
        if any(item.landmark_id not in _FROZEN_LANDMARK_IDS for item in self.landmarks):
            raise ValueError("system-ID comparison contains an unknown landmark")
        if landmark_ids != _FROZEN_LANDMARK_IDS:
            raise ValueError("system-ID comparison must retain all frozen landmarks")
        common_zones = tuple(
            zone
            for zone in MacroZone
            if any(item.strictly_matched and item.zone is zone for item in self.landmarks)
        )
        individually_qualified = tuple(
            item for item in self.landmarks if item.qualified
        )
        signs = {
            1 if item.tested_axis_positive_delta > 0 else -1
            for item in individually_qualified
        }
        coherent_sign = next(iter(signs)) if len(signs) == 1 else None
        qualified = individually_qualified if coherent_sign is not None else ()
        qualified_zones = tuple(
            zone
            for zone in MacroZone
            if any(item.zone is zone for item in qualified)
        )
        expected_usable = (
            len(qualified) >= _REQUIRED_LANDMARKS
            and qualified_zones == _FROZEN_ZONES
        )
        if self.required_landmarks != _REQUIRED_LANDMARKS:
            raise ValueError("system-ID comparison landmark requirement changed")
        if self.required_zones != _REQUIRED_ZONES:
            raise ValueError("system-ID comparison zone requirement changed")
        if self.common_matched_zones != common_zones:
            raise ValueError("common matched zones do not match landmark evidence")
        if self.coherent_forward_sign != coherent_sign:
            raise ValueError("coherent forward sign does not match landmark evidence")
        if self.qualified_landmark_ids != tuple(
            item.landmark_id for item in qualified
        ):
            raise ValueError("qualified IDs do not match landmark evidence")
        if self.qualified_zones != qualified_zones:
            raise ValueError("qualified zones do not match landmark evidence")
        if self.derivative_usable is not expected_usable:
            raise ValueError("derivative verdict does not match frozen evidence policy")
        if not self.detail.strip():
            raise ValueError("system-ID comparison detail must not be empty")


@dataclass(frozen=True, slots=True)
class CameraSystemIdAxisResult:
    """Two baselines plus the bounded +4/-4 result for one axis."""

    axis: CameraSystemIdAxis
    baseline_one: CameraSystemIdObservation | None
    baseline_two: CameraSystemIdObservation | None
    baseline_guard: CameraArmGuardResult | None
    positive_step: CameraSystemIdStepResult | None
    return_step: CameraSystemIdStepResult | None
    comparison: CameraSystemIdComparison | None
    detail: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError("system-ID axis detail must not be empty")
        if self.baseline_two is not None and self.baseline_one is None:
            raise ValueError("second baseline requires the first baseline")
        if (
            self.baseline_two is not None
            and self.baseline_one is not None
            and (self.baseline_guard is not None or self.positive_step is not None)
            and not _strictly_newer(
                self.baseline_one.frame,
                self.baseline_two.frame,
            )
        ):
            raise ValueError("guarded baselines must be strictly ordered")
        if self.baseline_guard is not None:
            if self.baseline_one is None or self.baseline_two is None:
                raise ValueError("baseline guard requires both exact baseline frames")
            if not _guard_binds(
                self.baseline_guard,
                before=self.baseline_one.evidence,
                after=self.baseline_two.evidence,
            ):
                raise ValueError("baseline guard does not bind the exact baselines")
        if self.positive_step is not None and (
            self.baseline_guard is None
            or self.baseline_guard.disposition is not CameraArmGuardDisposition.RETAIN
        ):
            raise ValueError("camera input requires a retained exact baseline guard")
        if self.comparison is not None and self.comparison.axis is not self.axis:
            raise ValueError("axis comparison must retain the same fixed axis")
        if self.positive_step is not None and (
            self.positive_step.axis is not self.axis
            or self.positive_step.direction != 1
        ):
            raise ValueError("positive step does not match the fixed axis/order")
        if self.return_step is not None and (
            self.return_step.axis is not self.axis
            or self.return_step.direction != -1
        ):
            raise ValueError("return step does not match the fixed axis/order")
        if self.return_step is not None and (
            self.positive_step is None
            or not self.positive_step.completed
            or self.positive_step.post is not self.return_step.decision
        ):
            raise ValueError("return step must bind the completed positive post frame")
        if self.comparison is not None and not (
            self.positive_step is not None
            and self.positive_step.completed
            and self.return_step is not None
            and self.return_step.completed
            and self.baseline_one is not None
            and self.baseline_two is not None
        ):
            raise ValueError("comparison requires a complete A/B/A frame chain")
        if self.comparison is not None:
            assert self.baseline_one is not None
            assert self.baseline_two is not None
            assert self.positive_step is not None
            assert self.positive_step.arm_observation is not None
            assert self.positive_step.commit_observation is not None
            assert self.positive_step.post is not None
            assert self.return_step is not None
            assert self.return_step.arm_observation is not None
            assert self.return_step.commit_observation is not None
            assert self.return_step.post is not None
            expected = _compare_landmarks(
                self.axis,
                self.baseline_one,
                self.baseline_two,
                self.positive_step.arm_observation,
                self.positive_step.commit_observation,
                self.positive_step.post,
                self.return_step.arm_observation,
                self.return_step.commit_observation,
                self.return_step.post,
            )
            if self.comparison != expected:
                raise ValueError("axis comparison does not bind the exact A/B/A frames")

    @property
    def complete(self) -> bool:
        return (
            self.positive_step is not None
            and self.positive_step.completed
            and self.return_step is not None
            and self.return_step.completed
            and self.comparison is not None
        )


@dataclass(frozen=True, slots=True)
class CameraSystemIdResult:
    """Horizontal A/B/A and an optional conditional vertical A/B/A."""

    horizontal: CameraSystemIdAxisResult
    vertical: CameraSystemIdAxisResult | None
    conclusion: CameraSystemIdConclusion | None
    detail: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError("system-ID result detail must not be empty")
        horizontal_usable = (
            self.horizontal.comparison is not None
            and self.horizontal.complete
            and self.horizontal.comparison.derivative_usable
        )
        vertical_usable = (
            self.vertical is not None
            and self.vertical.comparison is not None
            and self.vertical.complete
            and self.vertical.comparison.derivative_usable
        )
        if self.vertical is not None and not horizontal_usable:
            raise ValueError("vertical A/B/A requires a usable horizontal derivative")
        expected: CameraSystemIdConclusion | None
        if self.horizontal.complete and not horizontal_usable:
            expected = CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED
        elif horizontal_usable and self.vertical is not None and self.vertical.complete:
            expected = (
                CameraSystemIdConclusion.CONTROL_DERIVATIVE_USABLE
                if vertical_usable
                else CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED
            )
        else:
            expected = None
        if self.conclusion is not expected:
            raise ValueError("system-ID conclusion does not match the A/B/A evidence")

    @property
    def conclusive(self) -> bool:
        return self.conclusion is not None


type _InputGuard = Callable[
    [CameraServoFrameEvidence, CameraServoFrameEvidence, CameraServoFrameEvidence],
    None,
]


def _fixed_plan(axis: CameraSystemIdAxis, direction: int) -> CameraPlan:
    if not isinstance(axis, CameraSystemIdAxis):
        raise ValueError("system-identification axis is invalid")
    if isinstance(direction, bool) or direction not in (-1, 1):
        raise ValueError("system-identification direction must be -1 or +1")
    drag_axis = (
        CameraDragAxis.HORIZONTAL
        if axis is CameraSystemIdAxis.HORIZONTAL
        else CameraDragAxis.VERTICAL
    )
    sign_name = "positive" if direction > 0 else "negative"
    return CameraPlan(
        f"issue31-system-id-{axis.value}-{sign_name}",
        (
            CameraMiddleDrag(
                drag_axis,
                direction * CAMERA_SYSTEM_ID_DRAG_PIXELS,
                *REVIEWED_CAMERA_DRAG_POINT,
            ),
        ),
    )


def _vector_length(x: int, y: int) -> float:
    return math.hypot(float(x), float(y))


def _compare_landmarks(
    axis: CameraSystemIdAxis,
    baseline_one: CameraSystemIdObservation,
    baseline_two: CameraSystemIdObservation,
    positive_arm: CameraSystemIdObservation,
    positive_commit: CameraSystemIdObservation,
    positive: CameraSystemIdObservation,
    return_arm: CameraSystemIdObservation,
    return_commit: CameraSystemIdObservation,
    returned: CameraSystemIdObservation,
) -> CameraSystemIdComparison:
    analyses = tuple(
        observation.guidance.analysis
        for observation in (
            baseline_one,
            baseline_two,
            positive_arm,
            positive_commit,
            positive,
            return_arm,
            return_commit,
            returned,
        )
    )
    if any(analysis is None for analysis in analyses):
        raise ValueError("every A/B/A observation requires wide analysis")
    resolved = tuple(analysis for analysis in analyses if analysis is not None)
    by_id: list[dict[str, WideLandmarkSearch]] = []
    for analysis in resolved:
        if (
            analysis.search_radius != MAXIMUM_WIDE_REGISTRATION_RADIUS
            or analysis.coarse_step != DEFAULT_WIDE_COARSE_STEP
            or analysis.refinement_radius != DEFAULT_WIDE_REFINEMENT_RADIUS
        ):
            raise ValueError("wide A/B/A search configuration changed")
        if tuple(item.landmark_id for item in analysis.landmarks) != _FROZEN_LANDMARK_IDS:
            raise ValueError("wide A/B/A landmark catalog changed")
        mapping = {item.landmark_id: item for item in analysis.landmarks}
        if len(mapping) != len(analysis.landmarks):
            raise ValueError("wide analysis contains duplicate landmark IDs")
        for landmark_id, item in mapping.items():
            expected_zone, expected_threshold = _FROZEN_LANDMARKS[landmark_id]
            if item.zone is not expected_zone or item.maximum_distance != expected_threshold:
                raise ValueError("wide A/B/A landmark identity changed")
        by_id.append(mapping)
    comparisons: list[CameraSystemIdLandmarkComparison] = []
    for landmark_id in _FROZEN_LANDMARK_IDS:
        (
            first,
            second,
            positive_arm_search,
            positive_commit_search,
            probe,
            return_arm_search,
            return_commit_search,
            final,
        ) = (mapping[landmark_id] for mapping in by_id)
        comparisons.append(
            CameraSystemIdLandmarkComparison(
                landmark_id=landmark_id,
                zone=_FROZEN_LANDMARKS[landmark_id][0],
                axis=axis,
                search_radius=MAXIMUM_WIDE_REGISTRATION_RADIUS,
                baseline_one=first,
                baseline_two=second,
                positive_arm=positive_arm_search,
                positive_commit=positive_commit_search,
                positive=probe,
                return_arm=return_arm_search,
                return_commit=return_commit_search,
                returned=final,
            )
        )
    common_zones = tuple(
        zone
        for zone in MacroZone
        if any(item.strictly_matched and item.zone is zone for item in comparisons)
    )
    individually_qualified = tuple(item for item in comparisons if item.qualified)
    signs = {
        1 if item.tested_axis_positive_delta > 0 else -1
        for item in individually_qualified
    }
    coherent_sign = next(iter(signs)) if len(signs) == 1 else None
    qualified = individually_qualified if coherent_sign is not None else ()
    qualified_zones = tuple(
        zone for zone in MacroZone if any(item.zone is zone for item in qualified)
    )
    usable = (
        len(qualified) >= _REQUIRED_LANDMARKS
        and qualified_zones == _FROZEN_ZONES
    )
    detail = (
        f"{len(qualified)}/{len(comparisons)} common strict matches produced "
        f"coherent commit-to-post, descriptor-stable, above-jitter reversible "
        f"displacement across "
        f"{len(qualified_zones)} zones; required {_REQUIRED_LANDMARKS} "
        f"landmarks across {_REQUIRED_ZONES} zones."
    )
    return CameraSystemIdComparison(
        axis=axis,
        landmarks=tuple(comparisons),
        common_matched_zones=common_zones,
        qualified_landmark_ids=tuple(item.landmark_id for item in qualified),
        qualified_zones=qualified_zones,
        coherent_forward_sign=coherent_sign,
        required_landmarks=_REQUIRED_LANDMARKS,
        required_zones=_REQUIRED_ZONES,
        derivative_usable=usable,
        detail=detail,
    )


class _ClockError(ValueError):
    pass


def _read_clock(clock: Callable[[], float]) -> float:
    try:
        value = clock()
    except Exception as error:
        raise _ClockError("monotonic clock raised") from error
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
    ):
        raise _ClockError("monotonic clock must return a finite non-negative value")
    return float(value)


def _completed_age(origin: float, final: float) -> CameraServoArmAgeEvidence:
    if final < origin:
        raise _ClockError("monotonic clock regressed during the arm seam")
    age = final - origin
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin,
        final_clock_s=final,
        age_s=age,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=(
            CameraServoArmAgeStatus.EXPIRED
            if age >= MAXIMUM_ARM_TO_INPUT_AGE_SECONDS
            else CameraServoArmAgeStatus.WITHIN_LIMIT
        ),
    )


def _arm_age_not_reached(origin: float) -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.NOT_REACHED,
    )


def _clock_error_age(origin: float) -> CameraServoArmAgeEvidence:
    return CameraServoArmAgeEvidence(
        origin_clock_s=origin,
        final_clock_s=None,
        age_s=None,
        maximum_age_s=MAXIMUM_ARM_TO_INPUT_AGE_SECONDS,
        status=CameraServoArmAgeStatus.FINAL_CLOCK_ERROR,
    )


def _input_delivery_duration(start: float, receipt: float) -> float:
    if receipt < start:
        raise _ClockError("monotonic clock regressed during input delivery")
    return receipt - start


def _strictly_newer(before: Frame, after: Frame) -> bool:
    return (
        after.frame_id > before.frame_id
        and after.captured_monotonic_s > before.captured_monotonic_s
    )


def _record_verified_frame(
    recorder: CameraArtifactRecorder,
    label: str,
    frame: Frame,
) -> CameraFrameArtifact:
    artifact = recorder(label, frame)
    if (
        artifact.label != label
        or artifact.frame_id != frame.frame_id
        or artifact.width != frame.width
        or artifact.height != frame.height
        or artifact.pixel_format != frame.pixel_format.value
        or artifact.raw_sha256 != hashlib.sha256(frame.payload).hexdigest()
    ):
        raise ValueError("recorder artifact does not bind the exact captured frame")
    return artifact


def _evaluate_frame(
    frame: Frame,
    recorder: CameraArtifactRecorder,
    label: str,
) -> CameraServoFrameEvidence:
    artifact = _record_verified_frame(recorder, label, frame)
    readiness = evaluate_client_input_readiness(frame)
    production = (
        evaluate_varrock_east_camera(frame)
        if readiness.safe_to_attempt_camera_input
        else None
    )
    return CameraServoFrameEvidence(
        artifact=artifact,
        captured_monotonic_s=frame.captured_monotonic_s,
        readiness=readiness,
        production=production,
    )


def _capture_observation(
    source: CameraFrameSource,
    recorder: CameraArtifactRecorder,
    label: str,
) -> CameraSystemIdObservation:
    frame = source.capture()
    evidence = _evaluate_frame(frame, recorder, label)
    guidance = evaluate_varrock_east_camera_guidance(frame)
    return CameraSystemIdObservation(frame, evidence, guidance)


def _capture_frame_evidence(
    source: CameraFrameSource,
    recorder: CameraArtifactRecorder,
    label: str,
) -> tuple[Frame, CameraServoFrameEvidence]:
    frame = source.capture()
    return frame, _evaluate_frame(frame, recorder, label)


def _is_fail_closed(evaluation: CameraEvaluation) -> bool:
    return (
        not evaluation.passed
        and not evaluation.scene_validated
        and evaluation.definitive_target_ids == ()
        and bool(evaluation.resource_states)
        and all(
            item.state is ResourceVisualState.UNCERTAIN
            for item in evaluation.resource_states
        )
    )


def _frame_terminal(
    evidence: CameraServoFrameEvidence,
) -> tuple[CameraSystemIdStepTerminalReason, str] | None:
    if not evidence.readiness.safe_to_attempt_camera_input:
        return (
            CameraSystemIdStepTerminalReason.READINESS_LOST,
            "Gameplay readiness vetoed camera input.",
        )
    production = evidence.production
    assert production is not None
    if production.passed:
        return None
    if not _is_fail_closed(production):
        return (
            CameraSystemIdStepTerminalReason.PRODUCTION_REJECTION_NOT_FAIL_CLOSED,
            "Production rejection did not hide every definitive resource target.",
        )
    return None


def _guard_binds(
    guard: CameraArmGuardResult,
    *,
    before: CameraServoFrameEvidence,
    after: CameraServoFrameEvidence,
) -> bool:
    return (
        guard.decision_frame_id == before.artifact.frame_id
        and guard.decision_captured_monotonic_s == before.captured_monotonic_s
        and guard.decision_payload_sha256 == before.artifact.raw_sha256
        and guard.arm_frame_id == after.artifact.frame_id
        and guard.arm_captured_monotonic_s == after.captured_monotonic_s
        and guard.arm_payload_sha256 == after.artifact.raw_sha256
    )


def _exception(error: Exception | None) -> CameraServoExceptionEvidence | None:
    if error is None:
        return None
    return CameraServoExceptionEvidence(type(error).__name__, str(error))


class _PreparedCameraControl:
    """Replay one completed preflight while retaining runner safety."""

    __slots__ = ("_control", "_input_attempted", "_preflight", "_served")

    def __init__(
        self,
        control: CameraControl,
        preflight: CameraPreflightReceipt,
    ) -> None:
        self._control = control
        self._preflight = preflight
        self._served = False
        self._input_attempted = False

    @property
    def input_attempted(self) -> bool:
        return self._input_attempted

    def preflight(self) -> CameraPreflightReceipt:
        if self._served:
            raise RuntimeError("prepared preflight is single-use")
        self._served = True
        return self._preflight

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        self._input_attempted = True
        return self._control.click_compass(x, y)

    def key_down(self, key: str) -> CameraInputReceipt:
        self._input_attempted = True
        return self._control.key_down(key)

    def key_up(self, key: str) -> CameraInputReceipt:
        self._input_attempted = True
        return self._control.key_up(key)

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        self._input_attempted = True
        return self._control.scroll_camera(x, y, detents)

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        self._input_attempted = True
        return self._control.drag_camera(x, y, delta_x, delta_y)


def _step_result(
    *,
    axis: CameraSystemIdAxis,
    direction: int,
    decision: CameraSystemIdObservation,
    plan: CameraPlan,
    terminal_reason: CameraSystemIdStepTerminalReason,
    detail: str,
    arm: CameraServoFrameEvidence | None = None,
    arm_observation: CameraSystemIdObservation | None = None,
    arm_guard: CameraArmGuardResult | None = None,
    preflight: CameraPreflightReceipt | None = None,
    commit: CameraServoFrameEvidence | None = None,
    commit_observation: CameraSystemIdObservation | None = None,
    commit_guard: CameraArmGuardResult | None = None,
    decision_commit_guard: CameraArmGuardResult | None = None,
    arm_age: CameraServoArmAgeEvidence | None = None,
    receipt: CameraPlanReceipt | None = None,
    post: CameraSystemIdObservation | None = None,
    input_state: CameraSystemIdInputState | None = None,
    input_start_clock_s: float | None = None,
    input_receipt_clock_s: float | None = None,
    input_delivery_duration_s: float | None = None,
    error: Exception | None = None,
) -> CameraSystemIdStepResult:
    resolved_input_state = (
        CameraSystemIdInputState.COMPLETE
        if receipt is not None
        else CameraSystemIdInputState.NONE
        if input_state is None
        else input_state
    )
    return CameraSystemIdStepResult(
        axis=axis,
        direction=direction,
        decision=decision,
        plan=plan,
        arm=arm,
        arm_observation=arm_observation,
        arm_guard=arm_guard,
        preflight=receipt.preflight if receipt is not None else preflight,
        commit=commit,
        commit_observation=commit_observation,
        commit_guard=commit_guard,
        decision_commit_guard=decision_commit_guard,
        arm_age=arm_age,
        receipt=receipt,
        post=post,
        terminal_reason=terminal_reason,
        detail=detail,
        input_state=resolved_input_state,
        input_start_clock_s=input_start_clock_s,
        input_receipt_clock_s=input_receipt_clock_s,
        input_delivery_duration_s=input_delivery_duration_s,
        exception=_exception(error),
    )


def _run_step(
    source: CameraFrameSource,
    control: CameraControl,
    *,
    axis: CameraSystemIdAxis,
    direction: int,
    decision: CameraSystemIdObservation,
    label_prefix: str,
    sleeper: Sleeper,
    recorder: CameraArtifactRecorder,
    clock: Callable[[], float],
    pre_input_guard: _InputGuard | None,
    final_input_guard: _InputGuard | None,
) -> CameraSystemIdStepResult:
    plan = _fixed_plan(axis, direction)
    terminal = _frame_terminal(decision.evidence)
    if terminal is not None:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            terminal_reason=terminal[0],
            detail=f"Decision veto: {terminal[1]}",
        )
    try:
        arm_frame = source.capture()
        arm_origin_clock_s = _read_clock(clock)
        arm = _evaluate_frame(arm_frame, recorder, f"{label_prefix}-arm")
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            terminal_reason=(
                CameraSystemIdStepTerminalReason.CLOCK_ERROR
                if isinstance(error, _ClockError)
                else CameraSystemIdStepTerminalReason.OBSERVATION_EXCEPTION
            ),
            detail="Fresh arm capture, recording, evaluation, or clock failed closed.",
            error=error,
        )
    if not _strictly_newer(decision.frame, arm_frame):
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            terminal_reason=CameraSystemIdStepTerminalReason.NON_FRESH_OBSERVATION,
            detail="The arm frame was not strictly newer than the decision frame.",
        )
    terminal = _frame_terminal(arm)
    if terminal is not None:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            terminal_reason=terminal[0],
            detail=f"Arm veto: {terminal[1]}",
        )
    try:
        arm_guard = evaluate_camera_arm_guard(decision.frame, arm_frame)
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            terminal_reason=CameraSystemIdStepTerminalReason.OBSERVATION_EXCEPTION,
            detail="Decision-to-arm structural guard failed closed.",
            error=error,
        )
    if arm_guard.disposition is not CameraArmGuardDisposition.RETAIN:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            terminal_reason=CameraSystemIdStepTerminalReason.WORLD_CHANGED,
            detail="World structure changed before platform preflight.",
        )
    try:
        preflight = control.preflight()
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            terminal_reason=CameraSystemIdStepTerminalReason.INPUT_EXCEPTION,
            detail="No-camera-input platform preflight failed closed.",
            error=error,
        )
    try:
        commit_frame, commit = _capture_frame_evidence(
            source,
            recorder,
            f"{label_prefix}-commit",
        )
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            terminal_reason=CameraSystemIdStepTerminalReason.OBSERVATION_EXCEPTION,
            detail="Final commit capture, recording, or evaluation failed closed.",
            error=error,
        )
    if not _strictly_newer(arm_frame, commit_frame):
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            terminal_reason=CameraSystemIdStepTerminalReason.NON_FRESH_OBSERVATION,
            detail="The final commit was not strictly newer than the arm frame.",
        )
    terminal = _frame_terminal(commit)
    if terminal is not None:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            terminal_reason=terminal[0],
            detail=f"Final commit veto: {terminal[1]}",
        )
    try:
        commit_guard = evaluate_camera_arm_guard(arm_frame, commit_frame)
        decision_commit_guard = evaluate_camera_arm_guard(
            decision.frame,
            commit_frame,
        )
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            terminal_reason=CameraSystemIdStepTerminalReason.OBSERVATION_EXCEPTION,
            detail="Final world-only structural guard failed closed.",
            error=error,
        )
    if (
        not _guard_binds(arm_guard, before=decision.evidence, after=arm)
        or not _guard_binds(commit_guard, before=arm, after=commit)
        or not _guard_binds(
            decision_commit_guard,
            before=decision.evidence,
            after=commit,
        )
        or commit_guard.disposition is not CameraArmGuardDisposition.RETAIN
        or decision_commit_guard.disposition
        is not CameraArmGuardDisposition.RETAIN
    ):
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            terminal_reason=CameraSystemIdStepTerminalReason.WORLD_CHANGED,
            detail="The final commit did not retain the exact guarded world chain.",
        )
    try:
        if pre_input_guard is not None:
            pre_input_guard(decision.evidence, arm, commit)
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=_arm_age_not_reached(arm_origin_clock_s),
            terminal_reason=CameraSystemIdStepTerminalReason.OBSERVATION_EXCEPTION,
            detail="The external provenance/identity guard failed closed.",
            error=error,
        )
    try:
        if final_input_guard is not None:
            final_input_guard(decision.evidence, arm, commit)
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=_arm_age_not_reached(arm_origin_clock_s),
            terminal_reason=CameraSystemIdStepTerminalReason.OBSERVATION_EXCEPTION,
            detail="The last-seam external input guard failed closed.",
            error=error,
        )
    try:
        input_start_clock_s = _read_clock(clock)
        arm_age = _completed_age(arm_origin_clock_s, input_start_clock_s)
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=_clock_error_age(arm_origin_clock_s),
            terminal_reason=CameraSystemIdStepTerminalReason.CLOCK_ERROR,
            detail="The final arm-age clock sample failed closed.",
            error=error,
        )
    if arm_age.status is CameraServoArmAgeStatus.EXPIRED:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            terminal_reason=CameraSystemIdStepTerminalReason.ARM_FRESHNESS_EXPIRED,
            detail="The independent arm-to-input age reached its exclusive limit.",
        )
    prepared_control = _PreparedCameraControl(control, preflight)
    try:
        receipt = CameraPlanRunner(prepared_control, sleeper).run(plan)
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            input_state=(
                CameraSystemIdInputState.PARTIAL_OR_UNKNOWN
                if prepared_control.input_attempted
                else CameraSystemIdInputState.NONE
            ),
            input_start_clock_s=(
                input_start_clock_s if prepared_control.input_attempted else None
            ),
            terminal_reason=CameraSystemIdStepTerminalReason.INPUT_EXCEPTION,
            detail="Fixed drag input or receipt validation failed closed.",
            error=error,
        )
    try:
        receipt_clock_s = _read_clock(clock)
        delivery_duration_s = _input_delivery_duration(
            input_start_clock_s,
            receipt_clock_s,
        )
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            input_start_clock_s=input_start_clock_s,
            terminal_reason=CameraSystemIdStepTerminalReason.CLOCK_ERROR,
            detail="The immediate post-receipt clock sample failed closed.",
            error=error,
        )
    try:
        sleeper(CAMERA_SYSTEM_ID_SETTLE_SECONDS)
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            input_start_clock_s=input_start_clock_s,
            input_receipt_clock_s=receipt_clock_s,
            input_delivery_duration_s=delivery_duration_s,
            terminal_reason=CameraSystemIdStepTerminalReason.SETTLE_EXCEPTION,
            detail="Post-drag settle failed after acknowledged input.",
            error=error,
        )
    try:
        post = _capture_observation(
            source,
            recorder,
            f"{label_prefix}-post",
        )
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            input_start_clock_s=input_start_clock_s,
            input_receipt_clock_s=receipt_clock_s,
            input_delivery_duration_s=delivery_duration_s,
            terminal_reason=CameraSystemIdStepTerminalReason.OBSERVATION_EXCEPTION,
            detail="Post-drag capture, recording, or evaluation failed.",
            error=error,
        )
    if not _strictly_newer(commit_frame, post.frame):
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            post=post,
            input_start_clock_s=input_start_clock_s,
            input_receipt_clock_s=receipt_clock_s,
            input_delivery_duration_s=delivery_duration_s,
            terminal_reason=CameraSystemIdStepTerminalReason.NON_FRESH_OBSERVATION,
            detail="The post-drag frame was not strictly newer than commit.",
        )
    try:
        # Wide registration is deliberately post-hoc for the already captured
        # arm/commit frames.  It cannot influence input delivery and therefore
        # cannot turn diagnostic latency into a stale physical-input seam.
        arm_observation = CameraSystemIdObservation(
            arm_frame,
            arm,
            evaluate_varrock_east_camera_guidance(arm_frame),
        )
        commit_observation = CameraSystemIdObservation(
            commit_frame,
            commit,
            evaluate_varrock_east_camera_guidance(commit_frame),
        )
    except Exception as error:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            post=post,
            input_start_clock_s=input_start_clock_s,
            input_receipt_clock_s=receipt_clock_s,
            input_delivery_duration_s=delivery_duration_s,
            terminal_reason=CameraSystemIdStepTerminalReason.OBSERVATION_EXCEPTION,
            detail="Post-hoc arm/commit world diagnostics failed after input.",
            error=error,
        )
    terminal = _frame_terminal(post.evidence)
    if terminal is not None:
        return _step_result(
            axis=axis,
            direction=direction,
            decision=decision,
            plan=plan,
            arm=arm,
            arm_observation=arm_observation,
            arm_guard=arm_guard,
            preflight=preflight,
            commit=commit,
            commit_observation=commit_observation,
            commit_guard=commit_guard,
            decision_commit_guard=decision_commit_guard,
            arm_age=arm_age,
            receipt=receipt,
            post=post,
            input_start_clock_s=input_start_clock_s,
            input_receipt_clock_s=receipt_clock_s,
            input_delivery_duration_s=delivery_duration_s,
            terminal_reason=terminal[0],
            detail=f"Post-drag veto: {terminal[1]}",
        )
    return _step_result(
        axis=axis,
        direction=direction,
        decision=decision,
        plan=plan,
        arm=arm,
        arm_observation=arm_observation,
        arm_guard=arm_guard,
        preflight=preflight,
        commit=commit,
        commit_observation=commit_observation,
        commit_guard=commit_guard,
        decision_commit_guard=decision_commit_guard,
        arm_age=arm_age,
        receipt=receipt,
        post=post,
        input_start_clock_s=input_start_clock_s,
        input_receipt_clock_s=receipt_clock_s,
        input_delivery_duration_s=delivery_duration_s,
        terminal_reason=CameraSystemIdStepTerminalReason.COMPLETE,
        detail=(
            "One fixed four-pixel drag completed with a fresh production-safe "
            "post frame and exact post-hoc arm/commit diagnostics."
        ),
    )


def _axis_result(
    axis: CameraSystemIdAxis,
    detail: str,
    *,
    baseline_one: CameraSystemIdObservation | None = None,
    baseline_two: CameraSystemIdObservation | None = None,
    baseline_guard: CameraArmGuardResult | None = None,
    positive_step: CameraSystemIdStepResult | None = None,
    return_step: CameraSystemIdStepResult | None = None,
    comparison: CameraSystemIdComparison | None = None,
) -> CameraSystemIdAxisResult:
    return CameraSystemIdAxisResult(
        axis=axis,
        baseline_one=baseline_one,
        baseline_two=baseline_two,
        baseline_guard=baseline_guard,
        positive_step=positive_step,
        return_step=return_step,
        comparison=comparison,
        detail=detail,
    )


def _run_axis_system_id(
    source: CameraFrameSource,
    control: CameraControl,
    *,
    axis: CameraSystemIdAxis,
    sleeper: Sleeper,
    recorder: CameraArtifactRecorder,
    clock: Callable[[], float],
    pre_input_guard: _InputGuard | None,
    final_input_guard: _InputGuard | None,
) -> CameraSystemIdAxisResult:
    prefix = f"system-id-{axis.value}"
    try:
        baseline_one = _capture_observation(
            source,
            recorder,
            f"{prefix}-baseline-01",
        )
    except Exception as error:
        return _axis_result(
            axis,
            f"First no-input baseline failed: {type(error).__name__}: {error}",
        )
    terminal = _frame_terminal(baseline_one.evidence)
    if terminal is not None:
        return _axis_result(
            axis,
            f"First no-input baseline veto: {terminal[1]}",
            baseline_one=baseline_one,
        )
    try:
        sleeper(CAMERA_SYSTEM_ID_SETTLE_SECONDS)
    except Exception as error:
        return _axis_result(
            axis,
            f"No-input baseline settle failed: {type(error).__name__}: {error}",
            baseline_one=baseline_one,
        )
    try:
        baseline_two = _capture_observation(
            source,
            recorder,
            f"{prefix}-baseline-02",
        )
    except Exception as error:
        return _axis_result(
            axis,
            f"Second no-input baseline failed: {type(error).__name__}: {error}",
            baseline_one=baseline_one,
        )
    if not _strictly_newer(baseline_one.frame, baseline_two.frame):
        return _axis_result(
            axis,
            "Second no-input baseline was not strictly newer than the first.",
            baseline_one=baseline_one,
            baseline_two=baseline_two,
        )
    terminal = _frame_terminal(baseline_two.evidence)
    if terminal is not None:
        return _axis_result(
            axis,
            f"Second no-input baseline veto: {terminal[1]}",
            baseline_one=baseline_one,
            baseline_two=baseline_two,
        )
    try:
        baseline_guard = evaluate_camera_arm_guard(
            baseline_one.frame,
            baseline_two.frame,
        )
    except Exception as error:
        return _axis_result(
            axis,
            f"No-input baseline guard failed: {type(error).__name__}: {error}",
            baseline_one=baseline_one,
            baseline_two=baseline_two,
        )
    if baseline_guard.disposition is not CameraArmGuardDisposition.RETAIN:
        return _axis_result(
            axis,
            "Natural baseline frames did not retain stable world structure.",
            baseline_one=baseline_one,
            baseline_two=baseline_two,
            baseline_guard=baseline_guard,
        )
    positive_step = _run_step(
        source,
        control,
        axis=axis,
        direction=1,
        decision=baseline_two,
        label_prefix=f"{prefix}-positive",
        sleeper=sleeper,
        recorder=recorder,
        clock=clock,
        pre_input_guard=pre_input_guard,
        final_input_guard=final_input_guard,
    )
    if not positive_step.completed or positive_step.post is None:
        return _axis_result(
            axis,
            f"Positive pulse stopped: {positive_step.detail}",
            baseline_one=baseline_one,
            baseline_two=baseline_two,
            baseline_guard=baseline_guard,
            positive_step=positive_step,
        )
    return_step = _run_step(
        source,
        control,
        axis=axis,
        direction=-1,
        decision=positive_step.post,
        label_prefix=f"{prefix}-return",
        sleeper=sleeper,
        recorder=recorder,
        clock=clock,
        pre_input_guard=pre_input_guard,
        final_input_guard=final_input_guard,
    )
    if not return_step.completed or return_step.post is None:
        return _axis_result(
            axis,
            f"Return pulse stopped: {return_step.detail}",
            baseline_one=baseline_one,
            baseline_two=baseline_two,
            baseline_guard=baseline_guard,
            positive_step=positive_step,
            return_step=return_step,
        )
    try:
        assert positive_step.arm_observation is not None
        assert positive_step.commit_observation is not None
        assert return_step.arm_observation is not None
        assert return_step.commit_observation is not None
        comparison = _compare_landmarks(
            axis,
            baseline_one,
            baseline_two,
            positive_step.arm_observation,
            positive_step.commit_observation,
            positive_step.post,
            return_step.arm_observation,
            return_step.commit_observation,
            return_step.post,
        )
    except Exception as error:
        return _axis_result(
            axis,
            f"A/B/A comparison failed: {type(error).__name__}: {error}",
            baseline_one=baseline_one,
            baseline_two=baseline_two,
            baseline_guard=baseline_guard,
            positive_step=positive_step,
            return_step=return_step,
        )
    return _axis_result(
        axis,
        comparison.detail,
        baseline_one=baseline_one,
        baseline_two=baseline_two,
        baseline_guard=baseline_guard,
        positive_step=positive_step,
        return_step=return_step,
        comparison=comparison,
    )


def run_fixed_camera_system_identification(
    source: CameraFrameSource,
    control: CameraControl,
    *,
    sleeper: Sleeper,
    recorder: CameraArtifactRecorder = record_frame_digest,
    clock: Callable[[], float] = time.monotonic,
    pre_input_guard: _InputGuard | None = None,
    final_input_guard: _InputGuard | None = None,
) -> CameraSystemIdResult:
    """Run the fixed horizontal A/B/A and conditionally the vertical A/B/A.

    The action catalog is not caller-configurable. Horizontal must complete and
    satisfy the strict distributed response gate before any vertical capture or
    input begins. A usable final conclusion requires both axes; a completed but
    non-usable axis retires the landmark-only controller. Safety/observation
    failures remain explicitly inconclusive rather than being mislabeled as a
    control-model result.
    """

    horizontal = _run_axis_system_id(
        source,
        control,
        axis=CameraSystemIdAxis.HORIZONTAL,
        sleeper=sleeper,
        recorder=recorder,
        clock=clock,
        pre_input_guard=pre_input_guard,
        final_input_guard=final_input_guard,
    )
    if not horizontal.complete or horizontal.comparison is None:
        return CameraSystemIdResult(
            horizontal=horizontal,
            vertical=None,
            conclusion=None,
            detail="Horizontal A/B/A did not reach a conclusive comparison.",
        )
    if not horizontal.comparison.derivative_usable:
        return CameraSystemIdResult(
            horizontal=horizontal,
            vertical=None,
            conclusion=CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED,
            detail=(
                "Horizontal response was non-measurable, non-reversible, or "
                "not distributed across the three frozen macro zones."
            ),
        )
    vertical = _run_axis_system_id(
        source,
        control,
        axis=CameraSystemIdAxis.VERTICAL,
        sleeper=sleeper,
        recorder=recorder,
        clock=clock,
        pre_input_guard=pre_input_guard,
        final_input_guard=final_input_guard,
    )
    if not vertical.complete or vertical.comparison is None:
        return CameraSystemIdResult(
            horizontal=horizontal,
            vertical=vertical,
            conclusion=None,
            detail="Vertical A/B/A did not reach a conclusive comparison.",
        )
    if not vertical.comparison.derivative_usable:
        return CameraSystemIdResult(
            horizontal=horizontal,
            vertical=vertical,
            conclusion=CameraSystemIdConclusion.LANDMARK_CONTROLLER_RETIRED,
            detail=(
                "Horizontal response qualified, but the mandatory vertical "
                "response did not; the landmark-only multi-axis controller is retired."
            ),
        )
    return CameraSystemIdResult(
        horizontal=horizontal,
        vertical=vertical,
        conclusion=CameraSystemIdConclusion.CONTROL_DERIVATIVE_USABLE,
        detail=(
            "Both fixed A/B/A axes produced strict, distributed, above-jitter, "
            "reversible landmark response."
        ),
    )
