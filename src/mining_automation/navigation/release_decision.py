"""Deny-only production-binding readiness for the two fixed-route directions.

The evaluator performs read-only intake of exact synthetic durable evidence and
one source-issued offline causal result per direction.  It records which
contracts are bound without converting architecture evidence into real-route,
endpoint, activation, or input authority.
"""

from __future__ import annotations

import os
from dataclasses import InitVar, dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
from itertools import combinations
from math import copysign, isfinite
from pathlib import Path
from typing import Any, Final, Literal, cast

from .contracts import (
    AttemptEvidenceRole,
    CheckpointEvidenceRole,
    CheckpointMatchKind,
    CheckpointObservation,
    NavigationPolicy,
    RouteDirection,
    RouteEndpoint,
    RouteIdentity,
    RoutePlan,
    Sha256Digest,
    StepAttemptSourceIdentity,
)
from .durable_route_evidence import (
    DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE,
    DURABLE_WRITER_NAMESPACE_CONTRACT,
    DurableRouteEvidenceFilesystemExpectation,
    _load_and_verify_durable_synthetic_route_evidence_detailed,
    _snapshot_full_expectation,
    _VerifiedDurableRouteEvidence,
)
from .offline_route_session import (
    OfflineRouteSessionPhase,
    OfflineRouteSessionResult,
    _snapshot_result,
)
from .route_evidence import (
    SYNTHETIC_ROUTE_EVIDENCE_ROLE,
    RouteEvidenceIntegrityError,
    RouteEvidenceReview,
    RouteEvidenceReviewDecision,
    _snapshot_route_evidence_contract,
    canonical_route_evidence_bytes,
    digest_route_plan,
)

__all__ = [
    "NAVIGATION_PRODUCTION_BINDING_ROLE",
    "REQUIRED_NAVIGATION_HOST_THREAT_MODEL",
    "REQUIRED_NAVIGATION_TARGET_PLATFORM",
    "DirectionProductionBinding",
    "DirectionReleaseCheck",
    "DirectionReleaseRequirement",
    "DownstreamEvidenceRequirement",
    "DurableRootBinding",
    "DurableRootRole",
    "EndpointReleaseBinding",
    "NavigationReleaseDecision",
    "PairReleaseCheck",
    "PairReleaseRequirement",
    "PostAttemptCausalityExpectation",
    "PostAttemptCausalityBinding",
    "ReleaseCheckStatus",
    "ReviewerCaseDecisionBinding",
    "ReviewerDecisionSummary",
    "StepCausalityBinding",
    "evaluate_navigation_release_readiness",
]

NAVIGATION_PRODUCTION_BINDING_ROLE: Final[str] = (
    "synthetic_navigation_production_binding_readiness_only"
)
REQUIRED_NAVIGATION_TARGET_PLATFORM: Final[Literal["win32"]] = "win32"
REQUIRED_NAVIGATION_HOST_THREAT_MODEL: Final[str] = "trusted_single_windows_user_host_v1"

_DECISION_SCHEMA: Final[str] = "fixed-route-navigation-release-decision-v1"
_DIRECTION_SCHEMA: Final[str] = "fixed-route-direction-production-binding-v1"
_ROOT_SCHEMA: Final[str] = "fixed-route-durable-root-binding-v1"
_CAUSALITY_SCHEMA: Final[str] = "fixed-route-post-attempt-causality-binding-v1"
_STEP_CAUSALITY_SCHEMA: Final[str] = "fixed-route-step-causality-binding-v1"
_ENDPOINT_SCHEMA: Final[str] = "fixed-route-endpoint-release-binding-v1"
_CHECK_SCHEMA: Final[str] = "fixed-route-release-check-v1"
_FACTORY_TOKEN: Final[object] = object()


def _is_nonnegative_time(value: object) -> bool:
    return bool(
        type(value) is float
        and isfinite(value)
        and value >= 0.0
        and not (value == 0.0 and copysign(1.0, value) < 0.0)
    )


def _is_confidence(value: object) -> bool:
    return bool(type(value) is float and isfinite(value) and 0.0 <= value <= 1.0)


class ReleaseCheckStatus(StrEnum):
    """A binding may be recorded offline; release requirements stay unsatisfied."""

    BOUND_OFFLINE = "bound_offline"
    NOT_SATISFIED = "not_satisfied"


class DirectionReleaseRequirement(StrEnum):
    ROUTE_IDENTITY_VERSION = "route_identity_version"
    ORDERED_ROUTE_PLAN = "ordered_route_plan"
    DETECTOR_PROFILE = "detector_profile"
    FRAME_CONTRACT = "frame_contract"
    CAPTURE_BUILD_CONFIGURATION_ENVIRONMENT = "capture_build_configuration_environment"
    CAPTURE_SOURCE_SESSION = "capture_source_session"
    DURABLE_ACQUISITION_LINEAGE = "durable_acquisition_lineage"
    DURABLE_REVIEW_LINEAGE = "durable_review_lineage"
    REVIEWER_DECISION_BOUND = "reviewer_decision_bound"
    SYNTHETIC_EVIDENCE_CONFORMANCE = "synthetic_evidence_conformance"
    SYNTHETIC_ROUTE_ARRIVAL = "synthetic_route_arrival"
    OFFLINE_NAVIGATION_POLICY = "offline_navigation_policy"
    PRODUCTION_NAVIGATION_POLICY_ATTESTATION = "production_navigation_policy_attestation"
    OFFLINE_POST_ATTEMPT_CAUSALITY = "offline_post_attempt_causality"
    REAL_POST_ATTEMPT_CAUSALITY = "real_post_attempt_causality"
    REAL_ROUTE_EVIDENCE = "real_route_evidence"
    DOWNSTREAM_ENDPOINT_EVIDENCE = "downstream_endpoint_evidence"


class PairReleaseRequirement(StrEnum):
    CANONICAL_DIRECTION_SLOTS = "canonical_direction_slots"
    INDEPENDENT_DURABLE_DIRECTION_LINEAGES = "independent_durable_direction_lineages"
    PHYSICAL_ROOT_ISOLATION = "physical_root_isolation"
    ENDPOINT_CONTRACT_COHERENCE = "endpoint_contract_coherence"
    REQUIRED_TARGET_PLATFORM = "required_target_platform"
    REQUIRED_NAMESPACE_CONTRACT = "required_namespace_contract"
    HOST_NAMESPACE_ATTESTATION = "host_namespace_attestation"
    WRITER_FUTURE_REAL_ELIGIBILITY = "writer_future_real_eligibility"
    FINAL_RELEASE_DECISION = "final_release_decision"


class DurableRootRole(StrEnum):
    ACQUISITION = "acquisition"
    REVIEW = "review"


