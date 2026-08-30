"""Read-only evidence planner for the Issue #31 camera bridge experiment.

The planner ranks already-measured endpoints and identifies one missing
experiment.  It does not execute input, infer control from a rejected
registration matrix, validate a scene, or expose resources.  A graph bridge is
reported only when an exact receipt-backed directed action is supported by an
accepted, cycle-verified, all-three-zone registration edge.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations
from typing import Final

from ..perception.scene_landmarks import MacroZone
from .camera_bridge_capture import (
    CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
    CAMERA_BRIDGE_CAPTURE_ID,
)
from .camera_plan import CameraHoldKey
from .robust_view_graph import (
    NEGATIVE_GRAPH_ROLES,
    GraphEdgeEvidence,
    GraphNodeEvidence,
    ReadOnlyViewGraph,
    ViewRole,
)

__all__ = [
    "CAMERA_BRIDGE_PLANNER_ID",
    "CAMERA_BRIDGE_PLANNER_VERSION",
    "FROZEN_ENDPOINT_FAMILY_ID",
    "FROZEN_ENDPOINT_OBJECTIVE_ID",
    "FROZEN_ENDPOINT_SOURCE_SHA256",
    "FROZEN_ENDPOINT_OBJECTIVE",
    "FROZEN_PRIMITIVE_INVENTORY",
    "BridgeExperimentRecommendation",
    "BridgePlannerDisposition",
    "CameraBridgePlannerEvidence",
    "EndpointAnchorEvaluation",
    "EndpointExclusion",
    "EndpointFamilyEvaluation",
    "FrozenPrimitiveExperiment",
    "FrozenPrimitiveInventory",
    "GraphDerivedEndpointEvidence",
    "MeasuredEndpointEvidence",
    "RankedEndpointFamily",
    "plan_camera_bridge",
]

CAMERA_BRIDGE_PLANNER_ID: Final[str] = "issue31-read-only-camera-bridge-planner-r2"
CAMERA_BRIDGE_PLANNER_VERSION: Final[str] = "2.1.0"
FROZEN_ENDPOINT_FAMILY_ID: Final[str] = "north-up-p610-y043-reset"
FROZEN_ENDPOINT_OBJECTIVE_ID: Final[str] = (
    "north-up-p610-y043-reset:right-key-hold-0.043s"
)
FROZEN_ENDPOINT_SOURCE_SHA256: Final[str] = (
    "c1cb6fe144600ce153b1ceb2e90d6e375d42babea1eda6a08120efbc7ed2a4cd"
)
_FROZEN_REPEAT_RECEIPTS: Final[tuple[str, str]] = (
    "1925996eb4f431f44a71abc6a33d5198707fc6173f0c81ec91ee4b350241547f",
    "a9a75ac611789b9f4d900261c63ad03210764b6db34d55ac883d700546de1dc5",
)
_REQUIRED_ZONES: Final[frozenset[MacroZone]] = frozenset(
    (MacroZone.NORTH_WEST, MacroZone.NORTH_EAST, MacroZone.SOUTH_WEST)
)


def _validate_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")


def _validate_digest(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _validate_non_negative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_unit_interval(value: float, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"{name} must be finite and in [0, 1]")


def _validate_optional_non_negative(value: float | None, name: str) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise ValueError(f"{name} must be None or finite and non-negative")


class BridgePlannerDisposition(StrEnum):
    """Read-only outcome of bridge evidence planning."""

    BRIDGE_EVIDENCE_AVAILABLE = "bridge_evidence_available"
    MISSING_EXPERIMENT = "missing_experiment"
    NO_SAFE_ENDPOINT_EVIDENCE = "no_safe_endpoint_evidence"


@dataclass(frozen=True, slots=True)
class FrozenPrimitiveExperiment:
    """One fixed experiment backed by immutable prior family receipts."""

    experiment_id: str
    family_id: str
    action_id: str
    ordinal: int
    key: CameraHoldKey
    duration_s: float
    required_source_sha256: str
    selection_backing_report_sha256s: tuple[str, ...]
    minimum_distinct_receipt_endpoints: int = 2

    def __post_init__(self) -> None:
        for value, name in (
            (self.experiment_id, "experiment_id"),
            (self.family_id, "family_id"),
            (self.action_id, "action_id"),
        ):
            _validate_text(value, name)
        if (
            isinstance(self.ordinal, bool)
            or not isinstance(self.ordinal, int)
            or self.ordinal < 1
        ):
            raise ValueError("ordinal must be a positive integer")
        if not isinstance(self.key, CameraHoldKey):
            raise TypeError("key must be a CameraHoldKey")
        _validate_digest(self.required_source_sha256, "required_source_sha256")
        if (
            isinstance(self.duration_s, bool)
            or not isinstance(self.duration_s, (int, float))
            or not math.isfinite(float(self.duration_s))
            or float(self.duration_s) <= 0.0
        ):
            raise ValueError("duration_s must be finite and positive")
        if (
            not isinstance(self.selection_backing_report_sha256s, tuple)
            or not self.selection_backing_report_sha256s
        ):
            raise ValueError("an experiment requires frozen selection receipts")
        for digest in self.selection_backing_report_sha256s:
            _validate_digest(digest, "selection backing report SHA-256")
        if len(set(self.selection_backing_report_sha256s)) != len(
            self.selection_backing_report_sha256s
        ):
            raise ValueError("selection backing report digests must be unique")
        if (
            isinstance(self.minimum_distinct_receipt_endpoints, bool)
            or not isinstance(self.minimum_distinct_receipt_endpoints, int)
            or self.minimum_distinct_receipt_endpoints < 2
        ):
            raise ValueError("a repeated family requires at least two endpoints")

    def as_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "duration_s": round(float(self.duration_s), 12),
            "experiment_id": self.experiment_id,
            "family_id": self.family_id,
            "key": self.key.value,
            "minimum_distinct_receipt_endpoints": (
                self.minimum_distinct_receipt_endpoints
            ),
            "ordinal": self.ordinal,
            "required_source_sha256": self.required_source_sha256,
            "selection_backing_report_sha256s": sorted(
                self.selection_backing_report_sha256s
            ),
        }


@dataclass(frozen=True, slots=True)
class FrozenPrimitiveInventory:
    """Immutable allow-list of receipt-backed bridge experiments."""

    inventory_id: str
    inventory_version: str
    experiments: tuple[FrozenPrimitiveExperiment, ...]

    def __post_init__(self) -> None:
        _validate_text(self.inventory_id, "inventory_id")
        _validate_text(self.inventory_version, "inventory_version")
        if not isinstance(self.experiments, tuple) or not self.experiments:
            raise ValueError("primitive inventory must be a non-empty tuple")
        if any(not isinstance(item, FrozenPrimitiveExperiment) for item in self.experiments):
            raise TypeError("inventory entries must be FrozenPrimitiveExperiment values")
        experiment_ids = [item.experiment_id for item in self.experiments]
        if len(experiment_ids) != len(set(experiment_ids)):
            raise ValueError("frozen experiment ids must be unique")
        family_ordinals = [(item.family_id, item.ordinal) for item in self.experiments]
        if len(family_ordinals) != len(set(family_ordinals)):
            raise ValueError("experiment ordinals must be unique within each family")

    def as_dict(self) -> dict[str, object]:
        return {
            "experiments": [
                item.as_dict()
                for item in sorted(
                    self.experiments,
                    key=lambda item: (item.family_id, item.ordinal, item.experiment_id),
                )
            ],
            "inventory_id": self.inventory_id,
            "inventory_version": self.inventory_version,
        }


FROZEN_ENDPOINT_OBJECTIVE: Final[FrozenPrimitiveExperiment] = (
    FrozenPrimitiveExperiment(
        experiment_id=FROZEN_ENDPOINT_OBJECTIVE_ID,
        family_id=FROZEN_ENDPOINT_FAMILY_ID,
        action_id=CAMERA_BRIDGE_CAPTURE_ID,
        ordinal=1,
        key=CameraHoldKey.RIGHT,
        duration_s=CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
        required_source_sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
        selection_backing_report_sha256s=_FROZEN_REPEAT_RECEIPTS,
    )
)
FROZEN_PRIMITIVE_INVENTORY: Final[FrozenPrimitiveInventory] = (
    FrozenPrimitiveInventory(
        inventory_id="issue31-frozen-receipt-backed-camera-primitives-r2",
        inventory_version="2.0.0",
        experiments=(FROZEN_ENDPOINT_OBJECTIVE,),
    )
)


@dataclass(frozen=True, slots=True)
class MeasuredEndpointEvidence:
    """Receipt endpoint identity plus legacy claims that the planner ignores.

    ``target_sha256``, the receipt report digest, and receipt authentication are
    the only caller fields used by planning. Roles, readiness, production, and
    registration evidence are always read from the exact graph node and edges.
    """

    evidence_id: str
    family_id: str
    experiment_id: str
    source_sha256: str
    target_sha256: str
    receipt_report_sha256: str
    receipt_verified: bool
    readiness_safe: bool
    target_roles: tuple[ViewRole, ...]
    production_passed: bool
    production_matched_landmarks: int
    production_matched_zones: tuple[MacroZone, ...]
    registration_accepted: bool
    registration_cycle_verified: bool
    registration_matched_zones: tuple[MacroZone, ...]
    mutual_matches: int
    inliers: int
    inlier_ratio: float
    median_residual_px: float | None
    p90_residual_px: float | None
    cycle_p90_px: float | None

    def __post_init__(self) -> None:
        for value, name in (
            (self.evidence_id, "evidence_id"),
            (self.family_id, "family_id"),
            (self.experiment_id, "experiment_id"),
        ):
            _validate_text(value, name)
        for value, name in (
            (self.source_sha256, "source_sha256"),
            (self.target_sha256, "target_sha256"),
            (self.receipt_report_sha256, "receipt_report_sha256"),
        ):
            _validate_digest(value, name)
        for boolean_value, name in (
            (self.receipt_verified, "receipt_verified"),
            (self.readiness_safe, "readiness_safe"),
            (self.production_passed, "production_passed"),
            (self.registration_accepted, "registration_accepted"),
            (self.registration_cycle_verified, "registration_cycle_verified"),
        ):
            if not isinstance(boolean_value, bool):
                raise TypeError(f"{name} must be a boolean")
        if not isinstance(self.target_roles, tuple) or any(
            not isinstance(role, ViewRole) for role in self.target_roles
        ):
            raise TypeError("target_roles must be a tuple of ViewRole values")
        for zones, name in (
            (self.production_matched_zones, "production_matched_zones"),
            (self.registration_matched_zones, "registration_matched_zones"),
        ):
            if not isinstance(zones, tuple) or any(
                not isinstance(zone, MacroZone) for zone in zones
            ):
                raise TypeError(f"{name} must be a tuple of MacroZone values")
            if len(zones) != len(set(zones)):
                raise ValueError(f"{name} must contain unique zones")
        for integer_value, name in (
            (self.production_matched_landmarks, "production_matched_landmarks"),
            (self.mutual_matches, "mutual_matches"),
            (self.inliers, "inliers"),
        ):
            _validate_non_negative_int(integer_value, name)
        _validate_unit_interval(self.inlier_ratio, "inlier_ratio")
        for optional_value, name in (
            (self.median_residual_px, "median_residual_px"),
            (self.p90_residual_px, "p90_residual_px"),
            (self.cycle_p90_px, "cycle_p90_px"),
        ):
            _validate_optional_non_negative(optional_value, name)
        if self.registration_cycle_verified and not self.registration_accepted:
            raise ValueError("cycle-verified endpoint evidence must be accepted")
        if self.inliers > self.mutual_matches:
            raise ValueError("inliers cannot exceed mutual matches")

    @property
    def negative_target(self) -> bool:
        return any(role in NEGATIVE_GRAPH_ROLES for role in self.target_roles)

    @property
    def registration_all_required_zones(self) -> bool:
        return _REQUIRED_ZONES.issubset(self.registration_matched_zones)

    @property
    def endpoint_sha256(self) -> str:
        """Return the exact graph-node identity bound by this record."""

        return self.target_sha256

    def as_dict(self) -> dict[str, object]:
        return {
            "cycle_p90_px": _rounded_optional(self.cycle_p90_px),
            "evidence_id": self.evidence_id,
            "experiment_id": self.experiment_id,
            "family_id": self.family_id,
            "inlier_ratio": round(float(self.inlier_ratio), 12),
            "inliers": self.inliers,
            "median_residual_px": _rounded_optional(self.median_residual_px),
            "mutual_matches": self.mutual_matches,
            "negative_target": self.negative_target,
            "p90_residual_px": _rounded_optional(self.p90_residual_px),
            "production_matched_landmarks": self.production_matched_landmarks,
            "production_matched_zones": sorted(
                zone.value for zone in self.production_matched_zones
            ),
            "production_passed": self.production_passed,
            "readiness_safe": self.readiness_safe,
            "receipt_report_sha256": self.receipt_report_sha256,
            "receipt_verified": self.receipt_verified,
            "registration_accepted": self.registration_accepted,
            "registration_all_required_zones": (
                self.registration_all_required_zones
            ),
            "registration_cycle_verified": self.registration_cycle_verified,
            "registration_matched_zones": sorted(
                zone.value for zone in self.registration_matched_zones
            ),
            "source_sha256": self.source_sha256,
            "target_roles": sorted(role.value for role in self.target_roles),
            "target_sha256": self.target_sha256,
        }


@dataclass(frozen=True, slots=True)
class GraphDerivedEndpointEvidence:
    """Trusted endpoint facts derived exclusively from one graph node."""

    evidence_id: str
    family_id: str
    endpoint_sha256: str
    receipt_report_sha256: str
    labels: tuple[str, ...]
    roles: tuple[ViewRole, ...]
    readiness_safe: bool
    production_passed: bool
    production_scene_validated: bool
    production_matched_landmarks: int
    production_matched_zones: tuple[MacroZone, ...]
    production_definitive_target_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "endpoint_sha256": self.endpoint_sha256,
            "evidence_id": self.evidence_id,
            "family_id": self.family_id,
            "graph_node": {
                "labels": list(self.labels),
                "production": {
                    "definitive_target_ids": list(
                        self.production_definitive_target_ids
                    ),
                    "matched_landmarks": self.production_matched_landmarks,
                    "matched_zones": sorted(
                        zone.value for zone in self.production_matched_zones
                    ),
                    "passed": self.production_passed,
                    "scene_validated": self.production_scene_validated,
                },
                "readiness_safe": self.readiness_safe,
                "roles": sorted(role.value for role in self.roles),
            },
            "receipt_report_sha256": self.receipt_report_sha256,
        }


@dataclass(frozen=True, slots=True)
class EndpointExclusion:
    """Fail-closed exclusion of one supplied endpoint record."""

    evidence_id: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"evidence_id": self.evidence_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class EndpointAnchorEvaluation:
    """Per-anchor audit of one endpoint family's all-zone graph coverage."""

    anchor_sha256: str
    verified_edge_ids: tuple[str, ...]
    missing_edge_ids: tuple[str, ...]
    complete: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "anchor_sha256": self.anchor_sha256,
            "complete": self.complete,
            "missing_edge_ids": list(self.missing_edge_ids),
            "verified_edge_ids": list(self.verified_edge_ids),
        }


