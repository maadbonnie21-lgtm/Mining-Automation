"""Deny-first perception authority contract for the constrained v1 workflow.

This module is deliberately *not* a controller adapter.  It describes the
evidence that a future source-owned integration boundary must bind before any
mining, banking, navigation, or click authority can exist.  The current schema
is non-activating: a result cannot carry resource states, an inventory count,
target identifiers, or an action capability.

Resource evidence is accepted only as the existing production trust receipt
plus explicit same-cycle provenance.  Inventory evidence is a nominal future
contract; no inventory detector/validation identity is approved here.  A later
reviewed change must add exact source-owned approval records and introduce a
new activating schema rather than treating development readiness as production
truth.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, final

from ..contracts import FrameRef, InventoryState, ResourceState
from .production_profiles import (
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    VARROCK_EAST_IRON_PROFILE_ID,
    VARROCK_EAST_IRON_RESOURCE_IDS,
    ProductionResourceTrustResult,
    load_varrock_east_iron_profile,
)
from .resource import RESOURCE_PROFILE_SCHEMA_VERSION

__all__ = [
    "CONSTRAINED_V1_ACTIVATION_ALLOWED",
    "CONSTRAINED_V1_AUTHORITY_SCHEMA_VERSION",
    "INVENTORY_PUBLICATION_CONFIDENCE_FLOOR",
    "MAX_PERCEPTION_AUTHORITY_AGE_S",
    "AuthorityBlocker",
    "ConstrainedV1PerceptionSnapshot",
    "InventoryAuthorityEvidence",
    "InventoryAuthorityIdentity",
    "PerceptionCycleProvenance",
    "ResourceAuthorityEvidence",
    "ResourceAuthorityIdentity",
    "prepare_constrained_v1_perception_snapshot",
]

CONSTRAINED_V1_AUTHORITY_SCHEMA_VERSION: Final[int] = 1
CONSTRAINED_V1_ACTIVATION_ALLOWED: Final[bool] = False
MAX_PERCEPTION_AUTHORITY_AGE_S: Final[float] = 1.0
INVENTORY_PUBLICATION_CONFIDENCE_FLOOR: Final[float] = 0.8

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_INVENTORY_CAPACITY: Final[int] = 28
_VARROCK_EAST_LOCATION_ID: Final[str] = "varrock-east-mine"


def _validate_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_sha256(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _finite_float(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


@dataclass(frozen=True, slots=True)
class PerceptionCycleProvenance:
    """Exact source/frame identity shared by one perception cycle."""

    frame: FrameRef
    cycle_id: str
    frame_sha256: str
    capture_configuration_id: str

    def __post_init__(self) -> None:
        if type(self.frame) is not FrameRef:
            raise ValueError("frame must be an exact FrameRef")
        _validate_non_empty_string(self.cycle_id, "cycle_id")
        _validate_sha256(self.frame_sha256, "frame_sha256")
        _validate_non_empty_string(
            self.capture_configuration_id,
            "capture_configuration_id",
        )


@dataclass(frozen=True, slots=True)
class ResourceAuthorityIdentity:
    """Identity claimed by a production resource trust receipt."""

    detector_id: str
    detector_version: str
    profile_id: str
    profile_schema_version: int
    location_id: str

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.detector_id, "detector_id")
        _validate_non_empty_string(self.detector_version, "detector_version")
        _validate_non_empty_string(self.profile_id, "profile_id")
        if (
            not isinstance(self.profile_schema_version, int)
            or isinstance(self.profile_schema_version, bool)
            or self.profile_schema_version <= 0
        ):
            raise ValueError("profile_schema_version must be a positive integer")
        _validate_non_empty_string(self.location_id, "location_id")


@dataclass(frozen=True, slots=True)
class ResourceAuthorityEvidence:
    """Nominal resource evidence bound to one exact perception cycle."""

    identity: ResourceAuthorityIdentity
    provenance: PerceptionCycleProvenance
    trust_result: ProductionResourceTrustResult

    def __post_init__(self) -> None:
        if type(self.identity) is not ResourceAuthorityIdentity:
            raise ValueError("identity must be an exact ResourceAuthorityIdentity")
        if type(self.provenance) is not PerceptionCycleProvenance:
            raise ValueError("provenance must be an exact PerceptionCycleProvenance")
        if type(self.trust_result) is not ProductionResourceTrustResult:
            raise ValueError("trust_result must be an exact ProductionResourceTrustResult")


@dataclass(frozen=True, slots=True)
class InventoryAuthorityIdentity:
    """Complete identity of a future independently validated inventory path."""

    detector_id: str
    detector_version: str
    profile_id: str
    profile_schema_version: int
    configuration_id: str

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.detector_id, "detector_id")
        _validate_non_empty_string(self.detector_version, "detector_version")
        _validate_non_empty_string(self.profile_id, "profile_id")
        if (
            not isinstance(self.profile_schema_version, int)
            or isinstance(self.profile_schema_version, bool)
            or self.profile_schema_version <= 0
        ):
            raise ValueError("profile_schema_version must be a positive integer")
        _validate_non_empty_string(self.configuration_id, "configuration_id")


@dataclass(frozen=True, slots=True)
class InventoryAuthorityEvidence:
    """Future inventory publication plus independent-validation provenance.

    The validation fields are descriptive claims only.  They confer no
    authority unless their exact tuple appears in the source-owned approval
    registry, which is intentionally empty in this non-activating schema.
    """

    identity: InventoryAuthorityIdentity
    provenance: PerceptionCycleProvenance
    state: InventoryState
    independent_validation_protocol_id: str | None = None
    independent_validation_report_sha256: str | None = None

    def __post_init__(self) -> None:
        if type(self.identity) is not InventoryAuthorityIdentity:
            raise ValueError("identity must be an exact InventoryAuthorityIdentity")
        if type(self.provenance) is not PerceptionCycleProvenance:
            raise ValueError("provenance must be an exact PerceptionCycleProvenance")
        if type(self.state) is not InventoryState:
            raise ValueError("state must be an exact InventoryState")
        protocol_present = self.independent_validation_protocol_id is not None
        report_present = self.independent_validation_report_sha256 is not None
        if protocol_present != report_present:
            raise ValueError(
                "independent validation protocol and report digest must be supplied together"
            )
        if self.independent_validation_protocol_id is not None:
            _validate_non_empty_string(
                self.independent_validation_protocol_id,
                "independent_validation_protocol_id",
            )
        if self.independent_validation_report_sha256 is not None:
            _validate_sha256(
                self.independent_validation_report_sha256,
                "independent_validation_report_sha256",
            )


@dataclass(frozen=True, slots=True)
class _InventoryApprovalKey:
    identity: InventoryAuthorityIdentity
    validation_protocol_id: str
    validation_report_sha256: str


_EXPECTED_RESOURCE_IDENTITY: Final[ResourceAuthorityIdentity] = ResourceAuthorityIdentity(
    detector_id=VARROCK_EAST_IRON_DETECTOR_ID,
    detector_version=VARROCK_EAST_IRON_DETECTOR_VERSION,
    profile_id=VARROCK_EAST_IRON_PROFILE_ID,
    profile_schema_version=RESOURCE_PROFILE_SCHEMA_VERSION,
    location_id=_VARROCK_EAST_LOCATION_ID,
)

# These registries are deliberately source-owned and empty.  Runtime callers
# cannot provide an approval flag or policy override.  Filling either registry
# requires a reviewed source change; this schema would still remain deny-only
# until a separately reviewed activating contract replaces it.
_APPROVED_RESOURCE_IDENTITIES: Final[frozenset[ResourceAuthorityIdentity]] = frozenset()
_APPROVED_INVENTORY_AUTHORITIES: Final[frozenset[_InventoryApprovalKey]] = frozenset()


class AuthorityBlocker(StrEnum):
    """Deterministic reasons that the current evidence carries zero authority."""

    ACTIVATION_DISABLED = "activation_disabled"
    CURRENT_TIME_INVALID = "current_time_invalid"
    EVIDENCE_TIMESTAMP_INVALID = "evidence_timestamp_invalid"
    EVIDENCE_FROM_FUTURE = "evidence_from_future"
    EVIDENCE_STALE = "evidence_stale"
    RESOURCE_EVIDENCE_MISSING = "resource_evidence_missing"
    RESOURCE_EVIDENCE_TYPE_INVALID = "resource_evidence_type_invalid"
    RESOURCE_IDENTITY_MISMATCH = "resource_identity_mismatch"
    RESOURCE_PROVENANCE_MISMATCH = "resource_provenance_mismatch"
    RESOURCE_TRUST_REJECTED = "resource_trust_rejected"
    RESOURCE_ENSEMBLE_INVALID = "resource_ensemble_invalid"
    RESOURCE_UNCERTAIN = "resource_uncertain"
    RESOURCE_REGION_INVALID = "resource_region_invalid"
    RESOURCE_PACKAGED_PROFILE_UNAVAILABLE = "resource_packaged_profile_unavailable"
    RESOURCE_RELEASE_APPROVAL_MISSING = "resource_release_approval_missing"
    INVENTORY_EVIDENCE_MISSING = "inventory_evidence_missing"
    INVENTORY_EVIDENCE_TYPE_INVALID = "inventory_evidence_type_invalid"
    INVENTORY_PROVENANCE_MISMATCH = "inventory_provenance_mismatch"
    INVENTORY_UNKNOWN = "inventory_unknown"
    INVENTORY_LAYOUT_MISMATCH = "inventory_layout_mismatch"
    INVENTORY_CONFIDENCE_BELOW_FLOOR = "inventory_confidence_below_floor"
    INVENTORY_INDEPENDENT_VALIDATION_MISSING = (
        "inventory_independent_validation_missing"
    )
    INVENTORY_RELEASE_APPROVAL_MISSING = "inventory_release_approval_missing"
    MIXED_PERCEPTION_PROVENANCE = "mixed_perception_provenance"


@final
@dataclass(frozen=True, slots=True)
class ConstrainedV1PerceptionSnapshot:
    """A diagnostics-only, structurally denied constrained-v1 snapshot.

    There are intentionally no constructor parameters for observations,
    targets, or capabilities.  The public properties below always expose the
    fail-closed values.  This prevents a caller from turning a development
    result into an action-bearing object by setting a boolean.
    """

    provenance: PerceptionCycleProvenance
    blockers: tuple[AuthorityBlocker, ...]
    resource_shape_valid: bool
    inventory_shape_valid: bool
    schema_version: int = field(
        default=CONSTRAINED_V1_AUTHORITY_SCHEMA_VERSION,
        init=False,
    )

    def __init_subclass__(cls) -> None:
        raise TypeError("ConstrainedV1PerceptionSnapshot is sealed")

    def __post_init__(self) -> None:
        if type(self.provenance) is not PerceptionCycleProvenance:
            raise ValueError("provenance must be an exact PerceptionCycleProvenance")
        if not isinstance(self.blockers, tuple) or not self.blockers:
            raise ValueError("a deny-only snapshot requires at least one blocker")
        if any(type(blocker) is not AuthorityBlocker for blocker in self.blockers):
            raise ValueError("blockers must contain exact AuthorityBlocker values")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        required_blockers = {
            AuthorityBlocker.ACTIVATION_DISABLED,
            AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING,
            AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING,
        }
        if not required_blockers.issubset(self.blockers):
            raise ValueError("deny-only snapshot is missing a source-owned blocker")
        if not isinstance(self.resource_shape_valid, bool):
            raise ValueError("resource_shape_valid must be a boolean")
        if not isinstance(self.inventory_shape_valid, bool):
            raise ValueError("inventory_shape_valid must be a boolean")

    @property
    def resources(self) -> tuple[ResourceState, ...]:
        return ()

    @property
    def inventory(self) -> InventoryState:
        return InventoryState(occupied_slots=None, capacity=_INVENTORY_CAPACITY, confidence=0.0)

    @property
    def actionable_target_ids(self) -> tuple[str, ...]:
        return ()

    @property
    def mining_authorized(self) -> bool:
        return False

    @property
    def banking_authorized(self) -> bool:
        return False

    @property
    def navigation_authorized(self) -> bool:
        return False

    @property
    def click_authorized(self) -> bool:
        return False


def _append_unique(
    blockers: list[AuthorityBlocker],
    blocker: AuthorityBlocker,
) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


def _evaluate_current_freshness(
    provenance: PerceptionCycleProvenance,
    evaluated_monotonic_s: object,
) -> tuple[AuthorityBlocker | None, bool]:
    evaluated = _finite_float(evaluated_monotonic_s)
    if evaluated is None:
        return AuthorityBlocker.CURRENT_TIME_INVALID, False
    captured = _finite_float(provenance.frame.captured_monotonic_s)
    if captured is None:
        return AuthorityBlocker.EVIDENCE_TIMESTAMP_INVALID, False
    age_s = evaluated - captured
    if age_s < 0.0:
        return AuthorityBlocker.EVIDENCE_FROM_FUTURE, False
    if age_s > MAX_PERCEPTION_AUTHORITY_AGE_S:
        return AuthorityBlocker.EVIDENCE_STALE, False
    return None, True


def _evaluate_resource_evidence(
    evidence: object | None,
    *,
    current_provenance: PerceptionCycleProvenance,
) -> tuple[bool, tuple[AuthorityBlocker, ...]]:
    blockers: list[AuthorityBlocker] = []
    if evidence is None:
        return False, (AuthorityBlocker.RESOURCE_EVIDENCE_MISSING,)
    if type(evidence) is not ResourceAuthorityEvidence:
        return False, (AuthorityBlocker.RESOURCE_EVIDENCE_TYPE_INVALID,)

    if evidence.identity != _EXPECTED_RESOURCE_IDENTITY:
        _append_unique(blockers, AuthorityBlocker.RESOURCE_IDENTITY_MISMATCH)
    if evidence.provenance != current_provenance:
        _append_unique(blockers, AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH)

    result = evidence.trust_result
    if not result.accepted:
        _append_unique(blockers, AuthorityBlocker.RESOURCE_TRUST_REJECTED)
    else:
        if result.reason != "trusted_complete_production_ensemble":
            _append_unique(blockers, AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID)
        if result.frame != evidence.provenance.frame:
            _append_unique(blockers, AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH)
        resource_ids = tuple(resource.resource_id for resource in result.resources)
        if (
            resource_ids != VARROCK_EAST_IRON_RESOURCE_IDS
            or any(type(resource) is not ResourceState for resource in result.resources)
            or any(resource.resource_type != "iron" for resource in result.resources)
        ):
            _append_unique(blockers, AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID)
        if any(resource.available is None for resource in result.resources):
            _append_unique(blockers, AuthorityBlocker.RESOURCE_UNCERTAIN)
        try:
            profile = load_varrock_east_iron_profile()
        except (OSError, ValueError):
            _append_unique(
                blockers,
                AuthorityBlocker.RESOURCE_PACKAGED_PROFILE_UNAVAILABLE,
            )
        else:
            if (
                profile.profile_id != VARROCK_EAST_IRON_PROFILE_ID
                or profile.schema_version != RESOURCE_PROFILE_SCHEMA_VERSION
                or profile.location_id != _VARROCK_EAST_LOCATION_ID
                or tuple(candidate.resource_id for candidate in profile.candidates)
                != VARROCK_EAST_IRON_RESOURCE_IDS
                or result.frame is None
                or result.frame.width != profile.frame_width
                or result.frame.height != profile.frame_height
            ):
                _append_unique(blockers, AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID)
            expected_regions = {
                candidate.resource_id: candidate.region for candidate in profile.candidates
            }
            for resource in result.resources:
                expected_region = expected_regions.get(resource.resource_id)
                region_is_valid = (
                    resource.available is True
                    and resource.interaction_region == expected_region
                ) or (
                    resource.available is False and resource.interaction_region is None
                )
                if expected_region is None or not region_is_valid:
                    _append_unique(blockers, AuthorityBlocker.RESOURCE_REGION_INVALID)
                    break

    return not blockers, tuple(blockers)


def _evaluate_inventory_evidence(
    evidence: object | None,
    *,
    current_provenance: PerceptionCycleProvenance,
) -> tuple[bool, tuple[AuthorityBlocker, ...]]:
    blockers: list[AuthorityBlocker] = []
    if evidence is None:
        return False, (AuthorityBlocker.INVENTORY_EVIDENCE_MISSING,)
    if type(evidence) is not InventoryAuthorityEvidence:
        return False, (AuthorityBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,)

    if evidence.provenance != current_provenance:
        _append_unique(blockers, AuthorityBlocker.INVENTORY_PROVENANCE_MISMATCH)
    if evidence.state.occupied_slots is None:
        _append_unique(blockers, AuthorityBlocker.INVENTORY_UNKNOWN)
    if evidence.state.capacity != _INVENTORY_CAPACITY:
        _append_unique(blockers, AuthorityBlocker.INVENTORY_LAYOUT_MISMATCH)
    if evidence.state.confidence < INVENTORY_PUBLICATION_CONFIDENCE_FLOOR:
        _append_unique(
            blockers,
            AuthorityBlocker.INVENTORY_CONFIDENCE_BELOW_FLOOR,
        )

    if (
        evidence.independent_validation_protocol_id is None
        or evidence.independent_validation_report_sha256 is None
    ):
        _append_unique(
            blockers,
            AuthorityBlocker.INVENTORY_INDEPENDENT_VALIDATION_MISSING,
        )
    return not blockers, tuple(blockers)


def prepare_constrained_v1_perception_snapshot(
    *,
    current_provenance: PerceptionCycleProvenance,
    resource_evidence: object | None,
    inventory_evidence: object | None,
    evaluated_monotonic_s: object,
) -> ConstrainedV1PerceptionSnapshot:
    """Evaluate same-cycle evidence without granting runtime authority.

    Policy, approval registries, detector identities, resource IDs, inventory
    floor, layout, and freshness are all source-owned.  The caller can provide
    evidence, the exact current-cycle identity, and observation time only; it
    cannot provide thresholds, approval flags, activation switches, or target
    lists.
    """

    if type(current_provenance) is not PerceptionCycleProvenance:
        raise TypeError("current_provenance must be an exact PerceptionCycleProvenance")
    if (
        CONSTRAINED_V1_ACTIVATION_ALLOWED
        or _APPROVED_RESOURCE_IDENTITIES
        or _APPROVED_INVENTORY_AUTHORITIES
    ):
        raise RuntimeError(
            "deny-only authority schema cannot contain activation or approvals"
        )

    blockers: list[AuthorityBlocker] = [
        AuthorityBlocker.ACTIVATION_DISABLED,
        AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING,
        AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING,
    ]
    freshness_blocker, freshness_valid = _evaluate_current_freshness(
        current_provenance,
        evaluated_monotonic_s,
    )
    if freshness_blocker is not None:
        _append_unique(blockers, freshness_blocker)

    resource_valid, resource_blockers = _evaluate_resource_evidence(
        resource_evidence,
        current_provenance=current_provenance,
    )
    inventory_valid, inventory_blockers = _evaluate_inventory_evidence(
        inventory_evidence,
        current_provenance=current_provenance,
    )
    for blocker in (*resource_blockers, *inventory_blockers):
        _append_unique(blockers, blocker)

    if (
        type(resource_evidence) is ResourceAuthorityEvidence
        and type(inventory_evidence) is InventoryAuthorityEvidence
        and resource_evidence.provenance != inventory_evidence.provenance
    ):
        _append_unique(blockers, AuthorityBlocker.MIXED_PERCEPTION_PROVENANCE)

    return ConstrainedV1PerceptionSnapshot(
        provenance=current_provenance,
        blockers=tuple(blockers),
        resource_shape_valid=resource_valid and freshness_valid,
        inventory_shape_valid=inventory_valid and freshness_valid,
    )
