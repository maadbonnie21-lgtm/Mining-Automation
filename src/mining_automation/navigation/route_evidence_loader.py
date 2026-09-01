"""Strict read-only loader for one synthetic fixed-route evidence package.

The loader is deliberately a filesystem intake boundary, not a writer.  It
accepts only the two fixed canonical manifests and the exact artifact set
owned by the finalized package, reconstructs the typed evidence graph, pins it
to a caller-supplied expectation, and returns the existing nonactivating
verification report.
"""

from __future__ import annotations

import json
import math
import os
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from ..capture.frame import PixelFormat
from ..contracts import FrameRef
from .contracts import (
    Checkpoint,
    CheckpointDetection,
    CheckpointDetectorIdentity,
    CheckpointMatchKind,
    CheckpointProfileIdentity,
    CheckpointRole,
    RouteDirection,
    RouteEndpoint,
    RouteEndpointRole,
    RouteIdentity,
    RoutePlan,
    RouteStep,
    Sha256Digest,
)
from .route_evidence import (
    SYNTHETIC_ROUTE_EVIDENCE_ROLE,
    FinalizedRouteEvidencePackage,
    OwnedRouteEvidenceCase,
    RouteEvidenceAcquisitionBinding,
    RouteEvidenceArtifactRef,
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseRole,
    RouteEvidenceCaseSpec,
    RouteEvidenceCaseTruth,
    RouteEvidenceIntegrityError,
    RouteEvidenceLoadExpectation,
    RouteEvidenceOperatorIntent,
    RouteEvidenceReview,
    RouteEvidenceReviewDecision,
    RouteEvidenceVerificationReport,
    canonical_route_evidence_bytes,
    parse_synthetic_detector_report,
    verify_synthetic_route_evidence,
)

__all__ = [
    "FINALIZED_PACKAGE_FILENAME",
    "INDEPENDENT_REVIEW_FILENAME",
    "RouteEvidenceFilesystemExpectation",
    "load_and_verify_synthetic_route_evidence",
]


FINALIZED_PACKAGE_FILENAME: Final[str] = "finalized-package.json"
INDEPENDENT_REVIEW_FILENAME: Final[str] = "independent-review.json"

_CAMPAIGN_SCHEMA: Final[str] = "fixed-route-evidence-campaign-plan-v2"
_ACQUISITION_SCHEMA: Final[str] = "fixed-route-evidence-acquisition-binding-v1"
_CASE_SCHEMA: Final[str] = "fixed-route-evidence-owned-case-v2"
_PACKAGE_SCHEMA: Final[str] = "fixed-route-evidence-finalized-package-v2"
_REVIEW_SCHEMA: Final[str] = "fixed-route-evidence-independent-review-v1"
_SELECTION_POLICY: Final[str] = "all-owned-cases-in-plan-order-no-drop-no-replacement"
_OPERATOR_ROLE: Final[str] = "operator-staging-not-reviewer-truth"
_OPERATOR_INTENT_STATUS: Final[str] = "operator-intent-unverified"
_TRUTH_SOURCE: Final[str] = "independent-human-review"
_REPARSE_POINT_ATTRIBUTE: Final[int] = 0x400
_READ_CHUNK_SIZE: Final[int] = 1024 * 1024
_MAX_MANIFEST_BYTES: Final[int] = 16 * 1024 * 1024
_MAX_ARTIFACT_BYTES: Final[int] = 512 * 1024 * 1024
_MAX_JSON_DEPTH: Final[int] = 32
_MAX_JSON_NODES: Final[int] = 100_000
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


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    payload: bytes
    signature: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    is_directory: bool
    signature: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RouteEvidenceFilesystemExpectation(RouteEvidenceLoadExpectation):
    """Caller-owned pins for both immutable manifests at the filesystem boundary."""

    independent_review_sha256: Sha256Digest
    reviewer_id: str

    def __post_init__(self) -> None:
        super(RouteEvidenceFilesystemExpectation, self).__post_init__()
        if not isinstance(self.independent_review_sha256, Sha256Digest):
            raise ValueError("filesystem expectation review digest must be Sha256Digest")
        _identifier(self.reviewer_id, "filesystem expectation reviewer_id")


def _duplicate_key_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RouteEvidenceIntegrityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise RouteEvidenceIntegrityError(f"non-finite JSON number is forbidden: {value}")


def _strict_canonical_object(payload: bytes, context: str) -> dict[str, object]:
    if type(payload) is not bytes:
        raise TypeError(f"{context} payload must be immutable bytes")
    try:
        decoded = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_duplicate_key_object,
            parse_constant=_reject_nonfinite,
        )
    except RouteEvidenceIntegrityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context} JSON: {exc}") from exc
    root = _object(decoded, context)
    _validate_json_shape(root, context)
    try:
        canonical = canonical_route_evidence_bytes(root)
    except (RecursionError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context} canonical value: {exc}") from exc
    if canonical != payload:
        raise RouteEvidenceIntegrityError(f"{context} bytes are not canonical JSON")
    return root


