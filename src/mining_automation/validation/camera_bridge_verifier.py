"""Pure offline verification for one receipt-backed Issue #31 bridge sample.

This module has no camera-control or platform-input dependency.  It rederives
production from exact saved pixels, constructs one exact action transition only
after the frozen receipt and fail-closed production facts are established, and
then audits the transition inside the cycle-backed read-only view graph.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import combinations
from typing import Final

from ..capture import Frame
from ..perception.resource import ResourceVisualState
from ..perception.scene_landmarks import MacroZone
from .camera_bridge_capture import CAMERA_BRIDGE_CAPTURE_ID, camera_bridge_capture_plan
from .camera_bridge_planner import (
    FROZEN_ENDPOINT_OBJECTIVE_ID,
    FROZEN_ENDPOINT_SOURCE_SHA256,
)
from .camera_evaluation import CameraEvaluation, evaluate_varrock_east_camera
from .camera_plan import CameraPlanReceipt
from .client_readiness import evaluate_client_input_readiness
from .robust_registration import RobustRegistrationEngine
from .robust_view_graph import (
    NEGATIVE_GRAPH_ROLES,
    ActionTransition,
    GraphEdgeEvidence,
    ReadOnlyViewGraph,
    ViewNodeSpec,
    ViewRole,
    build_read_only_view_graph,
)

__all__ = [
    "CAMERA_BRIDGE_VERIFIER_ID",
    "CAMERA_BRIDGE_VERIFIER_VERSION",
    "AuthenticatedBridgeCapture",
    "BridgeAnchorAudit",
    "BridgeEdgeAudit",
    "BridgePostVerification",
    "verify_camera_bridge_post",
]

CAMERA_BRIDGE_VERIFIER_ID: Final[str] = "issue31-offline-camera-bridge-post-verifier-r2"
CAMERA_BRIDGE_VERIFIER_VERSION: Final[str] = "1.0.0"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REQUIRED_ZONES: Final[frozenset[MacroZone]] = frozenset(
    {
        MacroZone.NORTH_WEST,
        MacroZone.NORTH_EAST,
        MacroZone.SOUTH_WEST,
    }
)


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class AuthenticatedBridgeCapture:
    """Typed, report-authenticated inputs for the pure verifier.

    The strict read-only post loader owns JSON, sidecar, provenance, and
    raw-path authentication.  This type deliberately accepts a complete typed
    receipt rather than a caller-supplied ``receipt_verified`` boolean.
    """

    report_sha256: str
    objective_id: str
    objective_source_sha256: str
    receipt: CameraPlanReceipt
    commit: Frame
    post: Frame
    reported_post_production: CameraEvaluation

    def __post_init__(self) -> None:
        _require_digest(self.report_sha256, "report_sha256")
        _require_digest(self.objective_source_sha256, "objective_source_sha256")
        if self.objective_id != FROZEN_ENDPOINT_OBJECTIVE_ID:
            raise ValueError("capture objective_id is not the frozen bridge objective")
        if self.objective_source_sha256 != FROZEN_ENDPOINT_SOURCE_SHA256:
            raise ValueError("capture objective source is not the frozen bridge source")
        if not isinstance(self.receipt, CameraPlanReceipt):
            raise TypeError("receipt must be a CameraPlanReceipt")
        if self.receipt.plan is not camera_bridge_capture_plan():
            raise ValueError("receipt does not bind the exact frozen bridge plan")
        if len(self.receipt.action_receipts) != 1:
            raise ValueError("bridge receipt must contain exactly one physical primitive")
        if not isinstance(self.commit, Frame) or not isinstance(self.post, Frame):
            raise TypeError("commit and post must be Frame values")
        if not isinstance(self.reported_post_production, CameraEvaluation):
            raise TypeError("reported_post_production must be a CameraEvaluation")
        if (
            self.post.frame_id <= self.commit.frame_id
            or self.post.captured_monotonic_s <= self.commit.captured_monotonic_s
        ):
            raise ValueError("post frame must be strictly newer than commit")
        if _frame_sha256(self.commit) == _frame_sha256(self.post):
            raise ValueError("bridge action requires distinct commit and post pixels")


@dataclass(frozen=True, slots=True)
class BridgeEdgeAudit:
    """Cycle/all-zone verdict for one exact visual relationship."""

    edge_id: str
    registration_accepted: bool
    cycle_verified: bool
    all_required_zones: bool
    supporting_cycle_ids: tuple[str, ...]
    selected_model_family: str | None
    required_zones: tuple[MacroZone, ...]
    source_zone_inliers: tuple[tuple[MacroZone, int], ...]
    target_zone_inliers: tuple[tuple[MacroZone, int], ...]
    source_zone_cells: tuple[tuple[MacroZone, int], ...]
    target_zone_cells: tuple[tuple[MacroZone, int], ...]
    median_residual_px: float | None
    p90_residual_px: float | None
    cycle_median_px: float | None
    cycle_p90_px: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "all_required_zones": self.all_required_zones,
            "cycle_verified": self.cycle_verified,
            "edge_id": self.edge_id,
            "median_residual_px": self.median_residual_px,
            "p90_residual_px": self.p90_residual_px,
            "registration_accepted": self.registration_accepted,
            "required_zones": [zone.value for zone in self.required_zones],
            "selected_model_family": self.selected_model_family,
            "source_zone_cells": _zone_counts_dict(self.source_zone_cells),
            "source_zone_inliers": _zone_counts_dict(self.source_zone_inliers),
            "supporting_cycle_ids": list(self.supporting_cycle_ids),
            "target_zone_cells": _zone_counts_dict(self.target_zone_cells),
            "target_zone_inliers": _zone_counts_dict(self.target_zone_inliers),
            "cycle_median_px": self.cycle_median_px,
            "cycle_p90_px": self.cycle_p90_px,
        }


@dataclass(frozen=True, slots=True)
class BridgeAnchorAudit:
    """Whether all repeated endpoints reach one common reviewed anchor."""

    anchor_sha256: str
    endpoint_edge_ids: tuple[str, ...]
    complete: bool

    def __post_init__(self) -> None:
        _require_digest(self.anchor_sha256, "anchor_sha256")

    def as_dict(self) -> dict[str, object]:
        return {
            "anchor_sha256": self.anchor_sha256,
            "complete": self.complete,
            "endpoint_edge_ids": list(self.endpoint_edge_ids),
        }


@dataclass(frozen=True, slots=True)
class BridgePostVerification:
    """No-authority offline verdict for one exact bridge capture."""

    verifier_id: str
    verifier_version: str
    capture_report_sha256: str
    action_transition: ActionTransition
    graph: ReadOnlyViewGraph
    source_prefix_edge_audit: BridgeEdgeAudit | None
    source_prefix_path: tuple[str, ...] | None
    repeat_edge_audits: tuple[BridgeEdgeAudit, ...]
    anchor_audits: tuple[BridgeAnchorAudit, ...]
    qualifying_common_anchor_sha256s: tuple[str, ...]
    recomputed_commit_production: CameraEvaluation
    recomputed_post_production: CameraEvaluation
    visual_path_from_post_to_supported: tuple[str, ...] | None
    action_path_to_post: tuple[str, ...] | None
    mixed_bridge_path_to_supported: tuple[str, ...] | None
    raw_graph_action_path_to_supported: tuple[str, ...] | None
    verified: bool
    failure_reasons: tuple[str, ...]
    can_accept: bool = field(default=False, init=False)
    can_validate_scene: bool = field(default=False, init=False)
    can_expose_resources: bool = field(default=False, init=False)
    can_authorize_camera_input: bool = field(default=False, init=False)
    diagnostic_registration_can_override_production: bool = field(
        default=False,
        init=False,
    )
    live_input_performed_by_verifier: bool = field(default=False, init=False)
    second_live_action_authorized: bool = field(default=False, init=False)
    stop_after_single_sample: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if self.verifier_id != CAMERA_BRIDGE_VERIFIER_ID:
            raise ValueError("unexpected bridge verifier id")
        if self.verifier_version != CAMERA_BRIDGE_VERIFIER_VERSION:
            raise ValueError("unexpected bridge verifier version")
        _require_digest(self.capture_report_sha256, "capture_report_sha256")
        if self.action_transition.evidence_report_sha256 != self.capture_report_sha256:
            raise ValueError("action transition does not bind the capture report")
        if self.verified != (not self.failure_reasons):
            raise ValueError("verified must exactly reflect the failure set")

    def as_dict(self) -> dict[str, object]:
        return {
            "action_path_to_post": (
                None if self.action_path_to_post is None else list(self.action_path_to_post)
            ),
            "action_transition": self.action_transition.as_dict(),
            "anchor_audits": [item.as_dict() for item in self.anchor_audits],
            "authority": {
                "can_accept": self.can_accept,
                "can_authorize_camera_input": self.can_authorize_camera_input,
                "can_expose_resources": self.can_expose_resources,
                "can_validate_scene": self.can_validate_scene,
                "diagnostic_registration_can_override_production": (
                    self.diagnostic_registration_can_override_production
                ),
            },
            "capture_report_sha256": self.capture_report_sha256,
            "failure_reasons": list(self.failure_reasons),
            "graph": self.graph.as_dict(),
            "live_input_performed_by_verifier": (
                self.live_input_performed_by_verifier
            ),
            "mixed_bridge_path_to_supported": (
                None
                if self.mixed_bridge_path_to_supported is None
                else list(self.mixed_bridge_path_to_supported)
            ),
            "production": {
                "commit": _production_dict(self.recomputed_commit_production),
                "post": _production_dict(self.recomputed_post_production),
            },
            "qualifying_common_anchor_sha256s": list(
                self.qualifying_common_anchor_sha256s
            ),
            "repeat_edge_audits": [
                item.as_dict() for item in self.repeat_edge_audits
            ],
            "raw_graph_action_path_to_supported": (
                None
                if self.raw_graph_action_path_to_supported is None
                else list(self.raw_graph_action_path_to_supported)
            ),
            "second_live_action_authorized": self.second_live_action_authorized,
            "source_prefix_edge_audit": (
                None
                if self.source_prefix_edge_audit is None
                else self.source_prefix_edge_audit.as_dict()
            ),
            "source_prefix_path": (
                None
                if self.source_prefix_path is None
                else list(self.source_prefix_path)
            ),
            "stop_after_single_sample": self.stop_after_single_sample,
            "verified": self.verified,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "visual_path_from_post_to_supported": (
                None
                if self.visual_path_from_post_to_supported is None
                else list(self.visual_path_from_post_to_supported)
            ),
        }


def verify_camera_bridge_post(
    capture: AuthenticatedBridgeCapture,
    *,
    base_specs: tuple[ViewNodeSpec, ...],
    prior_endpoint_sha256s: tuple[str, str],
    reviewed_manifest_sha256: str,
    reviewed_anchor_sha256s: frozenset[str],
) -> BridgePostVerification:
    """Recompute one report-only bridge transition and its graph evidence."""

    if not isinstance(capture, AuthenticatedBridgeCapture):
        raise TypeError("capture must be AuthenticatedBridgeCapture")
    _validate_corpus_inputs(
        base_specs,
        prior_endpoint_sha256s=prior_endpoint_sha256s,
        reviewed_manifest_sha256=reviewed_manifest_sha256,
        reviewed_anchor_sha256s=reviewed_anchor_sha256s,
    )
    commit_readiness = evaluate_client_input_readiness(capture.commit)
    post_readiness = evaluate_client_input_readiness(capture.post)
    if not commit_readiness.safe_to_attempt_camera_input:
        raise ValueError("exact commit frame is not gameplay-ready")
    if not post_readiness.safe_to_attempt_camera_input:
        raise ValueError("exact post frame is not gameplay-ready")

    commit_production = evaluate_varrock_east_camera(capture.commit)
    post_production = evaluate_varrock_east_camera(capture.post)
    if post_production != capture.reported_post_production:
        raise ValueError("reported post production does not match exact re-evaluation")
    if not _is_fail_closed(commit_production):
        raise ValueError("exact commit production is not fail closed")
    if not _is_fail_closed(post_production):
        raise ValueError("exact post production is not fail closed")

    commit_sha256 = _frame_sha256(capture.commit)
    post_sha256 = _frame_sha256(capture.post)
    disallowed_post_aliases = {
        FROZEN_ENDPOINT_SOURCE_SHA256,
        *prior_endpoint_sha256s,
        *reviewed_anchor_sha256s,
    }
    if post_sha256 in disallowed_post_aliases:
        raise ValueError(
            "post endpoint aliases the frozen source, a prior endpoint, or a "
            "reviewed anchor"
        )
    transition = ActionTransition(
        action_id=CAMERA_BRIDGE_CAPTURE_ID,
        source_sha256=commit_sha256,
        target_sha256=post_sha256,
        evidence_report_sha256=capture.report_sha256,
        receipt_verified=True,
    )
    specs = (
        *base_specs,
        ViewNodeSpec(
            f"bridge-live:{capture.report_sha256}:commit",
            capture.commit,
            ViewRole.OTHER_UNSUPPORTED,
        ),
        ViewNodeSpec(
            f"bridge-live:{capture.report_sha256}:post",
            capture.post,
            ViewRole.OTHER_UNSUPPORTED,
        ),
    )
    graph = build_read_only_view_graph(
        specs,
        current_sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
        reviewed_manifest_sha256=reviewed_manifest_sha256,
        reviewed_anchor_sha256s=reviewed_anchor_sha256s,
        action_transitions=(transition,),
        registration_engine=RobustRegistrationEngine(),
    )

    source_prefix_edge_audit: BridgeEdgeAudit | None
    source_prefix_path: tuple[str, ...] | None
    if commit_sha256 == FROZEN_ENDPOINT_SOURCE_SHA256:
        # Pixel identity is a stronger precondition than a fitted visual edge.
        source_prefix_edge_audit = None
        source_prefix_path = (FROZEN_ENDPOINT_SOURCE_SHA256,)
    else:
        source_prefix_edge_audit = _audit_edge(
            graph,
            FROZEN_ENDPOINT_SOURCE_SHA256,
            commit_sha256,
        )
        source_prefix_path = (
            (FROZEN_ENDPOINT_SOURCE_SHA256, commit_sha256)
            if source_prefix_edge_audit.all_required_zones
            else None
        )

    repeated = (*prior_endpoint_sha256s, post_sha256)
    repeat_audits = tuple(
        _audit_edge(graph, first, second)
        for first, second in combinations(repeated, 2)
    )
    anchor_audits = tuple(
        _audit_anchor(graph, anchor_sha256, repeated)
        for anchor_sha256 in sorted(reviewed_anchor_sha256s)
    )
    qualifying_anchors = tuple(
        item.anchor_sha256 for item in anchor_audits if item.complete
    )
    visual_path = _verified_visual_path(
        graph,
        source_sha256=post_sha256,
        targets=reviewed_anchor_sha256s,
    )
    action_edge = _find_edge(graph, commit_sha256, post_sha256)
    action_path = (
        (commit_sha256, post_sha256)
        if _edge_is_all_zone_bridge(action_edge, graph)
        and transition.action_id in action_edge.action_ids
        else None
    )
    mixed_path = _mixed_bridge_path(
        source_prefix_path=source_prefix_path,
        action_path=action_path,
        visual_terminal_path=visual_path,
    )

    failures: list[str] = []
    if source_prefix_path is None:
        assert source_prefix_edge_audit is not None
        failures.append(
            "source_prefix_not_verified_all_zones:"
            f"{source_prefix_edge_audit.edge_id}"
        )
    failures.extend(
        f"repeat_edge_not_verified_all_zones:{item.edge_id}"
        for item in repeat_audits
        if not item.all_required_zones
    )
    if not qualifying_anchors:
        failures.append("no_common_supported_anchor_all_zones")
    if visual_path is None:
        failures.append("post_has_no_verified_visual_path_to_supported")
    if action_path is None:
        failures.append("exact_action_edge_not_verified_all_zones")
    if mixed_path is None:
        failures.append("no_mixed_bridge_path_to_supported")
    if (
        graph.negative_nodes
        or graph.false_edge_count != 0
        or graph.negative_accepted_edge_ids
        or graph.negative_verified_edge_ids
    ):
        failures.append("negative_graph_evidence_present")
    unique_failures = tuple(sorted(set(failures)))
    return BridgePostVerification(
        verifier_id=CAMERA_BRIDGE_VERIFIER_ID,
        verifier_version=CAMERA_BRIDGE_VERIFIER_VERSION,
        capture_report_sha256=capture.report_sha256,
        action_transition=transition,
        graph=graph,
        source_prefix_edge_audit=source_prefix_edge_audit,
        source_prefix_path=source_prefix_path,
        repeat_edge_audits=repeat_audits,
        anchor_audits=anchor_audits,
        qualifying_common_anchor_sha256s=qualifying_anchors,
        recomputed_commit_production=commit_production,
        recomputed_post_production=post_production,
        visual_path_from_post_to_supported=visual_path,
        action_path_to_post=action_path,
        mixed_bridge_path_to_supported=mixed_path,
        raw_graph_action_path_to_supported=graph.action_path_to_supported,
        verified=not unique_failures,
        failure_reasons=unique_failures,
    )


def _validate_corpus_inputs(
    base_specs: tuple[ViewNodeSpec, ...],
    *,
    prior_endpoint_sha256s: tuple[str, str],
    reviewed_manifest_sha256: str,
    reviewed_anchor_sha256s: frozenset[str],
) -> None:
    if not isinstance(base_specs, tuple) or any(
        not isinstance(item, ViewNodeSpec) for item in base_specs
    ):
        raise TypeError("base_specs must be a tuple of ViewNodeSpec values")
    if any(spec.role in NEGATIVE_GRAPH_ROLES for spec in base_specs):
        raise ValueError("base_specs cannot contain a negative graph role")
    if len(prior_endpoint_sha256s) != 2 or len(set(prior_endpoint_sha256s)) != 2:
        raise ValueError("exactly two distinct prior endpoint digests are required")
    for digest in prior_endpoint_sha256s:
        _require_digest(digest, "prior endpoint")
    _require_digest(reviewed_manifest_sha256, "reviewed_manifest_sha256")
    if not isinstance(reviewed_anchor_sha256s, frozenset) or not reviewed_anchor_sha256s:
        raise ValueError("reviewed_anchor_sha256s must be a non-empty frozenset")
    for digest in reviewed_anchor_sha256s:
        _require_digest(digest, "reviewed anchor")
    available = {_frame_sha256(spec.frame) for spec in base_specs}
    if FROZEN_ENDPOINT_SOURCE_SHA256 not in available:
        raise ValueError("frozen bridge source is absent from the frozen corpus")
    if not set(prior_endpoint_sha256s).issubset(available):
        raise ValueError("prior endpoint digest is absent from the frozen corpus")
    if not reviewed_anchor_sha256s.issubset(available):
        raise ValueError("reviewed anchor digest is absent from the frozen corpus")


def _audit_edge(
    graph: ReadOnlyViewGraph,
    first_sha256: str,
    second_sha256: str,
) -> BridgeEdgeAudit:
    edge = _find_edge(graph, first_sha256, second_sha256)
    registration = edge.registration
    model = None if registration is None else registration.selected_model
    return BridgeEdgeAudit(
        edge_id=edge.edge_id,
        registration_accepted=edge.registration_accepted,
        cycle_verified=edge.verified(graph.policy),
        all_required_zones=_edge_is_all_zone_bridge(edge, graph),
        supporting_cycle_ids=edge.supporting_cycle_ids,
        selected_model_family=(
            None if model is None else model.family.value
        ),
        required_zones=(
            () if registration is None else registration.required_zones
        ),
        source_zone_inliers=(
            () if model is None else model.source_zone_inliers
        ),
        target_zone_inliers=(
            () if model is None else model.target_zone_inliers
        ),
        source_zone_cells=(() if model is None else model.source_zone_cells),
        target_zone_cells=(() if model is None else model.target_zone_cells),
        median_residual_px=(None if model is None else model.median_residual_px),
        p90_residual_px=(None if model is None else model.p90_residual_px),
        cycle_median_px=(None if model is None else model.cycle_median_px),
        cycle_p90_px=(None if model is None else model.cycle_p90_px),
    )


def _audit_anchor(
    graph: ReadOnlyViewGraph,
    anchor_sha256: str,
    endpoints: tuple[str, str, str],
) -> BridgeAnchorAudit:
    edge_ids: list[str] = []
    complete = True
    for endpoint_sha256 in endpoints:
        if endpoint_sha256 == anchor_sha256:
            complete = False
            continue
        edge = _find_edge(graph, endpoint_sha256, anchor_sha256)
        edge_ids.append(edge.edge_id)
        complete = complete and _edge_is_all_zone_bridge(edge, graph)
    return BridgeAnchorAudit(
        anchor_sha256=anchor_sha256,
        endpoint_edge_ids=tuple(sorted(edge_ids)),
        complete=complete and len(edge_ids) == len(endpoints),
    )


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


def _verified_visual_path(
    graph: ReadOnlyViewGraph,
    *,
    source_sha256: str,
    targets: frozenset[str],
) -> tuple[str, ...] | None:
    if source_sha256 in targets:
        return (source_sha256,)
    negative = frozenset(
        node.sha256 for node in graph.nodes if node.negative_graph_case
    )
    if source_sha256 in negative:
        return None
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if (
            _edge_is_all_zone_bridge(edge, graph)
            and edge.source_sha256 not in negative
            and edge.target_sha256 not in negative
        ):
            adjacency[edge.source_sha256].add(edge.target_sha256)
            adjacency[edge.target_sha256].add(edge.source_sha256)
    queue: deque[tuple[str, tuple[str, ...]]] = deque(
        ((source_sha256, (source_sha256,)),)
    )
    visited = {source_sha256}
    while queue:
        node, path = queue.popleft()
        for target in sorted(adjacency.get(node, ())):
            if target in visited:
                continue
            candidate = (*path, target)
            if target in targets:
                return candidate
            visited.add(target)
            queue.append((target, candidate))
    return None


def _mixed_bridge_path(
    *,
    source_prefix_path: tuple[str, ...] | None,
    action_path: tuple[str, ...] | None,
    visual_terminal_path: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if (
        source_prefix_path is None
        or action_path is None
        or visual_terminal_path is None
        or source_prefix_path[-1] != action_path[0]
        or action_path[-1] != visual_terminal_path[0]
    ):
        return None
    return (
        *source_prefix_path,
        *action_path[1:],
        *visual_terminal_path[1:],
    )


def _find_edge(
    graph: ReadOnlyViewGraph,
    first_sha256: str,
    second_sha256: str,
) -> GraphEdgeEvidence:
    edge_id = ":".join(sorted((first_sha256, second_sha256)))
    try:
        return next(edge for edge in graph.edges if edge.edge_id == edge_id)
    except StopIteration as error:  # pragma: no cover - graph builder invariant
        raise ValueError(f"view graph omitted expected edge {edge_id}") from error


def _is_fail_closed(evaluation: CameraEvaluation) -> bool:
    return (
        not evaluation.passed
        and not evaluation.scene_validated
        and evaluation.definitive_target_ids == ()
        and bool(evaluation.resource_states)
        and all(
            resource.state is ResourceVisualState.UNCERTAIN
            for resource in evaluation.resource_states
        )
    )


def _frame_sha256(frame: Frame) -> str:
    return hashlib.sha256(frame.payload).hexdigest()


def _production_dict(evaluation: CameraEvaluation) -> dict[str, object]:
    return {
        "definitive_target_ids": list(evaluation.definitive_target_ids),
        "detector_id": evaluation.detector_id,
        "detector_version": evaluation.detector_version,
        "fail_closed": _is_fail_closed(evaluation),
        "matched_landmark_count": evaluation.matched_landmark_count,
        "matched_zones": [zone.value for zone in evaluation.matched_zones],
        "passed": evaluation.passed,
        "profile_id": evaluation.profile_id,
        "resource_states": [
            {
                "resource_id": resource.resource_id,
                "state": resource.state.value,
            }
            for resource in evaluation.resource_states
        ],
        "scene_validated": evaluation.scene_validated,
    }


def _zone_counts_dict(
    values: tuple[tuple[MacroZone, int], ...],
) -> dict[str, int]:
    return {zone.value: count for zone, count in values}