@dataclass(frozen=True, slots=True)
class EndpointFamilyEvaluation:
    """Graph-derived qualification of one repeated endpoint family."""

    family_id: str
    required_distinct_endpoints: int
    endpoints: tuple[GraphDerivedEndpointEvidence, ...]
    distinct_endpoint_sha256s: tuple[str, ...]
    distinct_receipt_report_sha256s: tuple[str, ...]
    frozen_anchor_sha256s: tuple[str, ...]
    repeat_edge_ids: tuple[str, ...]
    anchor_edge_ids: tuple[str, ...]
    anchor_evaluations: tuple[EndpointAnchorEvaluation, ...]
    qualifying_common_anchor_sha256s: tuple[str, ...]
    complete: bool
    failure_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "anchor_edge_ids": list(self.anchor_edge_ids),
            "anchor_evaluations": [
                item.as_dict() for item in self.anchor_evaluations
            ],
            "complete": self.complete,
            "distinct_endpoint_sha256s": list(self.distinct_endpoint_sha256s),
            "distinct_receipt_report_sha256s": list(
                self.distinct_receipt_report_sha256s
            ),
            "endpoints": [item.as_dict() for item in self.endpoints],
            "failure_reasons": list(self.failure_reasons),
            "family_id": self.family_id,
            "frozen_anchor_sha256s": list(self.frozen_anchor_sha256s),
            "repeat_edge_ids": list(self.repeat_edge_ids),
            "qualifying_common_anchor_sha256s": list(
                self.qualifying_common_anchor_sha256s
            ),
            "required_distinct_endpoints": self.required_distinct_endpoints,
        }


