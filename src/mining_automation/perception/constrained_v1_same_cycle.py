"""Deny-only same-cycle perception projection for constrained v1.

This module composes the frozen offline authority contract into a carrier that
always publishes the operationally safe denial view: four canonical UNKNOWN
resources, UNKNOWN inventory, no targets, and no downstream authority.  It is
integration preparation only.  It does not create ``WorldState``, consume a
release record, approve either perception lineage, or execute input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final, final

from ..contracts import FrameRef, InventoryState, ResourceState
from .constrained_v1_authority import (
    AuthorityBlocker,
    PerceptionCycleProvenance,
    ResourceAuthorityEvidence,
    prepare_constrained_v1_perception_snapshot,
)
from .production_profiles import (
    VARROCK_EAST_IRON_RESOURCE_IDS,
    ProductionResourceTrustResult,
)

__all__ = [
    "CONSTRAINED_V1_SAME_CYCLE_SCHEMA_VERSION",
    "ConstrainedV1SameCycleDenial",
    "prepare_constrained_v1_same_cycle_denial",
]

CONSTRAINED_V1_SAME_CYCLE_SCHEMA_VERSION: Final[int] = 1
_INVENTORY_CAPACITY: Final[int] = 28
_REQUIRED_SOURCE_BLOCKERS: Final[frozenset[AuthorityBlocker]] = frozenset(
    {
        AuthorityBlocker.ACTIVATION_DISABLED,
        AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING,
        AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING,
    }
)


def _plain_region(value: object) -> bool:
    return value is None or (
        type(value) is tuple
        and len(value) == 4
        and all(type(component) is int for component in value)
    )


def _plain_finite_number(value: object) -> bool:
    if type(value) not in {int, float}:
        return False
    assert isinstance(value, (int, float))
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _nested_resource_issues(
    evidence: object | None,
) -> tuple[bool, bool]:
    """Return frame/ensemble nominal-type failures without invoking subtypes."""

    if type(evidence) is not ResourceAuthorityEvidence:
        return False, False
    result = evidence.trust_result
    if type(result) is not ProductionResourceTrustResult:
        return False, True
    if type(result.accepted) is not bool:
        return False, True
    if result.accepted is False:
        return False, False

    frame = result.frame
    frame_invalid = not (
        type(frame) is FrameRef
        and type(frame.frame_id) is int
        and _plain_finite_number(frame.captured_monotonic_s)
        and type(frame.width) is int
        and type(frame.height) is int
    )
    resources = result.resources
    if type(result.reason) is not str or type(resources) is not tuple:
        return frame_invalid, True
    ensemble_invalid = any(
        type(resource) is not ResourceState
        or type(resource.resource_id) is not str
        or type(resource.resource_type) is not str
        or (
            resource.available is not None
            and type(resource.available) is not bool
        )
        or not _plain_finite_number(resource.confidence)
        or not _plain_region(resource.interaction_region)
        for resource in resources
    )
    return frame_invalid, ensemble_invalid


@final
@dataclass(frozen=True, slots=True)
class ConstrainedV1SameCycleDenial:
    """Sealed denial projection with no action-bearing evidence attached."""

    provenance: PerceptionCycleProvenance
    blockers: tuple[AuthorityBlocker, ...]
    resource_shape_valid: bool
    inventory_shape_valid: bool
    schema_version: int = field(
        default=CONSTRAINED_V1_SAME_CYCLE_SCHEMA_VERSION,
        init=False,
    )

    def __init_subclass__(cls) -> None:
        raise TypeError("ConstrainedV1SameCycleDenial is sealed")

    def __post_init__(self) -> None:
        if type(self.provenance) is not PerceptionCycleProvenance:
            raise ValueError("provenance must be an exact PerceptionCycleProvenance")
        if type(self.blockers) is not tuple or not self.blockers:
            raise ValueError("blockers must be a non-empty exact tuple")
        if any(type(blocker) is not AuthorityBlocker for blocker in self.blockers):
            raise ValueError("blockers must contain exact AuthorityBlocker values")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        if not _REQUIRED_SOURCE_BLOCKERS.issubset(self.blockers):
            raise ValueError("same-cycle denial is missing a source-owned blocker")
        if not isinstance(self.resource_shape_valid, bool):
            raise ValueError("resource_shape_valid must be a boolean")
        if not isinstance(self.inventory_shape_valid, bool):
            raise ValueError("inventory_shape_valid must be a boolean")

    @property
    def resources(self) -> tuple[ResourceState, ...]:
        """Return the exact canonical quartet with no definitive state/region."""

        return tuple(
            ResourceState(
                resource_id=resource_id,
                resource_type="iron",
                available=None,
                confidence=0.0,
                interaction_region=None,
            )
            for resource_id in VARROCK_EAST_IRON_RESOURCE_IDS
        )

    @property
    def inventory(self) -> InventoryState:
        return InventoryState(
            occupied_slots=None,
            capacity=_INVENTORY_CAPACITY,
            confidence=0.0,
        )

    @property
    def actionable_target_ids(self) -> tuple[str, ...]:
        return ()

    @property
    def resource_release_bound(self) -> bool:
        return False

    @property
    def inventory_release_bound(self) -> bool:
        return False

    @property
    def activation_allowed(self) -> bool:
        return False

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


def prepare_constrained_v1_same_cycle_denial(
    *,
    current_provenance: PerceptionCycleProvenance,
    resource_evidence: object | None,
    inventory_evidence: object | None,
    evaluated_monotonic_s: object,
) -> ConstrainedV1SameCycleDenial:
    """Project frozen same-cycle checks into an always non-activating result.

    The frozen PR #38 boundary owns the baseline identity, freshness, shape,
    and source-blocker decisions. This wrapper additionally rejects malformed
    nested trust-receipt and ensemble types before delegation, forwards only
    safe diagnostics, and never retains supplied evidence or potentially
    actionable regions.
    """

    (
        nested_resource_frame_invalid,
        nested_resource_ensemble_invalid,
    ) = _nested_resource_issues(resource_evidence)
    nested_resource_invalid = (
        nested_resource_frame_invalid or nested_resource_ensemble_invalid
    )
    snapshot = prepare_constrained_v1_perception_snapshot(
        current_provenance=current_provenance,
        resource_evidence=(
            object() if nested_resource_invalid else resource_evidence
        ),
        inventory_evidence=inventory_evidence,
        evaluated_monotonic_s=evaluated_monotonic_s,
    )
    blockers = list(snapshot.blockers)
    resource_shape_valid = snapshot.resource_shape_valid
    if nested_resource_frame_invalid:
        resource_shape_valid = False
        if AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH not in blockers:
            blockers.append(AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH)
    if nested_resource_ensemble_invalid:
        resource_shape_valid = False
        if AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID not in blockers:
            blockers.append(AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID)
    return ConstrainedV1SameCycleDenial(
        provenance=snapshot.provenance,
        blockers=tuple(blockers),
        resource_shape_valid=resource_shape_valid,
        inventory_shape_valid=snapshot.inventory_shape_valid,
    )
