"""Deterministic safety tests for the read-only Issue #31 bridge planner."""

from __future__ import annotations

import hashlib
import json

from mining_automation.capture import PixelFormat
from mining_automation.perception.resource import ResourceVisualState
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation.camera_bridge_capture import (
    CAMERA_BRIDGE_CAPTURE_ID,
)
from mining_automation.validation.camera_bridge_planner import (
    CAMERA_BRIDGE_PLANNER_ID,
    CAMERA_BRIDGE_PLANNER_VERSION,
    FROZEN_ENDPOINT_FAMILY_ID,
    FROZEN_ENDPOINT_OBJECTIVE,
    FROZEN_ENDPOINT_OBJECTIVE_ID,
    FROZEN_ENDPOINT_SOURCE_SHA256,
    BridgePlannerDisposition,
    FrozenPrimitiveExperiment,
    FrozenPrimitiveInventory,
    MeasuredEndpointEvidence,
    plan_camera_bridge,
)
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    CameraLandmarkEvaluation,
    CameraResourceEvaluation,
)
from mining_automation.validation.camera_plan import CameraHoldKey
from mining_automation.validation.client_readiness import (
    ClientInputReadiness,
    ClientReadinessAnchorEvaluation,
    ClientReadinessAnchorPolicy,
    ClientReadinessReason,
)
from mining_automation.validation.robust_registration import (
    CorrespondenceEvidence,
    EndpointEvidence,
    ModelEvidence,
    ModelFamily,
    RegistrationDisposition,
    RegistrationPolicy,
    RobustWorldRegistration,
)
from mining_automation.validation.robust_view_graph import (
    ActionTransition,
    GraphEdgeEvidence,
    GraphNodeEvidence,
    GraphPolicy,
    NegativeNodeGraphEvidence,
    ReadOnlyViewGraph,
    ViewRole,
)