@dataclass(frozen=True, slots=True)
class RankedEndpointFamily:
    """Stable rank of a fully graph-qualified repeated endpoint family."""

    rank: int
    family_id: str
    evidence_ids: tuple[str, ...]
    evaluation: EndpointFamilyEvaluation

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence_count": len(self.evidence_ids),
            "evidence_ids": list(self.evidence_ids),
            "evaluation": self.evaluation.as_dict(),
            "family_id": self.family_id,
            "rank": self.rank,
            "ranking_basis": (
                "verified endpoint count then deterministic family id; "
                "caller registration metrics are ignored"
            ),
        }


@dataclass(frozen=True, slots=True)
class BridgeExperimentRecommendation:
    """One read-only missing experiment; this is never input authorization."""

    experiment_id: str
    family_id: str
    action_id: str
    source_sha256: str
    key: CameraHoldKey
    duration_s: float
    receipt_backing_sha256s: tuple[str, ...]
    ranked_endpoint_evidence_ids: tuple[str, ...]
    uses_rejected_registration_matrix: bool = field(default=False, init=False)
    can_execute_input: bool = field(default=False, init=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "can_execute_input": self.can_execute_input,
            "duration_s": round(float(self.duration_s), 12),
            "experiment_id": self.experiment_id,
            "family_id": self.family_id,
            "key": self.key.value,
            "ranked_endpoint_evidence_ids": list(
                self.ranked_endpoint_evidence_ids
            ),
            "receipt_backing_sha256s": sorted(self.receipt_backing_sha256s),
            "source_sha256": self.source_sha256,
            "uses_rejected_registration_matrix": (
                self.uses_rejected_registration_matrix
            ),
        }


