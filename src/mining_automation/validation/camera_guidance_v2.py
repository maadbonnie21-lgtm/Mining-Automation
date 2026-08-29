"""Session-bound, refusal-oriented multi-axis guidance for Issue #31.

This development-only module consumes the profile-bound v1 world evidence and
may describe at most one bounded camera primitive. It cannot validate a scene
or expose a resource. A one-time north bootstrap is owned by a session; yaw
and pitch can request a signed calibration probe but cannot become corrections
until an evidence-derived calibration path is reviewed. Zoom preserves the
exact v1 sign and policy.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ..capture import Frame
from ..perception.production_profiles import (
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from .camera_guidance import (
    CAMERA_GUIDANCE_ID,
    CAMERA_GUIDANCE_VERSION,
    CameraGuidanceAxis,
    CameraGuidanceDirection,
    CameraGuidanceDisposition,
    CameraGuidanceReason,
    WorldCameraGuidance,
    evaluate_varrock_east_camera_guidance,
)
from .camera_plan import (
    REVIEWED_CAMERA_WHEEL_POINT,
    REVIEWED_COMPASS_POINT,
    CameraAction,
    CameraDragAxis,
    CameraInputOperation,
    CameraMiddleDrag,
    CameraPlan,
    CameraPlanReceipt,
    CameraWheel,
    CompassClick,
)
from .client_readiness import GAMEPLAY_CHROME_POLICIES

__all__ = [
    "CAMERA_GUIDANCE_V2_DRAG_PULSE_PIXELS",
    "CAMERA_GUIDANCE_V2_ID",
    "CAMERA_GUIDANCE_V2_VERSION",
    "CameraGuidanceV2Disposition",
    "CameraGuidanceV2Reason",
    "CameraGuidanceV2Session",
    "CameraPrimitiveAxis",
    "CameraTransformError",
    "WorldCameraGuidanceV2",
    "build_camera_guidance_v2_plan",
    "build_camera_guidance_v2_probe",
    "select_camera_guidance_v2",
]

CAMERA_GUIDANCE_V2_ID: Final[str] = "issue31-world-only-multi-axis-guidance"
CAMERA_GUIDANCE_V2_VERSION: Final[str] = "2.0.0"
CAMERA_GUIDANCE_V2_DRAG_PULSE_PIXELS: Final[int] = 4

_MINIMUM_AXIS_SCORE: Final[float] = 1.0
_AXIS_DOMINANCE_RATIO: Final[float] = 1.5

_PROFILE = load_varrock_east_iron_profile()
_TRUSTED_EXCLUDED_REGIONS: Final[tuple[tuple[int, int, int, int], ...]] = tuple(
    dict.fromkeys(
        (
            *varrock_east_iron_scene_excluded_regions(_PROFILE),
            *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
        )
    )
)


class CameraPrimitiveAxis(StrEnum):
    """The only independently executable V2 camera axes."""

    HEADING = "heading"
    YAW = "yaw"
    PITCH = "pitch"
    ZOOM = "zoom"


class CameraGuidanceV2Disposition(StrEnum):
    """Whether V2 authorizes a correction, a probe, or no input."""

    ACTIONABLE_BOOTSTRAP = "actionable_bootstrap"
    ACTIONABLE_CORRECTION = "actionable_correction"
    CALIBRATION_REQUIRED = "calibration_required"
    INSUFFICIENT_GUIDANCE = "insufficient_guidance"


class CameraGuidanceV2Reason(StrEnum):
    """Stable reason attached to one V2 decision."""

    DETERMINISTIC_NORTH_BOOTSTRAP = "deterministic_north_bootstrap"
    NORTH_BOOTSTRAP_RESERVED = "north_bootstrap_reserved"
    REVIEWED_ZOOM_SIGN = "reviewed_zoom_sign"
    SIGNED_EFFECT_REQUIRED = "signed_effect_required"
    UNSUPPORTED_OR_INCOHERENT = "unsupported_or_incoherent"
    INSUFFICIENT_DISTRIBUTED_EVIDENCE = "insufficient_distributed_evidence"
    AMBIGUOUS_AXIS = "ambiguous_axis"
    WITHIN_DEADBAND = "within_deadband"


@dataclass(frozen=True, slots=True)
class CameraTransformError:
    """Normalized world-only error vector from a coherent v1 fit."""

    log_scale: float
    rotation: float
    horizontal_shift: float
    vertical_shift: float

    def __post_init__(self) -> None:
        for name, value in (
            ("log_scale", self.log_scale),
            ("rotation", self.rotation),
            ("horizontal_shift", self.horizontal_shift),
            ("vertical_shift", self.vertical_shift),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"transform {name} must be finite")

    @property
    def norm(self) -> float:
        """Return the Euclidean normalized error magnitude."""

        return math.sqrt(
            self.log_scale * self.log_scale
            + self.rotation * self.rotation
            + self.horizontal_shift * self.horizontal_shift
            + self.vertical_shift * self.vertical_shift
        )


@dataclass(frozen=True, slots=True)
class WorldCameraGuidanceV2:
    """One immutable V2 decision with no scene authority."""

    selector_id: str
    selector_version: str
    disposition: CameraGuidanceV2Disposition
    reason: CameraGuidanceV2Reason
    detail: str
    base_guidance: WorldCameraGuidance
    decision_frame_id: int
    decision_captured_monotonic_s: float
    decision_raw_sha256: str
    heading_was_normalized: bool
    axis: CameraPrimitiveAxis | None
    direction: CameraGuidanceDirection | None
    transform_error: CameraTransformError | None
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.selector_id != CAMERA_GUIDANCE_V2_ID:
            raise ValueError("V2 selector_id must use the frozen identity")
        if self.selector_version != CAMERA_GUIDANCE_V2_VERSION:
            raise ValueError("V2 selector_version must use the frozen version")
        if not isinstance(self.disposition, CameraGuidanceV2Disposition):
            raise ValueError("V2 disposition is invalid")
        if not isinstance(self.reason, CameraGuidanceV2Reason):
            raise ValueError("V2 reason is invalid")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("V2 detail must not be empty")
        if not isinstance(self.heading_was_normalized, bool):
            raise ValueError("heading_was_normalized must be boolean")
        _require_trusted_base_guidance(self.base_guidance)
        if (
            isinstance(self.decision_frame_id, bool)
            or not isinstance(self.decision_frame_id, int)
            or self.decision_frame_id <= 0
        ):
            raise ValueError("V2 decision frame ID must be positive")
        if (
            isinstance(self.decision_captured_monotonic_s, bool)
            or not isinstance(self.decision_captured_monotonic_s, (int, float))
            or not math.isfinite(self.decision_captured_monotonic_s)
            or self.decision_captured_monotonic_s < 0.0
        ):
            raise ValueError("V2 decision timestamp must be finite and non-negative")
        if len(self.decision_raw_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.decision_raw_sha256
        ):
            raise ValueError("V2 decision hash must be a lowercase SHA-256 digest")
        if self.axis is not None and not isinstance(self.axis, CameraPrimitiveAxis):
            raise ValueError("V2 axis is invalid")
        if self.direction is not None and not isinstance(
            self.direction, CameraGuidanceDirection
        ):
            raise ValueError("V2 direction is invalid")
        if self.transform_error is not None and not isinstance(
            self.transform_error, CameraTransformError
        ):
            raise ValueError("V2 transform_error is invalid")
        expected_error = (
            _transform_error(self.base_guidance)
            if self.base_guidance.fit is not None
            else None
        )
        if self.transform_error != expected_error:
            raise ValueError("V2 transform error must bind the exact v1 fit")

        if self.disposition is CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP:
            if (
                self.heading_was_normalized
                or self.reason
                is not CameraGuidanceV2Reason.DETERMINISTIC_NORTH_BOOTSTRAP
                or self.base_guidance.disposition
                is not CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
                or self.base_guidance.reason
                is not CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS
                or self.base_guidance.fit is not None
                or self.axis is not CameraPrimitiveAxis.HEADING
                or self.direction is not None
            ):
                raise ValueError("north bootstrap fields do not bind trusted v1 evidence")
            return
        if self.disposition is CameraGuidanceV2Disposition.ACTIONABLE_CORRECTION:
            if (
                self.reason is not CameraGuidanceV2Reason.REVIEWED_ZOOM_SIGN
                or self.axis is not CameraPrimitiveAxis.ZOOM
                or self.direction is None
                or self.base_guidance.disposition
                is not CameraGuidanceDisposition.ACTIONABLE
                or self.base_guidance.axis is not CameraGuidanceAxis.ZOOM
                or self.base_guidance.direction is not self.direction
                or self.base_guidance.fit is None
            ):
                raise ValueError("V2 correction must be the exact reviewed v1 zoom sign")
            return
        if self.disposition is CameraGuidanceV2Disposition.CALIBRATION_REQUIRED:
            if (
                self.reason is not CameraGuidanceV2Reason.SIGNED_EFFECT_REQUIRED
                or self.base_guidance.reason
                is not CameraGuidanceReason.UNCALIBRATED_AXIS
                or self.axis not in (CameraPrimitiveAxis.YAW, CameraPrimitiveAxis.PITCH)
                or self.direction is not None
                or self.transform_error is None
                or _dominant_axis(self.transform_error) is not self.axis
            ):
                raise ValueError("probe request must bind one dominant uncalibrated axis")
            return
        if self.axis is not None or self.direction is not None:
            raise ValueError("insufficient guidance cannot authorize an axis or sign")


class CameraGuidanceV2Session:
    """Own the one-time north bootstrap state for one reacquisition attempt."""

    __slots__ = (
        "_north_reserved",
        "_north_receipt",
        "_reserved_frame",
        "_reserved_guidance",
        "_reserved_plan",
    )

    def __init__(self) -> None:
        self._north_reserved = False
        self._north_receipt: CameraPlanReceipt | None = None
        self._reserved_frame: Frame | None = None
        self._reserved_guidance: WorldCameraGuidanceV2 | None = None
        self._reserved_plan: CameraPlan | None = None

    def __copy__(self) -> CameraGuidanceV2Session:
        raise TypeError("camera guidance sessions cannot be copied")

    def __deepcopy__(self, _memo: object) -> CameraGuidanceV2Session:
        raise TypeError("camera guidance sessions cannot be copied")

    @property
    def heading_normalized(self) -> bool:
        """Return whether this session recorded one complete compass receipt."""

        return self._north_receipt is not None

    @property
    def north_bootstrap_reserved(self) -> bool:
        """Return whether this attempt already reserved its north action."""

        return self._north_reserved

    @property
    def north_receipt(self) -> CameraPlanReceipt | None:
        """Return the exact durable receipt completing north normalization."""

        return self._north_receipt

    @property
    def north_plan(self) -> CameraPlan | None:
        """Return the exact session-owned plan for its reserved token."""

        return self._reserved_plan

    def build_reserved_plan(
        self,
        guidance: WorldCameraGuidanceV2,
        frame: Frame,
        *,
        index: int,
    ) -> CameraPlan:
        """Return this session's exact one-time bootstrap plan object."""

        if isinstance(index, bool) or not isinstance(index, int) or index != 1:
            raise ValueError("north bootstrap reservation supports only index 1")
        if (
            guidance is not self._reserved_guidance
            or frame is not self._reserved_frame
            or self._reserved_plan is None
        ):
            raise ValueError("bootstrap plan requires this session's exact token")
        return self._reserved_plan

    def select(self, frame: Frame) -> WorldCameraGuidanceV2:
        """Select once while keeping bootstrap state inside this session."""

        result = _select_camera_guidance_v2(
            frame,
            heading_normalized=self.heading_normalized,
            bootstrap_available=not self._north_reserved,
        )
        if result.disposition is CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP:
            self._north_reserved = True
            self._reserved_frame = frame
            self._reserved_guidance = result
            self._reserved_plan = _build_north_bootstrap_plan(index=1)
        return result

    def record_north_receipt(
        self,
        guidance: WorldCameraGuidanceV2,
        decision_frame: Frame,
        receipt: CameraPlanReceipt,
    ) -> None:
        """Consume the reservation only with the exact complete compass receipt."""

        if not self._north_reserved or self._north_receipt is not None:
            raise ValueError("north bootstrap is not awaiting one receipt")
        if (
            guidance.disposition
            is not CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP
        ):
            raise ValueError("north receipt requires the reserved bootstrap decision")
        if (
            guidance is not self._reserved_guidance
            or decision_frame is not self._reserved_frame
        ):
            raise ValueError("north receipt requires this session's exact reservation token")
        if (
            self._reserved_plan is None
            or receipt.plan is not self._reserved_plan
            or len(receipt.action_receipts) != 1
        ):
            raise ValueError("north receipt does not bind the exact reserved plan")
        action_receipt = receipt.action_receipts[0]
        if tuple(item.operation for item in action_receipt.input_receipts) != (
            CameraInputOperation.COMPASS_CLICK,
        ):
            raise ValueError("north receipt must prove one complete compass click")
        self._north_receipt = receipt