_ZONES = (
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_EAST,
    MacroZone.SOUTH_WEST,
)
_IDENTITY = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)
_READY_POLICY = ClientReadinessAnchorPolicy(
    "test-gameplay-chrome",
    (0, 0, 1, 1),
    minimum_edge_density=0.1,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _readiness(*, safe: bool = True) -> ClientInputReadiness:
    return ClientInputReadiness(
        evaluator_id="test-readiness",
        evaluator_version="1.0.0",
        reason=(
            ClientReadinessReason.READY
            if safe
            else ClientReadinessReason.GAMEPLAY_CHROME_MISMATCH
        ),
        detail="deterministic test endpoint",
        anchors=(
            ClientReadinessAnchorEvaluation(
                policy=_READY_POLICY,
                luma_stddev=20.0 if safe else 0.0,
                edge_density=0.5 if safe else 0.0,
                dark_fraction=0.0 if safe else 1.0,
                matched=safe,
            ),
        ),
        safe_to_attempt_camera_input=safe,
    )


def _production(*, passed: bool) -> CameraEvaluation:
    landmark_zones = (*_ZONES, *_ZONES)
    landmarks = tuple(
        CameraLandmarkEvaluation(
            landmark_id=f"landmark-{index}",
            distance=0.1 if passed and index < 5 else 2.0,
            threshold=1.0,
            matched=passed and index < 5,
            zone=zone,
        )
        for index, zone in enumerate(landmark_zones)
    )
    state = (
        ResourceVisualState.AVAILABLE
        if passed
        else ResourceVisualState.UNCERTAIN
    )
    return CameraEvaluation(
        detector_id="test-production-detector",
        detector_version="1.0.0",
        profile_id="test-reviewed-profile",
        profile_schema_version=2,
        profile_frame_width=1,
        profile_frame_height=1,
        profile_pixel_format=PixelFormat.BGRA8888,
        frame_geometry_supported=True,
        landmarks=landmarks,
        matched_landmark_count=5 if passed else 0,
        required_landmark_count=6,
        required_landmark_matches=5,
        matched_zones=_ZONES if passed else (),
        required_matched_zones=3,
        scene_reason="validated" if passed else "insufficient_landmark_quorum",
        scene_validated=passed,
        resource_states=(CameraResourceEvaluation("rock", state, 1.0),),
        definitive_target_ids=("rock",) if passed else (),
        passed=passed,
    )


def _node(
    label: str,
    *,
    roles: tuple[ViewRole, ...],
    current: bool = False,
    anchor: bool = False,
    production_passed: bool = False,
    readiness_safe: bool = True,
    sha256: str | None = None,
) -> GraphNodeEvidence:
    return GraphNodeEvidence(
        sha256=_sha(label) if sha256 is None else sha256,
        payload_bytes=4,
        width=1,
        height=1,
        pixel_format=PixelFormat.BGRA8888.value,
        labels=(label,),
        roles=roles,
        readiness=_readiness(safe=readiness_safe),
        production=_production(passed=production_passed),
        reviewed_supported_anchor=anchor,
        current=current,
    )


def _registration(
    source_sha256: str,
    target_sha256: str,
    *,
    accepted: bool = True,
    included_zones: tuple[MacroZone, ...] = _ZONES,
    forward_matrix: tuple[tuple[float, float, float], ...] = _IDENTITY,
) -> RobustWorldRegistration:
    policy = RegistrationPolicy()
    reverse_matrix = _IDENTITY
    model = ModelEvidence(
        family=ModelFamily.TRANSLATION,
        forward_matrix=forward_matrix,
        reverse_matrix=reverse_matrix,
        inliers=60,
        inlier_ratio=0.8,
        source_zone_inliers=tuple((zone, 20) for zone in included_zones),
        target_zone_inliers=tuple((zone, 20) for zone in included_zones),
        source_zone_cells=tuple((zone, 5) for zone in included_zones),
        target_zone_cells=tuple((zone, 5) for zone in included_zones),
        median_residual_px=0.2,
        p90_residual_px=0.4,
        cycle_median_px=0.1,
        cycle_p90_px=0.2,
        distortion=None,
        adequate=accepted,
        rejection_reasons=() if accepted else ("test rejection",),
    )
    endpoint_kwargs = {
        "payload_bytes": 4,
        "width": 1,
        "height": 1,
        "pixel_format": PixelFormat.BGRA8888.value,
    }
    return RobustWorldRegistration(
        registration_id="test-registration",
        registration_version="1.0.0",
        source=EndpointEvidence(
            payload_sha256=source_sha256,
            **endpoint_kwargs,
        ),
        target=EndpointEvidence(
            payload_sha256=target_sha256,
            **endpoint_kwargs,
        ),
        profile_id="test-profile",
        profile_fingerprint_sha256=_sha("profile"),
        exclusion_fingerprint_sha256=_sha("exclusions"),
        algorithm_fingerprint_sha256=_sha("algorithm"),
        policy_fingerprint_sha256=_sha("policy"),
        disposition=(
            RegistrationDisposition.ACCEPTED
            if accepted
            else RegistrationDisposition.GLOBAL_MODEL_INADEQUATE
        ),
        detail="deterministic registration",
        correspondence=CorrespondenceEvidence(
            source_features=100,
            target_features=100,
            total_forward_matches=80,
            total_reverse_matches=80,
            forward_ratio_matches=70,
            reverse_ratio_matches=70,
            mutual_matches=60,
            balanced_matches=60,
            per_zone_mutual_matches=tuple(
                (zone, 20) for zone in included_zones
            ),
        ),
        required_zones=_ZONES,
        excluded_regions=(),
        models=(model,),
        selected_family=ModelFamily.TRANSLATION if accepted else None,
        policy=policy,
    )


def _edge(
    source_sha256: str,
    target_sha256: str,
    *,
    accepted: bool = True,
    included_zones: tuple[MacroZone, ...] = _ZONES,
    action_ids: tuple[str, ...] = (),
    cycle: bool = True,
    forward_matrix: tuple[tuple[float, float, float], ...] = _IDENTITY,
) -> GraphEdgeEvidence:
    source, target = sorted((source_sha256, target_sha256))
    return GraphEdgeEvidence(
        edge_id=f"{source}:{target}",
        source_sha256=source,
        target_sha256=target,
        registration=_registration(
            source,
            target,
            accepted=accepted,
            included_zones=included_zones,
            forward_matrix=forward_matrix,
        ),
        pre_registration_rejection=None,
        supporting_cycle_ids=("cycle-1",) if cycle else (),
        action_ids=action_ids,
    )


def _transition(
    experiment: FrozenPrimitiveExperiment,
    source_sha256: str,
    target_sha256: str,
    *,
    report_sha256: str | None = None,
) -> ActionTransition:
    return ActionTransition(
        action_id=experiment.action_id,
        source_sha256=source_sha256,
        target_sha256=target_sha256,
        evidence_report_sha256=(
            experiment.selection_backing_report_sha256s[0]
            if report_sha256 is None
            else report_sha256
        ),
        receipt_verified=True,
    )


def _graph(
    nodes: tuple[GraphNodeEvidence, ...],
    *,
    edges: tuple[GraphEdgeEvidence, ...] = (),
    components: tuple[tuple[str, ...], ...] | None = None,
    transitions: tuple[ActionTransition, ...] = (),
) -> ReadOnlyViewGraph:
    current = next(node for node in nodes if node.current)
    negative_nodes = tuple(
        NegativeNodeGraphEvidence(
            sha256=node.sha256,
            labels=node.labels,
            roles=tuple(
                role
                for role in node.roles
                if role in {ViewRole.DISCONNECTED, ViewRole.RISKY_STATE_CHANGE}
            ),
            registration_eligible=True,
            accepted_edge_ids=(),
            verified_edge_ids=(),
            verified_path_to_supported=None,
        )
        for node in nodes
        if any(
            role in {ViewRole.DISCONNECTED, ViewRole.RISKY_STATE_CHANGE}
            for role in node.roles
        )
    )
    if components is None:
        components = (tuple(sorted(node.sha256 for node in nodes)),)
    return ReadOnlyViewGraph(
        graph_id="test-view-graph",
        graph_version="1.0.0",
        reviewed_manifest_sha256=_sha("manifest"),
        current_sha256=current.sha256,
        nodes=nodes,
        edges=edges,
        cycles=(),
        components=components,
        visual_path_to_supported=None,
        action_path_to_supported=None,
        action_transitions=transitions,
        negative_nodes=negative_nodes,
        negative_accepted_edge_ids=(),
        negative_verified_edge_ids=(),
        false_edge_count=0,
        conclusion="test graph",
        missing_link=None,
        policy=GraphPolicy(),
    )


def _inventory(
    *experiments: FrozenPrimitiveExperiment,
) -> FrozenPrimitiveInventory:
    return FrozenPrimitiveInventory(
        inventory_id="test-frozen-inventory",
        inventory_version="1.0.0",
        experiments=experiments,
    )


def _experiment(
    family_id: str,
    *,
    ordinal: int = 1,
    receipt_label: str | None = None,
) -> FrozenPrimitiveExperiment:
    experiment_id = f"{family_id}:right-key-hold-0.043s"
    return FrozenPrimitiveExperiment(
        experiment_id=experiment_id,
        family_id=family_id,
        action_id=CAMERA_BRIDGE_CAPTURE_ID,
        ordinal=ordinal,
        key=CameraHoldKey.RIGHT,
        duration_s=0.043,
        required_source_sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
        selection_backing_report_sha256s=(_sha(receipt_label or family_id),),
    )


def _endpoint(
    *,
    evidence_id: str,
    family_id: str,
    source_sha256: str,
    target_sha256: str,
    receipt_sha256: str,
    experiment_id: str | None = None,
    target_roles: tuple[ViewRole, ...] = (ViewRole.SYSTEM_IDENTIFICATION,),
    production_passed: bool = False,
    production_zones: tuple[MacroZone, ...] = (),
    production_matches: int = 0,
    registration_accepted: bool = True,
    registration_cycle_verified: bool = True,
    registration_zones: tuple[MacroZone, ...] = _ZONES,
    inlier_ratio: float = 0.8,
    inliers: int = 60,
    mutual_matches: int = 70,
) -> MeasuredEndpointEvidence:
    return MeasuredEndpointEvidence(
        evidence_id=evidence_id,
        family_id=family_id,
        experiment_id=experiment_id or f"{family_id}:prior-measurement",
        source_sha256=source_sha256,
        target_sha256=target_sha256,
        receipt_report_sha256=receipt_sha256,
        receipt_verified=True,
        readiness_safe=True,
        target_roles=target_roles,
        production_passed=production_passed,
        production_matched_landmarks=production_matches,
        production_matched_zones=production_zones,
        registration_accepted=registration_accepted,
        registration_cycle_verified=registration_cycle_verified,
        registration_matched_zones=registration_zones,
        mutual_matches=mutual_matches,
        inliers=inliers,
        inlier_ratio=inlier_ratio,
        median_residual_px=0.2,
        p90_residual_px=0.4,
        cycle_p90_px=0.3,
    )


def test_repeated_frozen_endpoint_selects_first_missing_right_hold() -> None:
    current = _node(
        "compass-north",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first = _node("repeat-target-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    second = _node("repeat-target-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    anchor = _node(
        "anchor",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    graph = _graph(
        (current, first, second, anchor),
        edges=(
            _edge(first.sha256, second.sha256),
            _edge(first.sha256, anchor.sha256),
            _edge(second.sha256, anchor.sha256),
        ),
        components=(
            (current.sha256,),
            tuple(sorted((first.sha256, second.sha256, anchor.sha256))),
        ),
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    evidence = tuple(
        _endpoint(
            evidence_id=f"repeat-{index}",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=(first.sha256, second.sha256)[index - 1],
            receipt_sha256=receipt,
            experiment_id=f"legacy-repeat-{index}",
            production_passed=True,
            production_zones=_ZONES,
            production_matches=5,
        )
        for index, receipt in enumerate(receipts, start=1)
    )

    result = plan_camera_bridge(graph, evidence)

    assert result.planner_id == CAMERA_BRIDGE_PLANNER_ID
    assert result.planner_version == CAMERA_BRIDGE_PLANNER_VERSION
    assert result.disposition is BridgePlannerDisposition.MISSING_EXPERIMENT
    assert result.bridge_evidence_available is False
    assert result.missing_experiment is not None
    assert result.missing_experiment.action_id == CAMERA_BRIDGE_CAPTURE_ID
    assert result.missing_experiment.family_id == FROZEN_ENDPOINT_FAMILY_ID
    assert result.missing_experiment.experiment_id == FROZEN_ENDPOINT_OBJECTIVE_ID
    assert result.missing_experiment.key is CameraHoldKey.RIGHT
    assert result.missing_experiment.duration_s == 0.043
    assert result.missing_experiment.can_execute_input is False
    assert result.missing_experiment.uses_rejected_registration_matrix is False
    assert all(
        endpoint.production_passed is False
        for endpoint in result.ranked_families[0].evaluation.endpoints
    )


def test_negative_nodes_and_transitions_are_quarantined_from_bridge() -> None:
    current = _node(
        "current-negative-case",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    risky = _node(
        "risky",
        roles=(ViewRole.RISKY_STATE_CHANGE,),
    )
    anchor = _node(
        "anchor-negative-case",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    first = _experiment("first", receipt_label="first-receipt")
    second = _experiment("second", receipt_label="second-receipt")
    inventory = _inventory(first, second)
    graph = _graph(
        (current, risky, anchor),
        edges=(
            _edge(
                current.sha256,
                risky.sha256,
                action_ids=(first.action_id,),
            ),
            _edge(
                risky.sha256,
                anchor.sha256,
                action_ids=(second.action_id,),
            ),
        ),
        transitions=(
            _transition(first, current.sha256, risky.sha256),
            _transition(second, risky.sha256, anchor.sha256),
        ),
    )
    risky_endpoint = _endpoint(
        evidence_id="risky-endpoint",
        family_id=first.family_id,
        source_sha256=current.sha256,
        target_sha256=risky.sha256,
        receipt_sha256=first.selection_backing_report_sha256s[0],
        target_roles=(ViewRole.RISKY_STATE_CHANGE,),
    )

    result = plan_camera_bridge(
        graph,
        (risky_endpoint,),
        inventory=inventory,
    )

    assert result.bridge_evidence_available is False
    assert result.bridge_node_path is None
    assert result.bridge_action_ids == ()
    assert risky.sha256 in result.quarantined_sha256s
    assert risky.sha256 not in result.current_safe_component
    assert result.excluded_endpoint_evidence_ids == ("risky-endpoint",)


def test_disconnected_role_remains_quarantined() -> None:
    current = _node(
        "current-disconnected-case",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
    )
    disconnected = _node(
        "readiness-veto-node",
        roles=(ViewRole.DISCONNECTED,),
        readiness_safe=False,
    )
    graph = _graph((current, disconnected))

    result = plan_camera_bridge(graph, ())

    assert result.quarantined_sha256s == (disconnected.sha256,)
    assert disconnected.sha256 not in result.current_safe_component
    assert disconnected.registration_eligible is False
    assert result.disposition is BridgePlannerDisposition.NO_SAFE_ENDPOINT_EVIDENCE


def test_readiness_vetoed_current_has_no_safe_component_or_experiment() -> None:
    current = _node(
        "readiness-vetoed-current",
        roles=(ViewRole.OTHER_UNSUPPORTED,),
        current=True,
        readiness_safe=False,
    )
    anchor = _node(
        "anchor-readiness-veto",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    receipt = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s[0]
    prior = _endpoint(
        evidence_id="unsafe-current-prior",
        family_id=FROZEN_ENDPOINT_FAMILY_ID,
        source_sha256=current.sha256,
        target_sha256=_sha("unsafe-current-target"),
        receipt_sha256=receipt,
    )
    graph = _graph((current, anchor))

    result = plan_camera_bridge(graph, (prior,))

    assert result.current_safe_component == ()
    assert result.missing_experiment is None
    assert result.excluded_endpoint_evidence_ids == ("unsafe-current-prior",)
    assert result.disposition is BridgePlannerDisposition.NO_SAFE_ENDPOINT_EVIDENCE


def test_action_edge_missing_one_macro_zone_is_not_a_bridge() -> None:
    current = _node(
        "current-missing-zone",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    anchor = _node(
        "anchor-missing-zone",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    objective = _experiment("missing-zone")
    graph = _graph(
        (current, anchor),
        edges=(
            _edge(
                current.sha256,
                anchor.sha256,
                included_zones=(
                    MacroZone.NORTH_WEST,
                    MacroZone.SOUTH_WEST,
                ),
                action_ids=(objective.action_id,),
            ),
        ),
        transitions=(_transition(objective, current.sha256, anchor.sha256),),
    )

    result = plan_camera_bridge(
        graph,
        (),
        inventory=_inventory(objective),
    )

    assert result.bridge_evidence_available is False
    assert result.bridge_node_path is None
    assert result.can_authorize_camera_input is False


def test_action_from_non_frozen_source_cannot_create_a_bridge() -> None:
    current = _node(
        "non-frozen-source",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
    )
    anchor = _node(
        "anchor-forged-receipt",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    objective = _experiment("forged-receipt")
    forged_transition = ActionTransition(
        action_id=objective.action_id,
        source_sha256=current.sha256,
        target_sha256=anchor.sha256,
        evidence_report_sha256=_sha("unreviewed-report"),
        receipt_verified=True,
    )
    graph = _graph(
        (current, anchor),
        edges=(
            _edge(
                current.sha256,
                anchor.sha256,
                action_ids=(objective.action_id,),
            ),
        ),
        transitions=(forged_transition,),
    )

    result = plan_camera_bridge(
        graph,
        (),
        inventory=_inventory(objective),
    )

    assert result.bridge_evidence_available is False
    assert result.bridge_action_ids == ()


def test_transition_action_must_be_bound_to_the_exact_registration_edge() -> None:
    current = _node(
        "current-unbound-action",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    anchor = _node(
        "anchor-unbound-action",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    objective = _experiment("unbound-action")
    graph = _graph(
        (current, anchor),
        edges=(
            _edge(
                current.sha256,
                anchor.sha256,
                action_ids=("different-action",),
            ),
        ),
        transitions=(_transition(objective, current.sha256, anchor.sha256),),
    )

    result = plan_camera_bridge(
        graph,
        (),
        inventory=_inventory(objective),
    )

    assert result.bridge_evidence_available is False
    assert result.bridge_action_ids == ()


def test_pairwise_acceptance_without_cycle_support_is_not_a_bridge() -> None:
    current = _node(
        "current-no-cycle",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    anchor = _node(
        "anchor-no-cycle",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    objective = _experiment("no-cycle")
    source, target = sorted((current.sha256, anchor.sha256))
    pairwise_only = GraphEdgeEvidence(
        edge_id=f"{source}:{target}",
        source_sha256=source,
        target_sha256=target,
        registration=_registration(source, target),
        pre_registration_rejection=None,
        supporting_cycle_ids=(),
        action_ids=(objective.action_id,),
    )
    graph = _graph(
        (current, anchor),
        edges=(pairwise_only,),
        transitions=(_transition(objective, current.sha256, anchor.sha256),),
    )

    result = plan_camera_bridge(
        graph,
        (),
        inventory=_inventory(objective),
    )

    assert pairwise_only.registration_accepted is True
    assert pairwise_only.verified(graph.policy) is False
    assert result.bridge_evidence_available is False


def test_all_zone_cycle_verified_receipt_edge_is_evidence_not_authority() -> None:
    current = _node(
        "north-valid-bridge",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    fresh = _node("fresh-post-action", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    anchor = _node(
        "anchor-valid-bridge",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    objective = _experiment("valid-bridge")
    new_report_sha256 = _sha("new-honest-bridge-report")
    assert (
        new_report_sha256
        not in objective.selection_backing_report_sha256s
    )
    graph = _graph(
        (current, fresh, anchor),
        edges=(
            _edge(
                current.sha256,
                fresh.sha256,
                action_ids=(objective.action_id,),
            ),
            _edge(fresh.sha256, anchor.sha256),
        ),
        transitions=(
            _transition(
                objective,
                current.sha256,
                fresh.sha256,
                report_sha256=new_report_sha256,
            ),
        ),
    )

    result = plan_camera_bridge(
        graph,
        (),
        inventory=_inventory(objective),
    )

    assert result.disposition is BridgePlannerDisposition.BRIDGE_EVIDENCE_AVAILABLE
    assert result.bridge_node_path == (
        current.sha256,
        fresh.sha256,
        anchor.sha256,
    )
    assert result.bridge_action_ids == (objective.action_id,)
    assert result.bridge_report_sha256s == (new_report_sha256,)
    assert result.bridge_evidence_available is True
    assert result.can_accept is False
    assert result.can_validate_scene is False
    assert result.can_expose_resources is False
    assert result.can_authorize_camera_input is False
    assert result.diagnostic_registration_can_override_production is False


def test_production_pass_alone_does_not_grant_bridge_or_authority() -> None:
    current = _node(
        "current-production-alone",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
    )
    anchor = _node(
        "anchor-production-alone",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    graph = _graph((current, anchor))

    result = plan_camera_bridge(graph, ())
    serialized = result.as_dict()

    assert anchor.production.passed is True
    assert result.bridge_evidence_available is False
    assert result.disposition is BridgePlannerDisposition.NO_SAFE_ENDPOINT_EVIDENCE
    assert serialized["authority"] == {
        "can_accept": False,
        "can_authorize_camera_input": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
        "diagnostic_registration_can_override_production": False,
    }


def test_production_definitive_endpoint_cannot_qualify_unsupported_family() -> None:
    current = _node(
        "north-production-endpoint",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first = _node(
        "production-repeat-1",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        production_passed=True,
    )
    second = _node(
        "production-repeat-2",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        production_passed=True,
    )
    anchor = _node(
        "production-repeat-anchor",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    graph = _graph(
        (current, first, second, anchor),
        edges=(
            _edge(first.sha256, second.sha256),
            _edge(first.sha256, anchor.sha256),
            _edge(second.sha256, anchor.sha256),
        ),
        components=(
            (current.sha256,),
            tuple(sorted((first.sha256, second.sha256, anchor.sha256))),
        ),
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    evidence = (
        _endpoint(
            evidence_id="production-endpoint-1",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=first.sha256,
            receipt_sha256=receipts[0],
        ),
        _endpoint(
            evidence_id="production-endpoint-2",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=second.sha256,
            receipt_sha256=receipts[1],
        ),
    )

    result = plan_camera_bridge(graph, evidence)

    assert result.disposition is BridgePlannerDisposition.NO_SAFE_ENDPOINT_EVIDENCE
    assert result.missing_experiment is None
    assert result.ranked_families == ()
    assert {item.reason for item in result.excluded_endpoints} == {
        "endpoint_not_production_fail_closed"
    }


def test_ranking_and_serialization_are_deterministic_under_shuffle() -> None:
    current = _node(
        "north-ranking",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first_endpoint = _node(
        "rank-repeat-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,)
    )
    second_endpoint = _node(
        "rank-repeat-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,)
    )
    anchor = _node(
        "anchor-ranking",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    first_evidence = _endpoint(
        evidence_id="repeat-a",
        family_id=FROZEN_ENDPOINT_FAMILY_ID,
        source_sha256=current.sha256,
        target_sha256=first_endpoint.sha256,
        receipt_sha256=receipts[0],
    )
    second_evidence = _endpoint(
        evidence_id="repeat-b",
        family_id=FROZEN_ENDPOINT_FAMILY_ID,
        source_sha256=current.sha256,
        target_sha256=second_endpoint.sha256,
        receipt_sha256=receipts[1],
    )
    edges = (
        _edge(first_endpoint.sha256, second_endpoint.sha256),
        _edge(first_endpoint.sha256, anchor.sha256),
        _edge(second_endpoint.sha256, anchor.sha256),
    )
    graph = _graph(
        (current, first_endpoint, second_endpoint, anchor),
        edges=edges,
        components=(
            (current.sha256,),
            tuple(
                sorted(
                    (first_endpoint.sha256, second_endpoint.sha256, anchor.sha256)
                )
            ),
        ),
    )
    shuffled = _graph(
        (anchor, second_endpoint, current, first_endpoint),
        edges=tuple(reversed(edges)),
        components=tuple(reversed(graph.components)),
    )

    first = plan_camera_bridge(
        graph,
        (second_evidence, first_evidence),
    )
    second = plan_camera_bridge(
        shuffled,
        (first_evidence, second_evidence),
    )

    assert first.as_dict() == second.as_dict()
    assert first.ranked_families[0].family_id == FROZEN_ENDPOINT_FAMILY_ID
    assert first.missing_experiment is not None
    assert first.missing_experiment.family_id == FROZEN_ENDPOINT_FAMILY_ID
    assert json.dumps(
        first.as_dict(), sort_keys=True, separators=(",", ":")
    ) == json.dumps(second.as_dict(), sort_keys=True, separators=(",", ":"))


def test_rejected_matrix_coefficients_never_enter_control_recommendation() -> None:
    current = _node(
        "north-rejected-matrix",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first_endpoint = _node(
        "rejected-repeat-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,)
    )
    second_endpoint = _node(
        "rejected-repeat-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,)
    )
    anchor = _node(
        "anchor-rejected-matrix",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    rejected_coefficients = (
        (1.0, 0.0, 9123.456789),
        (0.0, 1.0, -8765.432198),
        (0.0, 0.0, 1.0),
    )
    graph = _graph(
        (current, first_endpoint, second_endpoint, anchor),
        edges=(
            _edge(first_endpoint.sha256, second_endpoint.sha256),
            _edge(first_endpoint.sha256, anchor.sha256),
            _edge(
                second_endpoint.sha256,
                anchor.sha256,
                accepted=False,
                forward_matrix=rejected_coefficients,
            ),
        ),
        components=((current.sha256,),),
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    records = (
        _endpoint(
            evidence_id="repeat-1",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=first_endpoint.sha256,
            receipt_sha256=receipts[0],
        ),
        _endpoint(
            evidence_id="repeat-2",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=second_endpoint.sha256,
            receipt_sha256=receipts[1],
            registration_accepted=True,
            inlier_ratio=1.0,
        ),
    )

    result = plan_camera_bridge(graph, records)
    report_json = json.dumps(result.as_dict(), sort_keys=True)

    assert result.ranked_families == ()
    assert result.missing_experiment is None
    assert "forward_matrix" not in report_json
    assert "reverse_matrix" not in report_json
    assert "9123.456789" not in report_json
    assert "-8765.432198" not in report_json


def test_absent_endpoint_node_is_excluded_before_family_selection() -> None:
    current = _node(
        "north-absent",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    anchor = _node(
        "anchor-absent",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    absent = _endpoint(
        evidence_id="absent-endpoint",
        family_id=FROZEN_ENDPOINT_FAMILY_ID,
        source_sha256=current.sha256,
        target_sha256=_sha("not-in-graph"),
        receipt_sha256=receipts[0],
    )

    result = plan_camera_bridge(_graph((current, anchor)), (absent,))

    assert result.ranked_families == ()
    assert result.excluded_endpoints[0].reason == "endpoint_not_in_graph"


def test_graph_roles_and_readiness_override_forged_safe_caller_claims() -> None:
    current = _node(
        "north-forged-claims",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    risky = _node("risky-claim", roles=(ViewRole.RISKY_STATE_CHANGE,))
    vetoed = _node(
        "vetoed-claim",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        readiness_safe=False,
    )
    anchor = _node(
        "anchor-forged-claims",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    risky_claim = _endpoint(
        evidence_id="risky-safe-claim",
        family_id=FROZEN_ENDPOINT_FAMILY_ID,
        source_sha256=current.sha256,
        target_sha256=risky.sha256,
        receipt_sha256=receipts[0],
        target_roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        production_passed=True,
        production_zones=_ZONES,
        production_matches=6,
    )
    vetoed_claim = _endpoint(
        evidence_id="vetoed-safe-claim",
        family_id=FROZEN_ENDPOINT_FAMILY_ID,
        source_sha256=current.sha256,
        target_sha256=vetoed.sha256,
        receipt_sha256=receipts[1],
        target_roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        production_passed=True,
        production_zones=_ZONES,
        production_matches=6,
    )

    result = plan_camera_bridge(
        _graph((current, risky, vetoed, anchor)),
        (risky_claim, vetoed_claim),
    )

    assert {(item.evidence_id, item.reason) for item in result.excluded_endpoints} == {
        ("risky-safe-claim", "endpoint_negative_graph_role"),
        ("vetoed-safe-claim", "endpoint_readiness_veto"),
    }
    assert result.ranked_families == ()


def test_repeated_family_requires_distinct_authenticated_reports() -> None:
    current = _node(
        "north-distinct-reports",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first = _node("same-report-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    second = _node("same-report-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    anchor = _node(
        "anchor-same-report",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    receipt = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s[0]
    graph = _graph(
        (current, first, second, anchor),
        edges=(
            _edge(first.sha256, second.sha256),
            _edge(first.sha256, anchor.sha256),
            _edge(second.sha256, anchor.sha256),
        ),
    )
    records = (
        _endpoint(
            evidence_id="same-report-a",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=first.sha256,
            receipt_sha256=receipt,
        ),
        _endpoint(
            evidence_id="same-report-b",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=second.sha256,
            receipt_sha256=receipt,
        ),
    )

    result = plan_camera_bridge(graph, records)

    assert result.ranked_families == ()
    assert "insufficient_distinct_receipt_reports:1/2" in (
        result.family_evaluations[0].failure_reasons
    )


def test_missing_endpoint_to_anchor_edge_keeps_family_incomplete() -> None:
    current = _node(
        "north-missing-anchor",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first = _node("missing-anchor-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    second = _node("missing-anchor-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    anchor = _node(
        "missing-anchor-target",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    graph = _graph(
        (current, first, second, anchor),
        edges=(
            _edge(first.sha256, second.sha256),
            _edge(first.sha256, anchor.sha256),
        ),
    )
    records = (
        _endpoint(
            evidence_id="missing-anchor-a",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=first.sha256,
            receipt_sha256=receipts[0],
        ),
        _endpoint(
            evidence_id="missing-anchor-b",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=second.sha256,
            receipt_sha256=receipts[1],
        ),
    )

    result = plan_camera_bridge(graph, records)

    assert result.ranked_families == ()
    evaluation = result.family_evaluations[0]
    assert "no_common_supported_anchor_all_zones" in evaluation.failure_reasons
    assert evaluation.qualifying_common_anchor_sha256s == ()
    assert evaluation.anchor_evaluations[0].missing_edge_ids == (
        ":".join(sorted((second.sha256, anchor.sha256))),
    )


def test_family_is_complete_when_every_endpoint_reaches_one_common_anchor() -> None:
    current = _node(
        "north-common-anchor",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first = _node("common-anchor-endpoint-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    second = _node("common-anchor-endpoint-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    common = _node(
        "common-supported-anchor",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    unrelated = _node(
        "unrelated-supported-anchor",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    graph = _graph(
        (current, first, second, common, unrelated),
        edges=(
            _edge(first.sha256, second.sha256),
            _edge(first.sha256, common.sha256),
            _edge(second.sha256, common.sha256),
            _edge(first.sha256, unrelated.sha256),
        ),
        components=(
            (current.sha256,),
            tuple(sorted((first.sha256, second.sha256, common.sha256, unrelated.sha256))),
        ),
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    records = (
        _endpoint(
            evidence_id="common-anchor-a",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=first.sha256,
            receipt_sha256=receipts[0],
        ),
        _endpoint(
            evidence_id="common-anchor-b",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=second.sha256,
            receipt_sha256=receipts[1],
        ),
    )

    result = plan_camera_bridge(graph, records)

    evaluation = result.family_evaluations[0]
    assert evaluation.complete is True
    assert evaluation.failure_reasons == ()
    assert evaluation.qualifying_common_anchor_sha256s == (common.sha256,)
    assert len(evaluation.anchor_evaluations) == 2
    assert result.ranked_families[0].evaluation is evaluation


def test_family_split_across_anchors_has_no_qualifying_common_anchor() -> None:
    current = _node(
        "north-split-anchor",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first = _node("split-anchor-endpoint-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    second = _node("split-anchor-endpoint-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    anchor_a = _node(
        "split-supported-anchor-a",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    anchor_b = _node(
        "split-supported-anchor-b",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    graph = _graph(
        (current, first, second, anchor_a, anchor_b),
        edges=(
            _edge(first.sha256, second.sha256),
            _edge(first.sha256, anchor_a.sha256),
            _edge(second.sha256, anchor_b.sha256),
        ),
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    records = (
        _endpoint(
            evidence_id="split-anchor-a",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=first.sha256,
            receipt_sha256=receipts[0],
        ),
        _endpoint(
            evidence_id="split-anchor-b",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=second.sha256,
            receipt_sha256=receipts[1],
        ),
    )

    result = plan_camera_bridge(graph, records)

    evaluation = result.family_evaluations[0]
    assert evaluation.complete is False
    assert evaluation.qualifying_common_anchor_sha256s == ()
    assert "no_common_supported_anchor_all_zones" in evaluation.failure_reasons
    assert all(not item.complete for item in evaluation.anchor_evaluations)
    assert result.ranked_families == ()


def test_quarantined_supported_node_cannot_be_a_common_anchor() -> None:
    current = _node(
        "north-negative-anchor",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first = _node("negative-anchor-endpoint-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    second = _node("negative-anchor-endpoint-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    negative_anchor = _node(
        "negative-supported-anchor",
        roles=(ViewRole.REVIEWED_SUPPORTED, ViewRole.RISKY_STATE_CHANGE),
        anchor=True,
        production_passed=True,
    )
    graph = _graph(
        (current, first, second, negative_anchor),
        edges=(
            _edge(first.sha256, second.sha256),
            _edge(first.sha256, negative_anchor.sha256),
            _edge(second.sha256, negative_anchor.sha256),
        ),
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    records = (
        _endpoint(
            evidence_id="negative-anchor-a",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=first.sha256,
            receipt_sha256=receipts[0],
        ),
        _endpoint(
            evidence_id="negative-anchor-b",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=second.sha256,
            receipt_sha256=receipts[1],
        ),
    )

    result = plan_camera_bridge(graph, records)

    evaluation = result.family_evaluations[0]
    assert evaluation.frozen_anchor_sha256s == ()
    assert evaluation.qualifying_common_anchor_sha256s == ()
    assert "no_frozen_supported_anchors" in evaluation.failure_reasons
    assert negative_anchor.sha256 in result.quarantined_sha256s


def test_actual_corpus_shape_unverified_repeat_cycle_yields_no_recommendation() -> None:
    current = _node(
        "north-no-repeat-cycle",
        roles=(ViewRole.SYSTEM_IDENTIFICATION,),
        current=True,
        sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    first = _node("no-cycle-repeat-1", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    second = _node("no-cycle-repeat-2", roles=(ViewRole.SYSTEM_IDENTIFICATION,))
    anchor = _node(
        "anchor-no-repeat-cycle",
        roles=(ViewRole.REVIEWED_SUPPORTED,),
        anchor=True,
        production_passed=True,
    )
    receipts = FROZEN_ENDPOINT_OBJECTIVE.selection_backing_report_sha256s
    graph = _graph(
        (current, first, second, anchor),
        edges=(
            _edge(first.sha256, second.sha256, cycle=False),
            _edge(first.sha256, anchor.sha256),
            _edge(second.sha256, anchor.sha256),
        ),
        components=(
            (current.sha256,),
            tuple(sorted((first.sha256, second.sha256, anchor.sha256))),
        ),
    )
    records = (
        _endpoint(
            evidence_id="no-cycle-a",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=first.sha256,
            receipt_sha256=receipts[0],
        ),
        _endpoint(
            evidence_id="no-cycle-b",
            family_id=FROZEN_ENDPOINT_FAMILY_ID,
            source_sha256=current.sha256,
            target_sha256=second.sha256,
            receipt_sha256=receipts[1],
        ),
    )

    result = plan_camera_bridge(graph, records)

    assert result.ranked_families == ()
    assert result.missing_experiment is None
    assert result.disposition is BridgePlannerDisposition.NO_SAFE_ENDPOINT_EVIDENCE
    assert any(
        reason.startswith("repeat_edge_not_verified_all_zones:")
        for reason in result.family_evaluations[0].failure_reasons
    )