@dataclass(frozen=True, slots=True)
class CameraBridgePlannerEvidence:
    """Canonical read-only bridge planning evidence."""

    planner_id: str
    planner_version: str
    disposition: BridgePlannerDisposition
    current_sha256: str
    current_safe_component: tuple[str, ...]
    frozen_anchor_sha256s: tuple[str, ...]
    quarantined_sha256s: tuple[str, ...]
    inventory: FrozenPrimitiveInventory
    family_evaluations: tuple[EndpointFamilyEvaluation, ...]
    ranked_families: tuple[RankedEndpointFamily, ...]
    excluded_endpoint_evidence_ids: tuple[str, ...]
    excluded_endpoints: tuple[EndpointExclusion, ...]
    bridge_node_path: tuple[str, ...] | None
    bridge_action_ids: tuple[str, ...]
    bridge_report_sha256s: tuple[str, ...]
    missing_experiment: BridgeExperimentRecommendation | None
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)
    can_authorize_camera_input: bool = field(default=False, init=False)
    diagnostic_registration_can_override_production: bool = field(
        default=False, init=False
    )

    @property
    def bridge_evidence_available(self) -> bool:
        return self.bridge_node_path is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "authority": {
                "can_accept": self.can_accept,
                "can_authorize_camera_input": self.can_authorize_camera_input,
                "can_expose_resources": self.can_expose_resources,
                "can_validate_scene": self.can_validate_scene,
                "diagnostic_registration_can_override_production": (
                    self.diagnostic_registration_can_override_production
                ),
            },
            "bridge": {
                "action_ids": list(self.bridge_action_ids),
                "evidence_available": self.bridge_evidence_available,
                "node_path": (
                    None
                    if self.bridge_node_path is None
                    else list(self.bridge_node_path)
                ),
                "report_sha256s": list(self.bridge_report_sha256s),
            },
            "current_safe_component": list(self.current_safe_component),
            "current_sha256": self.current_sha256,
            "disposition": self.disposition.value,
            "excluded_endpoint_evidence_ids": list(
                self.excluded_endpoint_evidence_ids
            ),
            "excluded_endpoints": [
                item.as_dict() for item in self.excluded_endpoints
            ],
            "family_evaluations": [
                item.as_dict() for item in self.family_evaluations
            ],
            "frozen_anchor_sha256s": list(self.frozen_anchor_sha256s),
            "inventory": self.inventory.as_dict(),
            "matrix_policy": {
                "rejected_registration_matrices_used_for_control": False,
                "rejected_registration_metrics_used_for_ranking": False,
            },
            "missing_experiment": (
                None
                if self.missing_experiment is None
                else self.missing_experiment.as_dict()
            ),
            "planner_id": self.planner_id,
            "planner_version": self.planner_version,
            "quarantined_sha256s": list(self.quarantined_sha256s),
            "ranked_families": [item.as_dict() for item in self.ranked_families],
        }