def select_camera_guidance_v2(
    frame: Frame,
    *,
    session: CameraGuidanceV2Session,
) -> WorldCameraGuidanceV2:
    """Select through session-owned state; callers cannot toggle a bare flag."""

    if not isinstance(session, CameraGuidanceV2Session):
        raise ValueError("session must be CameraGuidanceV2Session")
    if not isinstance(frame, Frame):
        raise ValueError("V2 selection requires a captured Frame")
    return session.select(frame)


def build_camera_guidance_v2_plan(
    guidance: WorldCameraGuidanceV2,
    *,
    frame: Frame,
    index: int,
) -> CameraPlan:
    """Build one bounded plan after revalidating its authorization token."""

    _require_guidance_frame(guidance, frame)
    if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
        raise ValueError("V2 primitive index must be a positive integer")
    if guidance.disposition is CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP:
        raise ValueError(
            "north bootstrap plan issuance is session-owned; use build_reserved_plan"
        )
    if guidance.disposition is not CameraGuidanceV2Disposition.ACTIONABLE_CORRECTION:
        raise ValueError("V2 guidance is not an actionable correction")
    assert guidance.direction is not None
    sign = 1 if guidance.direction is CameraGuidanceDirection.POSITIVE else -1
    return CameraPlan(
        f"issue31-v2-{index:02d}-correction-zoom-{guidance.direction.value}",
        (CameraWheel(*REVIEWED_CAMERA_WHEEL_POINT, sign),),
    )