def _validate_json_shape(value: object, context: str) -> None:
    remaining = _MAX_JSON_NODES

    def visit(item: object, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise RouteEvidenceIntegrityError(f"{context} exceeds the JSON node limit")
        if depth > _MAX_JSON_DEPTH:
            raise RouteEvidenceIntegrityError(f"{context} exceeds the JSON depth limit")
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise RouteEvidenceIntegrityError(f"{context} has a non-string JSON key")
                visit(child, depth + 1)
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if item is None or type(item) in {str, int, float, bool}:
            if type(item) is float and not math.isfinite(item):
                raise RouteEvidenceIntegrityError(f"{context} has a non-finite JSON number")
            return
        raise RouteEvidenceIntegrityError(f"{context} has an unsupported JSON value")

    visit(value, 0)


def _object(value: object, context: str) -> dict[str, object]:
    if type(value) is not dict or any(not isinstance(key, str) for key in value):
        raise RouteEvidenceIntegrityError(f"{context} must be a JSON object")
    return value


def _array(value: object, context: str) -> list[object]:
    if type(value) is not list:
        raise RouteEvidenceIntegrityError(f"{context} must be a JSON array")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise RouteEvidenceIntegrityError(
            f"{context} keys differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _string(value: object, context: str) -> str:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise RouteEvidenceIntegrityError(f"{context} must be trimmed printable text")
    return value


def _identifier(value: object, context: str) -> str:
    text = _string(value, context)
    if (
        len(text) > 128
        or not text[0].isalnum()
        or any(
            not (character.isascii() and (character.isalnum() or character in "._-"))
            for character in text
        )
    ):
        raise RouteEvidenceIntegrityError(f"{context} must be a portable identifier")
    return text


def _positive_int(value: object, context: str) -> int:
    if type(value) is not int or value <= 0:
        raise RouteEvidenceIntegrityError(f"{context} must be a positive integer")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RouteEvidenceIntegrityError(f"{context} must be a JSON number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise RouteEvidenceIntegrityError(f"{context} is not a representable number") from exc
    if not math.isfinite(result):
        raise RouteEvidenceIntegrityError(f"{context} must be finite")
    return result


def _fixed(value: object, expected: object, context: str) -> None:
    if value != expected or type(value) is not type(expected):
        raise RouteEvidenceIntegrityError(f"{context} changed from its fixed schema value")


def _digest(value: object, context: str) -> Sha256Digest:
    try:
        return Sha256Digest(_string(value, context))
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _enum_value(enum_type: type[RouteDirection], value: object, context: str) -> RouteDirection:
    try:
        return enum_type(_string(value, context))
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _route_identity(value: object, context: str) -> RouteIdentity:
    root = _object(value, context)
    _exact_keys(root, {"direction", "route_id", "version"}, context)
    try:
        return RouteIdentity(
            route_id=_identifier(root["route_id"], f"{context}.route_id"),
            version=_identifier(root["version"], f"{context}.version"),
            direction=_enum_value(RouteDirection, root["direction"], f"{context}.direction"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _route_endpoint(value: object, context: str) -> RouteEndpoint:
    root = _object(value, context)
    _exact_keys(root, {"location_id", "role"}, context)
    try:
        return RouteEndpoint(
            location_id=_identifier(root["location_id"], f"{context}.location_id"),
            role=RouteEndpointRole(_string(root["role"], f"{context}.role")),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _route_plan(value: object, context: str) -> RoutePlan:
    root = _object(value, context)
    _exact_keys(root, {"checkpoints", "destination", "identity", "origin", "steps"}, context)
    checkpoints: list[Checkpoint] = []
    for index, raw in enumerate(_array(root["checkpoints"], f"{context}.checkpoints")):
        item_context = f"{context}.checkpoints[{index}]"
        item = _object(raw, item_context)
        _exact_keys(item, {"checkpoint_id", "role"}, item_context)
        try:
            checkpoints.append(
                Checkpoint(
                    checkpoint_id=_identifier(
                        item["checkpoint_id"], f"{item_context}.checkpoint_id"
                    ),
                    role=CheckpointRole(_string(item["role"], f"{item_context}.role")),
                )
            )
        except ValueError as exc:
            raise RouteEvidenceIntegrityError(f"invalid {item_context}: {exc}") from exc
    steps: list[RouteStep] = []
    for index, raw in enumerate(_array(root["steps"], f"{context}.steps")):
        item_context = f"{context}.steps[{index}]"
        item = _object(raw, item_context)
        _exact_keys(
            item,
            {"from_checkpoint_id", "step_id", "to_checkpoint_id"},
            item_context,
        )
        try:
            steps.append(
                RouteStep(
                    step_id=_identifier(item["step_id"], f"{item_context}.step_id"),
                    from_checkpoint_id=_identifier(
                        item["from_checkpoint_id"], f"{item_context}.from_checkpoint_id"
                    ),
                    to_checkpoint_id=_identifier(
                        item["to_checkpoint_id"], f"{item_context}.to_checkpoint_id"
                    ),
                )
            )
        except ValueError as exc:
            raise RouteEvidenceIntegrityError(f"invalid {item_context}: {exc}") from exc
    try:
        return RoutePlan(
            identity=_route_identity(root["identity"], f"{context}.identity"),
            origin=_route_endpoint(root["origin"], f"{context}.origin"),
            destination=_route_endpoint(root["destination"], f"{context}.destination"),
            checkpoints=tuple(checkpoints),
            steps=tuple(steps),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _detector(value: object, context: str) -> CheckpointDetectorIdentity:
    root = _object(value, context)
    _exact_keys(root, {"detector_id", "version"}, context)
    try:
        return CheckpointDetectorIdentity(
            _identifier(root["detector_id"], f"{context}.detector_id"),
            _identifier(root["version"], f"{context}.version"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _profile(value: object, context: str) -> CheckpointProfileIdentity:
    root = _object(value, context)
    _exact_keys(root, {"content_sha256", "profile_id", "version"}, context)
    try:
        return CheckpointProfileIdentity(
            _identifier(root["profile_id"], f"{context}.profile_id"),
            _identifier(root["version"], f"{context}.version"),
            _digest(root["content_sha256"], f"{context}.content_sha256"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _capture_build(value: object, context: str) -> RouteEvidenceCaptureBuildIdentity:
    root = _object(value, context)
    _exact_keys(root, {"build_id", "content_sha256", "version"}, context)
    try:
        return RouteEvidenceCaptureBuildIdentity(
            build_id=_identifier(root["build_id"], f"{context}.build_id"),
            version=_identifier(root["version"], f"{context}.version"),
            content_sha256=_digest(root["content_sha256"], f"{context}.content_sha256"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _detection(value: object, context: str) -> CheckpointDetection:
    root = _object(value, context)
    _exact_keys(root, {"candidate_checkpoint_ids", "confidence", "match"}, context)
    raw_candidates = _array(root["candidate_checkpoint_ids"], f"{context}.candidate_checkpoint_ids")
    candidates = tuple(
        _identifier(item, f"{context}.candidate_checkpoint_ids[{index}]")
        for index, item in enumerate(raw_candidates)
    )
    try:
        return CheckpointDetection(
            match=CheckpointMatchKind(_string(root["match"], f"{context}.match")),
            candidate_checkpoint_ids=candidates,
            confidence=_number(root["confidence"], f"{context}.confidence"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _artifact(value: object, context: str) -> RouteEvidenceArtifactRef:
    root = _object(value, context)
    _exact_keys(root, {"path", "sha256", "size_bytes"}, context)
    try:
        return RouteEvidenceArtifactRef(
            relative_path=_strict_relative_path(root["path"], f"{context}.path"),
            size_bytes=_positive_int(root["size_bytes"], f"{context}.size_bytes"),
            sha256=_digest(root["sha256"], f"{context}.sha256"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _case_spec(value: object, context: str) -> RouteEvidenceCaseSpec:
    root = _object(value, context)
    _exact_keys(root, {"case_id", "checkpoint_id", "ordinal", "role"}, context)
    try:
        return RouteEvidenceCaseSpec(
            ordinal=_positive_int(root["ordinal"], f"{context}.ordinal"),
            case_id=_identifier(root["case_id"], f"{context}.case_id"),
            role=RouteEvidenceCaseRole(_string(root["role"], f"{context}.role")),
            checkpoint_id=_identifier(root["checkpoint_id"], f"{context}.checkpoint_id"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _operator_intent(value: object, context: str) -> RouteEvidenceOperatorIntent:
    root = _object(value, context)
    _exact_keys(
        root,
        {
            "case_id",
            "checkpoint_id",
            "operator_intent_is_reviewer_truth",
            "role",
            "status",
        },
        context,
    )
    _fixed(root["status"], _OPERATOR_INTENT_STATUS, f"{context}.status")
    _fixed(
        root["operator_intent_is_reviewer_truth"],
        False,
        f"{context}.operator_intent_is_reviewer_truth",
    )
    try:
        return RouteEvidenceOperatorIntent(
            case_id=_identifier(root["case_id"], f"{context}.case_id"),
            role=RouteEvidenceCaseRole(_string(root["role"], f"{context}.role")),
            checkpoint_id=_identifier(root["checkpoint_id"], f"{context}.checkpoint_id"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _acquisition(value: object, context: str) -> RouteEvidenceAcquisitionBinding:
    root = _object(value, context)
    _exact_keys(
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
        context,
    )
    _fixed(root["schema"], _ACQUISITION_SCHEMA, f"{context}.schema")
    for key in (
        "operator_acknowledgement_is_reviewer_truth",
        "checkpoint_truth_asserted",
        "navigation_automation_enabled",
        "camera_automation_enabled",
        "mouse_input_enabled",
        "keyboard_input_enabled",
    ):
        _fixed(root[key], False, f"{context}.{key}")
    try:
        return RouteEvidenceAcquisitionBinding(
            campaign_plan_sha256=_digest(
                root["campaign_plan_sha256"],
                f"{context}.campaign_plan_sha256",
            ),
            capture_source_identity_sha256=_digest(
                root["capture_source_identity_sha256"],
                f"{context}.capture_source_identity_sha256",
            ),
            capture_session_id=_identifier(
                root["capture_session_id"],
                f"{context}.capture_session_id",
            ),
            request_id=_identifier(root["request_id"], f"{context}.request_id"),
            sequence_index=_positive_int(
                root["sequence_index"],
                f"{context}.sequence_index",
            ),
            case_id=_identifier(root["case_id"], f"{context}.case_id"),
            capture_id=_identifier(root["capture_id"], f"{context}.capture_id"),
            operator_id=_identifier(root["operator_id"], f"{context}.operator_id"),
            acknowledged_monotonic_s=_number(
                root["acknowledged_monotonic_s"],
                f"{context}.acknowledged_monotonic_s",
            ),
            expires_monotonic_s=_number(
                root["expires_monotonic_s"],
                f"{context}.expires_monotonic_s",
            ),
            frame_captured_monotonic_s=_number(
                root["frame_captured_monotonic_s"],
                f"{context}.frame_captured_monotonic_s",
            ),
            recorded_monotonic_s=_number(
                root["recorded_monotonic_s"],
                f"{context}.recorded_monotonic_s",
            ),
            previous_acquisition_sha256=_digest(
                root["previous_acquisition_sha256"],
                f"{context}.previous_acquisition_sha256",
            ),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _campaign(value: object, context: str) -> RouteEvidenceCampaignPlan:
    root = _object(value, context)
    _exact_keys(
        root,
        {
            "activation_allowed",
            "campaign_id",
            "capture_build",
            "capture_configuration_sha256",
            "capture_environment_sha256",
            "capture_session_id",
            "capture_source_id",
            "cases",
            "checkpoint_detector",
            "checkpoint_profile",
            "created_at_utc",
            "evidence_role",
            "input_authority",
            "operator",
            "required_frame",
            "route_plan",
            "route_plan_sha256",
            "schema",
            "support_envelope_sha256",
        },
        context,
    )
    _fixed(root["schema"], _CAMPAIGN_SCHEMA, f"{context}.schema")
    _fixed(root["evidence_role"], SYNTHETIC_ROUTE_EVIDENCE_ROLE, f"{context}.evidence_role")
    _fixed(root["activation_allowed"], False, f"{context}.activation_allowed")
    _fixed(root["input_authority"], False, f"{context}.input_authority")
    operator = _object(root["operator"], f"{context}.operator")
    _exact_keys(operator, {"operator_id", "role"}, f"{context}.operator")
    _fixed(operator["role"], _OPERATOR_ROLE, f"{context}.operator.role")
    required_frame = _object(root["required_frame"], f"{context}.required_frame")
    _exact_keys(
        required_frame,
        {"height", "pixel_format", "width"},
        f"{context}.required_frame",
    )
    route_plan = _route_plan(root["route_plan"], f"{context}.route_plan")
    declared_route_digest = _digest(root["route_plan_sha256"], f"{context}.route_plan_sha256")
    cases = tuple(
        _case_spec(item, f"{context}.cases[{index}]")
        for index, item in enumerate(_array(root["cases"], f"{context}.cases"))
    )
    try:
        result = RouteEvidenceCampaignPlan(
            campaign_id=_identifier(root["campaign_id"], f"{context}.campaign_id"),
            route_plan=route_plan,
            detector=_detector(root["checkpoint_detector"], f"{context}.checkpoint_detector"),
            profile=_profile(root["checkpoint_profile"], f"{context}.checkpoint_profile"),
            capture_source_id=_identifier(
                root["capture_source_id"], f"{context}.capture_source_id"
            ),
            capture_session_id=_identifier(
                root["capture_session_id"], f"{context}.capture_session_id"
            ),
            capture_build=_capture_build(root["capture_build"], f"{context}.capture_build"),
            frame_width=_positive_int(required_frame["width"], f"{context}.required_frame.width"),
            frame_height=_positive_int(
                required_frame["height"], f"{context}.required_frame.height"
            ),
            pixel_format=PixelFormat(
                _string(
                    required_frame["pixel_format"],
                    f"{context}.required_frame.pixel_format",
                )
            ),
            capture_configuration_sha256=_digest(
                root["capture_configuration_sha256"],
                f"{context}.capture_configuration_sha256",
            ),
            capture_environment_sha256=_digest(
                root["capture_environment_sha256"],
                f"{context}.capture_environment_sha256",
            ),
            support_envelope_sha256=_digest(
                root["support_envelope_sha256"], f"{context}.support_envelope_sha256"
            ),
            operator_id=_identifier(operator["operator_id"], f"{context}.operator.operator_id"),
            created_at_utc=_string(root["created_at_utc"], f"{context}.created_at_utc"),
            cases=cases,
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc
    if declared_route_digest != result.route_plan_sha256:
        raise RouteEvidenceIntegrityError(f"{context} route-plan digest mismatch")
    return result


def _frame_ref(value: Mapping[str, object], context: str) -> FrameRef:
    try:
        return FrameRef(
            frame_id=_positive_int(value["frame_id"], f"{context}.frame_id"),
            captured_monotonic_s=_number(
                value["captured_monotonic_s"], f"{context}.captured_monotonic_s"
            ),
            width=_positive_int(value["width"], f"{context}.width"),
            height=_positive_int(value["height"], f"{context}.height"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _owned_case(value: object, context: str) -> OwnedRouteEvidenceCase:
    root = _object(value, context)
    _exact_keys(
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
            "captured_at_utc",
            "case_id",
            "checkpoint_detector",
            "checkpoint_profile",
            "detector_report",
            "evidence_role",
            "frame",
            "input_authority",
            "operator_id",
            "operator_intent",
            "route",
            "route_plan_sha256",
            "schema",
            "sequence_index",
            "support_envelope_sha256",
        },
        context,
    )
    _fixed(root["schema"], _CASE_SCHEMA, f"{context}.schema")
    _fixed(root["evidence_role"], SYNTHETIC_ROUTE_EVIDENCE_ROLE, f"{context}.evidence_role")
    _fixed(root["activation_allowed"], False, f"{context}.activation_allowed")
    _fixed(root["input_authority"], False, f"{context}.input_authority")
    frame = _object(root["frame"], f"{context}.frame")
    _exact_keys(
        frame,
        {"artifact", "captured_monotonic_s", "frame_id", "height", "pixel_format", "width"},
        f"{context}.frame",
    )
    try:
        return OwnedRouteEvidenceCase(
            campaign_id=_identifier(root["campaign_id"], f"{context}.campaign_id"),
            campaign_plan_sha256=_digest(
                root["campaign_plan_sha256"], f"{context}.campaign_plan_sha256"
            ),
            route=_route_identity(root["route"], f"{context}.route"),
            route_plan_sha256=_digest(root["route_plan_sha256"], f"{context}.route_plan_sha256"),
            sequence_index=_positive_int(root["sequence_index"], f"{context}.sequence_index"),
            case_id=_identifier(root["case_id"], f"{context}.case_id"),
            capture_id=_identifier(root["capture_id"], f"{context}.capture_id"),
            operator_id=_identifier(root["operator_id"], f"{context}.operator_id"),
            operator_intent=_operator_intent(root["operator_intent"], f"{context}.operator_intent"),
            acquisition=_acquisition(root["acquisition"], f"{context}.acquisition"),
            detector=_detector(root["checkpoint_detector"], f"{context}.checkpoint_detector"),
            profile=_profile(root["checkpoint_profile"], f"{context}.checkpoint_profile"),
            capture_source_id=_identifier(
                root["capture_source_id"], f"{context}.capture_source_id"
            ),
            capture_session_id=_identifier(
                root["capture_session_id"], f"{context}.capture_session_id"
            ),
            capture_build=_capture_build(root["capture_build"], f"{context}.capture_build"),
            capture_configuration_sha256=_digest(
                root["capture_configuration_sha256"],
                f"{context}.capture_configuration_sha256",
            ),
            capture_environment_sha256=_digest(
                root["capture_environment_sha256"],
                f"{context}.capture_environment_sha256",
            ),
            support_envelope_sha256=_digest(
                root["support_envelope_sha256"], f"{context}.support_envelope_sha256"
            ),
            captured_at_utc=_string(root["captured_at_utc"], f"{context}.captured_at_utc"),
            frame_ref=_frame_ref(frame, f"{context}.frame"),
            pixel_format=PixelFormat(
                _string(frame["pixel_format"], f"{context}.frame.pixel_format")
            ),
            frame_artifact=_artifact(frame["artifact"], f"{context}.frame.artifact"),
            detector_report_artifact=_artifact(
                root["detector_report"], f"{context}.detector_report"
            ),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _parse_package(payload: bytes) -> FinalizedRouteEvidencePackage:
    root = _strict_canonical_object(payload, "finalized package")
    _exact_keys(
        root,
        {
            "activation_allowed",
            "acquisition_head_sha256",
            "all_owned_cases_included",
            "campaign_plan",
            "campaign_plan_sha256",
            "cases",
            "evidence_role",
            "finalized_at_utc",
            "finalized_monotonic_s",
            "input_authority",
            "schema",
            "selection_policy",
            "status",
        },
        "finalized package",
    )
    _fixed(root["schema"], _PACKAGE_SCHEMA, "finalized package.schema")
    _fixed(root["status"], "finalized", "finalized package.status")
    _fixed(root["selection_policy"], _SELECTION_POLICY, "finalized package.selection_policy")
    _fixed(root["evidence_role"], SYNTHETIC_ROUTE_EVIDENCE_ROLE, "package.evidence_role")
    _fixed(root["activation_allowed"], False, "package.activation_allowed")
    _fixed(root["input_authority"], False, "package.input_authority")
    _fixed(root["all_owned_cases_included"], True, "package.all_owned_cases_included")
    campaign = _campaign(root["campaign_plan"], "finalized package.campaign_plan")
    if _digest(root["campaign_plan_sha256"], "finalized package.campaign_plan_sha256") != (
        campaign.content_sha256
    ):
        raise RouteEvidenceIntegrityError("finalized package campaign-plan digest mismatch")
    cases: list[OwnedRouteEvidenceCase] = []
    for index, raw in enumerate(_array(root["cases"], "finalized package.cases")):
        context = f"finalized package.cases[{index}]"
        entry = _object(raw, context)
        _exact_keys(entry, {"case", "case_record_sha256"}, context)
        owned = _owned_case(entry["case"], f"{context}.case")
        if _digest(entry["case_record_sha256"], f"{context}.case_record_sha256") != (
            owned.content_sha256
        ):
            raise RouteEvidenceIntegrityError(f"{context} record digest mismatch")
        cases.append(owned)
    try:
        result = FinalizedRouteEvidencePackage(
            campaign_plan=campaign,
            cases=tuple(cases),
            finalized_at_utc=_string(root["finalized_at_utc"], "package.finalized_at_utc"),
            finalized_monotonic_s=_number(
                root["finalized_monotonic_s"],
                "package.finalized_monotonic_s",
            ),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid finalized package: {exc}") from exc
    if (
        _digest(
            root["acquisition_head_sha256"],
            "finalized package.acquisition_head_sha256",
        )
        != result.acquisition_head_sha256
    ):
        raise RouteEvidenceIntegrityError("finalized package acquisition-head digest mismatch")
    if canonical_route_evidence_bytes(result.to_json_value()) != payload:
        raise RouteEvidenceIntegrityError("finalized package typed canonical round-trip changed")
    return result


def _case_truth(value: object, context: str) -> RouteEvidenceCaseTruth:
    root = _object(value, context)
    _exact_keys(
        root,
        {
            "case_id",
            "decision",
            "detector_report_sha256",
            "frame_sha256",
            "reviewed_detection",
        },
        context,
    )
    try:
        return RouteEvidenceCaseTruth(
            case_id=_identifier(root["case_id"], f"{context}.case_id"),
            frame_sha256=_digest(root["frame_sha256"], f"{context}.frame_sha256"),
            detector_report_sha256=_digest(
                root["detector_report_sha256"], f"{context}.detector_report_sha256"
            ),
            decision=RouteEvidenceReviewDecision(_string(root["decision"], f"{context}.decision")),
            detection=_detection(root["reviewed_detection"], f"{context}.reviewed_detection"),
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid {context}: {exc}") from exc


def _parse_review(payload: bytes) -> RouteEvidenceReview:
    root = _strict_canonical_object(payload, "independent review")
    _exact_keys(
        root,
        {
            "activation_allowed",
            "campaign_id",
            "cases",
            "evidence_role",
            "finalized_package_sha256",
            "input_authority",
            "reviewed_at_utc",
            "reviewer_id",
            "route",
            "route_plan_sha256",
            "schema",
            "truth_source",
        },
        "independent review",
    )
    _fixed(root["schema"], _REVIEW_SCHEMA, "independent review.schema")
    _fixed(root["truth_source"], _TRUTH_SOURCE, "independent review.truth_source")
    _fixed(root["evidence_role"], SYNTHETIC_ROUTE_EVIDENCE_ROLE, "review.evidence_role")
    _fixed(root["activation_allowed"], False, "review.activation_allowed")
    _fixed(root["input_authority"], False, "review.input_authority")
    cases = tuple(
        _case_truth(item, f"independent review.cases[{index}]")
        for index, item in enumerate(_array(root["cases"], "independent review.cases"))
    )
    try:
        result = RouteEvidenceReview(
            finalized_package_sha256=_digest(
                root["finalized_package_sha256"], "review.finalized_package_sha256"
            ),
            campaign_id=_identifier(root["campaign_id"], "review.campaign_id"),
            route=_route_identity(root["route"], "review.route"),
            route_plan_sha256=_digest(root["route_plan_sha256"], "review.route_plan_sha256"),
            reviewer_id=_identifier(root["reviewer_id"], "review.reviewer_id"),
            reviewed_at_utc=_string(root["reviewed_at_utc"], "review.reviewed_at_utc"),
            cases=cases,
        )
    except ValueError as exc:
        raise RouteEvidenceIntegrityError(f"invalid independent review: {exc}") from exc
    if canonical_route_evidence_bytes(result.to_json_value()) != payload:
        raise RouteEvidenceIntegrityError("independent review typed canonical round-trip changed")
    return result


def _strict_relative_path(value: object, context: str) -> str:
    text = _string(value, context)
    pure = PurePosixPath(text)
    if (
        "\\" in text
        or pure.is_absolute()
        or pure.as_posix() != text
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise RouteEvidenceIntegrityError(f"{context} must be a safe relative POSIX path")
    for part in pure.parts:
        if any(character in part for character in '<>:"|?*') or part.endswith((".", " ")):
            raise RouteEvidenceIntegrityError(f"{context} has a Windows-unsafe component")
        if part.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED_COMPONENTS:
            raise RouteEvidenceIntegrityError(f"{context} uses a reserved Windows device name")
    return text


def _path_alias(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _stat_signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_mode,
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        int(getattr(value, "st_file_attributes", 0)),
        int(getattr(value, "st_reparse_tag", 0)),
    )


def _cross_handle_identity(value: os.stat_result) -> tuple[int, ...]:
    """Fields Windows reports consistently for path and open-handle stats."""

    return (
        value.st_mode,
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        int(getattr(value, "st_file_attributes", 0)),
        int(getattr(value, "st_reparse_tag", 0)),
    )


def _directory_signature_identity(signature: tuple[int, ...]) -> tuple[int, ...]:
    """Exclude asynchronously updated Windows directory timestamps."""

    return (
        signature[0],
        signature[1],
        signature[2],
        signature[3],
        signature[7],
        signature[8],
    )


def _stable_tree_identity(tree: Mapping[str, _TreeEntry]) -> dict[str, object]:
    return {
        relative: (
            (True, _directory_signature_identity(entry.signature))
            if entry.is_directory
            else (False, entry.signature)
        )
        for relative, entry in tree.items()
    }


def _is_link_or_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        int(getattr(value, "st_file_attributes", 0)) & _REPARSE_POINT_ATTRIBUTE
    )


def _lstat(path: Path, context: str) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as exc:
        raise RouteEvidenceIntegrityError(f"cannot inspect {context}: {exc}") from exc
    if _is_link_or_reparse(result):
        raise RouteEvidenceIntegrityError(f"{context} is a symlink or reparse point")
    return result


def _root_path(root: str | os.PathLike[str]) -> tuple[Path, tuple[int, ...]]:
    path = Path(root).absolute()
    root_stat = _lstat(path, "route evidence root")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise RouteEvidenceIntegrityError("route evidence root must be a directory")
    return path, _stat_signature(root_stat)


def _owned_path(root: Path, relative_path: str) -> Path:
    safe = _strict_relative_path(relative_path, "owned relative path")
    pure = PurePosixPath(safe)
    current = root
    for index, part in enumerate(pure.parts):
        current = current / part
        current_stat = _lstat(current, f"owned path component {safe!r}")
        if index < len(pure.parts) - 1 and not stat.S_ISDIR(current_stat.st_mode):
            raise RouteEvidenceIntegrityError(f"owned path parent is not a directory: {safe}")
    try:
        resolved_root = root.resolve(strict=True)
        resolved = current.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise RouteEvidenceIntegrityError(f"owned path escapes evidence root: {safe}") from exc
    return current


def _read_owned_file(root: Path, relative_path: str, max_bytes: int) -> _FileSnapshot:
    path = _owned_path(root, relative_path)
    before = _lstat(path, f"owned file {relative_path!r}")
    if not stat.S_ISREG(before.st_mode):
        raise RouteEvidenceIntegrityError(f"owned path is not a regular file: {relative_path}")
    if before.st_size > max_bytes:
        raise RouteEvidenceIntegrityError(f"owned file exceeds its size limit: {relative_path}")
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RouteEvidenceIntegrityError(f"cannot open owned file {relative_path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        total_bytes = 0
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise RouteEvidenceIntegrityError(
                    f"owned file grew beyond its size limit: {relative_path}"
                )
        after_open = os.fstat(descriptor)
    except OSError as exc:
        raise RouteEvidenceIntegrityError(f"cannot read owned file {relative_path}: {exc}") from exc
    finally:
        os.close(descriptor)
    after_path = _lstat(path, f"owned file {relative_path!r}")
    if (
        _stat_signature(before) != _stat_signature(after_path)
        or _stat_signature(opened) != _stat_signature(after_open)
        or _cross_handle_identity(before) != _cross_handle_identity(opened)
    ):
        raise RouteEvidenceIntegrityError(f"owned file was replaced during read: {relative_path}")
    return _FileSnapshot(b"".join(chunks), _stat_signature(after_path))


def _scan_tree(root: Path) -> dict[str, _TreeEntry]:
    entries: dict[str, _TreeEntry] = {}
    aliases: dict[str, str] = {}

    def visit(directory: Path, prefix: PurePosixPath | None) -> None:
        try:
            children = list(os.scandir(directory))
        except OSError as exc:
            raise RouteEvidenceIntegrityError(
                f"cannot enumerate evidence directory: {exc}"
            ) from exc
        for child in children:
            relative = (
                PurePosixPath(child.name) if prefix is None else prefix / child.name
            ).as_posix()
            _strict_relative_path(relative, "filesystem entry")
            alias = _path_alias(relative)
            previous = aliases.get(alias)
            if previous is not None and previous != relative:
                raise RouteEvidenceIntegrityError(
                    f"case-fold or Unicode path alias collision: {previous!r}, {relative!r}"
                )
            aliases[alias] = relative
            child_stat = _lstat(Path(child.path), f"filesystem entry {relative!r}")
            if stat.S_ISDIR(child_stat.st_mode):
                entries[relative] = _TreeEntry(True, _stat_signature(child_stat))
                visit(Path(child.path), PurePosixPath(relative))
            elif stat.S_ISREG(child_stat.st_mode):
                entries[relative] = _TreeEntry(False, _stat_signature(child_stat))
            else:
                raise RouteEvidenceIntegrityError(
                    f"filesystem entry is not a regular file or directory: {relative}"
                )

    visit(root, None)
    return entries


def _expected_directories(expected_files: set[str]) -> set[str]:
    result: set[str] = set()
    for relative in expected_files:
        for parent in PurePosixPath(relative).parents:
            if parent.as_posix() != ".":
                result.add(parent.as_posix())
    return result


def _assert_exact_tree(root: Path, expected_files: set[str]) -> dict[str, _TreeEntry]:
    aliases: dict[str, str] = {}
    for relative in expected_files:
        _strict_relative_path(relative, "expected file path")
        alias = _path_alias(relative)
        previous = aliases.get(alias)
        if previous is not None and previous != relative:
            raise RouteEvidenceIntegrityError(
                f"expected paths have a case-fold alias collision: {previous!r}, {relative!r}"
            )
        aliases[alias] = relative
    expected_directories = _expected_directories(expected_files)
    tree = _scan_tree(root)
    actual_files = {path for path, entry in tree.items() if not entry.is_directory}
    actual_directories = {path for path, entry in tree.items() if entry.is_directory}
    if actual_files != expected_files or actual_directories != expected_directories:
        raise RouteEvidenceIntegrityError(
            "route evidence tree differs: "
            f"missing_files={sorted(expected_files - actual_files)}, "
            f"foreign_files={sorted(actual_files - expected_files)}, "
            f"missing_directories={sorted(expected_directories - actual_directories)}, "
            f"foreign_directories={sorted(actual_directories - expected_directories)}"
        )
    inode_owners: dict[tuple[int, int], str] = {}
    for relative in sorted(actual_files):
        signature = tree[relative].signature
        if signature[3] != 1:
            raise RouteEvidenceIntegrityError(
                f"owned file has an external hard-link alias: {relative!r}"
            )
        device, inode = signature[1], signature[2]
        if inode == 0:
            continue
        key = (device, inode)
        previous = inode_owners.get(key)
        if previous is not None:
            raise RouteEvidenceIntegrityError(
                f"owned files are hard-link aliases: {previous!r}, {relative!r}"
            )
        inode_owners[key] = relative
    return tree


def _validate_detector_reports(
    package: FinalizedRouteEvidencePackage,
    artifacts: Mapping[str, bytes],
) -> None:
    route_checkpoint_ids = {
        item.checkpoint_id for item in package.campaign_plan.route_plan.checkpoints
    }
    for owned in package.cases:
        path = owned.detector_report_artifact.relative_path
        payload = artifacts[path]
        _strict_canonical_object(payload, f"detector report artifact {owned.case_id}")
        try:
            report = parse_synthetic_detector_report(payload)
        except RouteEvidenceIntegrityError:
            raise
        except (OverflowError, RecursionError, TypeError, ValueError) as exc:
            raise RouteEvidenceIntegrityError(
                f"invalid detector report artifact {owned.case_id}: {exc}"
            ) from exc
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
        ):
            raise RouteEvidenceIntegrityError(
                f"detector report identity differs from owned case: {owned.case_id}"
            )
        if any(
            checkpoint_id not in route_checkpoint_ids
            for checkpoint_id in report.detection.candidate_checkpoint_ids
        ):
            raise RouteEvidenceIntegrityError(
                f"detector report names a foreign checkpoint: {owned.case_id}"
            )


def _reject_duplicate_artifact_content(package: FinalizedRouteEvidencePackage) -> None:
    frame_digests = tuple(item.frame_artifact.sha256 for item in package.cases)
    if len(set(frame_digests)) != len(frame_digests):
        raise RouteEvidenceIntegrityError("finalized package reuses exact frame content")
    report_digests = tuple(item.detector_report_artifact.sha256 for item in package.cases)
    if len(set(report_digests)) != len(report_digests):
        raise RouteEvidenceIntegrityError("finalized package reuses exact detector-report content")


def load_and_verify_synthetic_route_evidence(
    root: str | os.PathLike[str],
    expectation: RouteEvidenceFilesystemExpectation,
) -> RouteEvidenceVerificationReport:
    """Load and verify one exact canonical package without writing or granting authority."""

    if not isinstance(expectation, RouteEvidenceFilesystemExpectation):
        raise TypeError("expectation must be RouteEvidenceFilesystemExpectation")
    root_path, root_signature = _root_path(root)
    initial_package = _read_owned_file(root_path, FINALIZED_PACKAGE_FILENAME, _MAX_MANIFEST_BYTES)
    initial_review = _read_owned_file(root_path, INDEPENDENT_REVIEW_FILENAME, _MAX_MANIFEST_BYTES)
    package = _parse_package(initial_package.payload)
    review = _parse_review(initial_review.payload)
    if package.content_sha256 != expectation.finalized_package_sha256:
        raise RouteEvidenceIntegrityError("finalized package digest differs from expectation")
    if review.content_sha256 != expectation.independent_review_sha256:
        raise RouteEvidenceIntegrityError("independent review digest differs from expectation")
    if review.reviewer_id != expectation.reviewer_id:
        raise RouteEvidenceIntegrityError("independent reviewer differs from expectation")
    artifact_paths = {
        artifact.relative_path
        for owned in package.cases
        for artifact in (owned.frame_artifact, owned.detector_report_artifact)
    }
    fixed_paths = {FINALIZED_PACKAGE_FILENAME, INDEPENDENT_REVIEW_FILENAME}
    if artifact_paths & fixed_paths:
        raise RouteEvidenceIntegrityError("an artifact path collides with a fixed manifest")
    expected_files = fixed_paths | artifact_paths
    size_limits: dict[str, int] = {
        FINALIZED_PACKAGE_FILENAME: _MAX_MANIFEST_BYTES,
        INDEPENDENT_REVIEW_FILENAME: _MAX_MANIFEST_BYTES,
    }
    for owned in package.cases:
        for artifact in (owned.frame_artifact, owned.detector_report_artifact):
            if artifact.size_bytes > _MAX_ARTIFACT_BYTES:
                raise RouteEvidenceIntegrityError(
                    f"declared artifact exceeds the loader limit: {artifact.relative_path}"
                )
            size_limits[artifact.relative_path] = artifact.size_bytes
    initial_tree = _assert_exact_tree(root_path, expected_files)
    snapshots = {
        relative: _read_owned_file(root_path, relative, size_limits[relative])
        for relative in sorted(expected_files)
    }
    if snapshots[FINALIZED_PACKAGE_FILENAME].payload != initial_package.payload:
        raise RouteEvidenceIntegrityError("finalized package changed during intake")
    if snapshots[INDEPENDENT_REVIEW_FILENAME].payload != initial_review.payload:
        raise RouteEvidenceIntegrityError("independent review changed during intake")
    artifacts = {relative: snapshots[relative].payload for relative in artifact_paths}
    _reject_duplicate_artifact_content(package)
    _validate_detector_reports(package, artifacts)
    report = verify_synthetic_route_evidence(package, review, artifacts, expectation)

    final_tree = _assert_exact_tree(root_path, expected_files)
    if _stable_tree_identity(final_tree) != _stable_tree_identity(initial_tree):
        raise RouteEvidenceIntegrityError("route evidence tree changed during verification")
    for relative, before in snapshots.items():
        after = _read_owned_file(root_path, relative, size_limits[relative])
        if after != before:
            raise RouteEvidenceIntegrityError(
                f"owned file changed during final verification: {relative}"
            )
    final_root = _lstat(root_path, "route evidence root")
    if _directory_signature_identity(_stat_signature(final_root)) != (
        _directory_signature_identity(root_signature)
    ):
        raise RouteEvidenceIntegrityError("route evidence root changed during verification")
    return report
