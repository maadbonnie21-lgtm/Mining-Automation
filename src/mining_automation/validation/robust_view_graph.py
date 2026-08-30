"""Read-only, production-independent view graph for robust registration R1.

Every visual edge is recomputed from exact endpoint pixels.  The graph may
describe saved views and measured actions, but it cannot validate a scene,
expose a resource, or authorize live camera input.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ..capture import Frame
from .camera_evaluation import CameraEvaluation, evaluate_varrock_east_camera
from .client_readiness import ClientInputReadiness, evaluate_client_input_readiness
from .robust_registration import (
    Matrix3,
    RobustRegistrationEngine,
    RobustWorldRegistration,
)

__all__ = [
    "ROBUST_VIEW_GRAPH_ID",
    "ROBUST_VIEW_GRAPH_VERSION",
    "ActionTransition",
    "GraphCycleEvidence",
    "GraphEdgeEvidence",
    "NegativeNodeGraphEvidence",
    "GraphNodeEvidence",
    "GraphPolicy",
    "NEGATIVE_GRAPH_ROLES",
    "ReadOnlyViewGraph",
    "ViewNodeSpec",
    "ViewRole",
    "build_read_only_view_graph",
]

ROBUST_VIEW_GRAPH_ID: Final[str] = "issue31-read-only-view-graph-r1"
ROBUST_VIEW_GRAPH_VERSION: Final[str] = "1.1.0"


class ViewRole(StrEnum):
    """Corpus role supplied by the canonical R1 loader."""

    REVIEWED_SUPPORTED = "reviewed_supported"
    REAL_DRIFT = "real_drift"
    SYSTEM_IDENTIFICATION = "system_identification"
    RISKY_STATE_CHANGE = "risky_state_change"
    DISCONNECTED = "disconnected"
    OTHER_UNSUPPORTED = "other_unsupported"


NEGATIVE_GRAPH_ROLES: Final[frozenset[ViewRole]] = frozenset(
    {ViewRole.DISCONNECTED, ViewRole.RISKY_STATE_CHANGE}
)


@dataclass(frozen=True, slots=True)
class ViewNodeSpec:
    """One labeled exact saved frame before SHA-based alias collapse."""

    label: str
    frame: Frame
    role: ViewRole

    def __post_init__(self) -> None:
        if not self.label or self.label != self.label.strip():
            raise ValueError("view label must be a non-empty trimmed string")
        if not isinstance(self.frame, Frame):
            raise TypeError("view frame must be a Frame")
        if not isinstance(self.role, ViewRole):
            raise TypeError("view role must be a ViewRole")


@dataclass(frozen=True, slots=True)
class ActionTransition:
    """Exact receipt-proven directed transition between two saved frames."""

    action_id: str
    source_sha256: str
    target_sha256: str
    evidence_report_sha256: str
    receipt_verified: bool

    def __post_init__(self) -> None:
        if not self.action_id or self.action_id != self.action_id.strip():
            raise ValueError("action_id must be a non-empty trimmed string")
        for name, digest in (
            ("source_sha256", self.source_sha256),
            ("target_sha256", self.target_sha256),
            ("evidence_report_sha256", self.evidence_report_sha256),
        ):
            _validate_digest(digest, name)
        if self.source_sha256 == self.target_sha256:
            raise ValueError("an action transition requires distinct endpoint pixels")
        if self.receipt_verified is not True:
            raise ValueError("only an exact complete receipt may label an action edge")

    def as_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "evidence_report_sha256": self.evidence_report_sha256,
            "receipt_verified": self.receipt_verified,
            "source_sha256": self.source_sha256,
            "target_sha256": self.target_sha256,
        }


@dataclass(frozen=True, slots=True)
class GraphPolicy:
    """Frozen graph-cycle policy layered above pairwise registration."""

    maximum_cycle_median_px: float = 0.75
    maximum_cycle_p90_px: float = 1.50
    minimum_supporting_cycles_per_edge: int = 1

    def __post_init__(self) -> None:
        for name, value in (
            ("maximum_cycle_median_px", self.maximum_cycle_median_px),
            ("maximum_cycle_p90_px", self.maximum_cycle_p90_px),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0.0
            ):
                raise ValueError(f"{name} must be finite and positive")
        if (
            isinstance(self.minimum_supporting_cycles_per_edge, bool)
            or not isinstance(self.minimum_supporting_cycles_per_edge, int)
            or self.minimum_supporting_cycles_per_edge < 1
        ):
            raise ValueError("minimum_supporting_cycles_per_edge must be positive")

    def as_dict(self) -> dict[str, object]:
        return {
            "maximum_cycle_median_px": self.maximum_cycle_median_px,
            "maximum_cycle_p90_px": self.maximum_cycle_p90_px,
            "minimum_supporting_cycles_per_edge": (
                self.minimum_supporting_cycles_per_edge
            ),
        }


DEFAULT_GRAPH_POLICY: Final[GraphPolicy] = GraphPolicy()


@dataclass(frozen=True, slots=True)
class GraphNodeEvidence:
    """SHA-collapsed node with independent readiness and production evidence."""

    sha256: str
    payload_bytes: int
    width: int
    height: int
    pixel_format: str
    labels: tuple[str, ...]
    roles: tuple[ViewRole, ...]
    readiness: ClientInputReadiness
    production: CameraEvaluation
    reviewed_supported_anchor: bool
    current: bool

    @property
    def registration_eligible(self) -> bool:
        return self.readiness.safe_to_attempt_camera_input

    @property
    def explicitly_disconnected(self) -> bool:
        return ViewRole.DISCONNECTED in self.roles

    @property
    def negative_graph_roles(self) -> tuple[ViewRole, ...]:
        return tuple(role for role in self.roles if role in NEGATIVE_GRAPH_ROLES)

    @property
    def negative_graph_case(self) -> bool:
        return bool(self.negative_graph_roles)

    def as_dict(self) -> dict[str, object]:
        return {
            "current": self.current,
            "geometry": {
                "height": self.height,
                "payload_bytes": self.payload_bytes,
                "pixel_format": self.pixel_format,
                "width": self.width,
            },
            "labels": list(self.labels),
            "negative_graph_case": self.negative_graph_case,
            "negative_graph_roles": [
                role.value for role in self.negative_graph_roles
            ],
            "production": _production_dict(self.production),
            "readiness": _readiness_dict(self.readiness),
            "registration_eligible": self.registration_eligible,
            "reviewed_supported_anchor": self.reviewed_supported_anchor,
            "roles": [role.value for role in self.roles],
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class GraphCycleEvidence:
    """One deterministic three-edge composed-cycle check."""

    cycle_id: str
    nodes: tuple[str, str, str]
    edge_ids: tuple[str, str, str]
    median_error_px: float | None
    p90_error_px: float | None
    passed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "cycle_id": self.cycle_id,
            "edge_ids": list(self.edge_ids),
            "median_error_px": _rounded_optional(self.median_error_px),
            "nodes": list(self.nodes),
            "p90_error_px": _rounded_optional(self.p90_error_px),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class GraphEdgeEvidence:
    """One candidate pair and its pixel-derived/cycle-derived status."""

    edge_id: str
    source_sha256: str
    target_sha256: str
    registration: RobustWorldRegistration | None
    pre_registration_rejection: str | None
    supporting_cycle_ids: tuple[str, ...] = ()
    action_ids: tuple[str, ...] = ()

    @property
    def registration_accepted(self) -> bool:
        return self.registration is not None and self.registration.accepted

    def verified(self, policy: GraphPolicy) -> bool:
        return (
            self.registration_accepted
            and len(self.supporting_cycle_ids)
            >= policy.minimum_supporting_cycles_per_edge
        )

    def as_dict(self, policy: GraphPolicy) -> dict[str, object]:
        return {
            "action_ids": list(self.action_ids),
            "edge_id": self.edge_id,
            "pre_registration_rejection": self.pre_registration_rejection,
            "registration": (
                None if self.registration is None else self.registration.as_dict()
            ),
            "registration_accepted": self.registration_accepted,
            "source_sha256": self.source_sha256,
            "supporting_cycle_ids": list(self.supporting_cycle_ids),
            "target_sha256": self.target_sha256,
            "verified": self.verified(policy),
        }


@dataclass(frozen=True, slots=True)
class NegativeNodeGraphEvidence:
    """Observed graph connectivity for one explicitly labeled negative node."""

    sha256: str
    labels: tuple[str, ...]
    roles: tuple[ViewRole, ...]
    registration_eligible: bool
    accepted_edge_ids: tuple[str, ...]
    verified_edge_ids: tuple[str, ...]
    verified_path_to_supported: tuple[str, ...] | None

    @property
    def has_verified_path_to_supported(self) -> bool:
        return self.verified_path_to_supported is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted_edge_count": len(self.accepted_edge_ids),
            "accepted_edge_ids": list(self.accepted_edge_ids),
            "has_verified_path_to_supported": self.has_verified_path_to_supported,
            "labels": list(self.labels),
            "registration_eligible": self.registration_eligible,
            "roles": [role.value for role in self.roles],
            "sha256": self.sha256,
            "verified_edge_count": len(self.verified_edge_ids),
            "verified_edge_ids": list(self.verified_edge_ids),
            "verified_path_to_supported": (
                None
                if self.verified_path_to_supported is None
                else list(self.verified_path_to_supported)
            ),
        }


@dataclass(frozen=True, slots=True)
class ReadOnlyViewGraph:
    """Canonical graph result with no production or input authority."""

    graph_id: str
    graph_version: str
    reviewed_manifest_sha256: str
    current_sha256: str
    nodes: tuple[GraphNodeEvidence, ...]
    edges: tuple[GraphEdgeEvidence, ...]
    cycles: tuple[GraphCycleEvidence, ...]
    components: tuple[tuple[str, ...], ...]
    visual_path_to_supported: tuple[str, ...] | None
    action_path_to_supported: tuple[str, ...] | None
    action_transitions: tuple[ActionTransition, ...]
    negative_nodes: tuple[NegativeNodeGraphEvidence, ...]
    negative_accepted_edge_ids: tuple[str, ...]
    negative_verified_edge_ids: tuple[str, ...]
    false_edge_count: int
    conclusion: str
    missing_link: str | None
    policy: GraphPolicy
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)
    can_authorize_camera_input: bool = field(default=False, init=False)
    diagnostic_registration_can_override_production: bool = field(
        default=False, init=False
    )

    @property
    def offline_controller_path_available(self) -> bool:
        return self.action_path_to_supported is not None

    @property
    def false_path_count(self) -> int:
        return sum(
            node.has_verified_path_to_supported for node in self.negative_nodes
        )

    @property
    def negative_failure_count(self) -> int:
        return self.false_edge_count + self.false_path_count

    def as_dict(self) -> dict[str, object]:
        verified_edges = sum(edge.verified(self.policy) for edge in self.edges)
        accepted_edges = sum(edge.registration_accepted for edge in self.edges)
        return {
            "action_transitions": [item.as_dict() for item in self.action_transitions],
            "authority": {
                "can_accept": self.can_accept,
                "can_authorize_camera_input": self.can_authorize_camera_input,
                "can_expose_resources": self.can_expose_resources,
                "can_validate_scene": self.can_validate_scene,
                "diagnostic_registration_can_override_production": (
                    self.diagnostic_registration_can_override_production
                ),
            },
            "components": [list(component) for component in self.components],
            "conclusion": self.conclusion,
            "current_sha256": self.current_sha256,
            "cycles": [cycle.as_dict() for cycle in self.cycles],
            "edges": [edge.as_dict(self.policy) for edge in self.edges],
            "false_edge_count": self.false_edge_count,
            "false_path_count": self.false_path_count,
            "graph_id": self.graph_id,
            "graph_version": self.graph_version,
            "missing_link": self.missing_link,
            "nodes": [node.as_dict() for node in self.nodes],
            "negative_corpus": {
                "accepted_pairwise_edge_count": len(
                    self.negative_accepted_edge_ids
                ),
                "accepted_pairwise_edge_ids": list(
                    self.negative_accepted_edge_ids
                ),
                "aggregate_failure_count": self.negative_failure_count,
                "cycle_verified_edge_count": len(
                    self.negative_verified_edge_ids
                ),
                "cycle_verified_edge_ids": list(
                    self.negative_verified_edge_ids
                ),
                "nodes": [node.as_dict() for node in self.negative_nodes],
                "policy_roles": sorted(role.value for role in NEGATIVE_GRAPH_ROLES),
                "supported_path_count": self.false_path_count,
            },
            "policy": self.policy.as_dict(),
            "reachability": {
                "action_path_to_supported": (
                    None
                    if self.action_path_to_supported is None
                    else list(self.action_path_to_supported)
                ),
                "offline_controller_path_available": (
                    self.offline_controller_path_available
                ),
                "visual_path_to_supported": (
                    None
                    if self.visual_path_to_supported is None
                    else list(self.visual_path_to_supported)
                ),
            },
            "reviewed_manifest_sha256": self.reviewed_manifest_sha256,
            "summary": {
                "accepted_pairwise_edges": accepted_edges,
                "action_transition_count": len(self.action_transitions),
                "component_count": len(self.components),
                "consistent_cycle_count": sum(cycle.passed for cycle in self.cycles),
                "node_count": len(self.nodes),
                "pair_count": len(self.edges),
                "verified_edges": verified_edges,
            },
        }


def build_read_only_view_graph(
    specs: tuple[ViewNodeSpec, ...],
    *,
    current_sha256: str,
    reviewed_manifest_sha256: str,
    reviewed_anchor_sha256s: frozenset[str],
    action_transitions: tuple[ActionTransition, ...] = (),
    registration_engine: RobustRegistrationEngine | None = None,
    policy: GraphPolicy = DEFAULT_GRAPH_POLICY,
) -> ReadOnlyViewGraph:
    """Recompute a cycle-backed graph from exact saved pixels."""

    if not specs:
        raise ValueError("view graph requires at least one node specification")
    if not isinstance(specs, tuple) or any(
        not isinstance(spec, ViewNodeSpec) for spec in specs
    ):
        raise TypeError("specs must be a tuple of ViewNodeSpec values")
    _validate_digest(current_sha256, "current_sha256")
    _validate_digest(reviewed_manifest_sha256, "reviewed_manifest_sha256")
    if not isinstance(reviewed_anchor_sha256s, frozenset) or not reviewed_anchor_sha256s:
        raise ValueError("reviewed anchors must be a non-empty frozen digest set")
    for digest in reviewed_anchor_sha256s:
        _validate_digest(digest, "reviewed anchor")
    if not isinstance(action_transitions, tuple) or any(
        not isinstance(item, ActionTransition) for item in action_transitions
    ):
        raise TypeError("action_transitions must contain ActionTransition values")
    if not isinstance(policy, GraphPolicy):
        raise TypeError("policy must be GraphPolicy")

    labels = [spec.label for spec in specs]
    if len(labels) != len(set(labels)):
        raise ValueError("view labels must be unique")
    grouped: dict[str, list[ViewNodeSpec]] = defaultdict(list)
    for spec in specs:
        grouped[hashlib.sha256(spec.frame.payload).hexdigest()].append(spec)
    if current_sha256 not in grouped:
        raise ValueError("current_sha256 does not identify an exact supplied frame")
    missing_anchors = reviewed_anchor_sha256s - grouped.keys()
    if missing_anchors:
        raise ValueError("every reviewed anchor digest must identify a supplied frame")

    nodes: list[GraphNodeEvidence] = []
    frames: dict[str, Frame] = {}
    for digest in sorted(grouped):
        aliases = sorted(grouped[digest], key=lambda item: item.label)
        frame = aliases[0].frame
        if any(
            alias.frame.payload != frame.payload
            or alias.frame.width != frame.width
            or alias.frame.height != frame.height
            or alias.frame.pixel_format is not frame.pixel_format
            for alias in aliases
        ):
            raise ValueError(
                "SHA-collapsed aliases disagree on payload or endpoint metadata"
            )
        frames[digest] = frame
        readiness = evaluate_client_input_readiness(frame)
        production = evaluate_varrock_east_camera(frame)
        anchor = digest in reviewed_anchor_sha256s and production.passed
        nodes.append(
            GraphNodeEvidence(
                sha256=digest,
                payload_bytes=len(frame.payload),
                width=frame.width,
                height=frame.height,
                pixel_format=frame.pixel_format.value,
                labels=tuple(alias.label for alias in aliases),
                roles=tuple(sorted({alias.role for alias in aliases}, key=str)),
                readiness=readiness,
                production=production,
                reviewed_supported_anchor=anchor,
                current=digest == current_sha256,
            )
        )
    node_by_sha = {node.sha256: node for node in nodes}
    if any(not node_by_sha[digest].reviewed_supported_anchor for digest in reviewed_anchor_sha256s):
        raise ValueError("every reviewed anchor must independently pass production")

    engine = registration_engine or RobustRegistrationEngine()
    provisional: list[GraphEdgeEvidence] = []
    for source_index, source in enumerate(nodes):
        for target in nodes[source_index + 1 :]:
            edge_id = _edge_id(source.sha256, target.sha256)
            if not source.registration_eligible or not target.registration_eligible:
                rejected = "readiness_veto: one or both endpoints are not gameplay-ready"
                registration = None
            else:
                rejected = None
                registration = engine.analyze(
                    frames[source.sha256], frames[target.sha256]
                )
            provisional.append(
                GraphEdgeEvidence(
                    edge_id=edge_id,
                    source_sha256=source.sha256,
                    target_sha256=target.sha256,
                    registration=registration,
                    pre_registration_rejection=rejected,
                )
            )

    provisional_by_pair = {
        (edge.source_sha256, edge.target_sha256): edge for edge in provisional
    }
    cycles = _evaluate_graph_cycles(tuple(nodes), provisional_by_pair, policy)
    supporting: dict[str, list[str]] = defaultdict(list)
    for cycle in cycles:
        if cycle.passed:
            for edge_id in cycle.edge_ids:
                supporting[edge_id].append(cycle.cycle_id)
    actions_by_edge: dict[str, list[str]] = defaultdict(list)
    seen_action_ids: set[str] = set()
    for transition in action_transitions:
        if transition.action_id in seen_action_ids:
            raise ValueError("action transition ids must be unique")
        seen_action_ids.add(transition.action_id)
        if (
            transition.source_sha256 not in node_by_sha
            or transition.target_sha256 not in node_by_sha
        ):
            raise ValueError("action transition endpoint is not a graph node")
        actions_by_edge[
            _edge_id(transition.source_sha256, transition.target_sha256)
        ].append(transition.action_id)

    edges = tuple(
        GraphEdgeEvidence(
            edge_id=edge.edge_id,
            source_sha256=edge.source_sha256,
            target_sha256=edge.target_sha256,
            registration=edge.registration,
            pre_registration_rejection=edge.pre_registration_rejection,
            supporting_cycle_ids=tuple(sorted(supporting[edge.edge_id])),
            action_ids=tuple(sorted(actions_by_edge[edge.edge_id])),
        )
        for edge in provisional
    )
    raw_verified_adjacency: dict[str, set[str]] = defaultdict(set)
    edge_by_id = {edge.edge_id: edge for edge in edges}
    for edge in edges:
        if edge.verified(policy):
            raw_verified_adjacency[edge.source_sha256].add(edge.target_sha256)
            raw_verified_adjacency[edge.target_sha256].add(edge.source_sha256)
    negative_node_sha256s = frozenset(
        node.sha256 for node in nodes if node.negative_graph_case
    )
    verified_adjacency: dict[str, set[str]] = defaultdict(set)
    for source_sha256, targets in raw_verified_adjacency.items():
        if source_sha256 in negative_node_sha256s:
            continue
        verified_adjacency[source_sha256].update(
            neighbor_sha256
            for neighbor_sha256 in targets
            if neighbor_sha256 not in negative_node_sha256s
        )
    components = _components(tuple(node_by_sha), verified_adjacency)
    anchors = frozenset(reviewed_anchor_sha256s)
    visual_path = _shortest_path(current_sha256, anchors, verified_adjacency)

    action_adjacency: dict[str, set[str]] = defaultdict(set)
    for transition in action_transitions:
        edge = edge_by_id[_edge_id(transition.source_sha256, transition.target_sha256)]
        if edge.verified(policy) and not (
            transition.source_sha256 in negative_node_sha256s
            or transition.target_sha256 in negative_node_sha256s
        ):
            action_adjacency[transition.source_sha256].add(transition.target_sha256)
    action_path = _shortest_path(current_sha256, anchors, action_adjacency)
    negative_accepted_edge_ids = tuple(
        edge.edge_id
        for edge in edges
        if edge.registration_accepted
        and (
            edge.source_sha256 in negative_node_sha256s
            or edge.target_sha256 in negative_node_sha256s
        )
    )
    negative_verified_edge_ids = tuple(
        edge.edge_id
        for edge in edges
        if edge.verified(policy)
        and (
            edge.source_sha256 in negative_node_sha256s
            or edge.target_sha256 in negative_node_sha256s
        )
    )
    negative_nodes = tuple(
        NegativeNodeGraphEvidence(
            sha256=node.sha256,
            labels=node.labels,
            roles=node.negative_graph_roles,
            registration_eligible=node.registration_eligible,
            accepted_edge_ids=tuple(
                edge_id
                for edge_id in negative_accepted_edge_ids
                if node.sha256 in edge_id.split(":")
            ),
            verified_edge_ids=tuple(
                edge_id
                for edge_id in negative_verified_edge_ids
                if node.sha256 in edge_id.split(":")
            ),
            verified_path_to_supported=_shortest_path(
                node.sha256, anchors, raw_verified_adjacency
            ),
        )
        for node in nodes
        if node.negative_graph_case
    )
    false_edges = len(negative_accepted_edge_ids)
    if action_path is not None:
        conclusion = "offline controller path available"
        missing_link = None
    else:
        missing_link = (
            "one receipt-proven, readiness-safe camera transition from the current "
            "cycle-verified component to any exact reviewed production-supported "
            "anchor, with robust inliers distributed across north_west, north_east, "
            "and south_west at both endpoints"
        )
        conclusion = f"missing graph link: {missing_link}"
    return ReadOnlyViewGraph(
        graph_id=ROBUST_VIEW_GRAPH_ID,
        graph_version=ROBUST_VIEW_GRAPH_VERSION,
        reviewed_manifest_sha256=reviewed_manifest_sha256,
        current_sha256=current_sha256,
        nodes=tuple(nodes),
        edges=edges,
        cycles=cycles,
        components=components,
        visual_path_to_supported=visual_path,
        action_path_to_supported=action_path,
        action_transitions=tuple(sorted(action_transitions, key=lambda item: item.action_id)),
        negative_nodes=negative_nodes,
        negative_accepted_edge_ids=negative_accepted_edge_ids,
        negative_verified_edge_ids=negative_verified_edge_ids,
        false_edge_count=false_edges,
        conclusion=conclusion,
        missing_link=missing_link,
        policy=policy,
    )


def _evaluate_graph_cycles(
    nodes: tuple[GraphNodeEvidence, ...],
    edges: dict[tuple[str, str], GraphEdgeEvidence],
    policy: GraphPolicy,
) -> tuple[GraphCycleEvidence, ...]:
    results: list[GraphCycleEvidence] = []
    digests = tuple(node.sha256 for node in nodes)
    for first_index, first in enumerate(digests):
        for second_index in range(first_index + 1, len(digests)):
            second = digests[second_index]
            first_second = edges[(first, second)]
            if not first_second.registration_accepted:
                continue
            for third_index in range(second_index + 1, len(digests)):
                third = digests[third_index]
                first_third = edges[(first, third)]
                second_third = edges[(second, third)]
                if not (
                    first_third.registration_accepted
                    and second_third.registration_accepted
                ):
                    continue
                first_to_second = _oriented_matrix(first_second, first, second)
                second_to_third = _oriented_matrix(second_third, second, third)
                third_to_first = _oriented_matrix(first_third, third, first)
                composed = _matrix_multiply(
                    third_to_first,
                    _matrix_multiply(second_to_third, first_to_second),
                )
                errors = tuple(
                    _cycle_point_error(composed, point)
                    for point in _cycle_sample_points()
                )
                finite = tuple(value for value in errors if math.isfinite(value))
                if len(finite) != len(errors):
                    median_error = None
                    p90_error = None
                else:
                    median_error = _percentile(finite, 50.0)
                    p90_error = _percentile(finite, 90.0)
                passed = (
                    median_error is not None
                    and p90_error is not None
                    and median_error <= policy.maximum_cycle_median_px
                    and p90_error <= policy.maximum_cycle_p90_px
                )
                node_ids = (first, second, third)
                sorted_edge_ids = sorted(
                    (
                        first_second.edge_id,
                        second_third.edge_id,
                        first_third.edge_id,
                    )
                )
                edge_ids = (
                    sorted_edge_ids[0],
                    sorted_edge_ids[1],
                    sorted_edge_ids[2],
                )
                cycle_id = hashlib.sha256("|".join(node_ids).encode("ascii")).hexdigest()
                results.append(
                    GraphCycleEvidence(
                        cycle_id=cycle_id,
                        nodes=node_ids,
                        edge_ids=edge_ids,
                        median_error_px=median_error,
                        p90_error_px=p90_error,
                        passed=passed,
                    )
                )
    return tuple(results)


def _oriented_matrix(
    edge: GraphEdgeEvidence, source: str, target: str
) -> Matrix3:
    if edge.registration is None or edge.registration.selected_model is None:
        raise ValueError("an oriented edge requires an accepted registration")
    model = edge.registration.selected_model
    if source == edge.source_sha256 and target == edge.target_sha256:
        matrix = model.forward_matrix
    elif source == edge.target_sha256 and target == edge.source_sha256:
        matrix = model.reverse_matrix
    else:
        raise ValueError("requested orientation does not match edge endpoints")
    if matrix is None:
        raise ValueError("accepted registration is missing an oriented matrix")
    return matrix


def _matrix_multiply(first: Matrix3, second: Matrix3) -> Matrix3:
    return tuple(
        tuple(
            sum(first[row][inner] * second[inner][column] for inner in range(3))
            for column in range(3)
        )
        for row in range(3)
    )  # type: ignore[return-value]


def _cycle_sample_points() -> tuple[tuple[float, float], ...]:
    return (
        (128.0, 160.0),
        (384.0, 160.0),
        (576.0, 300.0),
        (704.0, 420.0),
        (128.0, 650.0),
        (384.0, 760.0),
    )


def _cycle_point_error(
    matrix: Matrix3, point: tuple[float, float]
) -> float:
    x, y = point
    denominator = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if not math.isfinite(denominator) or abs(denominator) <= 1e-12:
        return math.inf
    projected_x = (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / denominator
    projected_y = (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / denominator
    if not math.isfinite(projected_x) or not math.isfinite(projected_y):
        return math.inf
    return math.hypot(projected_x - x, projected_y - y)


def _percentile(values: tuple[float, ...], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _components(
    nodes: tuple[str, ...], adjacency: dict[str, set[str]]
) -> tuple[tuple[str, ...], ...]:
    remaining = set(nodes)
    components: list[tuple[str, ...]] = []
    while remaining:
        start = min(remaining)
        queue = deque((start,))
        found: set[str] = set()
        while queue:
            node = queue.popleft()
            if node in found:
                continue
            found.add(node)
            queue.extend(sorted(adjacency.get(node, set()) - found))
        remaining -= found
        components.append(tuple(sorted(found)))
    return tuple(sorted(components, key=lambda item: (item[0], len(item))))


def _shortest_path(
    start: str,
    targets: frozenset[str],
    adjacency: dict[str, set[str]],
) -> tuple[str, ...] | None:
    queue: deque[tuple[str, tuple[str, ...]]] = deque(((start, (start,)),))
    visited = {start}
    while queue:
        node, path = queue.popleft()
        if node in targets:
            return path
        for neighbor in sorted(adjacency.get(node, set())):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, (*path, neighbor)))
    return None


def _edge_id(first: str, second: str) -> str:
    source, target = sorted((first, second))
    return f"{source}:{target}"


def _production_dict(evaluation: CameraEvaluation) -> dict[str, object]:
    return {
        "definitive_target_ids": list(evaluation.definitive_target_ids),
        "detector_id": evaluation.detector_id,
        "detector_version": evaluation.detector_version,
        "frame_geometry_supported": evaluation.frame_geometry_supported,
        "landmarks": [
            {
                "distance": _rounded_optional(item.distance),
                "landmark_id": item.landmark_id,
                "matched": item.matched,
                "threshold": item.threshold,
                "zone": item.zone.value,
            }
            for item in evaluation.landmarks
        ],
        "matched_landmark_count": evaluation.matched_landmark_count,
        "matched_zones": [zone.value for zone in evaluation.matched_zones],
        "passed": evaluation.passed,
        "profile_id": evaluation.profile_id,
        "profile_schema_version": evaluation.profile_schema_version,
        "required_landmark_matches": evaluation.required_landmark_matches,
        "required_matched_zones": evaluation.required_matched_zones,
        "resource_states": [
            {
                "confidence": round(item.confidence, 12),
                "resource_id": item.resource_id,
                "state": item.state.value,
            }
            for item in evaluation.resource_states
        ],
        "scene_reason": evaluation.scene_reason,
        "scene_validated": evaluation.scene_validated,
    }


def _readiness_dict(readiness: ClientInputReadiness) -> dict[str, object]:
    return {
        "anchors": [
            {
                "anchor_id": item.policy.anchor_id,
                "dark_fraction": round(item.dark_fraction, 12),
                "edge_density": round(item.edge_density, 12),
                "luma_stddev": round(item.luma_stddev, 12),
                "matched": item.matched,
                "region": list(item.policy.region),
            }
            for item in readiness.anchors
        ],
        "detail": readiness.detail,
        "evaluator_id": readiness.evaluator_id,
        "evaluator_version": readiness.evaluator_version,
        "reason": readiness.reason.value,
        "safe_to_attempt_camera_input": readiness.safe_to_attempt_camera_input,
    }


def _rounded_optional(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 12)


def _validate_digest(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