def _build_north_bootstrap_plan(*, index: int) -> CameraPlan:
    return CameraPlan(
        f"issue31-v2-{index:02d}-heading-north",
        (CompassClick(*REVIEWED_COMPASS_POINT),),
    )


def build_camera_guidance_v2_probe(
    guidance: WorldCameraGuidanceV2,
    *,
    frame: Frame,
    direction: CameraGuidanceDirection,
    index: int,
) -> CameraPlan:
    """Build one explicit signed probe without claiming it is a correction."""

    _require_guidance_frame(guidance, frame)
    if guidance.disposition is not CameraGuidanceV2Disposition.CALIBRATION_REQUIRED:
        raise ValueError("V2 guidance does not request a calibration probe")
    if not isinstance(direction, CameraGuidanceDirection):
        raise ValueError("probe direction is invalid")
    if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
        raise ValueError("V2 probe index must be a positive integer")
    assert guidance.axis is not None
    sign = 1 if direction is CameraGuidanceDirection.POSITIVE else -1
    action: CameraAction
    if guidance.axis is CameraPrimitiveAxis.YAW:
        action = CameraMiddleDrag(
            CameraDragAxis.HORIZONTAL,
            sign * CAMERA_GUIDANCE_V2_DRAG_PULSE_PIXELS,
        )
    elif guidance.axis is CameraPrimitiveAxis.PITCH:
        action = CameraMiddleDrag(
            CameraDragAxis.VERTICAL,
            sign * CAMERA_GUIDANCE_V2_DRAG_PULSE_PIXELS,
        )
    else:
        raise ValueError("only yaw/pitch may request a signed probe")
    return CameraPlan(
        f"issue31-v2-{index:02d}-probe-{guidance.axis.value}-{direction.value}",
        (action,),
    )