class ReviewerDecisionSummary(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class DownstreamEvidenceRequirement(StrEnum):
    FRESH_BANK_INTERFACE_OPEN = "fresh_bank_interface_open"
    FRESH_SUPPORTED_MINING_VIEW = "fresh_supported_mining_view"


@dataclass(frozen=True, slots=True)
class DirectionReleaseCheck:
    direction: RouteDirection
    requirement: DirectionReleaseRequirement
    status: ReleaseCheckStatus
    _factory_token: InitVar[object | None] = None
    release_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("direction release checks are evaluator-owned")
        if type(self.direction) is not RouteDirection:
            raise RouteEvidenceIntegrityError("direction release check has an invalid direction")
        if type(self.requirement) is not DirectionReleaseRequirement:
            raise RouteEvidenceIntegrityError("direction release requirement is invalid")
        if type(self.status) is not ReleaseCheckStatus:
            raise RouteEvidenceIntegrityError("direction release status is invalid")
        if self.release_authority is not False:
            raise RouteEvidenceIntegrityError("direction release checks cannot carry authority")

    @property
    def report_local_check_id(self) -> str:
        return f"{self.direction.value}:{self.requirement.value}"

    def to_json_value(self) -> dict[str, object]:
        return {
            "direction": self.direction.value,
            "id_scope": "report_local",
            "release_authority": self.release_authority,
            "report_local_check_id": self.report_local_check_id,
            "requirement": self.requirement.value,
            "schema": _CHECK_SCHEMA,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class PairReleaseCheck:
    requirement: PairReleaseRequirement
    status: ReleaseCheckStatus
    _factory_token: InitVar[object | None] = None
    release_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("pair release checks are evaluator-owned")
        if type(self.requirement) is not PairReleaseRequirement:
            raise RouteEvidenceIntegrityError("pair release requirement is invalid")
        if type(self.status) is not ReleaseCheckStatus:
            raise RouteEvidenceIntegrityError("pair release status is invalid")
        if self.release_authority is not False:
            raise RouteEvidenceIntegrityError("pair release checks cannot carry authority")

    @property
    def report_local_check_id(self) -> str:
        return f"pair:{self.requirement.value}"

    def to_json_value(self) -> dict[str, object]:
        return {
            "id_scope": "report_local",
            "release_authority": self.release_authority,
            "report_local_check_id": self.report_local_check_id,
            "requirement": self.requirement.value,
            "schema": _CHECK_SCHEMA,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class DurableRootBinding:
    direction: RouteDirection
    role: DurableRootRole
    storage_path: str
    physical_root_identity: tuple[int, ...]
    stable_tree_identity_sha256: Sha256Digest
    _factory_token: InitVar[object | None] = None
    path_is_evidence_identity: Literal[False] = field(default=False, init=False)
    release_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("durable root bindings are evaluator-owned")
        if type(self.direction) is not RouteDirection or type(self.role) is not DurableRootRole:
            raise RouteEvidenceIntegrityError("durable root binding identity is invalid")
        if type(self.storage_path) is not str or not self.storage_path:
            raise RouteEvidenceIntegrityError("durable root binding path is invalid")
        if (
            type(self.physical_root_identity) is not tuple
            or not self.physical_root_identity
            or any(type(value) is not int for value in self.physical_root_identity)
        ):
            raise RouteEvidenceIntegrityError("durable root physical identity is invalid")
        if type(self.stable_tree_identity_sha256) is not Sha256Digest:
            raise RouteEvidenceIntegrityError("durable root tree digest is invalid")
        if self.path_is_evidence_identity is not False or self.release_authority is not False:
            raise RouteEvidenceIntegrityError("durable root paths cannot carry authority")

    def to_json_value(self) -> dict[str, object]:
        return {
            "direction": self.direction.value,
            "path_is_evidence_identity": self.path_is_evidence_identity,
            "physical_root_identity": list(self.physical_root_identity),
            "release_authority": self.release_authority,
            "role": self.role.value,
            "schema": _ROOT_SCHEMA,
            "stable_tree_identity_sha256": self.stable_tree_identity_sha256.value,
            "storage_path": self.storage_path,
        }


@dataclass(frozen=True, slots=True)
class ReviewerCaseDecisionBinding:
    case_id: str
    decision: RouteEvidenceReviewDecision
    match: CheckpointMatchKind
    candidate_checkpoint_ids: tuple[str, ...]
    confidence: float
    frame_sha256: Sha256Digest
    detector_report_sha256: Sha256Digest
    _factory_token: InitVar[object | None] = None
    release_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("reviewer decision bindings are evaluator-owned")
        if type(self.case_id) is not str or not self.case_id:
            raise RouteEvidenceIntegrityError("reviewer decision case id is invalid")
        if type(self.decision) is not RouteEvidenceReviewDecision:
            raise RouteEvidenceIntegrityError("reviewer decision is invalid")
        if type(self.match) is not CheckpointMatchKind:
            raise RouteEvidenceIntegrityError("reviewed checkpoint match is invalid")
        if (
            type(self.candidate_checkpoint_ids) is not tuple
            or any(
                type(checkpoint_id) is not str or not checkpoint_id
                for checkpoint_id in self.candidate_checkpoint_ids
            )
            or len(set(self.candidate_checkpoint_ids)) != len(self.candidate_checkpoint_ids)
        ):
            raise RouteEvidenceIntegrityError("reviewed candidates must be an exact tuple")
        candidate_count = len(self.candidate_checkpoint_ids)
        if (
            (self.match is CheckpointMatchKind.UNKNOWN and candidate_count != 0)
            or (self.match is CheckpointMatchKind.MATCHED and candidate_count != 1)
            or (self.match is CheckpointMatchKind.AMBIGUOUS and candidate_count < 2)
        ):
            raise RouteEvidenceIntegrityError("reviewed candidates differ from match kind")
        if not _is_confidence(self.confidence):
            raise RouteEvidenceIntegrityError("reviewed confidence is invalid")
        if (
            type(self.frame_sha256) is not Sha256Digest
            or type(self.detector_report_sha256) is not Sha256Digest
        ):
            raise RouteEvidenceIntegrityError("reviewed artifact digests are invalid")
        if self.release_authority is not False:
            raise RouteEvidenceIntegrityError("reviewer decisions cannot carry release authority")

    def to_json_value(self) -> dict[str, object]:
        return {
            "candidate_checkpoint_ids": list(self.candidate_checkpoint_ids),
            "case_id": self.case_id,
            "confidence": self.confidence,
            "decision": self.decision.value,
            "detector_report_sha256": self.detector_report_sha256.value,
            "frame_sha256": self.frame_sha256.value,
            "match": self.match.value,
            "release_authority": self.release_authority,
        }


@dataclass(frozen=True, slots=True)
class PostAttemptCausalityExpectation:
    """Caller-owned pins for one exact synthetic offline causal session."""

    route: RouteIdentity
    route_plan_sha256: Sha256Digest
    route_session_id: str
    attempt_source: StepAttemptSourceIdentity
    policy: NavigationPolicy
    evidence_role: Literal["synthetic_route_evidence_architecture_test_only"] = field(
        default=SYNTHETIC_ROUTE_EVIDENCE_ROLE,
        init=False,
    )
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.route) is not RouteIdentity:
            raise ValueError("post-attempt expectation route is invalid")
        if type(self.route_plan_sha256) is not Sha256Digest:
            raise ValueError("post-attempt expectation route digest is invalid")
        if type(self.route_session_id) is not str or not self.route_session_id:
            raise ValueError("post-attempt expectation route session is invalid")
        if type(self.attempt_source) is not StepAttemptSourceIdentity:
            raise ValueError("post-attempt expectation attempt source is invalid")
        if (
            self.attempt_source.evidence_role
            is not AttemptEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY
        ):
            raise ValueError("post-attempt expectation must remain synthetic")
        if type(self.policy) is not NavigationPolicy:
            raise ValueError("post-attempt expectation policy is invalid")
        if (
            self.evidence_role != SYNTHETIC_ROUTE_EVIDENCE_ROLE
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise ValueError("post-attempt expectation carries mutated authority fields")

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "attempt_source": {
                "evidence_role": self.attempt_source.evidence_role.value,
                "session_id": self.attempt_source.session_id,
                "source_id": self.attempt_source.source_id,
                "version": self.attempt_source.version,
            },
            "evidence_role": self.evidence_role,
            "input_authority": self.input_authority,
            "navigation_policy": {
                "max_attempt_receipt_age_s": self.policy.max_attempt_receipt_age_s,
                "max_frame_age_s": self.policy.max_frame_age_s,
                "minimum_confidence": self.policy.minimum_confidence,
                "support_attested": False,
            },
            "route": _route_json(self.route),
            "route_plan_sha256": self.route_plan_sha256.value,
            "route_session_id": self.route_session_id,
        }


@dataclass(frozen=True, slots=True)
class StepCausalityBinding:
    step_index: int
    step_id: str
    from_checkpoint_id: str
    to_checkpoint_id: str
    attempt_id: str
    attempt_source_id: str
    attempt_source_version: str
    attempt_source_session_id: str
    departure_frame_id: int
    departure_captured_monotonic_s: float
    departure_frame_payload_sha256: Sha256Digest
    departure_confidence: float
    prepared_monotonic_s: float
    post_attempt_monotonic_s: float
    receipt_recorded_monotonic_s: float
    next_checkpoint_id: str | None
    next_frame_id: int | None
    next_captured_monotonic_s: float | None
    next_frame_payload_sha256: Sha256Digest | None
    next_confidence: float | None
    post_attempt_checkpoint_bound: bool
    _factory_token: InitVar[object | None] = None
    attempt_authoritative: Literal[False] = field(default=False, init=False)
    movement_success_proven_by_receipt: Literal[False] = field(default=False, init=False)
    live_input_enabled: Literal[False] = field(default=False, init=False)
    release_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("step causality bindings are evaluator-owned")
        if type(self.step_index) is not int or self.step_index < 0:
            raise RouteEvidenceIntegrityError("step causality index is invalid")
        for value in (
            self.step_id,
            self.from_checkpoint_id,
            self.to_checkpoint_id,
            self.attempt_id,
            self.attempt_source_id,
            self.attempt_source_version,
            self.attempt_source_session_id,
        ):
            if type(value) is not str or not value:
                raise RouteEvidenceIntegrityError("step causality identity is invalid")
        if type(self.departure_frame_payload_sha256) is not Sha256Digest:
            raise RouteEvidenceIntegrityError("departure frame digest is invalid")
        if type(self.departure_frame_id) is not int or self.departure_frame_id < 1:
            raise RouteEvidenceIntegrityError("departure frame id is invalid")
        for time_value, label in (
            (self.departure_captured_monotonic_s, "departure frame time"),
            (self.prepared_monotonic_s, "preparation time"),
            (self.post_attempt_monotonic_s, "post-attempt time"),
            (self.receipt_recorded_monotonic_s, "receipt-recorded time"),
        ):
            if not _is_nonnegative_time(time_value):
                raise RouteEvidenceIntegrityError(f"step causality {label} is invalid")
        if not _is_confidence(self.departure_confidence):
            raise RouteEvidenceIntegrityError("departure confidence is invalid")
        if (
            self.prepared_monotonic_s < self.departure_captured_monotonic_s
            or self.post_attempt_monotonic_s <= self.prepared_monotonic_s
            or self.receipt_recorded_monotonic_s < self.post_attempt_monotonic_s
        ):
            raise RouteEvidenceIntegrityError("step receipt chronology is invalid")
        optional_values = (
            self.next_checkpoint_id,
            self.next_frame_id,
            self.next_captured_monotonic_s,
            self.next_frame_payload_sha256,
            self.next_confidence,
        )
        if type(self.post_attempt_checkpoint_bound) is not bool:
            raise RouteEvidenceIntegrityError(
                "post-attempt checkpoint summary must be an exact boolean"
            )
        if self.post_attempt_checkpoint_bound:
            if any(value is None for value in optional_values):
                raise RouteEvidenceIntegrityError("bound step causality lacks next evidence")
            if (
                self.next_checkpoint_id != self.to_checkpoint_id
                or type(self.next_frame_id) is not int
                or self.next_frame_id <= self.departure_frame_id
                or not _is_nonnegative_time(self.next_captured_monotonic_s)
                or cast(float, self.next_captured_monotonic_s) <= self.post_attempt_monotonic_s
                or type(self.next_frame_payload_sha256) is not Sha256Digest
                or not _is_confidence(self.next_confidence)
            ):
                raise RouteEvidenceIntegrityError("bound step next-checkpoint evidence is invalid")
        elif any(value is not None for value in optional_values):
            raise RouteEvidenceIntegrityError("unbound step causality carries partial evidence")
        if (
            self.attempt_authoritative is not False
            or self.movement_success_proven_by_receipt is not False
            or self.live_input_enabled is not False
            or self.release_authority is not False
        ):
            raise RouteEvidenceIntegrityError("synthetic step causality cannot carry authority")

    def to_json_value(self) -> dict[str, object]:
        return {
            "attempt_authoritative": self.attempt_authoritative,
            "attempt_id": self.attempt_id,
            "attempt_source": {
                "session_id": self.attempt_source_session_id,
                "source_id": self.attempt_source_id,
                "version": self.attempt_source_version,
            },
            "departure_evidence": {
                "captured_monotonic_s": self.departure_captured_monotonic_s,
                "confidence": self.departure_confidence,
                "frame_id": self.departure_frame_id,
                "frame_payload_sha256": self.departure_frame_payload_sha256.value,
            },
            "from_checkpoint_id": self.from_checkpoint_id,
            "live_input_enabled": self.live_input_enabled,
            "movement_success_proven_by_receipt": self.movement_success_proven_by_receipt,
            "next_checkpoint_evidence": (
                None
                if not self.post_attempt_checkpoint_bound
                else {
                    "captured_monotonic_s": self.next_captured_monotonic_s,
                    "checkpoint_id": self.next_checkpoint_id,
                    "confidence": self.next_confidence,
                    "frame_id": self.next_frame_id,
                    "frame_payload_sha256": cast(
                        Sha256Digest, self.next_frame_payload_sha256
                    ).value,
                }
            ),
            "post_attempt_checkpoint_bound": self.post_attempt_checkpoint_bound,
            "receipt": {
                "post_attempt_monotonic_s": self.post_attempt_monotonic_s,
                "prepared_monotonic_s": self.prepared_monotonic_s,
                "recorded_monotonic_s": self.receipt_recorded_monotonic_s,
            },
            "release_authority": self.release_authority,
            "schema": _STEP_CAUSALITY_SCHEMA,
            "step_id": self.step_id,
            "step_index": self.step_index,
            "to_checkpoint_id": self.to_checkpoint_id,
        }


@dataclass(frozen=True, slots=True)
class PostAttemptCausalityBinding:
    direction: RouteDirection
    route: RouteIdentity
    route_plan_sha256: Sha256Digest
    route_session_id: str
    attempt_source_id: str
    attempt_source_version: str
    attempt_source_session_id: str
    max_frame_age_s: float
    minimum_confidence: float
    max_attempt_receipt_age_s: float
    expected_step_ids: tuple[str, ...]
    steps: tuple[StepCausalityBinding, ...]
    terminal_phase: OfflineRouteSessionPhase
    failure_reason: str | None
    explicit_terminal_arrival_bound: bool
    synthetic_causality_conforms: bool
    session_result_sha256: Sha256Digest
    _factory_token: InitVar[object | None] = None
    real_post_attempt_causality_satisfied: Literal[False] = field(default=False, init=False)
    automatic_retry_enabled: Literal[False] = field(default=False, init=False)
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    release_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("post-attempt bindings are evaluator-owned")
        if type(self.direction) is not RouteDirection:
            raise RouteEvidenceIntegrityError("post-attempt direction is invalid")
        if type(self.route) is not RouteIdentity or self.route.direction is not self.direction:
            raise RouteEvidenceIntegrityError("post-attempt route binding is invalid")
        if type(self.route_plan_sha256) is not Sha256Digest:
            raise RouteEvidenceIntegrityError("post-attempt route digest is invalid")
        for value in (
            self.route_session_id,
            self.attempt_source_id,
            self.attempt_source_version,
            self.attempt_source_session_id,
        ):
            if type(value) is not str or not value:
                raise RouteEvidenceIntegrityError("post-attempt source identity is invalid")
        if type(self.expected_step_ids) is not tuple or type(self.steps) is not tuple:
            raise RouteEvidenceIntegrityError("post-attempt step sequences must be tuples")
        if (
            not self.expected_step_ids
            or any(type(item) is not str or not item for item in self.expected_step_ids)
            or len(set(self.expected_step_ids)) != len(self.expected_step_ids)
        ):
            raise RouteEvidenceIntegrityError("post-attempt expected step ids are invalid")
        if any(type(item) is not StepCausalityBinding for item in self.steps):
            raise RouteEvidenceIntegrityError("post-attempt step binding is invalid")
        if tuple(item.step_id for item in self.steps) != self.expected_step_ids[: len(self.steps)]:
            raise RouteEvidenceIntegrityError("post-attempt steps are not an exact route prefix")
        if len({item.attempt_id for item in self.steps}) != len(self.steps):
            raise RouteEvidenceIntegrityError("post-attempt attempt ids must be unique")
        try:
            policy = NavigationPolicy(
                self.max_frame_age_s,
                self.minimum_confidence,
                self.max_attempt_receipt_age_s,
            )
        except ValueError as exc:
            raise RouteEvidenceIntegrityError("post-attempt policy is invalid") from exc
        for index, item in enumerate(self.steps):
            if (
                item.step_index != index
                or item.attempt_source_id != self.attempt_source_id
                or item.attempt_source_version != self.attempt_source_version
                or item.attempt_source_session_id != self.attempt_source_session_id
                or item.departure_confidence < policy.minimum_confidence
                or item.prepared_monotonic_s - item.departure_captured_monotonic_s
                > policy.max_frame_age_s
                or item.receipt_recorded_monotonic_s - item.post_attempt_monotonic_s
                > policy.max_attempt_receipt_age_s
                or (
                    item.next_confidence is not None
                    and item.next_confidence < policy.minimum_confidence
                )
            ):
                raise RouteEvidenceIntegrityError("post-attempt step differs from source or policy")
            if index + 1 < len(self.steps):
                following = self.steps[index + 1]
                if (
                    not item.post_attempt_checkpoint_bound
                    or item.next_checkpoint_id != following.from_checkpoint_id
                    or item.next_frame_id != following.departure_frame_id
                    or item.next_captured_monotonic_s != following.departure_captured_monotonic_s
                    or item.next_frame_payload_sha256 != following.departure_frame_payload_sha256
                    or item.next_confidence != following.departure_confidence
                    or following.prepared_monotonic_s < item.receipt_recorded_monotonic_s
                ):
                    raise RouteEvidenceIntegrityError(
                        "adjacent post-attempt checkpoint evidence is disconnected"
                    )
        if type(self.terminal_phase) is not OfflineRouteSessionPhase:
            raise RouteEvidenceIntegrityError("post-attempt terminal phase is invalid")
        if self.failure_reason is not None and (
            type(self.failure_reason) is not str or not self.failure_reason
        ):
            raise RouteEvidenceIntegrityError("post-attempt failure reason is invalid")
        if (
            type(self.explicit_terminal_arrival_bound) is not bool
            or type(self.synthetic_causality_conforms) is not bool
        ):
            raise RouteEvidenceIntegrityError("post-attempt summaries must be exact booleans")
        if type(self.session_result_sha256) is not Sha256Digest:
            raise RouteEvidenceIntegrityError("post-attempt session result digest is invalid")
        if self.synthetic_causality_conforms is not bool(
            self.terminal_phase is OfflineRouteSessionPhase.ARRIVED
            and self.failure_reason is None
            and self.explicit_terminal_arrival_bound
            and len(self.steps) == len(self.expected_step_ids)
            and all(item.post_attempt_checkpoint_bound for item in self.steps)
        ):
            raise RouteEvidenceIntegrityError("post-attempt conformance summary is inconsistent")
        if (
            self.real_post_attempt_causality_satisfied is not False
            or self.automatic_retry_enabled is not False
            or self.live_navigation_enabled is not False
            or self.input_authority is not False
            or self.release_authority is not False
        ):
            raise RouteEvidenceIntegrityError("offline causality cannot carry authority")

    def to_json_value(self) -> dict[str, object]:
        return {
            "attempt_source": {
                "session_id": self.attempt_source_session_id,
                "source_id": self.attempt_source_id,
                "version": self.attempt_source_version,
            },
            "automatic_retry_enabled": self.automatic_retry_enabled,
            "direction": self.direction.value,
            "expected_step_ids": list(self.expected_step_ids),
            "explicit_terminal_arrival_bound": self.explicit_terminal_arrival_bound,
            "failure_reason": self.failure_reason,
            "input_authority": self.input_authority,
            "live_navigation_enabled": self.live_navigation_enabled,
            "navigation_policy": {
                "max_attempt_receipt_age_s": self.max_attempt_receipt_age_s,
                "max_frame_age_s": self.max_frame_age_s,
                "minimum_confidence": self.minimum_confidence,
                "support_attested": False,
            },
            "real_post_attempt_causality_satisfied": (self.real_post_attempt_causality_satisfied),
            "release_authority": self.release_authority,
            "route": _route_json(self.route),
            "route_plan_sha256": self.route_plan_sha256.value,
            "route_session_id": self.route_session_id,
            "schema": _CAUSALITY_SCHEMA,
            "session_result_sha256": self.session_result_sha256.value,
            "steps": [item.to_json_value() for item in self.steps],
            "synthetic_causality_conforms": self.synthetic_causality_conforms,
            "terminal_phase": self.terminal_phase.value,
        }


@dataclass(frozen=True, slots=True)
class EndpointReleaseBinding:
    direction: RouteDirection
    terminal: RouteEndpoint
    arrival_checkpoint_id: str
    finalized_package_sha256: Sha256Digest
    reviewer_truth_sha256: Sha256Digest
    synthetic_route_arrival_verified: bool
    required_downstream_evidence: DownstreamEvidenceRequirement
    _factory_token: InitVar[object | None] = None
    fresh_downstream_evidence_present: Literal[False] = field(default=False, init=False)
    bank_interface_open_proven: Literal[False] = field(default=False, init=False)
    supported_mining_view_proven: Literal[False] = field(default=False, init=False)
    downstream_handoff_eligible: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("endpoint release bindings are evaluator-owned")
        if type(self.direction) is not RouteDirection or type(self.terminal) is not RouteEndpoint:
            raise RouteEvidenceIntegrityError("endpoint release identity is invalid")
        if type(self.arrival_checkpoint_id) is not str or not self.arrival_checkpoint_id:
            raise RouteEvidenceIntegrityError("endpoint arrival checkpoint is invalid")
        if (
            type(self.finalized_package_sha256) is not Sha256Digest
            or type(self.reviewer_truth_sha256) is not Sha256Digest
        ):
            raise RouteEvidenceIntegrityError("endpoint evidence digests are invalid")
        if type(self.required_downstream_evidence) is not DownstreamEvidenceRequirement:
            raise RouteEvidenceIntegrityError("endpoint downstream requirement is invalid")
        if type(self.synthetic_route_arrival_verified) is not bool:
            raise RouteEvidenceIntegrityError(
                "synthetic route arrival summary must be an exact boolean"
            )
        expected = _downstream_requirement(self.direction)
        if self.required_downstream_evidence is not expected:
            raise RouteEvidenceIntegrityError("endpoint downstream requirement differs")
        if (
            self.fresh_downstream_evidence_present is not False
            or self.bank_interface_open_proven is not False
            or self.supported_mining_view_proven is not False
            or self.downstream_handoff_eligible is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("route arrival cannot prove endpoint state")

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "arrival_checkpoint_id": self.arrival_checkpoint_id,
            "bank_interface_open_proven": self.bank_interface_open_proven,
            "direction": self.direction.value,
            "downstream_handoff_eligible": self.downstream_handoff_eligible,
            "finalized_package_sha256": self.finalized_package_sha256.value,
            "fresh_downstream_evidence_present": self.fresh_downstream_evidence_present,
            "input_authority": self.input_authority,
            "required_downstream_evidence": self.required_downstream_evidence.value,
            "reviewer_truth_sha256": self.reviewer_truth_sha256.value,
            "schema": _ENDPOINT_SCHEMA,
            "supported_mining_view_proven": self.supported_mining_view_proven,
            "synthetic_route_arrival_verified": self.synthetic_route_arrival_verified,
            "terminal": _endpoint_json(self.terminal),
        }


@dataclass(frozen=True, slots=True)
class DirectionProductionBinding:
    route_plan: RoutePlan
    expectation: DurableRouteEvidenceFilesystemExpectation
    operator_id: str
    reviewer_id: str
    review: RouteEvidenceReview
    reviewer_decision: ReviewerDecisionSummary
    reviewer_cases: tuple[ReviewerCaseDecisionBinding, ...]
    acquisition_root: DurableRootBinding
    review_root: DurableRootBinding
    evidence_conformance_passed: bool
    endpoint: EndpointReleaseBinding
    post_attempt_expectation: PostAttemptCausalityExpectation
    post_attempt_causality: PostAttemptCausalityBinding
    checks: tuple[DirectionReleaseCheck, ...]
    _source_evidence: _VerifiedDurableRouteEvidence = field(repr=False)
    _source_post_attempt_result: OfflineRouteSessionResult = field(repr=False)
    _source_acquisition_storage_path: str = field(repr=False)
    _source_review_storage_path: str = field(repr=False)
    _factory_token: InitVar[object | None] = None
    real_route_evidence_satisfied: Literal[False] = field(default=False, init=False)
    release_eligible: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("direction production bindings are evaluator-owned")
        if type(self._source_evidence) is not _VerifiedDurableRouteEvidence:
            raise RouteEvidenceIntegrityError("direction source evidence is invalid")
        if type(self._source_post_attempt_result) is not OfflineRouteSessionResult:
            raise RouteEvidenceIntegrityError("direction source session result is invalid")
        if (
            type(self._source_acquisition_storage_path) is not str
            or not self._source_acquisition_storage_path
            or type(self._source_review_storage_path) is not str
            or not self._source_review_storage_path
        ):
            raise RouteEvidenceIntegrityError("direction source storage paths are invalid")
        if type(self.route_plan) is not RoutePlan:
            raise RouteEvidenceIntegrityError("direction binding route plan is invalid")
        if type(self.expectation) is not DurableRouteEvidenceFilesystemExpectation:
            raise RouteEvidenceIntegrityError("direction binding expectation is invalid")
        direction = self.route_plan.identity.direction
        source = self._source_evidence
        if (
            self.route_plan != source.package.campaign_plan.route_plan
            or self.expectation != source.expectation
            or self.operator_id != source.package.campaign_plan.operator_id
            or self.review != source.review
            or self.evidence_conformance_passed is not source.report.evidence_conformance_passed
        ):
            raise RouteEvidenceIntegrityError(
                "direction projections differ from strict durable intake"
            )
        if (
            self.expectation.route != self.route_plan.identity
            or self.expectation.direction is not direction
            or self.expectation.evidence_role != SYNTHETIC_ROUTE_EVIDENCE_ROLE
            or self.expectation.activation_allowed is not False
            or self.expectation.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("direction binding route identity differs")
        if self.expectation.route_plan_sha256 != _route_plan_sha256(self.route_plan):
            raise RouteEvidenceIntegrityError("direction binding route plan digest differs")
        if type(self.reviewer_decision) is not ReviewerDecisionSummary:
            raise RouteEvidenceIntegrityError("direction reviewer summary is invalid")
        if (
            type(self.review) is not RouteEvidenceReview
            or self.review.content_sha256 != self.expectation.independent_review_sha256
            or self.review.finalized_package_sha256 != self.expectation.finalized_package_sha256
            or self.review.campaign_id != self.expectation.campaign_id
            or self.review.route != self.route_plan.identity
            or self.review.route_plan_sha256 != self.expectation.route_plan_sha256
            or self.review.reviewer_id != self.reviewer_id
            or self.review.evidence_role != SYNTHETIC_ROUTE_EVIDENCE_ROLE
            or self.review.activation_allowed is not False
            or self.review.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("direction independent review binding differs")
        if (
            type(self.operator_id) is not str
            or not self.operator_id
            or type(self.reviewer_id) is not str
            or not self.reviewer_id
            or self.operator_id.casefold() == self.reviewer_id.casefold()
        ):
            raise RouteEvidenceIntegrityError("direction operator/reviewer identities are invalid")
        if (
            type(self.reviewer_cases) is not tuple
            or not self.reviewer_cases
            or any(type(item) is not ReviewerCaseDecisionBinding for item in self.reviewer_cases)
            or len({item.case_id for item in self.reviewer_cases}) != len(self.reviewer_cases)
        ):
            raise RouteEvidenceIntegrityError("direction reviewer cases are invalid")
        checkpoint_ids = {item.checkpoint_id for item in self.route_plan.checkpoints}
        if any(
            candidate not in checkpoint_ids
            for item in self.reviewer_cases
            for candidate in item.candidate_checkpoint_ids
        ):
            raise RouteEvidenceIntegrityError("direction reviewer case names a foreign checkpoint")
        if len(self.reviewer_cases) != len(self.review.cases) or any(
            binding.case_id != truth.case_id
            or binding.decision is not truth.decision
            or binding.match is not truth.detection.match
            or binding.candidate_checkpoint_ids != truth.detection.candidate_checkpoint_ids
            or binding.confidence != truth.detection.confidence
            or binding.frame_sha256 != truth.frame_sha256
            or binding.detector_report_sha256 != truth.detector_report_sha256
            for binding, truth in zip(self.reviewer_cases, self.review.cases, strict=True)
        ):
            raise RouteEvidenceIntegrityError(
                "direction reviewer cases differ from pinned reviewer truth"
            )
        if (
            type(self.acquisition_root) is not DurableRootBinding
            or type(self.review_root) is not DurableRootBinding
        ):
            raise RouteEvidenceIntegrityError("direction durable root bindings are invalid")
        if (
            self.acquisition_root.direction is not direction
            or self.review_root.direction is not direction
        ):
            raise RouteEvidenceIntegrityError("direction durable roots differ")
        if (
            self.acquisition_root.role is not DurableRootRole.ACQUISITION
            or self.review_root.role is not DurableRootRole.REVIEW
        ):
            raise RouteEvidenceIntegrityError("direction durable root roles differ")
        if (
            self.acquisition_root.storage_path != self._source_acquisition_storage_path
            or self.review_root.storage_path != self._source_review_storage_path
        ):
            raise RouteEvidenceIntegrityError("direction durable root paths differ from sources")
        if (
            self.acquisition_root.physical_root_identity
            != source.acquisition_filesystem_identity.root_identity
            or self.acquisition_root.stable_tree_identity_sha256
            != _tree_identity_sha256(source.acquisition_filesystem_identity.tree_identity)
            or self.review_root.physical_root_identity
            != source.review_filesystem_identity.root_identity
            or self.review_root.stable_tree_identity_sha256
            != _tree_identity_sha256(source.review_filesystem_identity.tree_identity)
        ):
            raise RouteEvidenceIntegrityError(
                "direction durable roots differ from strict intake identities"
            )
        if (
            type(self.post_attempt_expectation) is not PostAttemptCausalityExpectation
            or self.post_attempt_expectation.route != self.route_plan.identity
            or self.post_attempt_expectation.route_plan_sha256 != self.expectation.route_plan_sha256
            or self.post_attempt_expectation.evidence_role != SYNTHETIC_ROUTE_EVIDENCE_ROLE
            or self.post_attempt_expectation.activation_allowed is not False
            or self.post_attempt_expectation.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("direction post-attempt expectation differs")
        if type(self.evidence_conformance_passed) is not bool:
            raise RouteEvidenceIntegrityError(
                "direction evidence conformance must be an exact boolean"
            )
        causal = self.post_attempt_causality
        causal_expectation = self.post_attempt_expectation
        if type(causal) is not PostAttemptCausalityBinding:
            raise RouteEvidenceIntegrityError("direction post-attempt causality is invalid")
        _require_result_binding(
            source,
            causal_expectation,
            self._source_post_attempt_result,
        )
        if causal != _post_attempt_binding(
            self.route_plan,
            self.expectation,
            self._source_post_attempt_result,
        ):
            raise RouteEvidenceIntegrityError(
                "direction causal projection differs from exact offline session result"
            )
        if (
            causal.route != causal_expectation.route
            or causal.route_plan_sha256 != causal_expectation.route_plan_sha256
            or causal.route_session_id != causal_expectation.route_session_id
            or causal.attempt_source_id != causal_expectation.attempt_source.source_id
            or causal.attempt_source_version != causal_expectation.attempt_source.version
            or causal.attempt_source_session_id != causal_expectation.attempt_source.session_id
            or causal.max_frame_age_s != causal_expectation.policy.max_frame_age_s
            or causal.minimum_confidence != causal_expectation.policy.minimum_confidence
            or causal.max_attempt_receipt_age_s
            != causal_expectation.policy.max_attempt_receipt_age_s
        ):
            raise RouteEvidenceIntegrityError("direction post-attempt result differs from its pins")
        expected_steps = self.route_plan.steps[: len(causal.steps)]
        if causal.expected_step_ids != tuple(step.step_id for step in self.route_plan.steps):
            raise RouteEvidenceIntegrityError("direction expected steps differ from route plan")
        if any(
            item.step_index != index
            or item.step_id != step.step_id
            or item.from_checkpoint_id != step.from_checkpoint_id
            or item.to_checkpoint_id != step.to_checkpoint_id
            for index, (item, step) in enumerate(zip(causal.steps, expected_steps, strict=True))
        ):
            raise RouteEvidenceIntegrityError("direction post-attempt step route binding differs")
        if type(self.endpoint) is not EndpointReleaseBinding:
            raise RouteEvidenceIntegrityError("direction endpoint binding is invalid")
        if (
            self.endpoint.direction is not direction
            or self.post_attempt_causality.direction is not direction
        ):
            raise RouteEvidenceIntegrityError("direction endpoint or causality differs")
        if (
            self.endpoint.terminal != self.route_plan.destination
            or self.endpoint.arrival_checkpoint_id != self.route_plan.checkpoints[-1].checkpoint_id
            or self.endpoint.finalized_package_sha256 != self.expectation.finalized_package_sha256
            or self.endpoint.reviewer_truth_sha256 != self.expectation.independent_review_sha256
        ):
            raise RouteEvidenceIntegrityError("direction endpoint evidence binding differs")
        expected_endpoint = EndpointReleaseBinding(
            direction=direction,
            terminal=self.route_plan.destination,
            arrival_checkpoint_id=source.report.endpoint.arrival_checkpoint_id,
            finalized_package_sha256=source.report.finalized_package_sha256,
            reviewer_truth_sha256=source.report.reviewer_truth_sha256,
            synthetic_route_arrival_verified=source.report.endpoint.route_arrival_verified,
            required_downstream_evidence=_downstream_requirement(direction),
            _factory_token=_FACTORY_TOKEN,
        )
        if self.endpoint != expected_endpoint:
            raise RouteEvidenceIntegrityError(
                "direction endpoint differs from strict verification report"
            )
        expected_reviewer_decision = (
            ReviewerDecisionSummary.APPROVED
            if all(
                item.decision is RouteEvidenceReviewDecision.APPROVED
                for item in self.reviewer_cases
            )
            else ReviewerDecisionSummary.REJECTED
        )
        if self.reviewer_decision is not expected_reviewer_decision:
            raise RouteEvidenceIntegrityError("direction reviewer summary differs from cases")
        if self.endpoint.synthetic_route_arrival_verified is not self.evidence_conformance_passed:
            raise RouteEvidenceIntegrityError(
                "direction arrival summary differs from evidence conformance"
            )
        if (
            self.evidence_conformance_passed
            and self.reviewer_decision is not ReviewerDecisionSummary.APPROVED
        ):
            raise RouteEvidenceIntegrityError(
                "conforming direction evidence requires approved reviewer truth"
            )
        if type(self.checks) is not tuple or any(
            type(item) is not DirectionReleaseCheck for item in self.checks
        ):
            raise RouteEvidenceIntegrityError("direction release matrix types are invalid")
        if self.checks != _direction_checks(
            direction,
            self.evidence_conformance_passed,
            self.endpoint.synthetic_route_arrival_verified,
            causal.synthetic_causality_conforms,
        ):
            raise RouteEvidenceIntegrityError(
                "direction release matrix differs from bound evidence"
            )
        if (
            self.real_route_evidence_satisfied is not False
            or self.release_eligible is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("direction binding cannot carry release authority")

    @property
    def direction(self) -> RouteDirection:
        return self.route_plan.identity.direction

    @property
    def binding_sha256(self) -> Sha256Digest:
        return Sha256Digest.from_bytes(canonical_route_evidence_bytes(self.to_json_value()))

    def to_json_value(self) -> dict[str, object]:
        return {
            "acquisition_root": self.acquisition_root.to_json_value(),
            "activation_allowed": self.activation_allowed,
            "binding_role": NAVIGATION_PRODUCTION_BINDING_ROLE,
            "checks": [item.to_json_value() for item in self.checks],
            "direction": self.direction.value,
            "endpoint": self.endpoint.to_json_value(),
            "evidence_conformance_passed": self.evidence_conformance_passed,
            "evidence_role": SYNTHETIC_ROUTE_EVIDENCE_ROLE,
            "expectation": _expectation_json(self.expectation),
            "input_authority": self.input_authority,
            "operator_id": self.operator_id,
            "post_attempt_expectation": self.post_attempt_expectation.to_json_value(),
            "post_attempt_causality": self.post_attempt_causality.to_json_value(),
            "real_route_evidence_satisfied": self.real_route_evidence_satisfied,
            "release_eligible": self.release_eligible,
            "review_root": self.review_root.to_json_value(),
            "review": self.review.to_json_value(),
            "reviewer_cases": [item.to_json_value() for item in self.reviewer_cases],
            "reviewer_decision": self.reviewer_decision.value,
            "reviewer_id": self.reviewer_id,
            "route_plan": _route_plan_json(self.route_plan),
            "schema": _DIRECTION_SCHEMA,
        }


@dataclass(frozen=True, slots=True)
class NavigationReleaseDecision:
    mine_to_bank: DirectionProductionBinding
    bank_to_mine: DirectionProductionBinding
    pair_checks: tuple[PairReleaseCheck, ...]
    _factory_token: InitVar[object | None] = None
    required_target_platform: Literal["win32"] = field(
        default=REQUIRED_NAVIGATION_TARGET_PLATFORM, init=False
    )
    required_host_threat_model: str = field(
        default=REQUIRED_NAVIGATION_HOST_THREAT_MODEL, init=False
    )
    required_namespace_contract: str = field(default=DURABLE_WRITER_NAMESPACE_CONTRACT, init=False)
    supported_host_namespace_attested: Literal[False] = field(default=False, init=False)
    writer_future_real_evidence_eligible: Literal[False] = field(default=False, init=False)
    real_release_role_satisfied: Literal[False] = field(default=False, init=False)
    release_eligible: Literal[False] = field(default=False, init=False)
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    world_state_activation_allowed: Literal[False] = field(default=False, init=False)
    controller_activation_allowed: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RouteEvidenceIntegrityError("navigation release decisions are evaluator-owned")
        if (
            type(self.mine_to_bank) is not DirectionProductionBinding
            or self.mine_to_bank.direction is not RouteDirection.MINE_TO_BANK
            or type(self.bank_to_mine) is not DirectionProductionBinding
            or self.bank_to_mine.direction is not RouteDirection.BANK_TO_MINE
        ):
            raise RouteEvidenceIntegrityError("navigation decision direction slots are invalid")
        if type(self.pair_checks) is not tuple or any(
            type(item) is not PairReleaseCheck for item in self.pair_checks
        ):
            raise RouteEvidenceIntegrityError("pair release matrix types are invalid")
        if self.pair_checks != _pair_checks():
            raise RouteEvidenceIntegrityError("pair release matrix differs from fixed requirements")
        _require_endpoint_contracts(self.mine_to_bank.route_plan, self.bank_to_mine.route_plan)
        _require_distinct_lineage(self.mine_to_bank.expectation, self.bank_to_mine.expectation)
        physical_root_identities = tuple(
            root.physical_root_identity
            for root in (
                self.mine_to_bank.acquisition_root,
                self.mine_to_bank.review_root,
                self.bank_to_mine.acquisition_root,
                self.bank_to_mine.review_root,
            )
        )
        if len(set(physical_root_identities)) != len(physical_root_identities):
            raise RouteEvidenceIntegrityError("navigation decision physical roots are not isolated")
        if (
            self.required_target_platform != REQUIRED_NAVIGATION_TARGET_PLATFORM
            or self.required_host_threat_model != REQUIRED_NAVIGATION_HOST_THREAT_MODEL
            or self.required_namespace_contract != DURABLE_WRITER_NAMESPACE_CONTRACT
            or self.supported_host_namespace_attested is not False
            or self.writer_future_real_evidence_eligible is not False
            or self.real_release_role_satisfied is not False
            or self.release_eligible is not False
            or self.live_navigation_enabled is not False
            or self.world_state_activation_allowed is not False
            or self.controller_activation_allowed is not False
            or self.activation_allowed is not False
            or self.input_authority is not False
        ):
            raise RouteEvidenceIntegrityError("navigation release decision cannot carry authority")

    @property
    def release_blocker_labels(self) -> tuple[str, ...]:
        direction_checks = self.mine_to_bank.checks + self.bank_to_mine.checks
        return tuple(
            item.report_local_check_id
            for item in direction_checks
            if item.status is ReleaseCheckStatus.NOT_SATISFIED
        ) + tuple(
            item.report_local_check_id
            for item in self.pair_checks
            if item.status is ReleaseCheckStatus.NOT_SATISFIED
        )

    @property
    def content_sha256(self) -> Sha256Digest:
        return Sha256Digest.from_bytes(self.canonical_bytes)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_route_evidence_bytes(self.to_json_value())

    def to_json_value(self) -> dict[str, object]:
        return {
            "activation_allowed": self.activation_allowed,
            "bank_to_mine": self.bank_to_mine.to_json_value(),
            "binding_role": NAVIGATION_PRODUCTION_BINDING_ROLE,
            "controller_activation_allowed": self.controller_activation_allowed,
            "input_authority": self.input_authority,
            "live_navigation_enabled": self.live_navigation_enabled,
            "mine_to_bank": self.mine_to_bank.to_json_value(),
            "pair_checks": [item.to_json_value() for item in self.pair_checks],
            "real_release_role_satisfied": self.real_release_role_satisfied,
            "release_blocker_label_scope": "report_local",
            "release_blocker_labels": list(self.release_blocker_labels),
            "release_eligible": self.release_eligible,
            "required_host_threat_model": self.required_host_threat_model,
            "required_namespace_contract": self.required_namespace_contract,
            "required_target_platform": self.required_target_platform,
            "schema": _DECISION_SCHEMA,
            "supported_host_namespace_attested": self.supported_host_namespace_attested,
            "world_state_activation_allowed": self.world_state_activation_allowed,
            "writer_future_real_evidence_eligible": (self.writer_future_real_evidence_eligible),
        }


def evaluate_navigation_release_readiness(
    *,
    mine_to_bank_acquisition_root: str | os.PathLike[str],
    mine_to_bank_review_root: str | os.PathLike[str],
    mine_to_bank_expectation: DurableRouteEvidenceFilesystemExpectation,
    mine_to_bank_post_attempt_expectation: PostAttemptCausalityExpectation,
    mine_to_bank_post_attempt_result: OfflineRouteSessionResult,
    bank_to_mine_acquisition_root: str | os.PathLike[str],
    bank_to_mine_review_root: str | os.PathLike[str],
    bank_to_mine_expectation: DurableRouteEvidenceFilesystemExpectation,
    bank_to_mine_post_attempt_expectation: PostAttemptCausalityExpectation,
    bank_to_mine_post_attempt_result: OfflineRouteSessionResult,
) -> NavigationReleaseDecision:
    """Bind one exact synthetic direction pair while permanently denying release."""

    roots = (
        _absolute_path_once(mine_to_bank_acquisition_root, "mine_to_bank acquisition root"),
        _absolute_path_once(mine_to_bank_review_root, "mine_to_bank review root"),
        _absolute_path_once(bank_to_mine_acquisition_root, "bank_to_mine acquisition root"),
        _absolute_path_once(bank_to_mine_review_root, "bank_to_mine review root"),
    )
    _require_disjoint_paths(roots)
    m2b_expectation = _owned_expectation(mine_to_bank_expectation)
    b2m_expectation = _owned_expectation(bank_to_mine_expectation)
    _require_direction_slot(m2b_expectation, RouteDirection.MINE_TO_BANK, "mine_to_bank")
    _require_direction_slot(b2m_expectation, RouteDirection.BANK_TO_MINE, "bank_to_mine")
    _require_distinct_lineage(m2b_expectation, b2m_expectation)
    m2b_causality_expectation = _owned_causality_expectation(mine_to_bank_post_attempt_expectation)
    b2m_causality_expectation = _owned_causality_expectation(bank_to_mine_post_attempt_expectation)
    m2b_result = _owned_result(mine_to_bank_post_attempt_result)
    b2m_result = _owned_result(bank_to_mine_post_attempt_result)

    m2b_first = _load_and_verify_durable_synthetic_route_evidence_detailed(
        roots[0], roots[1], m2b_expectation
    )
    b2m_first = _load_and_verify_durable_synthetic_route_evidence_detailed(
        roots[2], roots[3], b2m_expectation
    )
    m2b_second = _load_and_verify_durable_synthetic_route_evidence_detailed(
        roots[0], roots[1], m2b_expectation
    )
    b2m_second = _load_and_verify_durable_synthetic_route_evidence_detailed(
        roots[2], roots[3], b2m_expectation
    )
    if m2b_first != m2b_second or b2m_first != b2m_second:
        raise RouteEvidenceIntegrityError(
            "paired durable evidence changed between repeated intake snapshots"
        )
    _require_distinct_physical_roots(m2b_second, b2m_second)
    _require_endpoint_contracts(
        m2b_second.package.campaign_plan.route_plan,
        b2m_second.package.campaign_plan.route_plan,
    )

    m2b = _direction_binding(
        m2b_second,
        roots[0],
        roots[1],
        m2b_causality_expectation,
        m2b_result,
    )
    b2m = _direction_binding(
        b2m_second,
        roots[2],
        roots[3],
        b2m_causality_expectation,
        b2m_result,
    )
    return NavigationReleaseDecision(
        mine_to_bank=m2b,
        bank_to_mine=b2m,
        pair_checks=_pair_checks(),
        _factory_token=_FACTORY_TOKEN,
    )


def _direction_binding(
    verified: _VerifiedDurableRouteEvidence,
    acquisition_path: Path,
    review_path: Path,
    causality_expectation: PostAttemptCausalityExpectation,
    result: OfflineRouteSessionResult,
) -> DirectionProductionBinding:
    plan = verified.package.campaign_plan.route_plan
    expectation = verified.expectation
    _require_result_binding(verified, causality_expectation, result)
    owned_plan = _snapshot_route_evidence_contract(plan)
    owned_expectation = _snapshot_full_expectation(expectation)
    owned_review = _snapshot_route_evidence_contract(verified.review)
    owned_result = _snapshot_result(result)
    causality = _post_attempt_binding(owned_plan, owned_expectation, owned_result)
    reviewer_cases = tuple(
        ReviewerCaseDecisionBinding(
            case_id=item.case_id,
            decision=item.decision,
            match=item.detection.match,
            candidate_checkpoint_ids=item.detection.candidate_checkpoint_ids,
            confidence=item.detection.confidence,
            frame_sha256=item.frame_sha256,
            detector_report_sha256=item.detector_report_sha256,
            _factory_token=_FACTORY_TOKEN,
        )
        for item in owned_review.cases
    )
    reviewer_decision = (
        ReviewerDecisionSummary.APPROVED
        if all(item.decision is RouteEvidenceReviewDecision.APPROVED for item in reviewer_cases)
        else ReviewerDecisionSummary.REJECTED
    )
    endpoint = EndpointReleaseBinding(
        direction=owned_plan.identity.direction,
        terminal=owned_plan.destination,
        arrival_checkpoint_id=verified.report.endpoint.arrival_checkpoint_id,
        finalized_package_sha256=Sha256Digest(verified.report.finalized_package_sha256.value),
        reviewer_truth_sha256=Sha256Digest(verified.report.reviewer_truth_sha256.value),
        synthetic_route_arrival_verified=verified.report.endpoint.route_arrival_verified,
        required_downstream_evidence=_downstream_requirement(owned_plan.identity.direction),
        _factory_token=_FACTORY_TOKEN,
    )
    acquisition_root = _root_binding(
        owned_plan.identity.direction,
        DurableRootRole.ACQUISITION,
        acquisition_path,
        verified.acquisition_filesystem_identity.root_identity,
        verified.acquisition_filesystem_identity.tree_identity,
    )
    review_root = _root_binding(
        owned_plan.identity.direction,
        DurableRootRole.REVIEW,
        review_path,
        verified.review_filesystem_identity.root_identity,
        verified.review_filesystem_identity.tree_identity,
    )
    return DirectionProductionBinding(
        route_plan=owned_plan,
        expectation=owned_expectation,
        operator_id=verified.package.campaign_plan.operator_id,
        reviewer_id=owned_review.reviewer_id,
        review=owned_review,
        reviewer_decision=reviewer_decision,
        reviewer_cases=reviewer_cases,
        acquisition_root=acquisition_root,
        review_root=review_root,
        evidence_conformance_passed=verified.report.evidence_conformance_passed,
        endpoint=endpoint,
        post_attempt_expectation=causality_expectation,
        post_attempt_causality=causality,
        checks=_direction_checks(
            owned_plan.identity.direction,
            verified.report.evidence_conformance_passed,
            verified.report.endpoint.route_arrival_verified,
            causality.synthetic_causality_conforms,
        ),
        _source_evidence=verified,
        _source_post_attempt_result=result,
        _source_acquisition_storage_path=acquisition_root.storage_path,
        _source_review_storage_path=review_root.storage_path,
        _factory_token=_FACTORY_TOKEN,
    )


def _post_attempt_binding(
    plan: RoutePlan,
    expectation: DurableRouteEvidenceFilesystemExpectation,
    result: OfflineRouteSessionResult,
) -> PostAttemptCausalityBinding:
    progress = result.progress
    navigation = progress.navigation
    completed = navigation.completed_attempts
    step_bindings: list[StepCausalityBinding] = []
    for index, completed_attempt in enumerate(completed):
        proposal = completed_attempt.proposal
        receipt = completed_attempt.receipt
        next_observation: CheckpointObservation | None = None
        if index + 1 < len(completed):
            next_observation = completed[index + 1].proposal.checkpoint_evidence
        elif index == len(plan.steps) - 1 and navigation.arrival_evidence is not None:
            next_observation = navigation.arrival_evidence.observation
        next_bound = bool(
            next_observation is not None
            and next_observation.route == plan.identity
            and next_observation.provenance.source == progress.session.context.expected_source
            and next_observation.matched_checkpoint_id == proposal.step.to_checkpoint_id
            and next_observation.provenance.frame.frame_id
            > proposal.checkpoint_evidence.provenance.frame.frame_id
            and next_observation.provenance.frame.captured_monotonic_s
            > receipt.post_attempt_monotonic_s
        )
        if next_bound:
            assert next_observation is not None
            next_checkpoint_id = next_observation.matched_checkpoint_id
            next_frame_id = next_observation.provenance.frame.frame_id
            next_captured_monotonic_s = next_observation.provenance.frame.captured_monotonic_s
            next_frame_payload_sha256 = next_observation.provenance.frame_payload_sha256
            next_confidence = next_observation.confidence
        else:
            next_checkpoint_id = None
            next_frame_id = None
            next_captured_monotonic_s = None
            next_frame_payload_sha256 = None
            next_confidence = None
        step_bindings.append(
            StepCausalityBinding(
                step_index=index,
                step_id=proposal.step.step_id,
                from_checkpoint_id=proposal.step.from_checkpoint_id,
                to_checkpoint_id=proposal.step.to_checkpoint_id,
                attempt_id=proposal.attempt_identity.attempt_id,
                attempt_source_id=receipt.source.source_id,
                attempt_source_version=receipt.source.version,
                attempt_source_session_id=receipt.source.session_id,
                departure_frame_id=proposal.checkpoint_evidence.provenance.frame.frame_id,
                departure_captured_monotonic_s=(
                    proposal.checkpoint_evidence.provenance.frame.captured_monotonic_s
                ),
                departure_frame_payload_sha256=(
                    proposal.checkpoint_evidence.provenance.frame_payload_sha256
                ),
                departure_confidence=proposal.checkpoint_evidence.confidence,
                prepared_monotonic_s=receipt.prepared_monotonic_s,
                post_attempt_monotonic_s=receipt.post_attempt_monotonic_s,
                receipt_recorded_monotonic_s=completed_attempt.recorded_monotonic_s,
                next_checkpoint_id=next_checkpoint_id,
                next_frame_id=next_frame_id,
                next_captured_monotonic_s=next_captured_monotonic_s,
                next_frame_payload_sha256=next_frame_payload_sha256,
                next_confidence=next_confidence,
                post_attempt_checkpoint_bound=next_bound,
                _factory_token=_FACTORY_TOKEN,
            )
        )
    explicit_arrival = bool(
        progress.phase is OfflineRouteSessionPhase.ARRIVED
        and navigation.arrival_evidence is not None
        and navigation.arrival_evidence.checkpoint == plan.checkpoints[-1]
        and navigation.arrival_evidence.observation.matched_checkpoint_id
        == plan.checkpoints[-1].checkpoint_id
    )
    failure_reason = None if navigation.failure_reason is None else navigation.failure_reason.value
    conforms = bool(
        progress.phase is OfflineRouteSessionPhase.ARRIVED
        and failure_reason is None
        and explicit_arrival
        and len(step_bindings) == len(plan.steps)
        and all(item.post_attempt_checkpoint_bound for item in step_bindings)
    )
    attempt_source = progress.session.context.expected_attempt_source
    policy = progress.session.context.policy
    return PostAttemptCausalityBinding(
        direction=plan.identity.direction,
        route=plan.identity,
        route_plan_sha256=expectation.route_plan_sha256,
        route_session_id=progress.session.session_id,
        attempt_source_id=attempt_source.source_id,
        attempt_source_version=attempt_source.version,
        attempt_source_session_id=attempt_source.session_id,
        max_frame_age_s=policy.max_frame_age_s,
        minimum_confidence=policy.minimum_confidence,
        max_attempt_receipt_age_s=policy.max_attempt_receipt_age_s,
        expected_step_ids=tuple(item.step_id for item in plan.steps),
        steps=tuple(step_bindings),
        terminal_phase=progress.phase,
        failure_reason=failure_reason,
        explicit_terminal_arrival_bound=explicit_arrival,
        synthetic_causality_conforms=conforms,
        session_result_sha256=Sha256Digest.from_bytes(
            canonical_route_evidence_bytes(_contract_graph_object(result))
        ),
        _factory_token=_FACTORY_TOKEN,
    )


def _require_result_binding(
    verified: _VerifiedDurableRouteEvidence,
    causality_expectation: PostAttemptCausalityExpectation,
    result: OfflineRouteSessionResult,
) -> None:
    plan = verified.package.campaign_plan.route_plan
    expectation = verified.expectation
    progress = result.progress
    context = progress.session.context
    source = context.expected_source
    profile = source.profile
    if (
        causality_expectation.route != plan.identity
        or causality_expectation.route_plan_sha256 != expectation.route_plan_sha256
        or progress.session.session_id != causality_expectation.route_session_id
        or context.expected_attempt_source != causality_expectation.attempt_source
        or context.policy != causality_expectation.policy
        or progress.session.direction is not plan.identity.direction
        or context.plan != plan
        or source.detector != expectation.detector
        or source.profile_identity != expectation.profile
        or source.frame_source_id != expectation.capture_source_id
        or source.capture_session_id != expectation.capture_session_id
        or source.frame_width != expectation.frame_width
        or source.frame_height != expectation.frame_height
        or source.pixel_format is not expectation.pixel_format
        or profile.checkpoint_ids
        != tuple(checkpoint.checkpoint_id for checkpoint in plan.checkpoints)
        or profile.evidence_role is not CheckpointEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY
        or context.expected_attempt_source.evidence_role
        is not AttemptEvidenceRole.SYNTHETIC_ARCHITECTURE_TEST_ONLY
    ):
        raise RouteEvidenceIntegrityError(
            "post-attempt result differs from the exact durable route/source binding"
        )


def _direction_checks(
    direction: RouteDirection,
    evidence_conformance: bool,
    synthetic_arrival: bool,
    offline_causality: bool,
) -> tuple[DirectionReleaseCheck, ...]:
    statuses = {
        DirectionReleaseRequirement.ROUTE_IDENTITY_VERSION: ReleaseCheckStatus.BOUND_OFFLINE,
        DirectionReleaseRequirement.ORDERED_ROUTE_PLAN: ReleaseCheckStatus.BOUND_OFFLINE,
        DirectionReleaseRequirement.DETECTOR_PROFILE: ReleaseCheckStatus.BOUND_OFFLINE,
        DirectionReleaseRequirement.FRAME_CONTRACT: ReleaseCheckStatus.BOUND_OFFLINE,
        DirectionReleaseRequirement.CAPTURE_BUILD_CONFIGURATION_ENVIRONMENT: (
            ReleaseCheckStatus.BOUND_OFFLINE
        ),
        DirectionReleaseRequirement.CAPTURE_SOURCE_SESSION: ReleaseCheckStatus.BOUND_OFFLINE,
        DirectionReleaseRequirement.DURABLE_ACQUISITION_LINEAGE: (ReleaseCheckStatus.BOUND_OFFLINE),
        DirectionReleaseRequirement.DURABLE_REVIEW_LINEAGE: ReleaseCheckStatus.BOUND_OFFLINE,
        DirectionReleaseRequirement.REVIEWER_DECISION_BOUND: ReleaseCheckStatus.BOUND_OFFLINE,
        DirectionReleaseRequirement.SYNTHETIC_EVIDENCE_CONFORMANCE: (
            ReleaseCheckStatus.BOUND_OFFLINE
            if evidence_conformance
            else ReleaseCheckStatus.NOT_SATISFIED
        ),
        DirectionReleaseRequirement.SYNTHETIC_ROUTE_ARRIVAL: (
            ReleaseCheckStatus.BOUND_OFFLINE
            if synthetic_arrival
            else ReleaseCheckStatus.NOT_SATISFIED
        ),
        DirectionReleaseRequirement.OFFLINE_NAVIGATION_POLICY: (ReleaseCheckStatus.BOUND_OFFLINE),
        DirectionReleaseRequirement.PRODUCTION_NAVIGATION_POLICY_ATTESTATION: (
            ReleaseCheckStatus.NOT_SATISFIED
        ),
        DirectionReleaseRequirement.OFFLINE_POST_ATTEMPT_CAUSALITY: (
            ReleaseCheckStatus.BOUND_OFFLINE
            if offline_causality
            else ReleaseCheckStatus.NOT_SATISFIED
        ),
        DirectionReleaseRequirement.REAL_POST_ATTEMPT_CAUSALITY: (ReleaseCheckStatus.NOT_SATISFIED),
        DirectionReleaseRequirement.REAL_ROUTE_EVIDENCE: ReleaseCheckStatus.NOT_SATISFIED,
        DirectionReleaseRequirement.DOWNSTREAM_ENDPOINT_EVIDENCE: (
            ReleaseCheckStatus.NOT_SATISFIED
        ),
    }
    return tuple(
        DirectionReleaseCheck(
            direction=direction,
            requirement=requirement,
            status=statuses[requirement],
            _factory_token=_FACTORY_TOKEN,
        )
        for requirement in DirectionReleaseRequirement
    )


def _pair_checks() -> tuple[PairReleaseCheck, ...]:
    statuses = {
        PairReleaseRequirement.CANONICAL_DIRECTION_SLOTS: ReleaseCheckStatus.BOUND_OFFLINE,
        PairReleaseRequirement.INDEPENDENT_DURABLE_DIRECTION_LINEAGES: (
            ReleaseCheckStatus.BOUND_OFFLINE
        ),
        PairReleaseRequirement.PHYSICAL_ROOT_ISOLATION: ReleaseCheckStatus.BOUND_OFFLINE,
        PairReleaseRequirement.ENDPOINT_CONTRACT_COHERENCE: ReleaseCheckStatus.BOUND_OFFLINE,
        PairReleaseRequirement.REQUIRED_TARGET_PLATFORM: ReleaseCheckStatus.BOUND_OFFLINE,
        PairReleaseRequirement.REQUIRED_NAMESPACE_CONTRACT: ReleaseCheckStatus.BOUND_OFFLINE,
        PairReleaseRequirement.HOST_NAMESPACE_ATTESTATION: ReleaseCheckStatus.NOT_SATISFIED,
        PairReleaseRequirement.WRITER_FUTURE_REAL_ELIGIBILITY: (ReleaseCheckStatus.NOT_SATISFIED),
        PairReleaseRequirement.FINAL_RELEASE_DECISION: ReleaseCheckStatus.NOT_SATISFIED,
    }
    return tuple(
        PairReleaseCheck(
            requirement=requirement,
            status=statuses[requirement],
            _factory_token=_FACTORY_TOKEN,
        )
        for requirement in PairReleaseRequirement
    )


def _root_binding(
    direction: RouteDirection,
    role: DurableRootRole,
    path: Path,
    root_identity: tuple[int, ...],
    tree_identity: tuple[tuple[str, bool, tuple[int, ...]], ...],
) -> DurableRootBinding:
    return DurableRootBinding(
        direction=direction,
        role=role,
        storage_path=str(path),
        physical_root_identity=root_identity,
        stable_tree_identity_sha256=_tree_identity_sha256(tree_identity),
        _factory_token=_FACTORY_TOKEN,
    )


def _tree_identity_sha256(
    tree_identity: tuple[tuple[str, bool, tuple[int, ...]], ...],
) -> Sha256Digest:
    return Sha256Digest.from_bytes(
        canonical_route_evidence_bytes(
            {
                "entries": [
                    {
                        "identity": list(identity),
                        "is_directory": is_directory,
                        "relative_path": relative_path,
                    }
                    for relative_path, is_directory, identity in tree_identity
                ]
            }
        )
    )


def _owned_expectation(
    value: DurableRouteEvidenceFilesystemExpectation,
) -> DurableRouteEvidenceFilesystemExpectation:
    try:
        first = _snapshot_full_expectation(value)
        second = _snapshot_full_expectation(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError("durable release expectation is malformed") from exc
    if first != second:
        raise RouteEvidenceIntegrityError("durable release expectation changed during snapshot")
    return second


def _copy_causality_expectation(
    value: PostAttemptCausalityExpectation,
) -> PostAttemptCausalityExpectation:
    if type(value) is not PostAttemptCausalityExpectation:
        raise TypeError("post-attempt expectation has the wrong type")
    if (
        value.evidence_role != SYNTHETIC_ROUTE_EVIDENCE_ROLE
        or value.activation_allowed is not False
        or value.input_authority is not False
    ):
        raise ValueError("post-attempt expectation carries mutated authority fields")
    route = value.route
    source = value.attempt_source
    policy = value.policy
    return PostAttemptCausalityExpectation(
        route=RouteIdentity(route.route_id, route.version, route.direction),
        route_plan_sha256=Sha256Digest(value.route_plan_sha256.value),
        route_session_id=value.route_session_id,
        attempt_source=StepAttemptSourceIdentity(
            source.source_id,
            source.version,
            source.session_id,
            source.evidence_role,
        ),
        policy=NavigationPolicy(
            policy.max_frame_age_s,
            policy.minimum_confidence,
            policy.max_attempt_receipt_age_s,
        ),
    )


def _owned_causality_expectation(
    value: PostAttemptCausalityExpectation,
) -> PostAttemptCausalityExpectation:
    try:
        first = _copy_causality_expectation(value)
        second = _copy_causality_expectation(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError("post-attempt expectation is malformed") from exc
    if first != second:
        raise RouteEvidenceIntegrityError("post-attempt expectation changed during snapshot")
    return second


def _owned_result(value: OfflineRouteSessionResult) -> OfflineRouteSessionResult:
    try:
        first = _snapshot_result(value)
        second = _snapshot_result(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError("post-attempt session result is malformed") from exc
    if first != second:
        raise RouteEvidenceIntegrityError("post-attempt session result changed during snapshot")
    return second


def _copy_direction_production_binding(
    value: DirectionProductionBinding,
) -> DirectionProductionBinding:
    """Rebuild one evaluator-owned direction from its retained strict sources."""

    if type(value) is not DirectionProductionBinding:
        raise TypeError("direction production binding has the wrong type")
    acquisition_path = _absolute_path_once(
        value._source_acquisition_storage_path,
        "direction acquisition storage path",
    )
    review_path = _absolute_path_once(
        value._source_review_storage_path,
        "direction review storage path",
    )
    return _direction_binding(
        value._source_evidence,
        acquisition_path,
        review_path,
        _owned_causality_expectation(value.post_attempt_expectation),
        _owned_result(value._source_post_attempt_result),
    )


def _snapshot_navigation_release_decision(
    value: NavigationReleaseDecision,
) -> NavigationReleaseDecision:
    """Detach and revalidate a complete B1 decision for offline composition."""

    def copy_once(candidate: NavigationReleaseDecision) -> NavigationReleaseDecision:
        if type(candidate) is not NavigationReleaseDecision:
            raise TypeError("navigation release decision has the wrong type")
        return NavigationReleaseDecision(
            mine_to_bank=_copy_direction_production_binding(candidate.mine_to_bank),
            bank_to_mine=_copy_direction_production_binding(candidate.bank_to_mine),
            pair_checks=_pair_checks(),
            _factory_token=_FACTORY_TOKEN,
        )

    try:
        first = copy_once(value)
        second = copy_once(value)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise RouteEvidenceIntegrityError("navigation release decision is malformed") from exc
    if first != second:
        raise RouteEvidenceIntegrityError("navigation release decision changed during snapshot")
    if second != value:
        raise RouteEvidenceIntegrityError("navigation release decision differs from its sources")
    return second


def _absolute_path_once(value: str | os.PathLike[str], field_name: str) -> Path:
    try:
        raw = os.fspath(value)
    except (TypeError, ValueError, OSError) as exc:
        raise RouteEvidenceIntegrityError(f"{field_name} is invalid") from exc
    if type(raw) is not str or not raw or "\x00" in raw:
        raise RouteEvidenceIntegrityError(f"{field_name} must resolve to one exact text path")
    return Path(raw).absolute()


def _require_disjoint_paths(paths: tuple[Path, ...]) -> None:
    resolved: list[Path] = []
    for path in paths:
        try:
            resolved.append(path.resolve(strict=True))
        except OSError as exc:
            raise RouteEvidenceIntegrityError(
                f"durable evidence root is unavailable: {exc}"
            ) from exc
    for first, second in combinations(resolved, 2):
        if _paths_overlap(first, second):
            raise RouteEvidenceIntegrityError(
                "all acquisition and review roots must be physically disjoint"
            )


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def _require_direction_slot(
    expectation: DurableRouteEvidenceFilesystemExpectation,
    direction: RouteDirection,
    slot: str,
) -> None:
    if expectation.direction is not direction or expectation.route.direction is not direction:
        raise RouteEvidenceIntegrityError(f"{slot} durable evidence has the wrong direction")


def _require_distinct_lineage(
    mine_to_bank: DurableRouteEvidenceFilesystemExpectation,
    bank_to_mine: DurableRouteEvidenceFilesystemExpectation,
) -> None:
    values: tuple[tuple[object, object, str], ...] = (
        (mine_to_bank.campaign_id, bank_to_mine.campaign_id, "campaign id"),
        (mine_to_bank.capture_session_id, bank_to_mine.capture_session_id, "capture session"),
        (mine_to_bank.route_plan_sha256, bank_to_mine.route_plan_sha256, "route plan"),
        (
            mine_to_bank.finalized_package_sha256,
            bank_to_mine.finalized_package_sha256,
            "finalized package",
        ),
        (
            mine_to_bank.acquisition_head_sha256,
            bank_to_mine.acquisition_head_sha256,
            "acquisition head",
        ),
        (
            mine_to_bank.acquisition_journal_head_sha256,
            bank_to_mine.acquisition_journal_head_sha256,
            "acquisition journal",
        ),
        (
            mine_to_bank.acquisition_finalization_sha256,
            bank_to_mine.acquisition_finalization_sha256,
            "acquisition finalization",
        ),
        (mine_to_bank.review_id, bank_to_mine.review_id, "review id"),
        (
            mine_to_bank.independent_review_sha256,
            bank_to_mine.independent_review_sha256,
            "independent review",
        ),
        (
            mine_to_bank.review_plan_sha256,
            bank_to_mine.review_plan_sha256,
            "review plan",
        ),
        (
            mine_to_bank.review_journal_head_sha256,
            bank_to_mine.review_journal_head_sha256,
            "review journal",
        ),
        (
            mine_to_bank.review_finalization_sha256,
            bank_to_mine.review_finalization_sha256,
            "review finalization",
        ),
    )
    for first, second, label in values:
        if first == second:
            raise RouteEvidenceIntegrityError(f"direction packages reuse {label} lineage")


def _require_distinct_physical_roots(
    mine_to_bank: _VerifiedDurableRouteEvidence,
    bank_to_mine: _VerifiedDurableRouteEvidence,
) -> None:
    identities = (
        mine_to_bank.acquisition_filesystem_identity.root_identity,
        mine_to_bank.review_filesystem_identity.root_identity,
        bank_to_mine.acquisition_filesystem_identity.root_identity,
        bank_to_mine.review_filesystem_identity.root_identity,
    )
    if len(set(identities)) != len(identities):
        raise RouteEvidenceIntegrityError("direction evidence reuses a physical transaction root")


def _require_endpoint_contracts(mine_to_bank: RoutePlan, bank_to_mine: RoutePlan) -> None:
    if (
        mine_to_bank.identity.direction is not RouteDirection.MINE_TO_BANK
        or bank_to_mine.identity.direction is not RouteDirection.BANK_TO_MINE
        or mine_to_bank.origin != bank_to_mine.destination
        or mine_to_bank.destination != bank_to_mine.origin
    ):
        raise RouteEvidenceIntegrityError(
            "direction plans do not share exact reversed mine and bank endpoint contracts"
        )


def _downstream_requirement(direction: RouteDirection) -> DownstreamEvidenceRequirement:
    if direction is RouteDirection.MINE_TO_BANK:
        return DownstreamEvidenceRequirement.FRESH_BANK_INTERFACE_OPEN
    if direction is RouteDirection.BANK_TO_MINE:
        return DownstreamEvidenceRequirement.FRESH_SUPPORTED_MINING_VIEW
    raise RouteEvidenceIntegrityError("unsupported route direction")


def _route_plan_sha256(plan: RoutePlan) -> Sha256Digest:
    return digest_route_plan(plan)


def _route_json(route: RouteIdentity) -> dict[str, object]:
    return {
        "direction": route.direction.value,
        "route_id": route.route_id,
        "version": route.version,
    }


def _endpoint_json(endpoint: RouteEndpoint) -> dict[str, object]:
    return {"location_id": endpoint.location_id, "role": endpoint.role.value}


def _route_plan_json(plan: RoutePlan) -> dict[str, object]:
    return {
        "checkpoints": [
            {"checkpoint_id": item.checkpoint_id, "role": item.role.value}
            for item in plan.checkpoints
        ],
        "destination": _endpoint_json(plan.destination),
        "origin": _endpoint_json(plan.origin),
        "identity": _route_json(plan.identity),
        "steps": [
            {
                "from_checkpoint_id": item.from_checkpoint_id,
                "step_id": item.step_id,
                "to_checkpoint_id": item.to_checkpoint_id,
            }
            for item in plan.steps
        ],
    }


def _expectation_json(
    value: DurableRouteEvidenceFilesystemExpectation,
) -> dict[str, object]:
    return {
        "acquisition_finalization_sha256": value.acquisition_finalization_sha256.value,
        "acquisition_head_sha256": value.acquisition_head_sha256.value,
        "acquisition_journal_head_sha256": value.acquisition_journal_head_sha256.value,
        "activation_allowed": value.activation_allowed,
        "campaign_id": value.campaign_id,
        "capture_build": value.capture_build.to_json_value(),
        "capture_configuration_sha256": value.capture_configuration_sha256.value,
        "capture_environment_sha256": value.capture_environment_sha256.value,
        "capture_session_id": value.capture_session_id,
        "capture_source_id": value.capture_source_id,
        "detector": {
            "detector_id": value.detector.detector_id,
            "version": value.detector.version,
        },
        "direction": value.direction.value,
        "evidence_role": value.evidence_role,
        "finalized_package_sha256": value.finalized_package_sha256.value,
        "frame_height": value.frame_height,
        "frame_width": value.frame_width,
        "independent_review_sha256": value.independent_review_sha256.value,
        "input_authority": value.input_authority,
        "pixel_format": value.pixel_format.value,
        "profile": {
            "content_sha256": value.profile.content_sha256.value,
            "profile_id": value.profile.profile_id,
            "version": value.profile.version,
        },
        "review_finalization_sha256": value.review_finalization_sha256.value,
        "review_id": value.review_id,
        "review_journal_head_sha256": value.review_journal_head_sha256.value,
        "review_plan_sha256": value.review_plan_sha256.value,
        "reviewer_id": value.reviewer_id,
        "route": _route_json(value.route),
        "route_plan_sha256": value.route_plan_sha256.value,
        "support_envelope_sha256": value.support_envelope_sha256.value,
    }


def _contract_graph_object(value: object) -> dict[str, object]:
    graph = _contract_graph_json(value)
    if type(graph) is not dict:
        raise RouteEvidenceIntegrityError("offline result graph did not serialize as an object")
    return cast(dict[str, object], graph)


def _contract_graph_json(value: object) -> object:
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, Enum):
        return value.value
    if type(value) is tuple:
        return [_contract_graph_json(item) for item in cast(tuple[object, ...], value)]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": f"{type(value).__module__}.{type(value).__qualname__}",
            **{
                item.name: _contract_graph_json(getattr(value, item.name))
                for item in fields(cast(Any, value))
            },
        }
    raise RouteEvidenceIntegrityError(
        f"unsupported offline result graph value {type(value).__name__}"
    )


assert DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE is False
