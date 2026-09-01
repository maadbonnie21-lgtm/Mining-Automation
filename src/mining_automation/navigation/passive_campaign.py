"""Single-writer, input-disabled acquisition for synthetic route evidence.

The sequencer owns one append-only campaign head. Inspectable snapshots cannot
be submitted back to fork, retry, or finalize a sibling history. A capture-only
source is invoked exactly once per acknowledged request, and the guarded
checkpoint detector runs internally over the exact returned frame.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from types import MappingProxyType
from typing import Final, Literal, NoReturn, Protocol, SupportsIndex, cast, runtime_checkable

from ..capture.frame import Frame
from ..contracts import FrameRef
from .checkpoint_evidence import CheckpointDetector, run_checkpoint_detector
from .contracts import (
    Checkpoint,
    CheckpointDetectorIdentity,
    CheckpointProfile,
    CheckpointProfileIdentity,
    CheckpointSourceIdentity,
    RouteDirection,
    RouteEndpoint,
    RouteIdentity,
    RoutePlan,
    RouteStep,
    Sha256Digest,
)
from .route_evidence import (
    FinalizedRouteEvidencePackage,
    OwnedRouteEvidenceCase,
    RouteEvidenceAcquisitionBinding,
    RouteEvidenceArtifactRef,
    RouteEvidenceCampaignPlan,
    RouteEvidenceCaptureBuildIdentity,
    RouteEvidenceCaseSpec,
    RouteEvidenceOperatorIntent,
    SyntheticRouteEvidenceDetectorReport,
    route_evidence_sha256,
)

__all__ = [
    "PASSIVE_CAPTURE_REQUEST_TIMEOUT_S",
    "PassiveCampaignFailure",
    "PassiveCampaignFailureReason",
    "PassiveCampaignFinalization",
    "PassiveCampaignFinalizationError",
    "PassiveCampaignPhase",
    "PassiveCampaignProgress",
    "PassiveCampaignSequencer",
    "PassiveCaptureRequest",
    "PassiveCaptureSource",
    "PassiveCaptureSourceIdentity",
    "PassiveMonotonicClock",
    "PassiveOwnedCapture",
    "PassiveSourceFrame",
]

PASSIVE_CAPTURE_REQUEST_TIMEOUT_S: Final[float] = 30.0
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_FACTORY_TOKEN: Final[object] = object()


class PassiveCampaignFinalizationError(RuntimeError):
    """A partial, failed, or already-finalized session cannot be sealed."""


class PassiveCampaignPhase(StrEnum):
    READY_FOR_REQUEST = "ready_for_request"
    AWAITING_CAPTURE = "awaiting_capture"
    COMPLETE = "complete"
    FINALIZED = "finalized"
    STOPPED = "stopped"


class PassiveCampaignFailureReason(StrEnum):
    OPERATOR_MISMATCH = "operator_mismatch"
    ACKNOWLEDGEMENT_NOT_EXPECTED = "acknowledgement_not_expected"
    OUT_OF_ORDER_EVENT = "out_of_order_event"
    CAPTURE_NOT_REQUESTED = "capture_not_requested"
    CAPTURE_TIMEOUT = "capture_timeout"
    FRAME_NOT_AFTER_REQUEST = "frame_not_after_request"
    FRAME_FROM_FUTURE = "frame_from_future"
    FRAME_PROVENANCE_MISMATCH = "frame_provenance_mismatch"
    CAPTURE_CONTRACT_MISMATCH = "capture_contract_mismatch"
    DUPLICATE_CAPTURE = "duplicate_capture"
    CAPTURE_REENTRANCY = "capture_reentrancy"
    CAPTURE_FAILED = "capture_failed"
    FINALIZATION_FAILED = "finalization_failed"
    FINALIZATION_REENTRANCY = "finalization_reentrancy"
    INTERRUPTED = "interrupted"
    SOURCE_REPLACED = "source_replaced"


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a portable non-empty identifier")
    return value


def _time(value: object, field_name: str) -> float:
    if (
        type(value) is not float
        or not math.isfinite(value)
        or value < 0
        or (value == 0.0 and math.copysign(1.0, value) < 0.0)
    ):
        raise ValueError(f"{field_name} must be an exact finite non-negative float")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"{field_name} must be an ISO-8601 UTC string ending in Z")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be valid ISO-8601 UTC") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ValueError(f"{field_name} must use UTC")
    return result


@dataclass(frozen=True, slots=True)
class PassiveCaptureSourceIdentity:
    """Exact source-owned pins required by one passive campaign."""

    checkpoint_source: CheckpointSourceIdentity
    capture_build: RouteEvidenceCaptureBuildIdentity
    capture_configuration_sha256: Sha256Digest
    capture_environment_sha256: Sha256Digest
    support_envelope_sha256: Sha256Digest
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_source, CheckpointSourceIdentity):
            raise ValueError("passive source requires CheckpointSourceIdentity")
        if not isinstance(self.capture_build, RouteEvidenceCaptureBuildIdentity):
            raise ValueError("passive source capture build has the wrong type")
        if any(
            not isinstance(value, Sha256Digest)
            for value in (
                self.capture_configuration_sha256,
                self.capture_environment_sha256,
                self.support_envelope_sha256,
            )
        ):
            raise ValueError("passive source digests must be Sha256Digest")

    @property
    def content_sha256(self) -> Sha256Digest:
        source = self.checkpoint_source
        profile = source.profile.identity
        return route_evidence_sha256(
            {
                "capture_build": self.capture_build.to_json_value(),
                "capture_configuration_sha256": self.capture_configuration_sha256.value,
                "capture_environment_sha256": self.capture_environment_sha256.value,
                "capture_session_id": source.capture_session_id,
                "capture_source_id": source.frame_source_id,
                "checkpoint_detector": {
                    "detector_id": source.detector.detector_id,
                    "version": source.detector.version,
                },
                "checkpoint_profile": {
                    "content_sha256": profile.content_sha256.value,
                    "profile_id": profile.profile_id,
                    "version": profile.version,
                },
                "required_frame": {
                    "height": source.frame_height,
                    "pixel_format": source.pixel_format.value,
                    "width": source.frame_width,
                },
                "input_authority": self.input_authority,
                "live_navigation_enabled": self.live_navigation_enabled,
                "support_envelope_sha256": self.support_envelope_sha256.value,
            }
        )


@dataclass(frozen=True, slots=True)
class PassiveCaptureRequest:
    """One capture-only request; the operator supplies no checkpoint truth."""

    campaign_id: str
    campaign_plan_sha256: Sha256Digest
    capture_session_id: str
    request_id: str
    sequence_index: int
    case_id: str
    operator_id: str
    acknowledged_monotonic_s: float
    expires_monotonic_s: float
    operator_acknowledgement_is_reviewer_truth: Literal[False] = field(default=False, init=False)
    checkpoint_truth_asserted: Literal[False] = field(default=False, init=False)
    navigation_automation_enabled: Literal[False] = field(default=False, init=False)
    camera_automation_enabled: Literal[False] = field(default=False, init=False)
    mouse_input_enabled: Literal[False] = field(default=False, init=False)
    keyboard_input_enabled: Literal[False] = field(default=False, init=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("capture requests may only be issued by the passive sequencer")
        _identifier(self.campaign_id, "request campaign_id")
        if not isinstance(self.campaign_plan_sha256, Sha256Digest):
            raise ValueError("request campaign digest must be Sha256Digest")
        _identifier(self.capture_session_id, "request capture_session_id")
        _identifier(self.request_id, "request_id")
        if type(self.sequence_index) is not int or self.sequence_index < 1:
            raise ValueError("request sequence_index must be a positive integer")
        _identifier(self.case_id, "request case_id")
        _identifier(self.operator_id, "request operator_id")
        acknowledged = _time(self.acknowledged_monotonic_s, "request acknowledgement time")
        expires = _time(self.expires_monotonic_s, "request expiry time")
        if (
            expires != acknowledged + PASSIVE_CAPTURE_REQUEST_TIMEOUT_S
            or expires - acknowledged != PASSIVE_CAPTURE_REQUEST_TIMEOUT_S
        ):
            raise ValueError("request expiry must use the fixed passive capture timeout")


@dataclass(frozen=True, slots=True)
class PassiveSourceFrame:
    """One immutable result returned directly by a capture-only source."""

    source: PassiveCaptureSourceIdentity
    request_id: str
    capture_id: str
    captured_at_utc: str
    frame: Frame

    def __post_init__(self) -> None:
        if not isinstance(self.source, PassiveCaptureSourceIdentity):
            raise ValueError("source frame identity has the wrong type")
        if (
            self.source.live_navigation_enabled is not False
            or self.source.input_authority is not False
        ):
            raise ValueError("source frame identity cannot carry navigation or input authority")
        _identifier(self.request_id, "source frame request_id")
        _identifier(self.capture_id, "source frame capture_id")
        _utc(self.captured_at_utc, "source frame captured_at_utc")
        if not isinstance(self.frame, Frame) or type(self.frame.payload) is not bytes:
            raise ValueError("source frame must contain an immutable owned Frame")


@runtime_checkable
class PassiveCaptureSource(Protocol):
    @property
    def identity(self) -> PassiveCaptureSourceIdentity: ...

    def capture(self, request: PassiveCaptureRequest, /) -> PassiveSourceFrame: ...


@runtime_checkable
class PassiveMonotonicClock(Protocol):
    """Trusted side-effect-free timing seam with no navigation/input authority."""

    @property
    def live_navigation_enabled(self) -> Literal[False]: ...

    @property
    def input_authority(self) -> Literal[False]: ...

    def now_monotonic_s(self) -> float: ...


@dataclass(frozen=True, slots=True)
class PassiveOwnedCapture:
    request: PassiveCaptureRequest
    owned_case: OwnedRouteEvidenceCase
    frame_payload: bytes
    detector_report_payload: bytes
    recorded_monotonic_s: float
    detector_output_is_reviewer_truth: Literal[False] = field(default=False, init=False)
    operator_acknowledgement_is_reviewer_truth: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("owned captures may only be issued by the passive sequencer")
        if not isinstance(self.request, PassiveCaptureRequest) or not isinstance(
            self.owned_case, OwnedRouteEvidenceCase
        ):
            raise ValueError("owned capture requires exact request and owned-case types")
        if type(self.frame_payload) is not bytes or type(self.detector_report_payload) is not bytes:
            raise ValueError("owned capture payloads must be immutable bytes")
        if self.owned_case.acquisition.request_id != self.request.request_id:
            raise ValueError("owned capture acquisition differs from its request")
        if (
            len(self.frame_payload) != self.owned_case.frame_artifact.size_bytes
            or Sha256Digest.from_bytes(self.frame_payload) != self.owned_case.frame_artifact.sha256
            or len(self.detector_report_payload)
            != self.owned_case.detector_report_artifact.size_bytes
            or Sha256Digest.from_bytes(self.detector_report_payload)
            != self.owned_case.detector_report_artifact.sha256
        ):
            raise ValueError("owned capture bytes differ from their artifact bindings")
        if _time(self.recorded_monotonic_s, "capture recorded time") != (
            self.owned_case.acquisition.recorded_monotonic_s
        ):
            raise ValueError("owned capture time differs from its acquisition binding")


@dataclass(frozen=True, slots=True)
class PassiveCampaignFailure:
    reason: PassiveCampaignFailureReason
    failed_monotonic_s: float
    request: PassiveCaptureRequest | None
    no_retry_in_same_session: Literal[True] = field(default=True, init=False)
    review_eligible: Literal[False] = field(default=False, init=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("campaign failures may only be issued by the passive sequencer")
        if not isinstance(self.reason, PassiveCampaignFailureReason):
            raise ValueError("failure reason has the wrong type")
        _time(self.failed_monotonic_s, "failure time")
        if self.request is not None and not isinstance(self.request, PassiveCaptureRequest):
            raise ValueError("failure request has the wrong type")


@dataclass(frozen=True, slots=True)
class PassiveCampaignProgress:
    plan: RouteEvidenceCampaignPlan
    source: PassiveCaptureSourceIdentity
    phase: PassiveCampaignPhase
    started_monotonic_s: float
    last_event_monotonic_s: float
    captures: tuple[PassiveOwnedCapture, ...] = ()
    pending_request: PassiveCaptureRequest | None = None
    failure: PassiveCampaignFailure | None = None
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("progress may only be issued by the passive sequencer")
        if not isinstance(self.plan, RouteEvidenceCampaignPlan):
            raise ValueError("progress plan has the wrong type")
        _require_source_matches_plan(self.plan, self.source)
        if not isinstance(self.phase, PassiveCampaignPhase):
            raise ValueError("progress phase has the wrong type")
        if _time(self.last_event_monotonic_s, "event time") < _time(
            self.started_monotonic_s, "start time"
        ):
            raise ValueError("event time cannot precede start")
        if type(self.captures) is not tuple or any(
            not isinstance(item, PassiveOwnedCapture) for item in self.captures
        ):
            raise ValueError("captures must be an exact tuple")
        if len(self.captures) > len(self.plan.cases):
            raise ValueError("captures exceed the preregistered campaign")
        previous_digest = self.plan.content_sha256
        previous_recorded: float | None = None
        request_ids: set[str] = set()
        capture_ids: set[str] = set()
        for spec, capture in zip(self.plan.cases, self.captures, strict=False):
            request = capture.request
            owned = capture.owned_case
            if (
                request.campaign_id != self.plan.campaign_id
                or request.campaign_plan_sha256 != self.plan.content_sha256
                or request.capture_session_id != self.plan.capture_session_id
                or request.operator_id != self.plan.operator_id
                or request.sequence_index != spec.ordinal
                or request.case_id != spec.case_id
                or owned.sequence_index != spec.ordinal
                or owned.case_id != spec.case_id
                or owned.acquisition.previous_acquisition_sha256 != previous_digest
            ):
                raise ValueError("captures do not follow the preregistered acquisition order")
            if request.request_id in request_ids or owned.capture_id in capture_ids:
                raise ValueError("capture request and capture ids must be unique")
            if (
                previous_recorded is not None
                and request.acknowledged_monotonic_s <= previous_recorded
            ):
                raise ValueError("capture requests must follow strict chronology")
            request_ids.add(request.request_id)
            capture_ids.add(owned.capture_id)
            previous_digest = owned.content_sha256
            previous_recorded = capture.recorded_monotonic_s
        if self.phase is PassiveCampaignPhase.READY_FOR_REQUEST:
            valid = (
                len(self.captures) < len(self.plan.cases)
                and self.pending_request is None
                and self.failure is None
            )
        elif self.phase is PassiveCampaignPhase.AWAITING_CAPTURE:
            next_spec = self.plan.cases[len(self.captures)]
            valid = (
                isinstance(self.pending_request, PassiveCaptureRequest)
                and self.pending_request.campaign_id == self.plan.campaign_id
                and self.pending_request.campaign_plan_sha256 == self.plan.content_sha256
                and self.pending_request.capture_session_id == self.plan.capture_session_id
                and self.pending_request.operator_id == self.plan.operator_id
                and self.pending_request.sequence_index == next_spec.ordinal
                and self.pending_request.case_id == next_spec.case_id
                and self.failure is None
            )
        elif self.phase in {PassiveCampaignPhase.COMPLETE, PassiveCampaignPhase.FINALIZED}:
            valid = len(self.captures) == len(self.plan.cases) and self.pending_request is None
        else:
            valid = self.pending_request is None and isinstance(
                self.failure, PassiveCampaignFailure
            )
            if valid and self.failure is not None and self.failure.request is not None:
                failed_request = self.failure.request
                valid = (
                    failed_request.campaign_id == self.plan.campaign_id
                    and failed_request.campaign_plan_sha256 == self.plan.content_sha256
                    and failed_request.capture_session_id == self.plan.capture_session_id
                    and failed_request.operator_id == self.plan.operator_id
                )
        if not valid or (self.phase is not PassiveCampaignPhase.STOPPED) != (self.failure is None):
            raise ValueError("phase differs from append-only state")

    @property
    def direction(self) -> RouteDirection:
        return self.plan.route.direction

    @property
    def review_eligible(self) -> bool:
        return self.phase is PassiveCampaignPhase.FINALIZED


@dataclass(frozen=True, slots=True)
class PassiveCampaignFinalization:
    progress: PassiveCampaignProgress
    package: FinalizedRouteEvidencePackage
    artifact_payloads: tuple[tuple[str, bytes], ...]
    review_created: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("finalizations may only be issued by the passive sequencer")
        if self.progress.phase is not PassiveCampaignPhase.FINALIZED:
            raise ValueError("finalization requires finalized progress")
        if self.package.campaign_plan != self.progress.plan or self.package.cases != tuple(
            capture.owned_case for capture in self.progress.captures
        ):
            raise ValueError("package differs from passive progress")
        expected = tuple(
            pair
            for capture in self.progress.captures
            for pair in (
                (capture.owned_case.frame_artifact.relative_path, capture.frame_payload),
                (
                    capture.owned_case.detector_report_artifact.relative_path,
                    capture.detector_report_payload,
                ),
            )
        )
        if self.artifact_payloads != expected:
            raise ValueError("artifact bytes differ from passive progress")

    @property
    def artifacts(self) -> Mapping[str, bytes]:
        return MappingProxyType(dict(self.artifact_payloads))


def _require_source_matches_plan(
    plan: RouteEvidenceCampaignPlan,
    source: PassiveCaptureSourceIdentity,
) -> None:
    if not isinstance(source, PassiveCaptureSourceIdentity):
        raise ValueError("passive campaign source has the wrong type")
    if source.live_navigation_enabled is not False or source.input_authority is not False:
        raise ValueError("passive campaign source cannot carry navigation or input authority")
    checkpoint_source = source.checkpoint_source
    if (
        checkpoint_source.detector != plan.detector
        or checkpoint_source.profile.identity != plan.profile
        or checkpoint_source.frame_source_id != plan.capture_source_id
        or checkpoint_source.capture_session_id != plan.capture_session_id
        or checkpoint_source.frame_width != plan.frame_width
        or checkpoint_source.frame_height != plan.frame_height
        or checkpoint_source.pixel_format is not plan.pixel_format
        or source.capture_build != plan.capture_build
        or source.capture_configuration_sha256 != plan.capture_configuration_sha256
        or source.capture_environment_sha256 != plan.capture_environment_sha256
        or source.support_envelope_sha256 != plan.support_envelope_sha256
        or source.content_sha256 != plan.capture_source_identity_sha256
    ):
        raise ValueError("passive campaign source differs from the plan")
    route_ids = tuple(item.checkpoint_id for item in plan.route_plan.checkpoints)
    route_id_set = set(route_ids)
    if (
        tuple(item for item in checkpoint_source.profile.checkpoint_ids if item in route_id_set)
        != route_ids
    ):
        raise ValueError("checkpoint profile does not preserve exact route order")


def _snapshot_source_identity(
    source: PassiveCaptureSourceIdentity,
) -> PassiveCaptureSourceIdentity:
    if type(source) is not PassiveCaptureSourceIdentity:
        raise ValueError("passive source identity must have the exact contract type")
    checkpoint_source = source.checkpoint_source
    profile = checkpoint_source.profile
    snapshot_profile = CheckpointProfile(
        profile_id=profile.profile_id,
        version=profile.version,
        evidence_role=profile.evidence_role,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
        pixel_format=profile.pixel_format,
        checkpoint_ids=tuple(profile.checkpoint_ids),
    )
    snapshot_source = CheckpointSourceIdentity(
        detector=CheckpointDetectorIdentity(
            checkpoint_source.detector.detector_id,
            checkpoint_source.detector.version,
        ),
        profile=snapshot_profile,
        frame_source_id=checkpoint_source.frame_source_id,
        capture_session_id=checkpoint_source.capture_session_id,
    )
    snapshot = PassiveCaptureSourceIdentity(
        checkpoint_source=snapshot_source,
        capture_build=RouteEvidenceCaptureBuildIdentity(
            source.capture_build.build_id,
            source.capture_build.version,
            Sha256Digest(source.capture_build.content_sha256.value),
        ),
        capture_configuration_sha256=Sha256Digest(source.capture_configuration_sha256.value),
        capture_environment_sha256=Sha256Digest(source.capture_environment_sha256.value),
        support_envelope_sha256=Sha256Digest(source.support_envelope_sha256.value),
    )
    if source.live_navigation_enabled is not False or source.input_authority is not False:
        raise ValueError("passive source identity changed its fixed-false authority")
    return snapshot


def _snapshot_campaign_plan(plan: RouteEvidenceCampaignPlan) -> RouteEvidenceCampaignPlan:
    if type(plan) is not RouteEvidenceCampaignPlan:
        raise ValueError("campaign plan must have the exact contract type")
    route = plan.route_plan
    snapshot_route = RoutePlan(
        identity=RouteIdentity(
            route.identity.route_id,
            route.identity.version,
            route.identity.direction,
        ),
        origin=RouteEndpoint(route.origin.location_id, route.origin.role),
        destination=RouteEndpoint(route.destination.location_id, route.destination.role),
        checkpoints=tuple(
            Checkpoint(checkpoint.checkpoint_id, checkpoint.role)
            for checkpoint in route.checkpoints
        ),
        steps=tuple(
            RouteStep(
                step.step_id,
                step.from_checkpoint_id,
                step.to_checkpoint_id,
            )
            for step in route.steps
        ),
    )
    snapshot = RouteEvidenceCampaignPlan(
        campaign_id=plan.campaign_id,
        route_plan=snapshot_route,
        detector=CheckpointDetectorIdentity(plan.detector.detector_id, plan.detector.version),
        profile=CheckpointProfileIdentity(
            plan.profile.profile_id,
            plan.profile.version,
            Sha256Digest(plan.profile.content_sha256.value),
        ),
        capture_source_id=plan.capture_source_id,
        capture_session_id=plan.capture_session_id,
        capture_build=RouteEvidenceCaptureBuildIdentity(
            plan.capture_build.build_id,
            plan.capture_build.version,
            Sha256Digest(plan.capture_build.content_sha256.value),
        ),
        frame_width=plan.frame_width,
        frame_height=plan.frame_height,
        pixel_format=plan.pixel_format,
        capture_configuration_sha256=Sha256Digest(plan.capture_configuration_sha256.value),
        capture_environment_sha256=Sha256Digest(plan.capture_environment_sha256.value),
        support_envelope_sha256=Sha256Digest(plan.support_envelope_sha256.value),
        operator_id=plan.operator_id,
        created_at_utc=plan.created_at_utc,
        cases=tuple(
            RouteEvidenceCaseSpec(
                case.ordinal,
                case.case_id,
                case.role,
                case.checkpoint_id,
            )
            for case in plan.cases
        ),
    )
    if plan.activation_allowed is not False or plan.input_authority is not False:
        raise ValueError("campaign plan changed its fixed-false authority")
    return snapshot


def _snapshot_request_contract(request: PassiveCaptureRequest) -> PassiveCaptureRequest:
    if type(request) is not PassiveCaptureRequest:
        raise ValueError("pending capture request has the wrong type")
    if (
        request.operator_acknowledgement_is_reviewer_truth is not False
        or request.checkpoint_truth_asserted is not False
        or request.navigation_automation_enabled is not False
        or request.camera_automation_enabled is not False
        or request.mouse_input_enabled is not False
        or request.keyboard_input_enabled is not False
    ):
        raise ValueError("pending capture request changed its fixed-false authority")
    return PassiveCaptureRequest(
        campaign_id=request.campaign_id,
        campaign_plan_sha256=Sha256Digest(request.campaign_plan_sha256.value),
        capture_session_id=request.capture_session_id,
        request_id=request.request_id,
        sequence_index=request.sequence_index,
        case_id=request.case_id,
        operator_id=request.operator_id,
        acknowledged_monotonic_s=request.acknowledged_monotonic_s,
        expires_monotonic_s=request.expires_monotonic_s,
        _factory_token=_FACTORY_TOKEN,
    )


def _snapshot_acquisition(
    acquisition: RouteEvidenceAcquisitionBinding,
) -> RouteEvidenceAcquisitionBinding:
    if type(acquisition) is not RouteEvidenceAcquisitionBinding:
        raise ValueError("acquisition binding has the wrong type")
    if (
        acquisition.schema != "fixed-route-evidence-acquisition-binding-v1"
        or acquisition.operator_acknowledgement_is_reviewer_truth is not False
        or acquisition.checkpoint_truth_asserted is not False
        or acquisition.navigation_automation_enabled is not False
        or acquisition.camera_automation_enabled is not False
        or acquisition.mouse_input_enabled is not False
        or acquisition.keyboard_input_enabled is not False
    ):
        raise ValueError("acquisition binding changed its fixed contract")
    return RouteEvidenceAcquisitionBinding(
        campaign_plan_sha256=Sha256Digest(acquisition.campaign_plan_sha256.value),
        capture_source_identity_sha256=Sha256Digest(
            acquisition.capture_source_identity_sha256.value
        ),
        capture_session_id=acquisition.capture_session_id,
        request_id=acquisition.request_id,
        sequence_index=acquisition.sequence_index,
        case_id=acquisition.case_id,
        capture_id=acquisition.capture_id,
        operator_id=acquisition.operator_id,
        acknowledged_monotonic_s=acquisition.acknowledged_monotonic_s,
        expires_monotonic_s=acquisition.expires_monotonic_s,
        frame_captured_monotonic_s=acquisition.frame_captured_monotonic_s,
        recorded_monotonic_s=acquisition.recorded_monotonic_s,
        previous_acquisition_sha256=Sha256Digest(acquisition.previous_acquisition_sha256.value),
    )


def _snapshot_owned_case(owned: OwnedRouteEvidenceCase) -> OwnedRouteEvidenceCase:
    if type(owned) is not OwnedRouteEvidenceCase:
        raise ValueError("owned route evidence case has the wrong type")
    intent = owned.operator_intent
    if (
        owned.evidence_role != "synthetic_route_evidence_architecture_test_only"
        or owned.activation_allowed is not False
        or owned.input_authority is not False
        or type(intent) is not RouteEvidenceOperatorIntent
        or intent.status != "operator-intent-unverified"
        or intent.operator_intent_is_reviewer_truth is not False
    ):
        raise ValueError("owned route evidence case changed its fixed contract")
    return OwnedRouteEvidenceCase(
        campaign_id=owned.campaign_id,
        campaign_plan_sha256=Sha256Digest(owned.campaign_plan_sha256.value),
        route=RouteIdentity(
            owned.route.route_id,
            owned.route.version,
            owned.route.direction,
        ),
        route_plan_sha256=Sha256Digest(owned.route_plan_sha256.value),
        sequence_index=owned.sequence_index,
        case_id=owned.case_id,
        capture_id=owned.capture_id,
        operator_id=owned.operator_id,
        operator_intent=RouteEvidenceOperatorIntent(
            intent.case_id,
            intent.role,
            intent.checkpoint_id,
        ),
        acquisition=_snapshot_acquisition(owned.acquisition),
        detector=CheckpointDetectorIdentity(
            owned.detector.detector_id,
            owned.detector.version,
        ),
        profile=CheckpointProfileIdentity(
            owned.profile.profile_id,
            owned.profile.version,
            Sha256Digest(owned.profile.content_sha256.value),
        ),
        capture_source_id=owned.capture_source_id,
        capture_session_id=owned.capture_session_id,
        capture_build=RouteEvidenceCaptureBuildIdentity(
            owned.capture_build.build_id,
            owned.capture_build.version,
            Sha256Digest(owned.capture_build.content_sha256.value),
        ),
        capture_configuration_sha256=Sha256Digest(owned.capture_configuration_sha256.value),
        capture_environment_sha256=Sha256Digest(owned.capture_environment_sha256.value),
        support_envelope_sha256=Sha256Digest(owned.support_envelope_sha256.value),
        captured_at_utc=owned.captured_at_utc,
        frame_ref=FrameRef(
            owned.frame_ref.frame_id,
            owned.frame_ref.captured_monotonic_s,
            owned.frame_ref.width,
            owned.frame_ref.height,
        ),
        pixel_format=owned.pixel_format,
        frame_artifact=RouteEvidenceArtifactRef(
            owned.frame_artifact.relative_path,
            owned.frame_artifact.size_bytes,
            Sha256Digest(owned.frame_artifact.sha256.value),
        ),
        detector_report_artifact=RouteEvidenceArtifactRef(
            owned.detector_report_artifact.relative_path,
            owned.detector_report_artifact.size_bytes,
            Sha256Digest(owned.detector_report_artifact.sha256.value),
        ),
    )


def _snapshot_owned_capture(capture: PassiveOwnedCapture) -> PassiveOwnedCapture:
    if type(capture) is not PassiveOwnedCapture:
        raise ValueError("owned passive capture has the wrong type")
    if (
        capture.detector_output_is_reviewer_truth is not False
        or capture.operator_acknowledgement_is_reviewer_truth is not False
        or capture.input_authority is not False
    ):
        raise ValueError("owned passive capture changed its fixed-false authority")
    return PassiveOwnedCapture(
        request=_snapshot_request_contract(capture.request),
        owned_case=_snapshot_owned_case(capture.owned_case),
        frame_payload=bytes(capture.frame_payload),
        detector_report_payload=bytes(capture.detector_report_payload),
        recorded_monotonic_s=capture.recorded_monotonic_s,
        _factory_token=_FACTORY_TOKEN,
    )


def _snapshot_failure(failure: PassiveCampaignFailure) -> PassiveCampaignFailure:
    if type(failure) is not PassiveCampaignFailure:
        raise ValueError("passive campaign failure has the wrong type")
    if failure.no_retry_in_same_session is not True or failure.review_eligible is not False:
        raise ValueError("passive campaign failure changed its fixed contract")
    return PassiveCampaignFailure(
        reason=failure.reason,
        failed_monotonic_s=failure.failed_monotonic_s,
        request=(None if failure.request is None else _snapshot_request_contract(failure.request)),
        _factory_token=_FACTORY_TOKEN,
    )


def _snapshot_progress(progress: PassiveCampaignProgress) -> PassiveCampaignProgress:
    if type(progress) is not PassiveCampaignProgress:
        raise ValueError("passive campaign progress has the wrong type")
    if progress.live_navigation_enabled is not False or progress.input_authority is not False:
        raise ValueError("passive campaign progress changed its fixed-false authority")
    return PassiveCampaignProgress(
        plan=_snapshot_campaign_plan(progress.plan),
        source=_snapshot_source_identity(progress.source),
        phase=progress.phase,
        started_monotonic_s=progress.started_monotonic_s,
        last_event_monotonic_s=progress.last_event_monotonic_s,
        captures=tuple(_snapshot_owned_capture(capture) for capture in progress.captures),
        pending_request=(
            None
            if progress.pending_request is None
            else _snapshot_request_contract(progress.pending_request)
        ),
        failure=None if progress.failure is None else _snapshot_failure(progress.failure),
        _factory_token=_FACTORY_TOKEN,
    )


def _snapshot_finalization(
    finalization: PassiveCampaignFinalization,
) -> PassiveCampaignFinalization:
    if type(finalization) is not PassiveCampaignFinalization:
        raise ValueError("passive campaign finalization has the wrong type")
    package = finalization.package
    if (
        finalization.review_created is not False
        or finalization.activation_allowed is not False
        or finalization.input_authority is not False
        or type(package) is not FinalizedRouteEvidencePackage
        or package.status != "finalized"
        or package.evidence_role != "synthetic_route_evidence_architecture_test_only"
        or package.all_owned_cases_included is not True
        or package.selection_policy != "all-owned-cases-in-plan-order-no-drop-no-replacement"
        or package.activation_allowed is not False
        or package.input_authority is not False
    ):
        raise ValueError("passive finalization changed its fixed contract")
    progress = _snapshot_progress(finalization.progress)
    package_snapshot = FinalizedRouteEvidencePackage(
        campaign_plan=progress.plan,
        cases=tuple(capture.owned_case for capture in progress.captures),
        finalized_at_utc=package.finalized_at_utc,
        finalized_monotonic_s=package.finalized_monotonic_s,
    )
    if package_snapshot.content_sha256 != package.content_sha256:
        raise ValueError("passive finalization package changed after sealing")
    artifact_payloads = tuple(
        (path, bytes(payload)) for path, payload in finalization.artifact_payloads
    )
    return PassiveCampaignFinalization(
        progress=progress,
        package=package_snapshot,
        artifact_payloads=artifact_payloads,
        _factory_token=_FACTORY_TOKEN,
    )


class PassiveCampaignSequencer:
    """Single mutable campaign head; stale snapshots have no transition API."""

    __slots__ = (
        "_detector",
        "_clock",
        "_finalization",
        "_finalization_in_progress",
        "_finalization_invalidated",
        "_head",
        "_pending_request_snapshot",
        "_retired_sessions",
        "_source",
        "_source_digest",
        "_transition_lock",
        "_active_capture_token",
        "_active_request_snapshot",
        "_restart_in_progress",
        "_restart_invalidated",
        "_used_campaign_ids",
        "_used_capture_session_ids",
        "_used_request_ids",
    )

    def __init__(
        self,
        plan: RouteEvidenceCampaignPlan,
        source: PassiveCaptureSource,
        detector: CheckpointDetector,
        clock: PassiveMonotonicClock,
        *,
        started_monotonic_s: float,
    ) -> None:
        started = _time(started_monotonic_s, "campaign start time")
        plan_snapshot = _snapshot_campaign_plan(plan)
        source_identity = self._source_identity(source)
        _require_source_matches_plan(plan_snapshot, source_identity)
        self._require_detector_matches_source(detector, source_identity)
        clock_at_bind = self._clock_time(clock)
        if clock_at_bind < started:
            raise ValueError("passive campaign clock cannot precede its start")
        post_clock_source = self._source_identity(source)
        self._require_detector_matches_source(detector, post_clock_source)
        final_source = self._source_identity(source)
        self._require_detector_matches_source(detector, final_source)
        ultimate_source = self._source_identity(source)
        post_callback_plan = _snapshot_campaign_plan(plan)
        if (
            post_clock_source != source_identity
            or final_source != source_identity
            or ultimate_source != source_identity
            or post_callback_plan.content_sha256 != plan_snapshot.content_sha256
        ):
            raise ValueError("passive campaign binding changed during construction")
        self._head = PassiveCampaignProgress(
            plan_snapshot,
            source_identity,
            PassiveCampaignPhase.READY_FOR_REQUEST,
            started,
            started,
            _factory_token=_FACTORY_TOKEN,
        )
        self._source = source
        self._detector = detector
        self._clock = clock
        self._source_digest = source_identity.content_sha256
        self._transition_lock = RLock()
        self._active_capture_token: object | None = None
        self._active_request_snapshot: PassiveCaptureRequest | None = None
        self._restart_in_progress = False
        self._restart_invalidated = False
        self._used_campaign_ids = {plan_snapshot.campaign_id}
        self._used_capture_session_ids = {plan_snapshot.capture_session_id}
        self._used_request_ids: set[str] = set()
        self._finalization: PassiveCampaignFinalization | None = None
        self._finalization_in_progress = False
        self._finalization_invalidated = False
        self._retired_sessions: tuple[PassiveCampaignProgress, ...] = ()
        self._pending_request_snapshot: PassiveCaptureRequest | None = None

    def __copy__(self) -> PassiveCampaignSequencer:
        raise TypeError("passive campaign sequencers cannot be copied")

    def __deepcopy__(self, memo: object) -> PassiveCampaignSequencer:
        del memo
        raise TypeError("passive campaign sequencers cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("passive campaign sequencers cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("passive campaign sequencers cannot be pickled")

    @property
    def progress(self) -> PassiveCampaignProgress:
        with self._transition_lock:
            return _snapshot_progress(self._head)

    @property
    def finalization(self) -> PassiveCampaignFinalization | None:
        with self._transition_lock:
            return (
                None if self._finalization is None else _snapshot_finalization(self._finalization)
            )

    @property
    def retired_sessions(self) -> tuple[PassiveCampaignProgress, ...]:
        with self._transition_lock:
            return tuple(_snapshot_progress(item) for item in self._retired_sessions)

    def _stop(
        self, reason: PassiveCampaignFailureReason, evaluated_monotonic_s: float
    ) -> PassiveCampaignProgress:
        with self._transition_lock:
            evaluated = _time(evaluated_monotonic_s, "campaign failure time")
            if self._head.phase in {PassiveCampaignPhase.STOPPED, PassiveCampaignPhase.FINALIZED}:
                return self._head
            if evaluated < self._head.last_event_monotonic_s:
                evaluated = self._head.last_event_monotonic_s
                reason = PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT
            failure = PassiveCampaignFailure(
                reason,
                evaluated,
                (
                    self._active_request_snapshot
                    if self._active_capture_token is not None
                    else self._pending_request_snapshot or self._head.pending_request
                ),
                _factory_token=_FACTORY_TOKEN,
            )
            self._pending_request_snapshot = None
            self._head = PassiveCampaignProgress(
                self._head.plan,
                self._head.source,
                PassiveCampaignPhase.STOPPED,
                self._head.started_monotonic_s,
                evaluated,
                self._head.captures,
                failure=failure,
                _factory_token=_FACTORY_TOKEN,
            )
            return self._head

    def fail(
        self,
        reason: PassiveCampaignFailureReason,
        *,
        evaluated_monotonic_s: float,
    ) -> PassiveCampaignProgress:
        if not isinstance(reason, PassiveCampaignFailureReason):
            raise ValueError("failure reason has the wrong type")
        return _snapshot_progress(self._stop(reason, evaluated_monotonic_s))

    def request_capture(
        self,
        *,
        request_id: str,
        operator_id: str,
        acknowledged_monotonic_s: float,
    ) -> PassiveCampaignProgress:
        with self._transition_lock:
            return _snapshot_progress(
                self._request_capture(
                    request_id=request_id,
                    operator_id=operator_id,
                    acknowledged_monotonic_s=acknowledged_monotonic_s,
                )
            )

    def _request_capture(
        self,
        *,
        request_id: str,
        operator_id: str,
        acknowledged_monotonic_s: float,
    ) -> PassiveCampaignProgress:
        acknowledged = _time(acknowledged_monotonic_s, "acknowledgement time")
        _identifier(request_id, "request_id")
        _identifier(operator_id, "operator_id")
        if self._head.phase in {PassiveCampaignPhase.STOPPED, PassiveCampaignPhase.FINALIZED}:
            return self._head
        if self._head.phase is not PassiveCampaignPhase.READY_FOR_REQUEST:
            return self._stop(
                PassiveCampaignFailureReason.ACKNOWLEDGEMENT_NOT_EXPECTED, acknowledged
            )
        if acknowledged <= self._head.last_event_monotonic_s:
            return self._stop(PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT, acknowledged)
        if operator_id != self._head.plan.operator_id:
            return self._stop(PassiveCampaignFailureReason.OPERATOR_MISMATCH, acknowledged)
        if request_id in self._used_request_ids:
            return self._stop(PassiveCampaignFailureReason.DUPLICATE_CAPTURE, acknowledged)
        spec = self._head.plan.cases[len(self._head.captures)]
        request = PassiveCaptureRequest(
            self._head.plan.campaign_id,
            self._head.plan.content_sha256,
            self._head.plan.capture_session_id,
            request_id,
            spec.ordinal,
            spec.case_id,
            operator_id,
            acknowledged,
            acknowledged + PASSIVE_CAPTURE_REQUEST_TIMEOUT_S,
            _factory_token=_FACTORY_TOKEN,
        )
        request_snapshot = self._snapshot_request(request)
        self._used_request_ids.add(request_id)
        self._pending_request_snapshot = request_snapshot
        self._head = PassiveCampaignProgress(
            self._head.plan,
            self._head.source,
            PassiveCampaignPhase.AWAITING_CAPTURE,
            self._head.started_monotonic_s,
            acknowledged,
            self._head.captures,
            request_snapshot,
            _factory_token=_FACTORY_TOKEN,
        )
        return self._head

    @staticmethod
    def _source_identity(source: object) -> PassiveCaptureSourceIdentity:
        try:
            conforms = isinstance(source, PassiveCaptureSource)
        except Exception as exc:
            raise ValueError("capture source protocol could not be inspected") from exc
        if not conforms:
            raise ValueError("capture source must satisfy PassiveCaptureSource")
        identity = cast(PassiveCaptureSource, source).identity
        if not isinstance(identity, PassiveCaptureSourceIdentity):
            raise ValueError("capture source identity has the wrong type")
        return _snapshot_source_identity(identity)

    @staticmethod
    def _clock_time(clock: object) -> float:
        try:
            conforms = isinstance(clock, PassiveMonotonicClock)
        except Exception as exc:
            raise ValueError("passive monotonic clock protocol could not be inspected") from exc
        if not conforms:
            raise ValueError("passive monotonic clock must satisfy PassiveMonotonicClock")
        typed_clock = cast(PassiveMonotonicClock, clock)
        if (
            typed_clock.live_navigation_enabled is not False
            or typed_clock.input_authority is not False
        ):
            raise ValueError("passive monotonic clock cannot carry navigation or input authority")
        reading = _time(
            typed_clock.now_monotonic_s(),
            "passive monotonic clock time",
        )
        if (
            typed_clock.live_navigation_enabled is not False
            or typed_clock.input_authority is not False
        ):
            raise ValueError("passive monotonic clock changed its fixed-false authority")
        return reading

    @staticmethod
    def _snapshot_request(request: PassiveCaptureRequest) -> PassiveCaptureRequest:
        return _snapshot_request_contract(request)

    @staticmethod
    def _require_detector_matches_source(
        detector: object,
        source: PassiveCaptureSourceIdentity,
    ) -> None:
        try:
            conforms = isinstance(detector, CheckpointDetector)
        except Exception as exc:
            raise ValueError("checkpoint detector protocol could not be inspected") from exc
        if not conforms:
            raise ValueError("checkpoint detector must satisfy CheckpointDetector")
        typed_detector = cast(CheckpointDetector, detector)
        try:
            raw_identity = typed_detector.identity
            raw_profile = typed_detector.profile
        except Exception as exc:
            raise ValueError("checkpoint detector identity or profile could not be read") from exc
        if (
            type(raw_identity) is not CheckpointDetectorIdentity
            or type(raw_profile) is not CheckpointProfile
        ):
            raise ValueError("checkpoint detector identity or profile has the wrong exact type")
        identity = CheckpointDetectorIdentity(raw_identity.detector_id, raw_identity.version)
        profile = CheckpointProfile(
            profile_id=raw_profile.profile_id,
            version=raw_profile.version,
            evidence_role=raw_profile.evidence_role,
            frame_width=raw_profile.frame_width,
            frame_height=raw_profile.frame_height,
            pixel_format=raw_profile.pixel_format,
            checkpoint_ids=tuple(raw_profile.checkpoint_ids),
        )
        if (
            raw_identity.detector_id != identity.detector_id
            or raw_identity.version != identity.version
            or raw_profile.profile_id != profile.profile_id
            or raw_profile.version != profile.version
            or raw_profile.evidence_role is not profile.evidence_role
            or raw_profile.frame_width != profile.frame_width
            or raw_profile.frame_height != profile.frame_height
            or raw_profile.pixel_format is not profile.pixel_format
            or raw_profile.checkpoint_ids != profile.checkpoint_ids
        ):
            raise ValueError("checkpoint detector identity or profile changed while snapshotted")
        if (
            identity != source.checkpoint_source.detector
            or profile != source.checkpoint_source.profile
        ):
            raise ValueError("checkpoint detector differs from the bound passive source")

    def _guard_active_capture(
        self,
        origin_head: PassiveCampaignProgress,
        request_snapshot: PassiveCaptureRequest,
        source_request: PassiveCaptureRequest,
        source: PassiveCaptureSource,
        detector: CheckpointDetector,
        clock: PassiveMonotonicClock,
        event_monotonic_s: float,
    ) -> PassiveCampaignProgress | None:
        with self._transition_lock:
            if self._head is not origin_head:
                return self._head
            if (
                self._source is not source
                or self._detector is not detector
                or self._clock is not clock
            ):
                return self._stop(
                    PassiveCampaignFailureReason.SOURCE_REPLACED,
                    event_monotonic_s,
                )
            if (
                self._head.pending_request != request_snapshot
                or self._pending_request_snapshot != request_snapshot
                or source_request != request_snapshot
            ):
                return self._stop(
                    PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH,
                    event_monotonic_s,
                )
            return None

    def capture(self) -> PassiveCampaignProgress:
        return _snapshot_progress(self._capture())

    def _capture(self) -> PassiveCampaignProgress:
        """Consume the current request through one clocked source invocation."""

        with self._transition_lock:
            if self._head.phase in {
                PassiveCampaignPhase.STOPPED,
                PassiveCampaignPhase.FINALIZED,
            }:
                return self._head
            event_time = self._head.last_event_monotonic_s
            if self._active_capture_token is not None:
                return self._stop(PassiveCampaignFailureReason.CAPTURE_REENTRANCY, event_time)
            if self._head.phase is not PassiveCampaignPhase.AWAITING_CAPTURE:
                return self._stop(PassiveCampaignFailureReason.CAPTURE_NOT_REQUESTED, event_time)
            origin_head = self._head
            request = origin_head.pending_request
            issued_request = self._pending_request_snapshot
            if request is None or issued_request is None:
                return self._stop(
                    PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH,
                    event_time,
                )
            try:
                request_snapshot = self._snapshot_request(issued_request)
                source_request = self._snapshot_request(request_snapshot)
            except Exception:
                return self._stop(
                    PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH,
                    event_time,
                )
            if request != request_snapshot:
                return self._stop(
                    PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH,
                    event_time,
                )
            source = self._source
            detector = self._detector
            clock = self._clock
            source_digest = self._source_digest
            capture_token = object()
            self._active_capture_token = capture_token
            self._active_request_snapshot = request_snapshot
        try:
            pre_capture_time = self._clock_time(clock)
            event_time = pre_capture_time
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            if pre_capture_time < origin_head.last_event_monotonic_s:
                return self._stop(PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT, event_time)
            if pre_capture_time > request_snapshot.expires_monotonic_s:
                return self._stop(PassiveCampaignFailureReason.CAPTURE_TIMEOUT, event_time)

            before = self._source_identity(source)
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            if before != origin_head.source or before.content_sha256 != source_digest:
                return self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, event_time)
            try:
                self._require_detector_matches_source(detector, before)
            except Exception:
                return self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, event_time)
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded

            result = source.capture(source_request)
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            after_source_time = self._clock_time(clock)
            event_time = after_source_time
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            if after_source_time < pre_capture_time:
                return self._stop(PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT, event_time)
            if after_source_time > request_snapshot.expires_monotonic_s:
                return self._stop(PassiveCampaignFailureReason.CAPTURE_TIMEOUT, event_time)

            after_source = self._source_identity(source)
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            if after_source != before or after_source.content_sha256 != source_digest:
                return self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, event_time)
            if type(result) is not PassiveSourceFrame:
                return self._stop(
                    PassiveCampaignFailureReason.CAPTURE_CONTRACT_MISMATCH,
                    event_time,
                )
            result_source = _snapshot_source_identity(result.source)
            if result_source != before or result.request_id != request_snapshot.request_id:
                return self._stop(
                    PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH,
                    event_time,
                )
            frame = result.frame
            result_request_id = result.request_id
            capture_id = result.capture_id
            captured_at_utc = result.captured_at_utc
            frame_ref = FrameRef(
                frame_id=frame.frame_id,
                captured_monotonic_s=frame.captured_monotonic_s,
                width=frame.width,
                height=frame.height,
            )
            frame_payload = frame.payload
            pixel_format = frame.pixel_format
            captured_at = _utc(captured_at_utc, "source frame captured_at_utc")
            previous_captured_at = (
                _utc(
                    origin_head.captures[-1].owned_case.captured_at_utc,
                    "previous source frame captured_at_utc",
                )
                if origin_head.captures
                else _utc(origin_head.plan.created_at_utc, "campaign created_at_utc")
            )
            if captured_at <= previous_captured_at:
                return self._stop(
                    PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH,
                    event_time,
                )
            if frame_ref.captured_monotonic_s <= request_snapshot.acknowledged_monotonic_s:
                return self._stop(PassiveCampaignFailureReason.FRAME_NOT_AFTER_REQUEST, event_time)
            if frame_ref.captured_monotonic_s > after_source_time:
                return self._stop(PassiveCampaignFailureReason.FRAME_FROM_FUTURE, event_time)
            frame_digest = Sha256Digest.from_bytes(frame_payload)
            if any(
                capture.owned_case.capture_id == capture_id
                or capture.owned_case.frame_ref.frame_id == frame_ref.frame_id
                or capture.owned_case.frame_artifact.sha256 == frame_digest
                for capture in origin_head.captures
            ):
                return self._stop(PassiveCampaignFailureReason.DUPLICATE_CAPTURE, event_time)
            if origin_head.captures:
                previous_frame = origin_head.captures[-1].owned_case.frame_ref
                if (
                    frame_ref.frame_id <= previous_frame.frame_id
                    or frame_ref.captured_monotonic_s <= previous_frame.captured_monotonic_s
                ):
                    return self._stop(
                        PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH,
                        event_time,
                    )

            evidence = run_checkpoint_detector(
                detector,
                frame,
                expected_source=before.checkpoint_source,
            )
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            after_detector = self._source_identity(source)
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            try:
                self._require_detector_matches_source(detector, after_detector)
            except Exception:
                return self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, event_time)
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            final_source = self._source_identity(source)
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            if (
                after_detector != before
                or final_source != before
                or after_detector.content_sha256 != source_digest
                or final_source.content_sha256 != source_digest
            ):
                return self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, event_time)
            if (
                _snapshot_source_identity(result.source) != result_source
                or result.request_id != result_request_id
                or result.capture_id != capture_id
                or result.captured_at_utc != captured_at_utc
                or result.frame is not frame
                or frame.ref != frame_ref
                or frame.payload != frame_payload
                or frame.pixel_format is not pixel_format
                or evidence.provenance.frame != frame_ref
                or evidence.provenance.frame_payload_sha256 != frame_digest
                or evidence.provenance.pixel_format is not pixel_format
            ):
                return self._stop(
                    PassiveCampaignFailureReason.FRAME_PROVENANCE_MISMATCH,
                    event_time,
                )

            post_evidence_time = self._clock_time(clock)
            event_time = post_evidence_time
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            if post_evidence_time < after_source_time:
                return self._stop(PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT, event_time)
            if post_evidence_time > request_snapshot.expires_monotonic_s:
                return self._stop(PassiveCampaignFailureReason.CAPTURE_TIMEOUT, event_time)

            post_clock_source = self._source_identity(source)
            try:
                self._require_detector_matches_source(detector, post_clock_source)
            except Exception:
                return self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, event_time)
            final_post_clock_source = self._source_identity(source)
            try:
                self._require_detector_matches_source(detector, final_post_clock_source)
            except Exception:
                return self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, event_time)
            ultimate_post_clock_source = self._source_identity(source)
            if (
                post_clock_source != before
                or final_post_clock_source != before
                or ultimate_post_clock_source != before
                or post_clock_source.content_sha256 != source_digest
                or final_post_clock_source.content_sha256 != source_digest
                or ultimate_post_clock_source.content_sha256 != source_digest
            ):
                return self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, event_time)
            recorded_time = self._clock_time(clock)
            event_time = recorded_time
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            if recorded_time < post_evidence_time:
                return self._stop(PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT, event_time)
            if recorded_time > request_snapshot.expires_monotonic_s:
                return self._stop(PassiveCampaignFailureReason.CAPTURE_TIMEOUT, event_time)

            spec = origin_head.plan.cases[len(origin_head.captures)]
            previous_digest = (
                origin_head.plan.content_sha256
                if not origin_head.captures
                else origin_head.captures[-1].owned_case.content_sha256
            )
            acquisition = RouteEvidenceAcquisitionBinding(
                campaign_plan_sha256=origin_head.plan.content_sha256,
                capture_source_identity_sha256=before.content_sha256,
                capture_session_id=before.checkpoint_source.capture_session_id,
                request_id=request_snapshot.request_id,
                sequence_index=request_snapshot.sequence_index,
                case_id=request_snapshot.case_id,
                capture_id=capture_id,
                operator_id=request_snapshot.operator_id,
                acknowledged_monotonic_s=request_snapshot.acknowledged_monotonic_s,
                expires_monotonic_s=request_snapshot.expires_monotonic_s,
                frame_captured_monotonic_s=frame_ref.captured_monotonic_s,
                recorded_monotonic_s=recorded_time,
                previous_acquisition_sha256=previous_digest,
            )
            report = SyntheticRouteEvidenceDetectorReport(
                campaign_id=origin_head.plan.campaign_id,
                campaign_plan_sha256=origin_head.plan.content_sha256,
                route=origin_head.plan.route,
                route_plan_sha256=origin_head.plan.route_plan_sha256,
                sequence_index=spec.ordinal,
                case_id=spec.case_id,
                capture_id=capture_id,
                acquisition=acquisition,
                detector=before.checkpoint_source.detector,
                profile=before.checkpoint_source.profile.identity,
                capture_source_id=before.checkpoint_source.frame_source_id,
                capture_session_id=before.checkpoint_source.capture_session_id,
                capture_build=before.capture_build,
                capture_configuration_sha256=before.capture_configuration_sha256,
                capture_environment_sha256=before.capture_environment_sha256,
                support_envelope_sha256=before.support_envelope_sha256,
                frame_ref=frame_ref,
                pixel_format=pixel_format,
                frame_sha256=frame_digest,
                detection=evidence.detection,
            )
            report_payload = report.canonical_bytes
            directory = f"cases/{spec.ordinal:03d}-{spec.case_id}"
            owned = OwnedRouteEvidenceCase(
                campaign_id=origin_head.plan.campaign_id,
                campaign_plan_sha256=origin_head.plan.content_sha256,
                route=origin_head.plan.route,
                route_plan_sha256=origin_head.plan.route_plan_sha256,
                sequence_index=spec.ordinal,
                case_id=spec.case_id,
                capture_id=capture_id,
                operator_id=origin_head.plan.operator_id,
                operator_intent=RouteEvidenceOperatorIntent(
                    case_id=spec.case_id,
                    role=spec.role,
                    checkpoint_id=spec.checkpoint_id,
                ),
                acquisition=acquisition,
                detector=before.checkpoint_source.detector,
                profile=before.checkpoint_source.profile.identity,
                capture_source_id=before.checkpoint_source.frame_source_id,
                capture_session_id=before.checkpoint_source.capture_session_id,
                capture_build=before.capture_build,
                capture_configuration_sha256=before.capture_configuration_sha256,
                capture_environment_sha256=before.capture_environment_sha256,
                support_envelope_sha256=before.support_envelope_sha256,
                captured_at_utc=captured_at_utc,
                frame_ref=frame_ref,
                pixel_format=pixel_format,
                frame_artifact=RouteEvidenceArtifactRef(
                    f"{directory}/frame.bin",
                    len(frame_payload),
                    frame_digest,
                ),
                detector_report_artifact=RouteEvidenceArtifactRef(
                    f"{directory}/detector-report.json",
                    len(report_payload),
                    Sha256Digest.from_bytes(report_payload),
                ),
            )
            capture = PassiveOwnedCapture(
                request_snapshot,
                owned,
                frame_payload,
                report_payload,
                recorded_time,
                _factory_token=_FACTORY_TOKEN,
            )
            captures = origin_head.captures + (capture,)
            phase = (
                PassiveCampaignPhase.COMPLETE
                if len(captures) == len(origin_head.plan.cases)
                else PassiveCampaignPhase.READY_FOR_REQUEST
            )
            next_head = PassiveCampaignProgress(
                origin_head.plan,
                origin_head.source,
                phase,
                origin_head.started_monotonic_s,
                recorded_time,
                captures,
                _factory_token=_FACTORY_TOKEN,
            )
            guarded = self._guard_active_capture(
                origin_head,
                request_snapshot,
                source_request,
                source,
                detector,
                clock,
                event_time,
            )
            if guarded is not None:
                return guarded
            with self._transition_lock:
                self._head = next_head
                self._pending_request_snapshot = None
                self._active_capture_token = None
                self._active_request_snapshot = None
                return self._head
        except Exception:
            return self._stop(PassiveCampaignFailureReason.CAPTURE_FAILED, event_time)
        except BaseException:
            self._stop(PassiveCampaignFailureReason.INTERRUPTED, event_time)
            raise
        finally:
            with self._transition_lock:
                if self._active_capture_token is capture_token:
                    self._active_capture_token = None
                    self._active_request_snapshot = None

    def finalize(
        self,
        *,
        finalized_at_utc: str,
    ) -> PassiveCampaignFinalization:
        with self._transition_lock:
            try:
                return _snapshot_finalization(self._finalize(finalized_at_utc=finalized_at_utc))
            except PassiveCampaignFinalizationError:
                raise
            except Exception as exc:
                if self._head.phase not in {
                    PassiveCampaignPhase.STOPPED,
                    PassiveCampaignPhase.FINALIZED,
                }:
                    self._stop(
                        PassiveCampaignFailureReason.FINALIZATION_FAILED,
                        self._head.last_event_monotonic_s,
                    )
                raise PassiveCampaignFinalizationError(
                    "malformed finalization attempt failed closed"
                ) from exc
            except BaseException:
                if self._head.phase not in {
                    PassiveCampaignPhase.STOPPED,
                    PassiveCampaignPhase.FINALIZED,
                }:
                    self._stop(
                        PassiveCampaignFailureReason.INTERRUPTED,
                        self._head.last_event_monotonic_s,
                    )
                raise

    def _finalize(
        self,
        *,
        finalized_at_utc: str,
    ) -> PassiveCampaignFinalization:
        if self._active_capture_token is not None:
            self._stop(
                PassiveCampaignFailureReason.CAPTURE_REENTRANCY,
                self._head.last_event_monotonic_s,
            )
            raise PassiveCampaignFinalizationError(
                "campaign finalization cannot reenter an active capture"
            )
        if self._finalization_in_progress:
            self._finalization_invalidated = True
            self._stop(
                PassiveCampaignFailureReason.FINALIZATION_REENTRANCY,
                self._head.last_event_monotonic_s,
            )
            raise PassiveCampaignFinalizationError(
                "campaign finalization cannot recursively reenter"
            )
        if self._head.phase is not PassiveCampaignPhase.COMPLETE:
            raise PassiveCampaignFinalizationError(
                "only a complete, failure-free campaign can be finalized once"
            )
        origin_head = self._head
        self._finalization_in_progress = True
        self._finalization_invalidated = False
        try:
            return self._finalize_once(
                origin_head,
                finalized_at_utc=finalized_at_utc,
            )
        finally:
            self._finalization_in_progress = False

    def _finalize_once(
        self,
        origin_head: PassiveCampaignProgress,
        *,
        finalized_at_utc: str,
    ) -> PassiveCampaignFinalization:
        source = self._source
        detector = self._detector
        clock = self._clock
        source_digest = self._source_digest
        try:
            source_identity = self._source_identity(source)
            _require_source_matches_plan(origin_head.plan, source_identity)
            self._require_detector_matches_source(detector, source_identity)
            second_source_identity = self._source_identity(source)
            self._require_detector_matches_source(detector, second_source_identity)
            final_source_identity = self._source_identity(source)
            post_provenance_time = self._clock_time(clock)
            post_clock_source_identity = self._source_identity(source)
            self._require_detector_matches_source(detector, post_clock_source_identity)
            final_post_clock_source_identity = self._source_identity(source)
            self._require_detector_matches_source(detector, final_post_clock_source_identity)
            ultimate_source_identity = self._source_identity(source)
            finalized_time = self._clock_time(clock)
        except Exception as exc:
            self._stop(
                PassiveCampaignFailureReason.SOURCE_REPLACED,
                origin_head.last_event_monotonic_s,
            )
            raise PassiveCampaignFinalizationError(
                "bound source, detector, or clock changed before finalization"
            ) from exc
        except BaseException:
            self._stop(
                PassiveCampaignFailureReason.INTERRUPTED,
                origin_head.last_event_monotonic_s,
            )
            raise
        if (
            self._finalization_invalidated
            or self._head is not origin_head
            or self._source is not source
            or self._detector is not detector
            or self._clock is not clock
            or source_identity != origin_head.source
            or second_source_identity != source_identity
            or final_source_identity != source_identity
            or post_clock_source_identity != source_identity
            or final_post_clock_source_identity != source_identity
            or ultimate_source_identity != source_identity
            or source_identity.content_sha256 != source_digest
            or second_source_identity.content_sha256 != source_digest
            or final_source_identity.content_sha256 != source_digest
            or post_clock_source_identity.content_sha256 != source_digest
            or final_post_clock_source_identity.content_sha256 != source_digest
            or ultimate_source_identity.content_sha256 != source_digest
            or finalized_time < post_provenance_time
        ):
            if self._head is origin_head:
                self._stop(PassiveCampaignFailureReason.SOURCE_REPLACED, finalized_time)
            raise PassiveCampaignFinalizationError(
                "campaign changed during finalization provenance checks"
            )
        finalized_utc = _utc(finalized_at_utc, "finalization UTC time")
        if finalized_time <= origin_head.last_event_monotonic_s:
            self._stop(PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT, finalized_time)
            raise PassiveCampaignFinalizationError("finalization must follow the last capture")
        if finalized_utc <= _utc(
            origin_head.captures[-1].owned_case.captured_at_utc,
            "last source frame captured_at_utc",
        ):
            self._stop(PassiveCampaignFailureReason.OUT_OF_ORDER_EVENT, finalized_time)
            raise PassiveCampaignFinalizationError(
                "finalization UTC time must follow the last source capture"
            )
        try:
            sealed_origin = _snapshot_progress(origin_head)
            package = FinalizedRouteEvidencePackage(
                campaign_plan=sealed_origin.plan,
                cases=tuple(capture.owned_case for capture in sealed_origin.captures),
                finalized_at_utc=finalized_at_utc,
                finalized_monotonic_s=finalized_time,
            )
            next_head = PassiveCampaignProgress(
                sealed_origin.plan,
                sealed_origin.source,
                PassiveCampaignPhase.FINALIZED,
                sealed_origin.started_monotonic_s,
                finalized_time,
                sealed_origin.captures,
                _factory_token=_FACTORY_TOKEN,
            )
            payloads = tuple(
                pair
                for capture in next_head.captures
                for pair in (
                    (capture.owned_case.frame_artifact.relative_path, capture.frame_payload),
                    (
                        capture.owned_case.detector_report_artifact.relative_path,
                        capture.detector_report_payload,
                    ),
                )
            )
            finalization = PassiveCampaignFinalization(
                next_head,
                package,
                payloads,
                _factory_token=_FACTORY_TOKEN,
            )
        except Exception as exc:
            self._stop(PassiveCampaignFailureReason.CAPTURE_FAILED, finalized_time)
            raise PassiveCampaignFinalizationError(
                "finalized package construction failed closed"
            ) from exc
        except BaseException:
            self._stop(PassiveCampaignFailureReason.INTERRUPTED, finalized_time)
            raise
        if (
            self._finalization_invalidated
            or self._head is not origin_head
            or self._source is not source
            or self._detector is not detector
            or self._clock is not clock
        ):
            if self._head is origin_head:
                self._stop(
                    PassiveCampaignFailureReason.FINALIZATION_REENTRANCY,
                    finalized_time,
                )
            raise PassiveCampaignFinalizationError(
                "campaign changed before finalization could commit"
            )
        self._head = next_head
        self._finalization = finalization
        return self._finalization

    def restart(
        self,
        replacement_plan: RouteEvidenceCampaignPlan,
        replacement_source: PassiveCaptureSource,
        replacement_detector: CheckpointDetector,
        replacement_clock: PassiveMonotonicClock,
        *,
        started_monotonic_s: float,
    ) -> PassiveCampaignProgress:
        """Start an explicit fresh same-direction session after a latched stop."""

        with self._transition_lock:
            return _snapshot_progress(
                self._restart(
                    replacement_plan,
                    replacement_source,
                    replacement_detector,
                    replacement_clock,
                    started_monotonic_s=started_monotonic_s,
                )
            )

    def _restart(
        self,
        replacement_plan: RouteEvidenceCampaignPlan,
        replacement_source: PassiveCaptureSource,
        replacement_detector: CheckpointDetector,
        replacement_clock: PassiveMonotonicClock,
        *,
        started_monotonic_s: float,
    ) -> PassiveCampaignProgress:
        if self._active_capture_token is not None:
            raise ValueError("campaign recovery cannot replace an active capture")
        if self._restart_in_progress:
            self._restart_invalidated = True
            raise ValueError("campaign recovery cannot reenter an active restart")

        origin_head = self._head
        self._restart_in_progress = True
        self._restart_invalidated = False
        try:
            started = _time(started_monotonic_s, "replacement campaign start time")
            replacement_snapshot = _snapshot_campaign_plan(replacement_plan)
            if origin_head.phase is not PassiveCampaignPhase.STOPPED:
                raise ValueError("only a stopped campaign can be explicitly restarted")
            if started <= origin_head.last_event_monotonic_s:
                raise ValueError("replacement campaign must start after the stopped session")
            if replacement_snapshot.route.direction is not origin_head.plan.route.direction:
                raise ValueError("campaign recovery cannot silently reverse direction")
            if replacement_snapshot.campaign_id in self._used_campaign_ids:
                raise ValueError("campaign recovery cannot reuse any campaign id")
            if replacement_snapshot.capture_session_id in self._used_capture_session_ids:
                raise ValueError("campaign recovery requires a never-used capture session")
            replacement_identity = self._source_identity(replacement_source)
            _require_source_matches_plan(replacement_snapshot, replacement_identity)
            self._require_detector_matches_source(replacement_detector, replacement_identity)
            replacement_clock_time = self._clock_time(replacement_clock)
            if replacement_clock_time < started:
                raise ValueError("replacement campaign clock cannot precede its start")
            post_clock_identity = self._source_identity(replacement_source)
            self._require_detector_matches_source(replacement_detector, post_clock_identity)
            final_identity = self._source_identity(replacement_source)
            self._require_detector_matches_source(replacement_detector, final_identity)
            ultimate_identity = self._source_identity(replacement_source)
            post_callback_snapshot = _snapshot_campaign_plan(replacement_plan)
            if (
                self._restart_invalidated
                or self._head is not origin_head
                or post_callback_snapshot.content_sha256 != replacement_snapshot.content_sha256
                or post_clock_identity != replacement_identity
                or final_identity != replacement_identity
                or ultimate_identity != replacement_identity
                or post_clock_identity.content_sha256 != replacement_identity.content_sha256
                or final_identity.content_sha256 != replacement_identity.content_sha256
                or ultimate_identity.content_sha256 != replacement_identity.content_sha256
            ):
                raise ValueError("campaign recovery was invalidated during replacement binding")
            next_head = PassiveCampaignProgress(
                replacement_snapshot,
                replacement_identity,
                PassiveCampaignPhase.READY_FOR_REQUEST,
                started,
                started,
                _factory_token=_FACTORY_TOKEN,
            )
            retired_sessions = self._retired_sessions + (_snapshot_progress(origin_head),)
            self._used_campaign_ids.add(replacement_snapshot.campaign_id)
            self._used_capture_session_ids.add(replacement_snapshot.capture_session_id)
            self._source = replacement_source
            self._detector = replacement_detector
            self._clock = replacement_clock
            self._source_digest = replacement_identity.content_sha256
            self._finalization = None
            self._pending_request_snapshot = None
            self._active_request_snapshot = None
            self._retired_sessions = retired_sessions
            self._head = next_head
            return self._head
        finally:
            self._restart_in_progress = False