def _select_camera_guidance_v2(
    frame: Frame,
    *,
    heading_normalized: bool,
    bootstrap_available: bool,
) -> WorldCameraGuidanceV2:
    base_guidance = evaluate_varrock_east_camera_guidance(frame)
    _require_trusted_base_guidance(base_guidance)
    if base_guidance.disposition is CameraGuidanceDisposition.ACTIONABLE:
        if (
            base_guidance.axis is CameraGuidanceAxis.ZOOM
            and base_guidance.direction is not None
            and base_guidance.fit is not None
        ):
            return _decision(
                frame,
                base_guidance,
                heading_normalized,
                CameraGuidanceV2Disposition.ACTIONABLE_CORRECTION,
                CameraGuidanceV2Reason.REVIEWED_ZOOM_SIGN,
                "The reviewed v1 selector proved one signed wheel detent.",
                axis=CameraPrimitiveAxis.ZOOM,
                direction=base_guidance.direction,
            )
        return _refusal(
            frame,
            base_guidance,
            heading_normalized,
            CameraGuidanceV2Reason.UNSUPPORTED_OR_INCOHERENT,
            "V1 actionable evidence was not the reviewed zoom primitive.",
        )

    if base_guidance.fit is None:
        eligible_bootstrap = (
            not heading_normalized
            and base_guidance.reason
            is CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS
        )
        if eligible_bootstrap and bootstrap_available:
            return _decision(
                frame,
                base_guidance,
                False,
                CameraGuidanceV2Disposition.ACTIONABLE_BOOTSTRAP,
                CameraGuidanceV2Reason.DETERMINISTIC_NORTH_BOOTSTRAP,
                (
                    "Distributed geometry is insufficient; this session reserved "
                    "one reviewed compass-north normalization before recomputation."
                ),
                axis=CameraPrimitiveAxis.HEADING,
            )
        if eligible_bootstrap:
            return _refusal(
                frame,
                base_guidance,
                heading_normalized,
                CameraGuidanceV2Reason.NORTH_BOOTSTRAP_RESERVED,
                "This session already reserved its sole compass-north primitive.",
            )
        reason = (
            CameraGuidanceV2Reason.INSUFFICIENT_DISTRIBUTED_EVIDENCE
            if base_guidance.reason
            is CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS
            else CameraGuidanceV2Reason.UNSUPPORTED_OR_INCOHERENT
        )
        return _refusal(
            frame,
            base_guidance,
            heading_normalized,
            reason,
            "No coherent distributed world transform is available for V2 control.",
        )

    if base_guidance.reason is CameraGuidanceReason.WITHIN_DEADBAND:
        return _refusal(
            frame,
            base_guidance,
            heading_normalized,
            CameraGuidanceV2Reason.WITHIN_DEADBAND,
            "The coherent v1 fit remains explicitly inside its deadband.",
        )
    if base_guidance.reason is CameraGuidanceReason.AMBIGUOUS_AXIS:
        return _refusal(
            frame,
            base_guidance,
            heading_normalized,
            CameraGuidanceV2Reason.AMBIGUOUS_AXIS,
            "The coherent v1 fit remains explicitly axis-ambiguous.",
        )
    if base_guidance.reason is not CameraGuidanceReason.UNCALIBRATED_AXIS:
        return _refusal(
            frame,
            base_guidance,
            heading_normalized,
            CameraGuidanceV2Reason.UNSUPPORTED_OR_INCOHERENT,
            "The fit-bearing v1 refusal was not a coherent transform authority.",
        )

    error = _transform_error(base_guidance)
    axis = _dominant_axis(error)
    if axis is None:
        scores = sorted(_axis_scores(error).values(), reverse=True)
        reason = (
            CameraGuidanceV2Reason.WITHIN_DEADBAND
            if scores[0] < _MINIMUM_AXIS_SCORE
            else CameraGuidanceV2Reason.AMBIGUOUS_AXIS
        )
        return _refusal(
            frame,
            base_guidance,
            heading_normalized,
            reason,
            "No camera axis clears the frozen deadband and dominance margin.",
        )
    if axis is CameraPrimitiveAxis.ZOOM:
        return _refusal(
            frame,
            base_guidance,
            heading_normalized,
            CameraGuidanceV2Reason.AMBIGUOUS_AXIS,
            "V2 cannot widen a zoom decision that the reviewed v1 policy refused.",
        )
    return _decision(
        frame,
        base_guidance,
        heading_normalized,
        CameraGuidanceV2Disposition.CALIBRATION_REQUIRED,
        CameraGuidanceV2Reason.SIGNED_EFFECT_REQUIRED,
        (
            f"{axis.value} dominates distributed world error, but no signed "
            "correction is authorized before an exact one-step probe."
        ),
        axis=axis,
    )