def _experiments_by_family(
    inventory: FrozenPrimitiveInventory,
) -> dict[str, tuple[FrozenPrimitiveExperiment, ...]]:
    result: dict[str, tuple[FrozenPrimitiveExperiment, ...]] = {}
    for family_id in sorted({item.family_id for item in inventory.experiments}):
        result[family_id] = tuple(
            sorted(
                (
                    item
                    for item in inventory.experiments
                    if item.family_id == family_id
                ),
                key=lambda item: (item.ordinal, item.experiment_id),
            )
        )
    return result


def _derive_endpoint(
    record: MeasuredEndpointEvidence,
    node: GraphNodeEvidence,
) -> GraphDerivedEndpointEvidence:
    production = node.production
    return GraphDerivedEndpointEvidence(
        evidence_id=record.evidence_id,
        family_id=record.family_id,
        endpoint_sha256=node.sha256,
        receipt_report_sha256=record.receipt_report_sha256,
        labels=tuple(sorted(node.labels)),
        roles=tuple(sorted(node.roles, key=str)),
        readiness_safe=node.registration_eligible,
        production_passed=production.passed,
        production_scene_validated=production.scene_validated,
        production_matched_landmarks=production.matched_landmark_count,
        production_matched_zones=tuple(sorted(production.matched_zones, key=str)),
        production_definitive_target_ids=tuple(
            sorted(production.definitive_target_ids)
        ),
    )


def _bind_endpoint_records(
    records: tuple[MeasuredEndpointEvidence, ...],
    *,
    node_by_sha: dict[str, GraphNodeEvidence],
    experiments_by_family: dict[str, tuple[FrozenPrimitiveExperiment, ...]],
    quarantined: frozenset[str],
) -> tuple[tuple[GraphDerivedEndpointEvidence, ...], tuple[EndpointExclusion, ...]]:
    derived: list[GraphDerivedEndpointEvidence] = []
    excluded: list[EndpointExclusion] = []
    for record in sorted(records, key=lambda item: item.evidence_id):
        experiments = experiments_by_family.get(record.family_id)
        node = node_by_sha.get(record.endpoint_sha256)
        reason = None
        if experiments is None:
            reason = "unknown_family"
        elif not record.receipt_verified:
            reason = "receipt_not_verified"
        elif record.receipt_report_sha256 not in {
            digest
            for experiment in experiments
            for digest in experiment.selection_backing_report_sha256s
        }:
            reason = "receipt_not_frozen_selection_backing"
        elif node is None:
            reason = "endpoint_not_in_graph"
        elif record.endpoint_sha256 in quarantined:
            reason = "endpoint_negative_graph_role"
        elif not node.registration_eligible:
            reason = "endpoint_readiness_veto"
        elif (
            node.production.passed
            or node.production.scene_validated
            or bool(node.production.definitive_target_ids)
        ):
            reason = "endpoint_not_production_fail_closed"
        if reason is not None:
            excluded.append(EndpointExclusion(record.evidence_id, reason))
            continue
        if node is None:
            raise AssertionError("accepted endpoint must bind an exact graph node")
        derived.append(_derive_endpoint(record, node))
    return tuple(derived), tuple(excluded)


