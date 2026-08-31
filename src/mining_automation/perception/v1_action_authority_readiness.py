"""Deny-only readiness contract for a future V1 perception snapshot.

This module is not imported by the application or controller.  It cannot
create an ActionIntent and deliberately grants no runtime authority.  Its
purpose is to freeze fail-closed test cases before a source-owned perception
snapshot factory and activation record exist.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "UnactivatedV1PerceptionEvidence",
    "V1ActionAuthorityReadiness",
    "assess_unactivated_v1_action_authority",
]


@dataclass(frozen=True, slots=True)
class UnactivatedV1PerceptionEvidence:
    """Facts a future source-owned snapshot factory must prove.

    Caller-supplied values never assert validation or grant authority.  They
    only make additional negative scenarios observable in deterministic tests.
    """

    inventory_label: str
    inventory_frame_id: int
    scene_frame_id: int
    resource_frame_ids: tuple[int, ...]
    scene_supported: bool
    resource_evidence_fresh: bool
    resource_identity_approved: bool
    resource_ensemble_complete: bool
    resource_states_definitive: bool
    interaction_regions_valid: bool

    def __post_init__(self) -> None:
        if self.inventory_label not in {"empty", "partial", "full", "unknown"}:
            raise ValueError("inventory_label is not a supported readiness label")
        frame_ids = (
            self.inventory_frame_id,
            self.scene_frame_id,
            *self.resource_frame_ids,
        )
        if any(type(frame_id) is not int for frame_id in frame_ids):
            raise TypeError("frame identities must be integers")
        if len(self.resource_frame_ids) != 4:
            raise ValueError("resource_frame_ids must describe all four resources")
        boolean_fields = (
            self.scene_supported,
            self.resource_evidence_fresh,
            self.resource_identity_approved,
            self.resource_ensemble_complete,
            self.resource_states_definitive,
            self.interaction_regions_valid,
        )
        if any(type(value) is not bool for value in boolean_fields):
            raise TypeError("readiness predicates must be booleans")


_SOURCE_OWNED_BLOCKERS = (
    "inventory_not_independently_approved",
    "production_activation_record_absent",
    "runtime_activation_not_implemented",
)


@dataclass(frozen=True, slots=True)
class V1ActionAuthorityReadiness:
    """Non-production readiness result with source-owned zero authority."""

    blocking_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not set(_SOURCE_OWNED_BLOCKERS).issubset(self.blocking_reasons):
            raise ValueError("readiness results must retain source-owned blockers")

    @property
    def future_snapshot_prerequisites_satisfied(self) -> bool:
        """Remain false until a separate reviewed factory replaces this module."""

        return False

    @property
    def mining_authority(self) -> bool:
        return False

    @property
    def banking_authority(self) -> bool:
        return False

    @property
    def click_authority(self) -> bool:
        return False

    @property
    def target_ids(self) -> tuple[str, ...]:
        return ()

    def to_dict(self) -> dict[str, object]:
        return {
            "banking_authority": False,
            "blocking_reasons": list(self.blocking_reasons),
            "click_authority": False,
            "future_snapshot_prerequisites_satisfied": False,
            "mining_authority": False,
            "runtime_activation_implemented": False,
            "target_ids": [],
        }


def assess_unactivated_v1_action_authority(
    evidence: UnactivatedV1PerceptionEvidence,
) -> V1ActionAuthorityReadiness:
    """Assess future prerequisites while always withholding present authority."""
    if not isinstance(evidence, UnactivatedV1PerceptionEvidence):
        raise TypeError("evidence must be UnactivatedV1PerceptionEvidence")
    reasons: list[str] = list(_SOURCE_OWNED_BLOCKERS)
    if evidence.inventory_label == "unknown":
        reasons.append("inventory_unknown")
    if not evidence.scene_supported:
        reasons.append("scene_not_supported")
    all_frame_ids = (
        evidence.inventory_frame_id,
        evidence.scene_frame_id,
        *evidence.resource_frame_ids,
    )
    if (
        any(frame_id < 1 for frame_id in all_frame_ids)
        or len(set(all_frame_ids)) != 1
    ):
        reasons.append("mixed_or_invalid_frame_identity")
    if not evidence.resource_evidence_fresh:
        reasons.append("resource_evidence_stale")
    if not evidence.resource_identity_approved:
        reasons.append("resource_identity_not_approved")
    if not evidence.resource_ensemble_complete:
        reasons.append("resource_ensemble_incomplete")
    if not evidence.resource_states_definitive:
        reasons.append("resource_states_not_definitive")
    if not evidence.interaction_regions_valid:
        reasons.append("interaction_regions_not_validated")

    return V1ActionAuthorityReadiness(
        blocking_reasons=tuple(reasons),
    )