def _require_trusted_base_guidance(value: object) -> None:
    if not isinstance(value, WorldCameraGuidance):
        raise ValueError("V2 base guidance must be WorldCameraGuidance")
    if (
        value.selector_id != CAMERA_GUIDANCE_ID
        or value.selector_version != CAMERA_GUIDANCE_VERSION
    ):
        raise ValueError("V2 requires the frozen profile-bound v1 selector")
    if value.excluded_regions != _TRUSTED_EXCLUDED_REGIONS:
        raise ValueError("V2 requires every centralized candidate and fixed-UI exclusion")
    if (
        value.reason is not CameraGuidanceReason.UNSUPPORTED_FRAME
        and value.analysis is None
    ):
        raise ValueError("supported-geometry v1 guidance must retain wide analysis")


def _require_guidance_frame(
    guidance: WorldCameraGuidanceV2,
    frame: Frame,
) -> None:
    if not isinstance(guidance, WorldCameraGuidanceV2):
        raise ValueError("guidance must be WorldCameraGuidanceV2")
    if not isinstance(frame, Frame):
        raise ValueError("V2 plan requires the exact decision Frame")
    guidance.__post_init__()
    if (
        guidance.decision_frame_id != frame.frame_id
        or guidance.decision_captured_monotonic_s != frame.captured_monotonic_s
        or guidance.decision_raw_sha256 != hashlib.sha256(frame.payload).hexdigest()
        or guidance.base_guidance != evaluate_varrock_east_camera_guidance(frame)
    ):
        raise ValueError("V2 guidance does not bind the exact decision frame")