def _evaluate_endpoint_families(
    experiments_by_family: dict[str, tuple[FrozenPrimitiveExperiment, ...]],
    derived: tuple[GraphDerivedEndpointEvidence, ...],
    *,
    anchors: frozenset[str],
    edge_by_id: dict[str, GraphEdgeEvidence],
    graph: ReadOnlyViewGraph,
) -> tuple[EndpointFamilyEvaluation, ...]:
    records_by_family: dict[str, list[GraphDerivedEndpointEvidence]] = defaultdict(
        list
    )
    for item in derived:
        records_by_family[item.family_id].append(item)
    evaluations: list[EndpointFamilyEvaluation] = []
    for family_id, experiments in sorted(experiments_by_family.items()):
        records = tuple(
            sorted(
                records_by_family.get(family_id, ()),
                key=lambda item: (
                    item.endpoint_sha256,
                    item.receipt_report_sha256,
                    item.evidence_id,
                ),
            )
        )
        endpoint_sha256s = tuple(sorted({item.endpoint_sha256 for item in records}))
        report_sha256s = tuple(
            sorted({item.receipt_report_sha256 for item in records})
        )
        minimum = max(
            item.minimum_distinct_receipt_endpoints for item in experiments
        )
        failures: list[str] = []
        if len(endpoint_sha256s) < minimum:
            failures.append(
                f"insufficient_distinct_endpoint_nodes:{len(endpoint_sha256s)}/{minimum}"
            )
        if len(report_sha256s) < minimum:
            failures.append(
                f"insufficient_distinct_receipt_reports:{len(report_sha256s)}/{minimum}"
            )
        if not anchors:
            failures.append("no_frozen_supported_anchors")
        repeat_edges: list[str] = []
        for first, second in combinations(endpoint_sha256s, 2):
            edge_id = _edge_id(first, second)
            edge = edge_by_id.get(edge_id)
            if edge is None or not _edge_is_all_zone_bridge(edge, graph):
                failures.append(f"repeat_edge_not_verified_all_zones:{edge_id}")
            else:
                repeat_edges.append(edge_id)
        anchor_edges: list[str] = []
        anchor_evaluations: list[EndpointAnchorEvaluation] = []
        qualifying_common_anchors: list[str] = []
        for anchor_sha256 in sorted(anchors):
            verified_for_anchor: list[str] = []
            missing_for_anchor: list[str] = []
            for endpoint_sha256 in endpoint_sha256s:
                if endpoint_sha256 == anchor_sha256:
                    missing_for_anchor.append(
                        f"endpoint_is_frozen_supported_anchor:{endpoint_sha256}"
                    )
                    continue
                edge_id = _edge_id(endpoint_sha256, anchor_sha256)
                edge = edge_by_id.get(edge_id)
                if edge is None or not _edge_is_all_zone_bridge(edge, graph):
                    missing_for_anchor.append(edge_id)
                else:
                    anchor_edges.append(edge_id)
                    verified_for_anchor.append(edge_id)
            anchor_complete = (
                len(endpoint_sha256s) >= minimum and not missing_for_anchor
            )
            if anchor_complete:
                qualifying_common_anchors.append(anchor_sha256)
            anchor_evaluations.append(
                EndpointAnchorEvaluation(
                    anchor_sha256=anchor_sha256,
                    verified_edge_ids=tuple(sorted(verified_for_anchor)),
                    missing_edge_ids=tuple(sorted(missing_for_anchor)),
                    complete=anchor_complete,
                )
            )
        if anchors and not qualifying_common_anchors:
            failures.append("no_common_supported_anchor_all_zones")
        unique_failures = tuple(sorted(set(failures)))
        evaluations.append(
            EndpointFamilyEvaluation(
                family_id=family_id,
                required_distinct_endpoints=minimum,
                endpoints=records,
                distinct_endpoint_sha256s=endpoint_sha256s,
                distinct_receipt_report_sha256s=report_sha256s,
                frozen_anchor_sha256s=tuple(sorted(anchors)),
                repeat_edge_ids=tuple(sorted(repeat_edges)),
                anchor_edge_ids=tuple(sorted(anchor_edges)),
                anchor_evaluations=tuple(anchor_evaluations),
                qualifying_common_anchor_sha256s=tuple(
                    qualifying_common_anchors
                ),
                complete=not unique_failures,
                failure_reasons=unique_failures,
            )
        )
    return tuple(evaluations)


