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
    "FROZEN_ENDPOINT_OBJECTIVE",
    "FROZEN_PRIMITIVE_INVENTORY",
    "BridgeExperimentRecommendation",
    "BridgePlannerDisposition",
    "CameraBridgePlannerEvidence",
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
CAMERA_BRIDGE_PLANNER_VERSION: Final[str] = "2.0.0"
FROZEN_ENDPOINT_FAMILY_ID: Final[str] = "north-up-p610-y043-reset"
FROZEN_ENDPOINT_OBJECTIVE_ID: Final[str] = (
    "north-up-p610-y043-reset:right-key-hold-0.043s"
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
    """Caller-supplied scalar endpoint evidence; matrices are unrepresentable."""

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
class RankedEndpointFamily:
    """Stable family rank using only scalar measured endpoint evidence."""

    rank: int
    family_id: str
    evidence_ids: tuple[str, ...]
    best_evidence: MeasuredEndpointEvidence

    def as_dict(self) -> dict[str, object]:
        return {
            "best_evidence": self.best_evidence.as_dict(),
            "evidence_count": len(self.evidence_ids),
            "evidence_ids": list(self.evidence_ids),
            "family_id": self.family_id,
            "rank": self.rank,
        }


@dataclass(frozen=True, slots=True)
class BridgeExperimentRecommendation:
    """One read-only missing experiment; this is never input authorization."""

    experiment_id: str
    family_id: str
    source_sha256: str
    key: CameraHoldKey
    duration_s: float
    receipt_backing_sha256s: tuple[str, ...]
    ranked_endpoint_evidence_ids: tuple[str, ...]
    uses_rejected_registration_matrix: bool = field(default=False, init=False)
    can_execute_input: bool = field(default=False, init=False)

    def as_dict(self) -> dict[str, object]:
        return {
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
    ranked_families: tuple[RankedEndpointFamily, ...]
    excluded_endpoint_evidence_ids: tuple[str, ...]
    bridge_node_path: tuple[str, ...] | None
    bridge_action_ids: tuple[str, ...]
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
            },
            "current_safe_component": list(self.current_safe_component),
            "current_sha256": self.current_sha256,
            "disposition": self.disposition.value,
            "excluded_endpoint_evidence_ids": list(
                self.excluded_endpoint_evidence_ids
            ),
            "frozen_anchor_sha256s": list(self.frozen_anchor_sha256s),
            "inventory": self.inventory.as_dict(),
            "matrix_policy": {
                "rejected_registration_matrices_used_for_control": False,
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
    bridge_path, bridge_actions = _receipt_backed_bridge_path(
        graph,
        current_component=frozenset(current_component),
        anchors=anchors,
        quarantined=quarantined,
        inventory=inventory,
    )

    family_experiments: dict[str, tuple[FrozenPrimitiveExperiment, ...]] = {}
    for family_id in sorted({item.family_id for item in inventory.experiments}):
        family_experiments[family_id] = tuple(
            sorted(
                (
                    item
                    for item in inventory.experiments
                    if item.family_id == family_id
                ),
                key=lambda item: (item.ordinal, item.experiment_id),
            )
        )
    safe_evidence: list[MeasuredEndpointEvidence] = []
    excluded: list[str] = []
    for item in endpoint_evidence:
        objectives = family_experiments.get(item.family_id, ())
        receipt_digests = {
            digest
            for objective in objectives
            for digest in objective.receipt_report_sha256s
        }
        target_node = node_by_sha.get(item.target_sha256)
        target_quarantined = (
            item.target_sha256 in quarantined
            or item.negative_target
            or (
                target_node is not None
                and any(
                    role in NEGATIVE_GRAPH_ROLES for role in target_node.roles
                )
            )
        )
        eligible = (
            bool(objectives)
            and item.source_sha256 in current_component
            and item.receipt_verified
            and item.readiness_safe
            and item.receipt_report_sha256 in receipt_digests
            and not target_quarantined
        )
        if eligible:
            safe_evidence.append(item)
        else:
            excluded.append(item.evidence_id)

    ranked = _rank_endpoint_families(tuple(safe_evidence))
    missing = None
    if bridge_path is None:
        for family in ranked:
            objectives = family_experiments[family.family_id]
            completed = {
                item.experiment_id
                for item in safe_evidence
                if item.family_id == family.family_id
            }
            objective = next(
                (item for item in objectives if item.experiment_id not in completed),
                None,
            )
            if objective is not None:
                missing = BridgeExperimentRecommendation(
                    experiment_id=objective.experiment_id,
                    family_id=objective.family_id,
                    source_sha256=graph.current_sha256,
                    key=objective.key,
                    duration_s=objective.duration_s,
                    receipt_backing_sha256s=objective.receipt_report_sha256s,
                    ranked_endpoint_evidence_ids=family.evidence_ids,
                )
                break

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
        ranked_families=ranked,
        excluded_endpoint_evidence_ids=tuple(sorted(excluded)),
        bridge_node_path=bridge_path,
        bridge_action_ids=bridge_actions,
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
) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    if graph.current_sha256 not in current_component:
        return None, ()
    objective_by_action = {item.action_id: item for item in inventory.experiments}
    edge_by_id = {edge.edge_id: edge for edge in graph.edges}
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for transition in graph.action_transitions:
        objective = objective_by_action.get(transition.action_id)
        if objective is None or (
            transition.evidence_report_sha256
            not in objective.receipt_report_sha256s
        ):
            continue
        if (
            transition.source_sha256 not in current_component
            or transition.target_sha256 not in current_component
            or transition.source_sha256 in quarantined
            or transition.target_sha256 in quarantined
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
        adjacency[transition.source_sha256].append(
            (transition.target_sha256, transition.action_id)
        )
    queue: deque[tuple[str, tuple[str, ...], tuple[str, ...]]] = deque(
        ((graph.current_sha256, (graph.current_sha256,), ()),)
    )
    visited = {graph.current_sha256}
    while queue:
        node, node_path, action_path = queue.popleft()
        if node in anchors:
            return node_path, action_path
        for target, action_id in sorted(adjacency.get(node, ())):
            if target not in visited:
                visited.add(target)
                queue.append(
                    (target, (*node_path, target), (*action_path, action_id))
                )
    return None, ()


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


def _rank_endpoint_families(
    evidence: tuple[MeasuredEndpointEvidence, ...],
) -> tuple[RankedEndpointFamily, ...]:
    grouped: dict[str, list[MeasuredEndpointEvidence]] = defaultdict(list)
    for item in evidence:
        grouped[item.family_id].append(item)
    families: list[tuple[MeasuredEndpointEvidence, tuple[MeasuredEndpointEvidence, ...]]] = []
    for family_id in sorted(grouped):
        ordered = tuple(
            sorted(
                grouped[family_id],
                key=lambda item: (*_endpoint_quality_key(item), item.evidence_id),
            )
        )
        families.append((ordered[0], ordered))
    families.sort(
        key=lambda item: (
            *_endpoint_quality_key(item[0]),
            -len(item[1]),
            item[0].family_id,
        )
    )
    return tuple(
        RankedEndpointFamily(
            rank=index,
            family_id=best.family_id,
            evidence_ids=tuple(sorted(item.evidence_id for item in records)),
            best_evidence=best,
        )
        for index, (best, records) in enumerate(families, start=1)
    )


def _endpoint_quality_key(item: MeasuredEndpointEvidence) -> tuple[object, ...]:
    return (
        -int(item.production_passed),
        -len(item.production_matched_zones),
        -item.production_matched_landmarks,
        -int(item.registration_cycle_verified),
        -int(item.registration_accepted),
        -len(_REQUIRED_ZONES.intersection(item.registration_matched_zones)),
        -float(item.inlier_ratio),
        -item.inliers,
        -item.mutual_matches,
        _none_last(item.p90_residual_px),
        _none_last(item.median_residual_px),
        _none_last(item.cycle_p90_px),
    )


def _none_last(value: float | None) -> float:
    return math.inf if value is None else float(value)


def _edge_id(first: str, second: str) -> str:
    source, target = sorted((first, second))
    return f"{source}:{target}"


def _rounded_optional(value: float | None) -> float | None:
    return None if value is None else round(float(value), 12)