def _transform_error(guidance: WorldCameraGuidance) -> CameraTransformError:
    fit = guidance.fit
    if fit is None:
        raise ValueError("transform error requires a coherent similarity fit")
    return CameraTransformError(
        log_scale=math.log(fit.scale) / 0.01,
        rotation=fit.rotation_degrees / 0.25,
        horizontal_shift=fit.centre_shift_x / 4.0,
        vertical_shift=fit.centre_shift_y / 4.0,
    )


def _axis_scores(error: CameraTransformError) -> dict[CameraPrimitiveAxis, float]:
    return {
        CameraPrimitiveAxis.ZOOM: abs(error.log_scale),
        CameraPrimitiveAxis.YAW: math.hypot(error.rotation, error.horizontal_shift),
        CameraPrimitiveAxis.PITCH: abs(error.vertical_shift),
    }


def _dominant_axis(error: CameraTransformError) -> CameraPrimitiveAxis | None:
    ordered = sorted(
        _axis_scores(error).items(), key=lambda item: (-item[1], item[0].value)
    )
    best_axis, best_score = ordered[0]
    runner_up = ordered[1][1]
    if (
        best_score < _MINIMUM_AXIS_SCORE
        or best_score < runner_up * _AXIS_DOMINANCE_RATIO
    ):
        return None
    return best_axis


def _decision(
    frame: Frame,
    base_guidance: WorldCameraGuidance,
    heading_normalized: bool,
    disposition: CameraGuidanceV2Disposition,
    reason: CameraGuidanceV2Reason,
    detail: str,
    *,
    axis: CameraPrimitiveAxis | None = None,
    direction: CameraGuidanceDirection | None = None,
) -> WorldCameraGuidanceV2:
    return WorldCameraGuidanceV2(
        selector_id=CAMERA_GUIDANCE_V2_ID,
        selector_version=CAMERA_GUIDANCE_V2_VERSION,
        disposition=disposition,
        reason=reason,
        detail=detail,
        base_guidance=base_guidance,
        decision_frame_id=frame.frame_id,
        decision_captured_monotonic_s=frame.captured_monotonic_s,
        decision_raw_sha256=hashlib.sha256(frame.payload).hexdigest(),
        heading_was_normalized=heading_normalized,
        axis=axis,
        direction=direction,
        transform_error=(
            _transform_error(base_guidance) if base_guidance.fit is not None else None
        ),
    )


def _refusal(
    frame: Frame,
    base_guidance: WorldCameraGuidance,
    heading_normalized: bool,
    reason: CameraGuidanceV2Reason,
    detail: str,
) -> WorldCameraGuidanceV2:
    return _decision(
        frame,
        base_guidance,
        heading_normalized,
        CameraGuidanceV2Disposition.INSUFFICIENT_GUIDANCE,
        reason,
        detail,
    )