def plan_camera_bridge(
    graph: ReadOnlyViewGraph,
    endpoint_evidence: tuple[MeasuredEndpointEvidence, ...],
    *,
    inventory: FrozenPrimitiveInventory = FROZEN_PRIMITIVE_INVENTORY,
) -> CameraBridgePlannerEvidence:
    """Return deterministic bridge evidence without authorizing any input."""

    if not isinstance(graph, ReadOnlyViewGraph):
        raise TypeError("graph must be a ReadOnlyViewGraph")
    if not isinstance(endpoint_evidence, tuple) or any(
        not isinstance(item, MeasuredEndpointEvidence) for item in endpoint_evidence
    ):
        raise TypeError("endpoint_evidence must be a tuple of measured endpoints")
    if not isinstance(inventory, FrozenPrimitiveInventory):
        raise TypeError("inventory must be a FrozenPrimitiveInventory")
    evidence_ids = [item.evidence_id for item in endpoint_evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("endpoint evidence ids must be unique")

    node_by_sha = {node.sha256: node for node in graph.nodes}
    quarantined = frozenset(
        node.sha256
        for node in graph.nodes
        if any(role in NEGATIVE_GRAPH_ROLES for role in node.roles)
    )
    current_component = _current_safe_component(graph, quarantined)
    anchors = frozenset(
        node.sha256
        for node in graph.nodes
        if node.reviewed_supported_anchor
        and node.registration_eligible
        and node.sha256 not in quarantined
    )
    family_experiments = _experiments_by_family(inventory)
    derived, excluded = _bind_endpoint_records(
        endpoint_evidence,
        node_by_sha=node_by_sha,
        experiments_by_family=family_experiments,
        quarantined=quarantined,
    )
    edge_by_id = {edge.edge_id: edge for edge in graph.edges}
    family_evaluations = _evaluate_endpoint_families(
        family_experiments,
        derived,
        anchors=anchors,
        edge_by_id=edge_by_id,
        graph=graph,
    )
    complete = [item for item in family_evaluations if item.complete]
    complete.sort(
        key=lambda item: (-len(item.distinct_endpoint_sha256s), item.family_id)
    )
    ranked = tuple(
        RankedEndpointFamily(
            rank=index,
            family_id=evaluation.family_id,
            evidence_ids=tuple(
                sorted(item.evidence_id for item in evaluation.endpoints)
            ),
            evaluation=evaluation,
        )
        for index, evaluation in enumerate(complete, start=1)
    )
    bridge_path, bridge_actions, bridge_reports = _receipt_backed_bridge_path(
        graph,
        current_component=frozenset(current_component),
        anchors=anchors,
        quarantined=quarantined,
        inventory=inventory,
    )
    missing = None
    if bridge_path is None and current_component and ranked:
        family = ranked[0]
        objective = family_experiments[family.family_id][0]
        if graph.current_sha256 == objective.required_source_sha256:
            missing = BridgeExperimentRecommendation(
                experiment_id=objective.experiment_id,
                family_id=objective.family_id,
                action_id=objective.action_id,
                source_sha256=graph.current_sha256,
                key=objective.key,
                duration_s=objective.duration_s,
                receipt_backing_sha256s=(
                    objective.selection_backing_report_sha256s
                ),
                ranked_endpoint_evidence_ids=family.evidence_ids,
            )

    if bridge_path is not None:
        disposition = BridgePlannerDisposition.BRIDGE_EVIDENCE_AVAILABLE
    elif missing is not None:
        disposition = BridgePlannerDisposition.MISSING_EXPERIMENT
    else:
        disposition = BridgePlannerDisposition.NO_SAFE_ENDPOINT_EVIDENCE
    return CameraBridgePlannerEvidence(
        planner_id=CAMERA_BRIDGE_PLANNER_ID,
        planner_version=CAMERA_BRIDGE_PLANNER_VERSION,
        disposition=disposition,
        current_sha256=graph.current_sha256,
        current_safe_component=current_component,
        frozen_anchor_sha256s=tuple(sorted(anchors)),
        quarantined_sha256s=tuple(sorted(quarantined)),
        inventory=inventory,
        family_evaluations=family_evaluations,
        ranked_families=ranked,
        excluded_endpoint_evidence_ids=tuple(
            item.evidence_id for item in excluded
        ),
        excluded_endpoints=excluded,
        bridge_node_path=bridge_path,
        bridge_action_ids=bridge_actions,
        bridge_report_sha256s=bridge_reports,
        missing_experiment=missing,
    )


def _current_safe_component(
    graph: ReadOnlyViewGraph,
    quarantined: frozenset[str],
) -> tuple[str, ...]:
    safe_nodes = frozenset(
        node.sha256
        for node in graph.nodes
        if node.registration_eligible and node.sha256 not in quarantined
    )
    if graph.current_sha256 not in safe_nodes:
        return ()
    for component in graph.components:
        if graph.current_sha256 in component:
            return tuple(sorted(set(component).intersection(safe_nodes)))
    return ()


def _receipt_backed_bridge_path(
    graph: ReadOnlyViewGraph,
    *,
    current_component: frozenset[str],
    anchors: frozenset[str],
    quarantined: frozenset[str],
    inventory: FrozenPrimitiveInventory,
) -> tuple[tuple[str, ...] | None, tuple[str, ...], tuple[str, ...]]:
    if graph.current_sha256 not in current_component:
        return None, (), ()
    allowed_sources_by_action: dict[str, set[str]] = defaultdict(set)
    for experiment in inventory.experiments:
        allowed_sources_by_action[experiment.action_id].add(
            experiment.required_source_sha256
        )
    edge_by_id = {edge.edge_id: edge for edge in graph.edges}
    safe_nodes = frozenset(
        node.sha256
        for node in graph.nodes
        if node.registration_eligible and node.sha256 not in quarantined
    )
    visual_adjacency = _verified_visual_adjacency(
        graph,
        edge_by_id=edge_by_id,
        safe_nodes=safe_nodes,
    )
    candidates: list[
        tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
    ] = []
    for transition in sorted(
        graph.action_transitions,
        key=lambda item: (
            item.source_sha256,
            item.target_sha256,
            item.action_id,
            item.evidence_report_sha256,
        ),
    ):
        allowed_sources = allowed_sources_by_action.get(transition.action_id, set())
        if (
            transition.source_sha256 not in allowed_sources
            or not transition.receipt_verified
            or transition.source_sha256 != graph.current_sha256
            or transition.source_sha256 not in current_component
            or transition.source_sha256 not in safe_nodes
            or transition.target_sha256 not in safe_nodes
        ):
            continue
        edge = edge_by_id.get(
            _edge_id(transition.source_sha256, transition.target_sha256)
        )
        if (
            edge is None
            or transition.action_id not in edge.action_ids
            or frozenset((edge.source_sha256, edge.target_sha256))
            != frozenset((transition.source_sha256, transition.target_sha256))
            or not _edge_is_all_zone_bridge(edge, graph)
        ):
            continue
        prefix = _shortest_visual_path(
            visual_adjacency,
            graph.current_sha256,
            frozenset((transition.source_sha256,)),
        )
        terminal = _shortest_visual_path(
            visual_adjacency,
            transition.target_sha256,
            anchors,
        )
        if prefix is None or terminal is None:
            continue
        candidates.append(
            (
                (*prefix, transition.target_sha256, *terminal[1:]),
                (transition.action_id,),
                (transition.evidence_report_sha256,),
            )
        )
    if not candidates:
        return None, (), ()
    candidates.sort(key=lambda item: (len(item[0]), item))
    return candidates[0]


def _verified_visual_adjacency(
    graph: ReadOnlyViewGraph,
    *,
    edge_by_id: dict[str, GraphEdgeEvidence],
    safe_nodes: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edge_by_id.values():
        if (
            edge.source_sha256 in safe_nodes
            and edge.target_sha256 in safe_nodes
            and _edge_is_all_zone_bridge(edge, graph)
        ):
            adjacency[edge.source_sha256].append(edge.target_sha256)
            adjacency[edge.target_sha256].append(edge.source_sha256)
    return {
        source: tuple(sorted(set(targets)))
        for source, targets in sorted(adjacency.items())
    }


def _shortest_visual_path(
    adjacency: dict[str, tuple[str, ...]],
    source: str,
    targets: frozenset[str],
) -> tuple[str, ...] | None:
    if source in targets:
        return (source,)
    queue: deque[tuple[str, tuple[str, ...]]] = deque(((source, (source,)),))
    visited = {source}
    while queue:
        node, path = queue.popleft()
        for target in adjacency.get(node, ()):
            if target in visited:
                continue
            next_path = (*path, target)
            if target in targets:
                return next_path
            visited.add(target)
            queue.append((target, next_path))
    return None


def _edge_is_all_zone_bridge(
    edge: GraphEdgeEvidence,
    graph: ReadOnlyViewGraph,
) -> bool:
    if not edge.registration_accepted or not edge.verified(graph.policy):
        return False
    registration = edge.registration
    if registration is None or registration.selected_model is None:
        return False
    if not _REQUIRED_ZONES.issubset(registration.required_zones):
        return False
    model = registration.selected_model
    policy = registration.policy
    source_inliers = dict(model.source_zone_inliers)
    target_inliers = dict(model.target_zone_inliers)
    source_cells = dict(model.source_zone_cells)
    target_cells = dict(model.target_zone_cells)
    return all(
        source_inliers.get(zone, 0) >= policy.minimum_inliers_per_zone
        and target_inliers.get(zone, 0) >= policy.minimum_inliers_per_zone
        and source_cells.get(zone, 0) >= policy.minimum_spatial_cells_per_zone
        and target_cells.get(zone, 0) >= policy.minimum_spatial_cells_per_zone
        for zone in _REQUIRED_ZONES
    )


def _edge_id(first: str, second: str) -> str:
    source, target = sorted((first, second))
    return f"{source}:{target}"


def _rounded_optional(value: float | None) -> float | None:
    return None if value is None else round(float(value), 12)
