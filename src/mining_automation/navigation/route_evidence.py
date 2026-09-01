"""Immutable, synthetic-only evidence packages for one fixed-route direction.

This module is an offline intake and review boundary.  It deliberately has no
capture backend, route executor, controller hook, production registration, or
live-evidence role.  The only accepted role is an architecture-test role, so a
passing report can exercise package mechanics but can never establish real
route support.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final, Literal, cast

from ..capture.frame import PixelFormat
from ..contracts import FrameRef
from .contracts import (
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointMatchKind,
    CheckpointProfileIdentity,
    RouteDirection,
    RouteIdentity,
    RoutePlan,
    Sha256Digest,
)

__all__ = [
    "SYNTHETIC_ROUTE_EVIDENCE_ROLE",
    "FinalizedRouteEvidencePackage",
    "OwnedRouteEvidenceCase",
    "RouteEndpointVerification",
    "RouteEvidenceArtifactRef",
    "RouteEvidenceAcquisitionBinding",
    "RouteEvidenceCaptureBuildIdentity",
    "RouteEvidenceCampaignPlan",
    "RouteEvidenceCaseRole",
    "RouteEvidenceCaseSpec",
    "RouteEvidenceCaseTruth",
    "RouteEvidenceIntegrityError",
    "RouteEvidenceLoadExpectation",
    "RouteEvidenceOperatorIntent",
    "RouteEvidenceReview",
    "RouteEvidenceReviewDecision",
    "RouteEvidenceVerificationReport",
    "SyntheticRouteEvidenceDetectorReport",
    "canonical_route_evidence_bytes",
    "digest_route_plan",
    "parse_synthetic_detector_report",
    "route_evidence_sha256",
    "verify_synthetic_route_evidence",
]


SYNTHETIC_ROUTE_EVIDENCE_ROLE: Final[Literal["synthetic_route_evidence_architecture_test_only"]] = (
    "synthetic_route_evidence_architecture_test_only"
)
_CAMPAIGN_SCHEMA: Final[str] = "fixed-route-evidence-campaign-plan-v2"
_ACQUISITION_SCHEMA: Final[Literal["fixed-route-evidence-acquisition-binding-v1"]] = (
    "fixed-route-evidence-acquisition-binding-v1"
)
_CASE_SCHEMA: Final[str] = "fixed-route-evidence-owned-case-v2"
_DETECTOR_REPORT_SCHEMA: Final[Literal["fixed-route-evidence-synthetic-detector-report-v2"]] = (
    "fixed-route-evidence-synthetic-detector-report-v2"
)
_PACKAGE_SCHEMA: Final[str] = "fixed-route-evidence-finalized-package-v2"
_REVIEW_SCHEMA: Final[str] = "fixed-route-evidence-independent-review-v1"
_VERIFICATION_SCHEMA: Final[Literal["fixed-route-evidence-verification-report-v1"]] = (
    "fixed-route-evidence-verification-report-v1"
)
_SELECTION_POLICY: Final[Literal["all-owned-cases-in-plan-order-no-drop-no-replacement"]] = (
    "all-owned-cases-in-plan-order-no-drop-no-replacement"
)
_OPERATOR_INTENT_STATUS: Final[Literal["operator-intent-unverified"]] = "operator-intent-unverified"
_PASSIVE_CAPTURE_REQUEST_TIMEOUT_S: Final[float] = 30.0
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WINDOWS_RESERVED_COMPONENTS: Final[frozenset[str]] = frozenset(
    {
        "AUX",
        "CLOCK$",
        "CON",
        "CONIN$",
        "CONOUT$",
        "NUL",
        "PRN",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)
_REPORT_FACTORY_TOKEN: Final[object] = object()
_MAX_DETECTOR_REPORT_BYTES: Final[int] = 1024 * 1024


class RouteEvidenceIntegrityError(ValueError):
    """A route-evidence identity, artifact, or review binding failed closed."""


class _DuplicateJsonKeyError(ValueError):
    pass


class RouteEvidenceCaseRole(StrEnum):
    """Closed roles in one direction-specific, preregistered campaign."""

    CHECKPOINT_POSITIVE = "checkpoint_positive"
    CHECKPOINT_NEGATIVE = "checkpoint_negative"
    ROUTE_ARRIVAL = "route_arrival"


class RouteEvidenceReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RouteEvidenceCaptureBuildIdentity:
    """Exact build of the passive frame-acquisition implementation."""

    build_id: str
    version: str
    content_sha256: Sha256Digest

    def __post_init__(self) -> None:
        _require_identifier(self.build_id, "capture build id")
        _require_identifier(self.version, "capture build version")
        if not isinstance(self.content_sha256, Sha256Digest):
            raise ValueError("capture build content digest must be Sha256Digest")

    def to_json_value(self) -> dict[str, object]:
        return {
            "build_id": self.build_id,
            "content_sha256": self.content_sha256.value,
            "version": self.version,
        }


def _require_identifier(value: object, field_name: str) -> str:
    if type(value) is not str or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a portable non-empty identifier")
    return value


def _require_portable_detector(
    detector: CheckpointDetectorIdentity,
    field_name: str,
) -> None:
    if type(detector) is not CheckpointDetectorIdentity:
        raise ValueError(f"{field_name} must be CheckpointDetectorIdentity")
    _require_identifier(detector.detector_id, f"{field_name} id")
    _require_identifier(detector.version, f"{field_name} version")


def _require_portable_profile(
    profile: CheckpointProfileIdentity,
    field_name: str,
) -> None:
    if type(profile) is not CheckpointProfileIdentity:
        raise ValueError(f"{field_name} must be CheckpointProfileIdentity")
    _require_identifier(profile.profile_id, f"{field_name} id")
    _require_identifier(profile.version, f"{field_name} version")


def _require_portable_route_identity(route: RouteIdentity, field_name: str) -> None:
    if type(route) is not RouteIdentity:
        raise ValueError(f"{field_name} must be RouteIdentity")
    _require_identifier(route.route_id, f"{field_name} id")
    _require_identifier(route.version, f"{field_name} version")


def _require_portable_route_plan(plan: RoutePlan, field_name: str) -> None:
    if type(plan) is not RoutePlan:
        raise ValueError(f"{field_name} must be RoutePlan")
    _require_portable_route_identity(plan.identity, f"{field_name} identity")
    _require_identifier(plan.origin.location_id, f"{field_name} origin")
    _require_identifier(plan.destination.location_id, f"{field_name} destination")
    for checkpoint in plan.checkpoints:
        _require_identifier(checkpoint.checkpoint_id, f"{field_name} checkpoint")
    for step in plan.steps:
        _require_identifier(step.step_id, f"{field_name} step")
        _require_identifier(step.from_checkpoint_id, f"{field_name} step source")
        _require_identifier(step.to_checkpoint_id, f"{field_name} step destination")


def _require_exact_frame_ref(frame_ref: FrameRef, field_name: str) -> None:
    if (
        type(frame_ref) is not FrameRef
        or type(frame_ref.frame_id) is not int
        or frame_ref.frame_id < 1
        or type(frame_ref.width) is not int
        or type(frame_ref.height) is not int
    ):
        raise ValueError(f"{field_name} must be an exact positive captured FrameRef")
    _require_nonnegative_number(
        frame_ref.captured_monotonic_s,
        f"{field_name} captured_monotonic_s",
    )


def _require_exact_detection(
    detection: CheckpointDetection,
    field_name: str,
) -> None:
    if type(detection) is not CheckpointDetection:
        raise ValueError(f"{field_name} must be an exact CheckpointDetection")
    confidence = _require_nonnegative_number(
        detection.confidence,
        f"{field_name} confidence",
    )
    if confidence > 1.0:
        raise ValueError(f"{field_name} confidence must not exceed one")
    for checkpoint_id in detection.candidate_checkpoint_ids:
        _require_identifier(checkpoint_id, f"{field_name} candidate checkpoint")


def _require_text(value: object, field_name: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ValueError(f"{field_name} must be non-empty, trimmed, printable text")
    return value


def _require_positive_integer(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _require_nonnegative_number(value: object, field_name: str) -> float:
    if (
        type(value) is not float
        or not math.isfinite(value)
        or value < 0
        or (value == 0.0 and math.copysign(1.0, value) < 0.0)
    ):
        raise ValueError(f"{field_name} must be an exact finite non-negative float")
    return value


def _require_utc(value: object, field_name: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"{field_name} must be an ISO-8601 UTC string ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be valid ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field_name} must use UTC")
    return parsed


def _require_safe_relative_path(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    pure = PurePosixPath(text)
    if (
        "\\" in text
        or pure.is_absolute()
        or pure.as_posix() != text
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"{field_name} must be a safe relative POSIX path")
    for part in pure.parts:
        if ":" in part or part.endswith((".", " ")):
            raise ValueError(f"{field_name} contains a Windows-unsafe path component")
        device_stem = part.split(".", maxsplit=1)[0].rstrip(" .").upper()
        if device_stem in _WINDOWS_RESERVED_COMPONENTS:
            raise ValueError(f"{field_name} contains a reserved Windows device name")
    return text


def canonical_route_evidence_bytes(value: Mapping[str, object]) -> bytes:
    """Serialize one schema object using the navigation evidence canonical form."""

    if type(value) is not dict or any(type(key) is not str for key in value):
        raise TypeError("canonical route evidence must be a string-keyed mapping")

    def validate_json_value(item: object) -> None:
        item_type = type(item)
        if item is None or item_type in {bool, int}:
            return
        if item_type is float:
            number = cast(float, item)
            if not math.isfinite(number):
                raise ValueError("route evidence numbers must be finite")
            return
        if item_type is str:
            text = cast(str, item)
            if any(0xD800 <= ord(character) <= 0xDFFF for character in text):
                raise ValueError("route evidence strings cannot contain Unicode surrogates")
            return
        if item_type is dict:
            mapping = cast(dict[object, object], item)
            for key, child in mapping.items():
                if type(key) is not str:
                    raise TypeError("route evidence object keys must be exact strings")
                validate_json_value(key)
                validate_json_value(child)
            return
        if item_type is list:
            sequence = cast(list[object], item)
            for child in sequence:
                validate_json_value(child)
            return
        raise TypeError("route evidence contains a non-canonical JSON value")

    validate_json_value(value)
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("route evidence contains a non-canonical JSON value") from exc
    return payload.encode("ascii") + b"\n"


def route_evidence_sha256(value: Mapping[str, object]) -> Sha256Digest:
    return Sha256Digest.from_bytes(canonical_route_evidence_bytes(value))


def _route_identity_json(value: RouteIdentity) -> dict[str, object]:
    return {
        "direction": value.direction.value,
        "route_id": value.route_id,
        "version": value.version,
    }


def _route_plan_json(value: RoutePlan) -> dict[str, object]:
    return {
        "checkpoints": [
            {"checkpoint_id": item.checkpoint_id, "role": item.role.value}
            for item in value.checkpoints
        ],
        "destination": {
            "location_id": value.destination.location_id,
            "role": value.destination.role.value,
        },
        "identity": _route_identity_json(value.identity),
        "origin": {
            "location_id": value.origin.location_id,
            "role": value.origin.role.value,
        },
        "steps": [
            {
                "from_checkpoint_id": item.from_checkpoint_id,
                "step_id": item.step_id,
                "to_checkpoint_id": item.to_checkpoint_id,
            }
            for item in value.steps
        ],
    }


def digest_route_plan(value: RoutePlan) -> Sha256Digest:
    if not isinstance(value, RoutePlan):
        raise TypeError("route plan digest requires RoutePlan")
    return route_evidence_sha256(_route_plan_json(value))


def _detector_json(value: CheckpointDetectorIdentity) -> dict[str, object]:
    return {"detector_id": value.detector_id, "version": value.version}


def _profile_json(value: CheckpointProfileIdentity) -> dict[str, object]:
    return {
        "content_sha256": value.content_sha256.value,
        "profile_id": value.profile_id,
        "version": value.version,
    }


def _capture_build_json(value: RouteEvidenceCaptureBuildIdentity) -> dict[str, object]:
    return value.to_json_value()


def _frame_ref_json(value: FrameRef) -> dict[str, object]:
    return {
        "captured_monotonic_s": value.captured_monotonic_s,
        "frame_id": value.frame_id,
        "height": value.height,
        "width": value.width,
    }


def _detection_json(value: CheckpointDetection) -> dict[str, object]:
    return {
        "candidate_checkpoint_ids": list(value.candidate_checkpoint_ids),
        "confidence": value.confidence,
        "match": value.match.value,
    }


@dataclass(frozen=True, slots=True)
class RouteEvidenceArtifactRef:
    relative_path: str
    size_bytes: int
    sha256: Sha256Digest

    def __post_init__(self) -> None:
        _require_safe_relative_path(self.relative_path, "artifact relative_path")
        _require_positive_integer(self.size_bytes, "artifact size_bytes")
        if not isinstance(self.sha256, Sha256Digest):
            raise ValueError("artifact sha256 must be Sha256Digest")

    def to_json_value(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "sha256": self.sha256.value,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class RouteEvidenceCaseSpec:
    ordinal: int
    case_id: str
    role: RouteEvidenceCaseRole
    checkpoint_id: str

    def __post_init__(self) -> None:
        _require_positive_integer(self.ordinal, "case ordinal")
        _require_identifier(self.case_id, "case_id")
        if not isinstance(self.role, RouteEvidenceCaseRole):
            raise ValueError("case role must be RouteEvidenceCaseRole")
        _require_text(self.checkpoint_id, "case checkpoint_id")

    def to_json_value(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "checkpoint_id": self.checkpoint_id,
            "ordinal": self.ordinal,
            "role": self.role.value,
        }


@dataclass(frozen=True, slots=True)
class RouteEvidenceOperatorIntent:
    """Acquisition staging only; structurally incapable of becoming truth."""

    case_id: str
    role: RouteEvidenceCaseRole
    checkpoint_id: str
    status: Literal["operator-intent-unverified"] = field(
        default=_OPERATOR_INTENT_STATUS,
        init=False,
    )
    operator_intent_is_reviewer_truth: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.case_id, "operator intent case_id")
        if not isinstance(self.role, RouteEvidenceCaseRole):
            raise ValueError("operator intent role must be RouteEvidenceCaseRole")
        _require_text(self.checkpoint_id, "operator intent checkpoint_id")

    def to_json_value(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "checkpoint_id": self.checkpoint_id,
            "operator_intent_is_reviewer_truth": self.operator_intent_is_reviewer_truth,
            "role": self.role.value,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RouteEvidenceAcquisitionBinding:
    """Digest-bound, capture-only request chronology for one owned frame."""

    campaign_plan_sha256: Sha256Digest
    capture_source_identity_sha256: Sha256Digest
    capture_session_id: str
    request_id: str
    sequence_index: int
    case_id: str
    capture_id: str
    operator_id: str
    acknowledged_monotonic_s: float
    expires_monotonic_s: float
    frame_captured_monotonic_s: float
    recorded_monotonic_s: float
    previous_acquisition_sha256: Sha256Digest
    schema: Literal["fixed-route-evidence-acquisition-binding-v1"] = field(
        default=_ACQUISITION_SCHEMA,
        init=False,
    )
    operator_acknowledgement_is_reviewer_truth: Literal[False] = field(
        default=False,
        init=False,
    )
    checkpoint_truth_asserted: Literal[False] = field(default=False, init=False)
    navigation_automation_enabled: Literal[False] = field(default=False, init=False)
    camera_automation_enabled: Literal[False] = field(default=False, init=False)
    mouse_input_enabled: Literal[False] = field(default=False, init=False)
    keyboard_input_enabled: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.campaign_plan_sha256, "campaign plan digest"),
            (self.capture_source_identity_sha256, "capture source identity digest"),
            (self.previous_acquisition_sha256, "previous acquisition digest"),
        ):
            if not isinstance(value, Sha256Digest):
                raise ValueError(f"acquisition {name} must be Sha256Digest")
        _require_identifier(self.capture_session_id, "acquisition capture_session_id")
        _require_identifier(self.request_id, "acquisition request_id")
        _require_positive_integer(self.sequence_index, "acquisition sequence_index")
        _require_identifier(self.case_id, "acquisition case_id")
        _require_identifier(self.capture_id, "acquisition capture_id")
        _require_identifier(self.operator_id, "acquisition operator_id")
        acknowledged = _require_nonnegative_number(
            self.acknowledged_monotonic_s,
            "acquisition acknowledgement time",
        )
        expires = _require_nonnegative_number(
            self.expires_monotonic_s,
            "acquisition expiry time",
        )
        captured = _require_nonnegative_number(
            self.frame_captured_monotonic_s,
            "acquisition frame capture time",
        )
        recorded = _require_nonnegative_number(
            self.recorded_monotonic_s,
            "acquisition recorded time",
        )
        if not acknowledged < captured <= recorded <= expires:
            raise ValueError(
                "acquisition chronology must be acknowledgement < frame <= record <= expiry"
            )
        if (
            expires != acknowledged + _PASSIVE_CAPTURE_REQUEST_TIMEOUT_S
            or expires - acknowledged != _PASSIVE_CAPTURE_REQUEST_TIMEOUT_S
        ):
            raise ValueError("acquisition expiry must use the fixed passive capture timeout")

    @property
    def content_sha256(self) -> Sha256Digest:
        return route_evidence_sha256(self.to_json_value())

    def to_json_value(self) -> dict[str, object]:
        return {
            "acknowledged_monotonic_s": self.acknowledged_monotonic_s,
            "camera_automation_enabled": self.camera_automation_enabled,
            "campaign_plan_sha256": self.campaign_plan_sha256.value,
            "capture_id": self.capture_id,
            "capture_session_id": self.capture_session_id,
            "capture_source_identity_sha256": self.capture_source_identity_sha256.value,
            "case_id": self.case_id,
            "checkpoint_truth_asserted": self.checkpoint_truth_asserted,
            "expires_monotonic_s": self.expires_monotonic_s,
            "frame_captured_monotonic_s": self.frame_captured_monotonic_s,
            "keyboard_input_enabled": self.keyboard_input_enabled,
            "mouse_input_enabled": self.mouse_input_enabled,
            "navigation_automation_enabled": self.navigation_automation_enabled,
            "operator_acknowledgement_is_reviewer_truth": (
                self.operator_acknowledgement_is_reviewer_truth
            ),
            "operator_id": self.operator_id,
            "previous_acquisition_sha256": self.previous_acquisition_sha256.value,
            "recorded_monotonic_s": self.recorded_monotonic_s,
            "request_id": self.request_id,
            "schema": self.schema,
            "sequence_index": self.sequence_index,
        }


def _acquisition_json(value: RouteEvidenceAcquisitionBinding) -> dict[str, object]:
    return value.to_json_value()


@dataclass(frozen=True, slots=True)
class SyntheticRouteEvidenceDetectorReport:
    """Canonical detector output for one exact owned synthetic frame."""

    campaign_id: str
    campaign_plan_sha256: Sha256Digest
    route: RouteIdentity
    route_plan_sha256: Sha256Digest
    sequence_index: int
    case_id: str
    capture_id: str
    acquisition: RouteEvidenceAcquisitionBinding
    detector: CheckpointDetectorIdentity
    profile: CheckpointProfileIdentity
    capture_source_id: str
    capture_session_id: str
    capture_build: RouteEvidenceCaptureBuildIdentity
    capture_configuration_sha256: Sha256Digest
    capture_environment_sha256: Sha256Digest
    support_envelope_sha256: Sha256Digest
    frame_ref: FrameRef
    pixel_format: PixelFormat
    frame_sha256: Sha256Digest
    detection: CheckpointDetection
    schema: Literal["fixed-route-evidence-synthetic-detector-report-v2"] = field(
        default=_DETECTOR_REPORT_SCHEMA,
        init=False,
    )
    evidence_role: Literal["synthetic_route_evidence_architecture_test_only"] = field(
        default=SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        init=False,
    )
    detector_output_is_reviewer_truth: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.campaign_id, "detector report campaign_id")
        for value, name in (
            (self.campaign_plan_sha256, "campaign plan digest"),
            (self.route_plan_sha256, "route plan digest"),
            (self.capture_configuration_sha256, "capture configuration digest"),
            (self.capture_environment_sha256, "capture environment digest"),
            (self.support_envelope_sha256, "support envelope digest"),
            (self.frame_sha256, "frame digest"),
        ):
            if not isinstance(value, Sha256Digest):
                raise ValueError(f"detector report {name} must be Sha256Digest")
        _require_portable_route_identity(self.route, "detector report route")
        _require_positive_integer(self.sequence_index, "detector report sequence_index")
        _require_identifier(self.case_id, "detector report case_id")
        _require_identifier(self.capture_id, "detector report capture_id")
        if not isinstance(self.acquisition, RouteEvidenceAcquisitionBinding):
            raise ValueError("detector report acquisition has the wrong type")
        _require_portable_detector(self.detector, "detector report detector")
        _require_portable_profile(self.profile, "detector report profile")
        _require_identifier(self.capture_source_id, "detector report capture_source_id")
        _require_identifier(self.capture_session_id, "detector report capture_session_id")
        if not isinstance(self.capture_build, RouteEvidenceCaptureBuildIdentity):
            raise ValueError("detector report capture build has the wrong type")
        _require_exact_frame_ref(self.frame_ref, "detector report frame")
        if (
            self.acquisition.campaign_plan_sha256 != self.campaign_plan_sha256
            or self.acquisition.capture_session_id != self.capture_session_id
            or self.acquisition.sequence_index != self.sequence_index
            or self.acquisition.case_id != self.case_id
            or self.acquisition.capture_id != self.capture_id
            or self.acquisition.frame_captured_monotonic_s != self.frame_ref.captured_monotonic_s
        ):
            raise ValueError("detector report acquisition differs from its capture identity")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("detector report pixel_format must be PixelFormat")
        _require_exact_detection(self.detection, "detector report detection")

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_route_evidence_bytes(self.to_json_value())

    @property
    def content_sha256(self) -> Sha256Digest:
        return Sha256Digest.from_bytes(self.canonical_bytes)

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "acquisition": _acquisition_json(self.acquisition),
            "campaign_id": self.campaign_id,
            "campaign_plan_sha256": self.campaign_plan_sha256.value,
            "capture_build": _capture_build_json(self.capture_build),
            "capture_configuration_sha256": self.capture_configuration_sha256.value,
            "capture_environment_sha256": self.capture_environment_sha256.value,
            "capture_id": self.capture_id,
            "capture_session_id": self.capture_session_id,
            "capture_source_id": self.capture_source_id,
            "case_id": self.case_id,
            "checkpoint_detector": _detector_json(self.detector),
            "checkpoint_profile": _profile_json(self.profile),
            "detection": _detection_json(self.detection),
            "detector_output_is_reviewer_truth": self.detector_output_is_reviewer_truth,
            "evidence_role": self.evidence_role,
            "frame": {
                **_frame_ref_json(self.frame_ref),
                "pixel_format": self.pixel_format.value,
                "sha256": self.frame_sha256.value,
            },
            "input_authority": self.input_authority,
            "route": _route_identity_json(self.route),
            "route_plan_sha256": self.route_plan_sha256.value,
            "schema": self.schema,
            "sequence_index": self.sequence_index,
            "support_envelope_sha256": self.support_envelope_sha256.value,
        }


def _reject_duplicate_json_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _json_object(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _json_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    field_name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{field_name} keys differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _json_string(value: Mapping[str, object], key: str) -> str:
    return _require_text(value.get(key), key)


def _json_identifier(value: Mapping[str, object], key: str) -> str:
    return _require_identifier(value.get(key), key)


def _json_positive_integer(value: Mapping[str, object], key: str) -> int:
    return _require_positive_integer(value.get(key), key)


def _json_number(value: Mapping[str, object], key: str) -> float:
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{key} must be a JSON number")
    try:
        result = float(raw)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{key} must be a representable JSON number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{key} must be finite")
    return result


def _json_digest(value: Mapping[str, object], key: str) -> Sha256Digest:
    return Sha256Digest(_json_string(value, key))


def _parse_acquisition_json(
    value: object,
    field_name: str,
) -> RouteEvidenceAcquisitionBinding:
    root = _json_object(value, field_name)
    _json_exact_keys(
        root,
        {
            "acknowledged_monotonic_s",
            "camera_automation_enabled",
            "campaign_plan_sha256",
            "capture_id",
            "capture_session_id",
            "capture_source_identity_sha256",
            "case_id",
            "checkpoint_truth_asserted",
            "expires_monotonic_s",
            "frame_captured_monotonic_s",
            "keyboard_input_enabled",
            "mouse_input_enabled",
            "navigation_automation_enabled",
            "operator_acknowledgement_is_reviewer_truth",
            "operator_id",
            "previous_acquisition_sha256",
            "recorded_monotonic_s",
            "request_id",
            "schema",
            "sequence_index",
        },
        field_name,
    )
    if (
        root.get("schema") != _ACQUISITION_SCHEMA
        or root.get("operator_acknowledgement_is_reviewer_truth") is not False
        or root.get("checkpoint_truth_asserted") is not False
        or root.get("navigation_automation_enabled") is not False
        or root.get("camera_automation_enabled") is not False
        or root.get("mouse_input_enabled") is not False
        or root.get("keyboard_input_enabled") is not False
    ):
        raise ValueError("acquisition binding identity or fixed-false policy changed")
    return RouteEvidenceAcquisitionBinding(
        campaign_plan_sha256=_json_digest(root, "campaign_plan_sha256"),
        capture_source_identity_sha256=_json_digest(
            root,
            "capture_source_identity_sha256",
        ),
        capture_session_id=_json_identifier(root, "capture_session_id"),
        request_id=_json_identifier(root, "request_id"),
        sequence_index=_json_positive_integer(root, "sequence_index"),
        case_id=_json_identifier(root, "case_id"),
        capture_id=_json_identifier(root, "capture_id"),
        operator_id=_json_identifier(root, "operator_id"),
        acknowledged_monotonic_s=_json_number(root, "acknowledged_monotonic_s"),
        expires_monotonic_s=_json_number(root, "expires_monotonic_s"),
        frame_captured_monotonic_s=_json_number(root, "frame_captured_monotonic_s"),
        recorded_monotonic_s=_json_number(root, "recorded_monotonic_s"),
        previous_acquisition_sha256=_json_digest(root, "previous_acquisition_sha256"),
    )


def parse_synthetic_detector_report(
    payload: bytes,
) -> SyntheticRouteEvidenceDetectorReport:
    """Strictly parse exact canonical detector-report bytes, without filesystem I/O."""

    if type(payload) is not bytes:
        raise TypeError("synthetic detector report payload must be immutable bytes")
    if len(payload) > _MAX_DETECTOR_REPORT_BYTES:
        raise RouteEvidenceIntegrityError("synthetic detector report exceeds the byte limit")
    try:
        decoded = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_constant=_reject_json_constant,
        )
        root = _json_object(decoded, "detector report")
        _json_exact_keys(
            root,
            {
                "activation_allowed",
                "acquisition",
                "campaign_id",
                "campaign_plan_sha256",
                "capture_build",
                "capture_configuration_sha256",
                "capture_environment_sha256",
                "capture_id",
                "capture_session_id",
                "capture_source_id",
                "case_id",
                "checkpoint_detector",
                "checkpoint_profile",
                "detection",
                "detector_output_is_reviewer_truth",
                "evidence_role",
                "frame",
                "input_authority",
                "route",
                "route_plan_sha256",
                "schema",
                "sequence_index",
                "support_envelope_sha256",
            },
            "detector report",
        )
        if (
            root.get("schema") != _DETECTOR_REPORT_SCHEMA
            or root.get("evidence_role") != SYNTHETIC_ROUTE_EVIDENCE_ROLE
            or root.get("detector_output_is_reviewer_truth") is not False
            or root.get("activation_allowed") is not False
            or root.get("input_authority") is not False
        ):
            raise ValueError("detector report identity or fixed-false policy changed")

        route_value = _json_object(root.get("route"), "detector report route")
        _json_exact_keys(
            route_value,
            {"direction", "route_id", "version"},
            "detector report route",
        )
        detector_value = _json_object(
            root.get("checkpoint_detector"),
            "detector report checkpoint_detector",
        )
        _json_exact_keys(
            detector_value,
            {"detector_id", "version"},
            "detector report checkpoint_detector",
        )
        profile_value = _json_object(
            root.get("checkpoint_profile"),
            "detector report checkpoint_profile",
        )
        _json_exact_keys(
            profile_value,
            {"content_sha256", "profile_id", "version"},
            "detector report checkpoint_profile",
        )
        capture_build_value = _json_object(
            root.get("capture_build"),
            "detector report capture_build",
        )
        _json_exact_keys(
            capture_build_value,
            {"build_id", "content_sha256", "version"},
            "detector report capture_build",
        )
        frame_value = _json_object(root.get("frame"), "detector report frame")
        _json_exact_keys(
            frame_value,
            {
                "captured_monotonic_s",
                "frame_id",
                "height",
                "pixel_format",
                "sha256",
                "width",
            },
            "detector report frame",
        )
        detection_value = _json_object(
            root.get("detection"),
            "detector report detection",
        )
        _json_exact_keys(
            detection_value,
            {"candidate_checkpoint_ids", "confidence", "match"},
            "detector report detection",
        )
        candidates = detection_value.get("candidate_checkpoint_ids")
        if not isinstance(candidates, list) or any(
            not isinstance(item, str) for item in candidates
        ):
            raise ValueError("detector report candidates must be a string array")
        result = SyntheticRouteEvidenceDetectorReport(
            campaign_id=_json_identifier(root, "campaign_id"),
            campaign_plan_sha256=_json_digest(root, "campaign_plan_sha256"),
            route=RouteIdentity(
                route_id=_json_string(route_value, "route_id"),
                version=_json_string(route_value, "version"),
                direction=RouteDirection(_json_string(route_value, "direction")),
            ),
            route_plan_sha256=_json_digest(root, "route_plan_sha256"),
            sequence_index=_json_positive_integer(root, "sequence_index"),
            case_id=_json_identifier(root, "case_id"),
            capture_id=_json_identifier(root, "capture_id"),
            acquisition=_parse_acquisition_json(root.get("acquisition"), "acquisition"),
            detector=CheckpointDetectorIdentity(
                _json_string(detector_value, "detector_id"),
                _json_string(detector_value, "version"),
            ),
            profile=CheckpointProfileIdentity(
                _json_string(profile_value, "profile_id"),
                _json_string(profile_value, "version"),
                _json_digest(profile_value, "content_sha256"),
            ),
            capture_source_id=_json_identifier(root, "capture_source_id"),
            capture_session_id=_json_identifier(root, "capture_session_id"),
            capture_build=RouteEvidenceCaptureBuildIdentity(
                _json_string(capture_build_value, "build_id"),
                _json_string(capture_build_value, "version"),
                _json_digest(capture_build_value, "content_sha256"),
            ),
            capture_configuration_sha256=_json_digest(root, "capture_configuration_sha256"),
            capture_environment_sha256=_json_digest(root, "capture_environment_sha256"),
            support_envelope_sha256=_json_digest(root, "support_envelope_sha256"),
            frame_ref=FrameRef(
                frame_id=_json_positive_integer(frame_value, "frame_id"),
                captured_monotonic_s=_json_number(frame_value, "captured_monotonic_s"),
                width=_json_positive_integer(frame_value, "width"),
                height=_json_positive_integer(frame_value, "height"),
            ),
            pixel_format=PixelFormat(_json_string(frame_value, "pixel_format")),
            frame_sha256=_json_digest(frame_value, "sha256"),
            detection=CheckpointDetection(
                match=CheckpointMatchKind(_json_string(detection_value, "match")),
                candidate_checkpoint_ids=tuple(candidates),
                confidence=_json_number(detection_value, "confidence"),
            ),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKeyError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
        raise RouteEvidenceIntegrityError(f"invalid synthetic detector report: {exc}") from exc
    if result.canonical_bytes != payload:
        raise RouteEvidenceIntegrityError("synthetic detector report bytes are not canonical JSON")
    return result


@dataclass(frozen=True, slots=True)
class RouteEvidenceCampaignPlan:
    """One preregistered, direction-specific synthetic campaign plan."""

    campaign_id: str
    route_plan: RoutePlan
    detector: CheckpointDetectorIdentity
    profile: CheckpointProfileIdentity
    capture_source_id: str
    capture_session_id: str
    capture_build: RouteEvidenceCaptureBuildIdentity
    frame_width: int
    frame_height: int
    pixel_format: PixelFormat
    capture_configuration_sha256: Sha256Digest
    capture_environment_sha256: Sha256Digest
    support_envelope_sha256: Sha256Digest
    operator_id: str
    created_at_utc: str
    cases: tuple[RouteEvidenceCaseSpec, ...]
    evidence_role: Literal["synthetic_route_evidence_architecture_test_only"] = field(
        default=SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        init=False,
    )
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.campaign_id, "campaign_id")
        _require_portable_route_plan(self.route_plan, "campaign route_plan")
        _require_portable_detector(self.detector, "campaign detector")
        _require_portable_profile(self.profile, "campaign profile")
        _require_identifier(self.capture_source_id, "campaign capture_source_id")
        _require_identifier(self.capture_session_id, "campaign capture_session_id")
        if not isinstance(self.capture_build, RouteEvidenceCaptureBuildIdentity):
            raise ValueError("campaign capture build has the wrong type")
        _require_positive_integer(self.frame_width, "campaign frame_width")
        _require_positive_integer(self.frame_height, "campaign frame_height")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("campaign pixel_format must be PixelFormat")
        for value, name in (
            (self.capture_configuration_sha256, "capture configuration digest"),
            (self.capture_environment_sha256, "capture environment digest"),
            (self.support_envelope_sha256, "support envelope digest"),
        ):
            if not isinstance(value, Sha256Digest):
                raise ValueError(f"campaign {name} must be Sha256Digest")
        _require_identifier(self.operator_id, "operator_id")
        _require_utc(self.created_at_utc, "campaign created_at_utc")
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("campaign cases must be a non-empty tuple")
        if any(not isinstance(item, RouteEvidenceCaseSpec) for item in self.cases):
            raise ValueError("campaign cases must contain RouteEvidenceCaseSpec values")
        if tuple(item.ordinal for item in self.cases) != tuple(range(1, len(self.cases) + 1)):
            raise ValueError("campaign case ordinals must be contiguous from one")
        case_ids = tuple(item.case_id for item in self.cases)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("campaign case ids must be unique")
        route_checkpoint_ids = tuple(item.checkpoint_id for item in self.route_plan.checkpoints)
        if any(item.checkpoint_id not in route_checkpoint_ids for item in self.cases):
            raise ValueError("campaign cases cannot name a checkpoint outside the route")
        arrivals = tuple(
            item for item in self.cases if item.role is RouteEvidenceCaseRole.ROUTE_ARRIVAL
        )
        if len(arrivals) != 1:
            raise ValueError("campaign must contain exactly one explicit route-arrival case")
        if (
            self.cases[0].role is not RouteEvidenceCaseRole.CHECKPOINT_POSITIVE
            or self.cases[0].checkpoint_id != route_checkpoint_ids[0]
        ):
            raise ValueError("campaign first case must positively prove the departure checkpoint")
        if self.cases[-1].role is not RouteEvidenceCaseRole.ROUTE_ARRIVAL:
            raise ValueError("route-arrival case must be the final campaign case")
        if arrivals[0].checkpoint_id != route_checkpoint_ids[-1]:
            raise ValueError("route-arrival case must name the terminal checkpoint")
        checkpoint_indexes = {
            checkpoint_id: index for index, checkpoint_id in enumerate(route_checkpoint_ids)
        }
        positive_checkpoint_ids = tuple(
            item.checkpoint_id
            for item in self.cases
            if item.role
            in {
                RouteEvidenceCaseRole.CHECKPOINT_POSITIVE,
                RouteEvidenceCaseRole.ROUTE_ARRIVAL,
            }
        )
        positive_indexes = tuple(
            checkpoint_indexes[checkpoint_id] for checkpoint_id in positive_checkpoint_ids
        )
        if positive_indexes != tuple(sorted(positive_indexes)):
            raise ValueError("positive checkpoint cases must follow nondecreasing route order")
        if set(positive_checkpoint_ids) != set(route_checkpoint_ids):
            raise ValueError("every route checkpoint requires a preregistered positive case")

    @property
    def route(self) -> RouteIdentity:
        return self.route_plan.identity

    @property
    def route_plan_sha256(self) -> Sha256Digest:
        return digest_route_plan(self.route_plan)

    @property
    def capture_source_identity_sha256(self) -> Sha256Digest:
        return route_evidence_sha256(
            {
                "capture_build": _capture_build_json(self.capture_build),
                "capture_configuration_sha256": self.capture_configuration_sha256.value,
                "capture_environment_sha256": self.capture_environment_sha256.value,
                "capture_session_id": self.capture_session_id,
                "capture_source_id": self.capture_source_id,
                "checkpoint_detector": _detector_json(self.detector),
                "checkpoint_profile": _profile_json(self.profile),
                "input_authority": self.input_authority,
                "live_navigation_enabled": False,
                "required_frame": {
                    "height": self.frame_height,
                    "pixel_format": self.pixel_format.value,
                    "width": self.frame_width,
                },
                "support_envelope_sha256": self.support_envelope_sha256.value,
            }
        )

    @property
    def content_sha256(self) -> Sha256Digest:
        return route_evidence_sha256(self.to_json_value())

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "campaign_id": self.campaign_id,
            "capture_build": _capture_build_json(self.capture_build),
            "capture_configuration_sha256": self.capture_configuration_sha256.value,
            "capture_environment_sha256": self.capture_environment_sha256.value,
            "capture_session_id": self.capture_session_id,
            "capture_source_id": self.capture_source_id,
            "cases": [item.to_json_value() for item in self.cases],
            "checkpoint_detector": _detector_json(self.detector),
            "checkpoint_profile": _profile_json(self.profile),
            "created_at_utc": self.created_at_utc,
            "evidence_role": self.evidence_role,
            "input_authority": self.input_authority,
            "operator": {
                "operator_id": self.operator_id,
                "role": "operator-staging-not-reviewer-truth",
            },
            "required_frame": {
                "height": self.frame_height,
                "pixel_format": self.pixel_format.value,
                "width": self.frame_width,
            },
            "route_plan": _route_plan_json(self.route_plan),
            "route_plan_sha256": self.route_plan_sha256.value,
            "schema": _CAMPAIGN_SCHEMA,
            "support_envelope_sha256": self.support_envelope_sha256.value,
        }


@dataclass(frozen=True, slots=True)
class OwnedRouteEvidenceCase:
    """One immutable, digest-bound case acquired for a campaign plan."""

    campaign_id: str
    campaign_plan_sha256: Sha256Digest
    route: RouteIdentity
    route_plan_sha256: Sha256Digest
    sequence_index: int
    case_id: str
    capture_id: str
    operator_id: str
    operator_intent: RouteEvidenceOperatorIntent
    acquisition: RouteEvidenceAcquisitionBinding
    detector: CheckpointDetectorIdentity
    profile: CheckpointProfileIdentity
    capture_source_id: str
    capture_session_id: str
    capture_build: RouteEvidenceCaptureBuildIdentity
    capture_configuration_sha256: Sha256Digest
    capture_environment_sha256: Sha256Digest
    support_envelope_sha256: Sha256Digest
    captured_at_utc: str
    frame_ref: FrameRef
    pixel_format: PixelFormat
    frame_artifact: RouteEvidenceArtifactRef
    detector_report_artifact: RouteEvidenceArtifactRef
    evidence_role: Literal["synthetic_route_evidence_architecture_test_only"] = field(
        default=SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        init=False,
    )
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.campaign_id, "owned case campaign_id")
        for value, name in (
            (self.campaign_plan_sha256, "campaign plan digest"),
            (self.route_plan_sha256, "route plan digest"),
            (self.capture_configuration_sha256, "capture configuration digest"),
            (self.capture_environment_sha256, "capture environment digest"),
            (self.support_envelope_sha256, "support envelope digest"),
        ):
            if not isinstance(value, Sha256Digest):
                raise ValueError(f"owned case {name} must be Sha256Digest")
        _require_portable_route_identity(self.route, "owned case route")
        _require_positive_integer(self.sequence_index, "owned case sequence_index")
        _require_identifier(self.case_id, "owned case case_id")
        _require_identifier(self.capture_id, "owned case capture_id")
        _require_identifier(self.operator_id, "owned case operator_id")
        if not isinstance(self.operator_intent, RouteEvidenceOperatorIntent):
            raise ValueError("owned case operator intent has the wrong type")
        if not isinstance(self.acquisition, RouteEvidenceAcquisitionBinding):
            raise ValueError("owned case acquisition has the wrong type")
        _require_portable_detector(self.detector, "owned case detector")
        _require_portable_profile(self.profile, "owned case profile")
        _require_identifier(self.capture_source_id, "owned case capture_source_id")
        _require_identifier(self.capture_session_id, "owned case capture_session_id")
        if not isinstance(self.capture_build, RouteEvidenceCaptureBuildIdentity):
            raise ValueError("owned case capture build has the wrong type")
        _require_utc(self.captured_at_utc, "owned case captured_at_utc")
        _require_exact_frame_ref(self.frame_ref, "owned case frame_ref")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("owned case pixel_format must be PixelFormat")
        if not isinstance(self.frame_artifact, RouteEvidenceArtifactRef):
            raise ValueError("owned case frame artifact has the wrong type")
        if not isinstance(self.detector_report_artifact, RouteEvidenceArtifactRef):
            raise ValueError("owned case detector report artifact has the wrong type")
        expected_frame_size = (
            self.frame_ref.width * self.frame_ref.height * self.pixel_format.bytes_per_pixel
        )
        if self.frame_artifact.size_bytes != expected_frame_size:
            raise ValueError("owned frame size differs from FrameRef and pixel format")
        if self.frame_artifact.relative_path == self.detector_report_artifact.relative_path:
            raise ValueError("owned frame and detector report require distinct paths")
        if (
            self.acquisition.campaign_plan_sha256 != self.campaign_plan_sha256
            or self.acquisition.capture_session_id != self.capture_session_id
            or self.acquisition.sequence_index != self.sequence_index
            or self.acquisition.case_id != self.case_id
            or self.acquisition.capture_id != self.capture_id
            or self.acquisition.operator_id != self.operator_id
            or self.acquisition.frame_captured_monotonic_s != self.frame_ref.captured_monotonic_s
        ):
            raise ValueError("owned case acquisition differs from its capture identity")

    @property
    def content_sha256(self) -> Sha256Digest:
        return route_evidence_sha256(self.to_json_value())

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "acquisition": _acquisition_json(self.acquisition),
            "campaign_id": self.campaign_id,
            "campaign_plan_sha256": self.campaign_plan_sha256.value,
            "capture_build": _capture_build_json(self.capture_build),
            "capture_configuration_sha256": self.capture_configuration_sha256.value,
            "capture_environment_sha256": self.capture_environment_sha256.value,
            "capture_id": self.capture_id,
            "capture_session_id": self.capture_session_id,
            "capture_source_id": self.capture_source_id,
            "captured_at_utc": self.captured_at_utc,
            "case_id": self.case_id,
            "checkpoint_detector": _detector_json(self.detector),
            "checkpoint_profile": _profile_json(self.profile),
            "detector_report": self.detector_report_artifact.to_json_value(),
            "evidence_role": self.evidence_role,
            "frame": {
                **_frame_ref_json(self.frame_ref),
                "artifact": self.frame_artifact.to_json_value(),
                "pixel_format": self.pixel_format.value,
            },
            "input_authority": self.input_authority,
            "operator_id": self.operator_id,
            "operator_intent": self.operator_intent.to_json_value(),
            "route": _route_identity_json(self.route),
            "route_plan_sha256": self.route_plan_sha256.value,
            "schema": _CASE_SCHEMA,
            "sequence_index": self.sequence_index,
            "support_envelope_sha256": self.support_envelope_sha256.value,
        }


@dataclass(frozen=True, slots=True)
class FinalizedRouteEvidencePackage:
    """The exact acquisition package to which later reviewer truth is bound."""

    campaign_plan: RouteEvidenceCampaignPlan
    cases: tuple[OwnedRouteEvidenceCase, ...]
    finalized_at_utc: str
    finalized_monotonic_s: float
    status: Literal["finalized"] = field(default="finalized", init=False)
    evidence_role: Literal["synthetic_route_evidence_architecture_test_only"] = field(
        default=SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        init=False,
    )
    all_owned_cases_included: Literal[True] = field(default=True, init=False)
    selection_policy: Literal["all-owned-cases-in-plan-order-no-drop-no-replacement"] = field(
        default=_SELECTION_POLICY, init=False
    )
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.campaign_plan, RouteEvidenceCampaignPlan):
            raise ValueError("finalized package requires RouteEvidenceCampaignPlan")
        if not isinstance(self.cases, tuple) or any(
            not isinstance(item, OwnedRouteEvidenceCase) for item in self.cases
        ):
            raise ValueError("finalized package cases must contain owned cases")
        _require_utc(self.finalized_at_utc, "package finalized_at_utc")
        finalized_monotonic = _require_nonnegative_number(
            self.finalized_monotonic_s,
            "package finalized_monotonic_s",
        )
        if len(self.cases) != len(self.campaign_plan.cases):
            raise ValueError("finalized package has missing or foreign cases")

        plan_digest = self.campaign_plan.content_sha256
        route_digest = self.campaign_plan.route_plan_sha256
        captured_times: list[datetime] = []
        capture_ids: set[str] = set()
        artifact_path_aliases: set[str] = set()
        frame_keys: set[tuple[int, float]] = set()
        frame_digests: set[Sha256Digest] = set()
        detector_report_digests: set[Sha256Digest] = set()
        record_digests: set[Sha256Digest] = set()
        request_ids: set[str] = set()
        previous_acquisition_sha256 = plan_digest
        previous_recorded_monotonic_s: float | None = None
        for spec, owned in zip(self.campaign_plan.cases, self.cases, strict=True):
            expected_intent = RouteEvidenceOperatorIntent(
                case_id=spec.case_id,
                role=spec.role,
                checkpoint_id=spec.checkpoint_id,
            )
            if (
                owned.sequence_index != spec.ordinal
                or owned.case_id != spec.case_id
                or owned.operator_intent != expected_intent
            ):
                raise ValueError("owned case order or operator intent differs from the plan")
            if (
                owned.campaign_id != self.campaign_plan.campaign_id
                or owned.campaign_plan_sha256 != plan_digest
                or owned.route != self.campaign_plan.route
                or owned.route_plan_sha256 != route_digest
                or owned.operator_id != self.campaign_plan.operator_id
                or owned.detector != self.campaign_plan.detector
                or owned.profile != self.campaign_plan.profile
                or owned.capture_source_id != self.campaign_plan.capture_source_id
                or owned.capture_session_id != self.campaign_plan.capture_session_id
                or owned.capture_build != self.campaign_plan.capture_build
                or owned.frame_ref.width != self.campaign_plan.frame_width
                or owned.frame_ref.height != self.campaign_plan.frame_height
                or owned.pixel_format is not self.campaign_plan.pixel_format
                or owned.capture_configuration_sha256
                != self.campaign_plan.capture_configuration_sha256
                or owned.capture_environment_sha256 != self.campaign_plan.capture_environment_sha256
                or owned.support_envelope_sha256 != self.campaign_plan.support_envelope_sha256
                or owned.acquisition.capture_source_identity_sha256
                != self.campaign_plan.capture_source_identity_sha256
                or owned.acquisition.previous_acquisition_sha256 != previous_acquisition_sha256
            ):
                raise ValueError("owned case contains foreign campaign provenance")
            if owned.acquisition.request_id in request_ids:
                raise ValueError("finalized package reuses an acquisition request id")
            request_ids.add(owned.acquisition.request_id)
            if (
                previous_recorded_monotonic_s is not None
                and owned.acquisition.acknowledged_monotonic_s <= previous_recorded_monotonic_s
            ):
                raise ValueError("acquisition requests must follow strict source chronology")
            if owned.capture_id in capture_ids:
                raise ValueError("finalized package reuses a capture id")
            capture_ids.add(owned.capture_id)
            for artifact in (
                owned.frame_artifact,
                owned.detector_report_artifact,
            ):
                path_alias = artifact.relative_path.casefold()
                if path_alias in artifact_path_aliases:
                    raise ValueError(
                        "finalized package reuses an owned artifact path or case-fold alias"
                    )
                artifact_path_aliases.add(path_alias)
            if owned.frame_artifact.sha256 in frame_digests:
                raise ValueError("finalized package reuses exact frame content")
            frame_digests.add(owned.frame_artifact.sha256)
            if owned.detector_report_artifact.sha256 in detector_report_digests:
                raise ValueError("finalized package reuses exact detector report content")
            detector_report_digests.add(owned.detector_report_artifact.sha256)
            frame_key = (
                owned.frame_ref.frame_id,
                owned.frame_ref.captured_monotonic_s,
            )
            if frame_key in frame_keys:
                raise ValueError("finalized package reuses a FrameRef")
            frame_keys.add(frame_key)
            record_digest = owned.content_sha256
            if record_digest in record_digests:
                raise ValueError("finalized package reuses an owned case record")
            record_digests.add(record_digest)
            previous_acquisition_sha256 = record_digest
            previous_recorded_monotonic_s = owned.acquisition.recorded_monotonic_s
            captured_times.append(_require_utc(owned.captured_at_utc, "owned case captured_at_utc"))

        created = _require_utc(
            self.campaign_plan.created_at_utc,
            "campaign created_at_utc",
        )
        finalized = _require_utc(self.finalized_at_utc, "package finalized_at_utc")
        if not captured_times or not created < captured_times[0]:
            raise ValueError("campaign plan must predate every owned capture")
        if any(
            later <= earlier
            for earlier, later in zip(captured_times, captured_times[1:], strict=False)
        ):
            raise ValueError("owned capture times must be strictly source ordered")
        if captured_times[-1] >= finalized:
            raise ValueError("package finalization must follow every owned capture")
        if (
            previous_recorded_monotonic_s is None
            or finalized_monotonic <= previous_recorded_monotonic_s
        ):
            raise ValueError("package finalization must follow the acquisition chain")
        frame_ids = tuple(item.frame_ref.frame_id for item in self.cases)
        frame_times = tuple(item.frame_ref.captured_monotonic_s for item in self.cases)
        if any(later <= earlier for earlier, later in zip(frame_ids, frame_ids[1:], strict=False)):
            raise ValueError("owned frame ids must be strictly source ordered")
        if any(
            later <= earlier for earlier, later in zip(frame_times, frame_times[1:], strict=False)
        ):
            raise ValueError("owned frame times must be strictly source ordered")

    @property
    def route(self) -> RouteIdentity:
        return self.campaign_plan.route

    @property
    def content_sha256(self) -> Sha256Digest:
        return route_evidence_sha256(self.to_json_value())

    @property
    def acquisition_head_sha256(self) -> Sha256Digest:
        return self.cases[-1].content_sha256

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "acquisition_head_sha256": self.acquisition_head_sha256.value,
            "all_owned_cases_included": self.all_owned_cases_included,
            "campaign_plan": self.campaign_plan.to_json_value(),
            "campaign_plan_sha256": self.campaign_plan.content_sha256.value,
            "cases": [
                {
                    "case": item.to_json_value(),
                    "case_record_sha256": item.content_sha256.value,
                }
                for item in self.cases
            ],
            "evidence_role": self.evidence_role,
            "finalized_at_utc": self.finalized_at_utc,
            "finalized_monotonic_s": self.finalized_monotonic_s,
            "input_authority": self.input_authority,
            "schema": _PACKAGE_SCHEMA,
            "selection_policy": self.selection_policy,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RouteEvidenceLoadExpectation:
    """Caller-owned authority pin for loading one exact synthetic package."""

    finalized_package_sha256: Sha256Digest
    acquisition_head_sha256: Sha256Digest
    campaign_id: str
    route: RouteIdentity
    direction: RouteDirection
    route_plan_sha256: Sha256Digest
    detector: CheckpointDetectorIdentity
    profile: CheckpointProfileIdentity
    capture_source_id: str
    capture_session_id: str
    capture_build: RouteEvidenceCaptureBuildIdentity
    frame_width: int
    frame_height: int
    pixel_format: PixelFormat
    capture_configuration_sha256: Sha256Digest
    capture_environment_sha256: Sha256Digest
    support_envelope_sha256: Sha256Digest
    evidence_role: Literal["synthetic_route_evidence_architecture_test_only"] = field(
        default=SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        init=False,
    )
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.finalized_package_sha256, "finalized package digest"),
            (self.acquisition_head_sha256, "acquisition head digest"),
            (self.route_plan_sha256, "route plan digest"),
            (self.capture_configuration_sha256, "capture configuration digest"),
            (self.capture_environment_sha256, "capture environment digest"),
            (self.support_envelope_sha256, "support envelope digest"),
        ):
            if not isinstance(value, Sha256Digest):
                raise ValueError(f"load expectation {name} must be Sha256Digest")
        _require_identifier(self.campaign_id, "load expectation campaign_id")
        _require_portable_route_identity(self.route, "load expectation route")
        if not isinstance(self.direction, RouteDirection):
            raise ValueError("load expectation direction must be RouteDirection")
        if self.route.direction is not self.direction:
            raise ValueError("load expectation direction differs from route identity")
        _require_portable_detector(self.detector, "load expectation detector")
        _require_portable_profile(self.profile, "load expectation profile")
        if not isinstance(self.capture_build, RouteEvidenceCaptureBuildIdentity):
            raise ValueError("load expectation capture build has the wrong type")
        _require_positive_integer(self.frame_width, "load expectation frame_width")
        _require_positive_integer(self.frame_height, "load expectation frame_height")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("load expectation pixel_format must be PixelFormat")
        _require_identifier(
            self.capture_source_id,
            "load expectation capture_source_id",
        )
        _require_identifier(
            self.capture_session_id,
            "load expectation capture_session_id",
        )


@dataclass(frozen=True, slots=True)
class RouteEvidenceCaseTruth:
    """Reviewer-owned truth for exact finalized case artifacts."""

    case_id: str
    frame_sha256: Sha256Digest
    detector_report_sha256: Sha256Digest
    decision: RouteEvidenceReviewDecision
    detection: CheckpointDetection

    def __post_init__(self) -> None:
        _require_identifier(self.case_id, "review truth case_id")
        if not isinstance(self.frame_sha256, Sha256Digest):
            raise ValueError("review truth frame digest must be Sha256Digest")
        if not isinstance(self.detector_report_sha256, Sha256Digest):
            raise ValueError("review truth report digest must be Sha256Digest")
        if not isinstance(self.decision, RouteEvidenceReviewDecision):
            raise ValueError("review decision must be RouteEvidenceReviewDecision")
        _require_exact_detection(self.detection, "review truth detection")

    def to_json_value(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "decision": self.decision.value,
            "detector_report_sha256": self.detector_report_sha256.value,
            "frame_sha256": self.frame_sha256.value,
            "reviewed_detection": _detection_json(self.detection),
        }


@dataclass(frozen=True, slots=True)
class RouteEvidenceReview:
    """Independent truth bound to one already-finalized package digest."""

    finalized_package_sha256: Sha256Digest
    campaign_id: str
    route: RouteIdentity
    route_plan_sha256: Sha256Digest
    reviewer_id: str
    reviewed_at_utc: str
    cases: tuple[RouteEvidenceCaseTruth, ...]
    truth_source: Literal["independent-human-review"] = field(
        default="independent-human-review",
        init=False,
    )
    evidence_role: Literal["synthetic_route_evidence_architecture_test_only"] = field(
        default=SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        init=False,
    )
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.finalized_package_sha256, Sha256Digest):
            raise ValueError("review package digest must be Sha256Digest")
        _require_identifier(self.campaign_id, "review campaign_id")
        _require_portable_route_identity(self.route, "review route")
        if not isinstance(self.route_plan_sha256, Sha256Digest):
            raise ValueError("review route plan digest must be Sha256Digest")
        _require_identifier(self.reviewer_id, "reviewer_id")
        _require_utc(self.reviewed_at_utc, "review reviewed_at_utc")
        if not isinstance(self.cases, tuple) or any(
            not isinstance(item, RouteEvidenceCaseTruth) for item in self.cases
        ):
            raise ValueError("review cases must contain RouteEvidenceCaseTruth values")
        case_ids = tuple(item.case_id for item in self.cases)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("review truth case ids must be unique")

    @property
    def content_sha256(self) -> Sha256Digest:
        return route_evidence_sha256(self.to_json_value())

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "campaign_id": self.campaign_id,
            "cases": [item.to_json_value() for item in self.cases],
            "evidence_role": self.evidence_role,
            "finalized_package_sha256": self.finalized_package_sha256.value,
            "input_authority": self.input_authority,
            "reviewed_at_utc": self.reviewed_at_utc,
            "reviewer_id": self.reviewer_id,
            "route": _route_identity_json(self.route),
            "route_plan_sha256": self.route_plan_sha256.value,
            "schema": _REVIEW_SCHEMA,
            "truth_source": self.truth_source,
        }


@dataclass(frozen=True, slots=True)
class RouteEndpointVerification:
    """Terminal route proof only; downstream endpoint claims remain false."""

    route: RouteIdentity
    finalized_package_sha256: Sha256Digest
    reviewer_truth_sha256: Sha256Digest
    arrival_case_id: str
    arrival_checkpoint_id: str
    route_arrival_verified: bool
    supported_mining_view_proven: Literal[False] = field(default=False, init=False)
    bank_interface_open_proven: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _REPORT_FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError(
                "endpoint verification may only be constructed by the package verifier"
            )
        _require_portable_route_identity(self.route, "endpoint route")
        if not isinstance(self.finalized_package_sha256, Sha256Digest):
            raise ValueError("endpoint package digest must be Sha256Digest")
        if not isinstance(self.reviewer_truth_sha256, Sha256Digest):
            raise ValueError("endpoint review digest must be Sha256Digest")
        _require_identifier(self.arrival_case_id, "endpoint arrival case_id")
        _require_text(self.arrival_checkpoint_id, "endpoint arrival checkpoint_id")
        if not isinstance(self.route_arrival_verified, bool):
            raise ValueError("route_arrival_verified must be boolean")

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "arrival_case_id": self.arrival_case_id,
            "arrival_checkpoint_id": self.arrival_checkpoint_id,
            "bank_interface_open_proven": self.bank_interface_open_proven,
            "finalized_package_sha256": self.finalized_package_sha256.value,
            "input_authority": self.input_authority,
            "reviewer_truth_sha256": self.reviewer_truth_sha256.value,
            "route": _route_identity_json(self.route),
            "route_arrival_verified": self.route_arrival_verified,
            "supported_mining_view_proven": self.supported_mining_view_proven,
        }


@dataclass(frozen=True, slots=True)
class RouteEvidenceVerificationReport:
    """Deterministic package result that can never authorize a real route."""

    campaign_id: str
    route: RouteIdentity
    finalized_package_sha256: Sha256Digest
    reviewer_truth_sha256: Sha256Digest
    evidence_conformance_passed: bool
    failure_reasons: tuple[str, ...]
    endpoint: RouteEndpointVerification
    schema: Literal["fixed-route-evidence-verification-report-v1"] = field(
        default=_VERIFICATION_SCHEMA,
        init=False,
    )
    evidence_role: Literal["synthetic_route_evidence_architecture_test_only"] = field(
        default=SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        init=False,
    )
    real_release_role_satisfied: Literal[False] = field(default=False, init=False)
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _REPORT_FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError(
                "verification reports may only be constructed by the package verifier"
            )
        _require_identifier(self.campaign_id, "verification campaign_id")
        _require_portable_route_identity(self.route, "verification route")
        if not isinstance(self.finalized_package_sha256, Sha256Digest):
            raise ValueError("verification package digest must be Sha256Digest")
        if not isinstance(self.reviewer_truth_sha256, Sha256Digest):
            raise ValueError("verification review digest must be Sha256Digest")
        if not isinstance(self.evidence_conformance_passed, bool):
            raise ValueError("evidence_conformance_passed must be boolean")
        if not isinstance(self.failure_reasons, tuple) or any(
            not isinstance(item, str) or not item for item in self.failure_reasons
        ):
            raise ValueError("verification failure reasons must be non-empty strings")
        if self.evidence_conformance_passed is bool(self.failure_reasons):
            raise ValueError("verification pass state must agree with failure reasons")
        if not isinstance(self.endpoint, RouteEndpointVerification):
            raise ValueError("verification endpoint has the wrong type")
        if self.endpoint.route != self.route:
            raise ValueError("verification endpoint belongs to another route")
        if (
            self.endpoint.finalized_package_sha256 != self.finalized_package_sha256
            or self.endpoint.reviewer_truth_sha256 != self.reviewer_truth_sha256
        ):
            raise ValueError("verification endpoint package/review hashes differ from the report")
        if self.endpoint.route_arrival_verified and not self.evidence_conformance_passed:
            raise ValueError("route arrival cannot pass a failed evidence package")

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "campaign_id": self.campaign_id,
            "endpoint": self.endpoint.to_json_value(),
            "evidence_conformance_passed": self.evidence_conformance_passed,
            "evidence_role": self.evidence_role,
            "failure_reasons": list(self.failure_reasons),
            "finalized_package_sha256": self.finalized_package_sha256.value,
            "input_authority": self.input_authority,
            "live_navigation_enabled": self.live_navigation_enabled,
            "real_release_role_satisfied": self.real_release_role_satisfied,
            "reviewer_truth_sha256": self.reviewer_truth_sha256.value,
            "route": _route_identity_json(self.route),
            "schema": self.schema,
        }


def _snapshot_artifacts(
    artifacts: Mapping[str, bytes],
    expected_paths: set[str],
) -> dict[str, bytes]:
    if not isinstance(artifacts, Mapping):
        raise TypeError("route evidence artifacts must be a mapping")
    supplied_paths = tuple(artifacts)
    if any(type(path) is not str for path in supplied_paths):
        raise RouteEvidenceIntegrityError("artifact mapping keys must be strings")
    actual_paths = set(supplied_paths)
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        foreign = sorted(actual_paths - expected_paths)
        raise RouteEvidenceIntegrityError(
            f"route evidence artifact set differs: missing={missing}, foreign={foreign}"
        )
    snapshot: dict[str, bytes] = {}
    for path in sorted(expected_paths):
        payload = artifacts[path]
        if type(payload) is not bytes:
            raise RouteEvidenceIntegrityError(f"owned artifact must be immutable bytes: {path}")
        snapshot[path] = payload
    for path, before in snapshot.items():
        after = artifacts[path]
        if type(after) is not bytes or after != before:
            raise RouteEvidenceIntegrityError(
                f"owned artifact was replaced during verification: {path}"
            )
    return snapshot


def _require_artifact_matches(
    artifact: RouteEvidenceArtifactRef,
    payload: bytes,
) -> None:
    if len(payload) != artifact.size_bytes:
        raise RouteEvidenceIntegrityError(f"owned artifact size changed: {artifact.relative_path}")
    if Sha256Digest.from_bytes(payload) != artifact.sha256:
        raise RouteEvidenceIntegrityError(
            f"owned artifact digest changed: {artifact.relative_path}"
        )


def _case_truth_failure(
    spec: RouteEvidenceCaseSpec,
    truth: RouteEvidenceCaseTruth,
) -> str | None:
    if truth.decision is RouteEvidenceReviewDecision.REJECTED:
        return f"{spec.case_id}:reviewer_rejected"
    if spec.role in {
        RouteEvidenceCaseRole.CHECKPOINT_POSITIVE,
        RouteEvidenceCaseRole.ROUTE_ARRIVAL,
    }:
        if (
            truth.detection.match is not CheckpointMatchKind.MATCHED
            or truth.detection.candidate_checkpoint_ids != (spec.checkpoint_id,)
        ):
            return f"{spec.case_id}:reviewed_truth_does_not_prove_checkpoint"
        return None
    if truth.detection.match is CheckpointMatchKind.MATCHED:
        return f"{spec.case_id}:negative_case_was_definitively_matched"
    return None


def _require_package_matches_expectation(
    package: FinalizedRouteEvidencePackage,
    package_sha256: Sha256Digest,
    expectation: RouteEvidenceLoadExpectation,
) -> None:
    plan = package.campaign_plan
    if (
        expectation.finalized_package_sha256 != package_sha256
        or expectation.acquisition_head_sha256 != package.acquisition_head_sha256
        or expectation.campaign_id != plan.campaign_id
        or expectation.route != plan.route
        or expectation.direction is not plan.route.direction
        or expectation.route_plan_sha256 != plan.route_plan_sha256
        or expectation.detector != plan.detector
        or expectation.profile != plan.profile
        or expectation.capture_source_id != plan.capture_source_id
        or expectation.capture_session_id != plan.capture_session_id
        or expectation.capture_build != plan.capture_build
        or expectation.frame_width != plan.frame_width
        or expectation.frame_height != plan.frame_height
        or expectation.pixel_format is not plan.pixel_format
        or expectation.capture_configuration_sha256 != plan.capture_configuration_sha256
        or expectation.capture_environment_sha256 != plan.capture_environment_sha256
        or expectation.support_envelope_sha256 != plan.support_envelope_sha256
    ):
        raise RouteEvidenceIntegrityError(
            "route evidence package differs from the caller load expectation"
        )


def _require_detector_report_matches_owned_case(
    report: SyntheticRouteEvidenceDetectorReport,
    plan: RouteEvidenceCampaignPlan,
    owned: OwnedRouteEvidenceCase,
) -> None:
    if (
        report.campaign_id != owned.campaign_id
        or report.campaign_plan_sha256 != owned.campaign_plan_sha256
        or report.route != owned.route
        or report.route_plan_sha256 != owned.route_plan_sha256
        or report.sequence_index != owned.sequence_index
        or report.case_id != owned.case_id
        or report.capture_id != owned.capture_id
        or report.acquisition != owned.acquisition
        or report.detector != owned.detector
        or report.profile != owned.profile
        or report.capture_source_id != owned.capture_source_id
        or report.capture_session_id != owned.capture_session_id
        or report.capture_build != owned.capture_build
        or report.capture_configuration_sha256 != owned.capture_configuration_sha256
        or report.capture_environment_sha256 != owned.capture_environment_sha256
        or report.support_envelope_sha256 != owned.support_envelope_sha256
        or report.frame_ref != owned.frame_ref
        or report.pixel_format is not owned.pixel_format
        or report.frame_sha256 != owned.frame_artifact.sha256
        or report.campaign_id != plan.campaign_id
        or report.campaign_plan_sha256 != plan.content_sha256
        or report.route != plan.route
        or report.route_plan_sha256 != plan.route_plan_sha256
        or report.capture_build != plan.capture_build
        or report.frame_ref.width != plan.frame_width
        or report.frame_ref.height != plan.frame_height
        or report.pixel_format is not plan.pixel_format
    ):
        raise RouteEvidenceIntegrityError(
            f"synthetic detector report was rebound or mismatched: {owned.case_id}"
        )


def verify_synthetic_route_evidence(
    package: FinalizedRouteEvidencePackage,
    review: RouteEvidenceReview,
    artifacts: Mapping[str, bytes],
    expectation: RouteEvidenceLoadExpectation,
) -> RouteEvidenceVerificationReport:
    """Verify one immutable synthetic package without granting release authority."""

    if not isinstance(package, FinalizedRouteEvidencePackage):
        raise TypeError("package must be FinalizedRouteEvidencePackage")
    if not isinstance(review, RouteEvidenceReview):
        raise TypeError("review must be RouteEvidenceReview")
    if not isinstance(expectation, RouteEvidenceLoadExpectation):
        raise TypeError("expectation must be RouteEvidenceLoadExpectation")

    package_sha = package.content_sha256
    _require_package_matches_expectation(package, package_sha, expectation)
    if review.finalized_package_sha256 != package_sha:
        raise RouteEvidenceIntegrityError(
            "reviewer truth is not bound to this finalized package hash"
        )
    if (
        review.campaign_id != package.campaign_plan.campaign_id
        or review.route != package.route
        or review.route_plan_sha256 != package.campaign_plan.route_plan_sha256
    ):
        raise RouteEvidenceIntegrityError(
            "reviewer truth contains a foreign campaign, route, or direction"
        )
    if review.reviewer_id.casefold() == package.campaign_plan.operator_id.casefold():
        raise RouteEvidenceIntegrityError(
            "independent reviewer must differ from the capture operator"
        )
    finalized = _require_utc(package.finalized_at_utc, "package finalized_at_utc")
    reviewed = _require_utc(review.reviewed_at_utc, "review reviewed_at_utc")
    if reviewed <= finalized:
        raise RouteEvidenceIntegrityError("review must follow package finalization")
    expected_case_ids = tuple(item.case_id for item in package.campaign_plan.cases)
    reviewed_case_ids = tuple(item.case_id for item in review.cases)
    if reviewed_case_ids != expected_case_ids:
        missing = sorted(set(expected_case_ids) - set(reviewed_case_ids))
        foreign = sorted(set(reviewed_case_ids) - set(expected_case_ids))
        raise RouteEvidenceIntegrityError(
            f"review truth coverage/order differs: missing={missing}, foreign={foreign}"
        )

    expected_artifacts = {
        artifact.relative_path
        for item in package.cases
        for artifact in (item.frame_artifact, item.detector_report_artifact)
    }
    snapshot = _snapshot_artifacts(artifacts, expected_artifacts)
    route_checkpoint_ids = {
        item.checkpoint_id for item in package.campaign_plan.route_plan.checkpoints
    }
    failures: list[str] = []
    arrival_truth_passed = False
    arrival_spec: RouteEvidenceCaseSpec | None = None
    for spec, owned, truth in zip(
        package.campaign_plan.cases,
        package.cases,
        review.cases,
        strict=True,
    ):
        _require_artifact_matches(
            owned.frame_artifact,
            snapshot[owned.frame_artifact.relative_path],
        )
        _require_artifact_matches(
            owned.detector_report_artifact,
            snapshot[owned.detector_report_artifact.relative_path],
        )
        detector_report = parse_synthetic_detector_report(
            snapshot[owned.detector_report_artifact.relative_path]
        )
        _require_detector_report_matches_owned_case(
            detector_report,
            package.campaign_plan,
            owned,
        )
        if (
            truth.frame_sha256 != owned.frame_artifact.sha256
            or truth.detector_report_sha256 != owned.detector_report_artifact.sha256
        ):
            raise RouteEvidenceIntegrityError(
                f"review truth is bound to foreign or replaced artifacts: {spec.case_id}"
            )
        if any(
            checkpoint_id not in route_checkpoint_ids
            for checkpoint_id in truth.detection.candidate_checkpoint_ids
        ):
            raise RouteEvidenceIntegrityError(
                f"review truth names a foreign route checkpoint: {spec.case_id}"
            )
        if any(
            checkpoint_id not in route_checkpoint_ids
            for checkpoint_id in detector_report.detection.candidate_checkpoint_ids
        ):
            raise RouteEvidenceIntegrityError(
                f"detector report names a foreign route checkpoint: {spec.case_id}"
            )
        failure = _case_truth_failure(spec, truth)
        if failure is not None:
            failures.append(failure)
        if detector_report.detection != truth.detection:
            failures.append(f"{spec.case_id}:detector_output_disagrees_with_reviewer_truth")
        if spec.role is RouteEvidenceCaseRole.ROUTE_ARRIVAL:
            arrival_spec = spec
            arrival_truth_passed = failure is None

    assert arrival_spec is not None  # enforced by RouteEvidenceCampaignPlan
    passed = not failures
    review_sha = review.content_sha256
    endpoint = RouteEndpointVerification(
        route=package.route,
        finalized_package_sha256=package_sha,
        reviewer_truth_sha256=review_sha,
        arrival_case_id=arrival_spec.case_id,
        arrival_checkpoint_id=arrival_spec.checkpoint_id,
        route_arrival_verified=passed and arrival_truth_passed,
        _factory_token=_REPORT_FACTORY_TOKEN,
    )
    return RouteEvidenceVerificationReport(
        campaign_id=package.campaign_plan.campaign_id,
        route=package.route,
        finalized_package_sha256=package_sha,
        reviewer_truth_sha256=review_sha,
        evidence_conformance_passed=passed,
        failure_reasons=tuple(failures),
        endpoint=endpoint,
        _factory_token=_REPORT_FACTORY_TOKEN,
    )
