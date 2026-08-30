"""Pure deterministic tests for the Issue #31 offline bridge verifier."""

from __future__ import annotations

import ast
import hashlib
import inspect
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.resource import ResourceVisualState
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation import camera_bridge_verifier as verifier
from mining_automation.validation.camera_bridge_capture import (
    CAMERA_BRIDGE_CAPTURE_ID,
    camera_bridge_capture_plan,
)
from mining_automation.validation.camera_bridge_planner import (
    FROZEN_ENDPOINT_OBJECTIVE_ID,
)
from mining_automation.validation.camera_bridge_verifier import (
    AuthenticatedBridgeCapture,
    verify_camera_bridge_post,
)
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    CameraResourceEvaluation,
)
from mining_automation.validation.camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    CameraActionReceipt,
    CameraInputOperation,
    CameraInputReceipt,
    CameraPlanReceipt,
    CameraPreflightReceipt,
)
from mining_automation.validation.client_readiness import (
    CLIENT_INPUT_READINESS_ID,
    CLIENT_INPUT_READINESS_VERSION,
    GAMEPLAY_CHROME_POLICIES,
    ClientInputReadiness,
    ClientReadinessAnchorEvaluation,
    ClientReadinessReason,
)
from mining_automation.validation.robust_registration import ModelFamily
from mining_automation.validation.robust_view_graph import (
    GraphEdgeEvidence,
    GraphPolicy,
    ReadOnlyViewGraph,
    ViewNodeSpec,
    ViewRole,
)

_ZONES = (
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_EAST,
    MacroZone.SOUTH_WEST,
)


def _frame(frame_id: int, value: int) -> Frame:
    return Frame.from_raw(
        RawFrame(bytes((value, value, value, 0)), 1, 1, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _sha(frame: Frame) -> str:
    return hashlib.sha256(frame.payload).hexdigest()


def _ready() -> ClientInputReadiness:
    return ClientInputReadiness(
        evaluator_id=CLIENT_INPUT_READINESS_ID,
        evaluator_version=CLIENT_INPUT_READINESS_VERSION,
        reason=ClientReadinessReason.READY,
        detail="deterministic ready frame",
        anchors=tuple(
            ClientReadinessAnchorEvaluation(
                policy=policy,
                luma_stddev=100.0,
                edge_density=1.0,
                dark_fraction=0.0,
                matched=True,
            )
            for policy in GAMEPLAY_CHROME_POLICIES
        ),
        safe_to_attempt_camera_input=True,
    )


def _production(
    *,
    detector_id: str = "test-production-detector",
    fail_closed: bool = True,
) -> CameraEvaluation:
    state = (
        ResourceVisualState.UNCERTAIN
        if fail_closed
        else ResourceVisualState.AVAILABLE
    )
    return CameraEvaluation(
        detector_id=detector_id,
        detector_version="1.0.0",
        profile_id="test-reviewed-profile",
        profile_schema_version=2,
        profile_frame_width=1,
        profile_frame_height=1,
        profile_pixel_format=PixelFormat.BGRA8888,
        frame_geometry_supported=True,
        landmarks=(),
        matched_landmark_count=0,
        required_landmark_count=6,
        required_landmark_matches=5,
        matched_zones=(),
        required_matched_zones=3,
        scene_reason="insufficient_landmark_quorum",
        scene_validated=False,
        resource_states=(CameraResourceEvaluation("rock", state, 0.0),),
        definitive_target_ids=() if fail_closed else ("rock",),
        passed=False,
    )


def _receipt() -> CameraPlanReceipt:
    plan = camera_bridge_capture_plan()
    action = plan.actions[0]
    return CameraPlanReceipt(
        plan,
        CameraPreflightReceipt(
            True,
            EXPECTED_CLIENT_WIDTH,
            EXPECTED_CLIENT_HEIGHT,
        ),
        (
            CameraActionReceipt(
                0,
                action,
                (
                    CameraInputReceipt(CameraInputOperation.KEY_DOWN, 1, 1),
                    CameraInputReceipt(CameraInputOperation.KEY_UP, 1, 1),
                ),
            ),
        ),
    )


def _registration(*, all_zones: bool = True) -> SimpleNamespace:
    zones = _ZONES if all_zones else _ZONES[:2]
    zone_counts = tuple((zone, 1) for zone in zones)
    return SimpleNamespace(
        accepted=True,
        required_zones=zones,
        selected_model=SimpleNamespace(
            family=ModelFamily.TRANSLATION,
            source_zone_inliers=zone_counts,
            target_zone_inliers=zone_counts,
            source_zone_cells=zone_counts,
            target_zone_cells=zone_counts,
            median_residual_px=0.1,
            p90_residual_px=0.2,
            cycle_median_px=0.05,
            cycle_p90_px=0.1,
        ),
        policy=SimpleNamespace(
            minimum_inliers_per_zone=1,
            minimum_spatial_cells_per_zone=1,
        ),
    )


class _Graph:
    def __init__(
        self,
        digests: tuple[str, ...],
        transitions: tuple[Any, ...],
        *,
        blocked_pairs: frozenset[frozenset[str]],
        non_all_zone_pairs: frozenset[frozenset[str]],
        negative_evidence: bool = False,
    ) -> None:
        self.policy = GraphPolicy()
        self.action_transitions = transitions
        self.action_path_to_supported = None
        self.negative_nodes = ()
        self.negative_accepted_edge_ids = ()
        self.negative_verified_edge_ids = ()
        self.false_edge_count = int(negative_evidence)
        self.nodes = tuple(
            SimpleNamespace(sha256=digest, negative_graph_case=False)
            for digest in digests
        )
        action_by_pair = {
            frozenset((item.source_sha256, item.target_sha256)): item.action_id
            for item in transitions
        }
        edges: list[GraphEdgeEvidence] = []
        for first, second in combinations(sorted(digests), 2):
            pair = frozenset((first, second))
            blocked = pair in blocked_pairs
            edges.append(
                GraphEdgeEvidence(
                    edge_id=":".join(sorted((first, second))),
                    source_sha256=first,
                    target_sha256=second,
                    registration=(
                        None
                        if blocked
                        else cast(
                            Any,
                            _registration(
                                all_zones=pair not in non_all_zone_pairs
                            ),
                        )
                    ),
                    pre_registration_rejection=(
                        "deterministic test rejection" if blocked else None
                    ),
                    supporting_cycle_ids=() if blocked else ("cycle-1",),
                    action_ids=(
                        (action_by_pair[pair],) if pair in action_by_pair else ()
                    ),
                )
            )
        self.edges = tuple(edges)

    def as_dict(self) -> dict[str, object]:
        return {
            "action_transition_count": len(self.action_transitions),
            "edge_count": len(self.edges),
            "node_count": len(self.nodes),
        }


def _spec(label: str, frame: Frame, role: ViewRole) -> ViewNodeSpec:
    return ViewNodeSpec(label, frame, role)


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    blocked_pairs: frozenset[frozenset[str]] = frozenset(),
    non_all_zone_pairs: frozenset[frozenset[str]] = frozenset(),
    anchors: int = 1,
    negative_evidence: bool = False,
) -> tuple[
    AuthenticatedBridgeCapture,
    tuple[ViewNodeSpec, ...],
    tuple[str, str],
    frozenset[str],
    list[dict[str, object]],
]:
    source = _frame(1, 1)
    prior_first = _frame(2, 2)
    prior_second = _frame(3, 3)
    anchor_first = _frame(4, 4)
    anchor_second = _frame(5, 5)
    commit = _frame(10, 10)
    post = _frame(11, 11)
    source_sha256 = _sha(source)
    monkeypatch.setattr(verifier, "FROZEN_ENDPOINT_SOURCE_SHA256", source_sha256)
    production = _production()
    monkeypatch.setattr(verifier, "evaluate_client_input_readiness", lambda _frame: _ready())
    monkeypatch.setattr(verifier, "evaluate_varrock_east_camera", lambda _frame: production)

    base_specs = (
        _spec("frozen-source", source, ViewRole.OTHER_UNSUPPORTED),
        _spec("prior-first", prior_first, ViewRole.SYSTEM_IDENTIFICATION),
        _spec("prior-second", prior_second, ViewRole.SYSTEM_IDENTIFICATION),
        _spec("anchor-first", anchor_first, ViewRole.REVIEWED_SUPPORTED),
        *(
            (_spec("anchor-second", anchor_second, ViewRole.REVIEWED_SUPPORTED),)
            if anchors == 2
            else ()
        ),
    )
    anchor_digests = frozenset(
        {_sha(anchor_first), *({_sha(anchor_second)} if anchors == 2 else set())}
    )
    builder_calls: list[dict[str, object]] = []

    def _build(
        specs: tuple[ViewNodeSpec, ...],
        **kwargs: object,
    ) -> ReadOnlyViewGraph:
        digests = tuple(dict.fromkeys(_sha(spec.frame) for spec in specs))
        transitions = cast(tuple[Any, ...], kwargs["action_transitions"])
        builder_calls.append(
            {
                "current_sha256": kwargs["current_sha256"],
                "transitions": transitions,
            }
        )
        return cast(
            ReadOnlyViewGraph,
            _Graph(
                digests,
                transitions,
                blocked_pairs=blocked_pairs,
                non_all_zone_pairs=non_all_zone_pairs,
                negative_evidence=negative_evidence,
            ),
        )

    monkeypatch.setattr(verifier, "build_read_only_view_graph", _build)
    capture = AuthenticatedBridgeCapture(
        report_sha256="f" * 64,
        objective_id=FROZEN_ENDPOINT_OBJECTIVE_ID,
        objective_source_sha256=source_sha256,
        receipt=_receipt(),
        commit=commit,
        post=post,
        reported_post_production=production,
    )
    return (
        capture,
        base_specs,
        (_sha(prior_first), _sha(prior_second)),
        anchor_digests,
        builder_calls,
    )


def _verify(
    capture: AuthenticatedBridgeCapture,
    base_specs: tuple[ViewNodeSpec, ...],
    prior: tuple[str, str],
    anchors: frozenset[str],
):
    return verify_camera_bridge_post(
        capture,
        base_specs=base_specs,
        prior_endpoint_sha256s=prior,
        reviewed_manifest_sha256="e" * 64,
        reviewed_anchor_sha256s=anchors,
    )


def test_verifier_builds_one_exact_commit_to_post_transition_after_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, base_specs, prior, anchors, builder_calls = _setup(monkeypatch)

    result = _verify(capture, base_specs, prior, anchors)

    commit_sha256 = _sha(capture.commit)
    post_sha256 = _sha(capture.post)
    assert result.verified
    assert result.failure_reasons == ()
    assert result.action_transition.source_sha256 == commit_sha256
    assert result.action_transition.target_sha256 == post_sha256
    assert result.action_transition.action_id == CAMERA_BRIDGE_CAPTURE_ID
    assert result.action_transition.evidence_report_sha256 == capture.report_sha256
    assert result.action_transition.receipt_verified
    assert result.source_prefix_path == (
        capture.objective_source_sha256,
        commit_sha256,
    )
    assert result.source_prefix_edge_audit is not None
    assert result.source_prefix_edge_audit.all_required_zones
    assert result.source_prefix_edge_audit.selected_model_family == "translation"
    assert dict(result.source_prefix_edge_audit.source_zone_inliers) == {
        zone: 1 for zone in _ZONES
    }
    assert result.source_prefix_edge_audit.median_residual_px == 0.1
    assert result.source_prefix_edge_audit.p90_residual_px == 0.2
    assert len(result.repeat_edge_audits) == 3
    assert all(item.all_required_zones for item in result.repeat_edge_audits)
    assert result.qualifying_common_anchor_sha256s == tuple(sorted(anchors))
    assert result.action_path_to_post == (commit_sha256, post_sha256)
    assert result.visual_path_from_post_to_supported is not None
    assert result.mixed_bridge_path_to_supported == (
        *result.source_prefix_path,
        post_sha256,
        *result.visual_path_from_post_to_supported[1:],
    )
    assert result.raw_graph_action_path_to_supported is None
    assert builder_calls == [
        {
            "current_sha256": capture.objective_source_sha256,
            "transitions": (result.action_transition,),
        }
    ]
    assert not result.can_accept
    assert not result.can_validate_scene
    assert not result.can_expose_resources
    assert not result.can_authorize_camera_input
    assert not result.diagnostic_registration_can_override_production
    assert not result.live_input_performed_by_verifier
    assert not result.second_live_action_authorized
    assert result.stop_after_single_sample
    assert result.as_dict()["authority"] == {
        "can_accept": False,
        "can_authorize_camera_input": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
        "diagnostic_registration_can_override_production": False,
    }


def test_verifier_rejects_split_anchor_evidence_without_existential_mixing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provisional = [_frame(index, index) for index in range(1, 12)]
    prior_first_sha = _sha(provisional[1])
    post_sha = _sha(provisional[10])
    anchor_first_sha = _sha(provisional[3])
    anchor_second_sha = _sha(provisional[4])
    capture, base_specs, prior, anchors, _calls = _setup(
        monkeypatch,
        anchors=2,
        blocked_pairs=frozenset(
            {
                frozenset((post_sha, anchor_first_sha)),
                frozenset((prior_first_sha, anchor_second_sha)),
            }
        ),
    )

    result = _verify(capture, base_specs, prior, anchors)

    assert not result.verified
    assert result.qualifying_common_anchor_sha256s == ()
    assert "no_common_supported_anchor_all_zones" in result.failure_reasons


def test_verifier_requires_all_zone_visual_prefix_from_frozen_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_sha = _sha(_frame(1, 1))
    commit_sha = _sha(_frame(10, 10))
    capture, base_specs, prior, anchors, _calls = _setup(
        monkeypatch,
        non_all_zone_pairs=frozenset({frozenset((source_sha, commit_sha))}),
    )

    result = _verify(capture, base_specs, prior, anchors)

    assert not result.verified
    assert result.source_prefix_path is None
    assert result.source_prefix_edge_audit is not None
    assert not result.source_prefix_edge_audit.all_required_zones
    assert any(
        reason.startswith("source_prefix_not_verified_all_zones:")
        for reason in result.failure_reasons
    )


def test_exact_action_edge_requires_all_zone_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit_sha = _sha(_frame(10, 10))
    post_sha = _sha(_frame(11, 11))
    capture, base_specs, prior, anchors, _calls = _setup(
        monkeypatch,
        non_all_zone_pairs=frozenset({frozenset((commit_sha, post_sha))}),
    )

    result = _verify(capture, base_specs, prior, anchors)

    assert result.action_path_to_post is None
    assert result.mixed_bridge_path_to_supported is None
    assert "exact_action_edge_not_verified_all_zones" in result.failure_reasons
    assert "no_mixed_bridge_path_to_supported" in result.failure_reasons


def test_visual_terminal_path_does_not_traverse_verified_two_zone_edge() -> None:
    post_sha = "1" * 64
    anchor_sha = "2" * 64
    graph = cast(
        ReadOnlyViewGraph,
        _Graph(
            (post_sha, anchor_sha),
            (),
            blocked_pairs=frozenset(),
            non_all_zone_pairs=frozenset({frozenset((post_sha, anchor_sha))}),
        ),
    )

    assert (
        verifier._verified_visual_path(
            graph,
            source_sha256=post_sha,
            targets=frozenset({anchor_sha}),
        )
        is None
    )


def test_negative_graph_evidence_keeps_verifier_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, base_specs, prior, anchors, _calls = _setup(
        monkeypatch,
        negative_evidence=True,
    )

    result = _verify(capture, base_specs, prior, anchors)

    assert not result.verified
    assert "negative_graph_evidence_present" in result.failure_reasons


def test_reported_post_production_mismatch_stops_before_transition_or_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, base_specs, prior, anchors, _calls = _setup(monkeypatch)
    mismatched = replace(capture.reported_post_production, detector_id="other")
    monkeypatch.setattr(
        verifier,
        "evaluate_varrock_east_camera",
        lambda frame: mismatched if frame.frame_id == capture.post.frame_id else _production(),
    )
    monkeypatch.setattr(
        verifier,
        "ActionTransition",
        lambda **_kwargs: pytest.fail("transition constructed before authentication"),
    )
    monkeypatch.setattr(
        verifier,
        "build_read_only_view_graph",
        lambda *_args, **_kwargs: pytest.fail("graph built before authentication"),
    )

    with pytest.raises(ValueError, match="reported post production"):
        _verify(capture, base_specs, prior, anchors)


def test_fail_open_post_stops_before_transition_or_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, base_specs, prior, anchors, _calls = _setup(monkeypatch)
    fail_open = _production(fail_closed=False)
    capture = replace(capture, reported_post_production=fail_open)
    monkeypatch.setattr(
        verifier,
        "evaluate_varrock_east_camera",
        lambda frame: fail_open if frame.frame_id == capture.post.frame_id else _production(),
    )
    monkeypatch.setattr(
        verifier,
        "ActionTransition",
        lambda **_kwargs: pytest.fail("transition constructed for fail-open endpoint"),
    )
    monkeypatch.setattr(
        verifier,
        "build_read_only_view_graph",
        lambda *_args, **_kwargs: pytest.fail("graph built for fail-open endpoint"),
    )

    with pytest.raises(ValueError, match="post production is not fail closed"):
        _verify(capture, base_specs, prior, anchors)


def test_verifier_requires_frozen_source_in_base_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, base_specs, prior, anchors, _calls = _setup(monkeypatch)
    without_source = tuple(
        spec for spec in base_specs if spec.label != "frozen-source"
    )

    with pytest.raises(ValueError, match="frozen bridge source is absent"):
        _verify(capture, without_source, prior, anchors)


def test_verifier_rejects_negative_role_in_base_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, base_specs, prior, anchors, _calls = _setup(monkeypatch)
    negative_specs = (
        replace(base_specs[0], role=ViewRole.DISCONNECTED),
        *base_specs[1:],
    )

    with pytest.raises(ValueError, match="negative graph role"):
        _verify(capture, negative_specs, prior, anchors)


def test_post_endpoint_may_not_alias_any_frozen_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, base_specs, prior, anchors, _calls = _setup(monkeypatch)
    aliased = Frame.from_raw(
        RawFrame(base_specs[1].frame.payload, 1, 1, PixelFormat.BGRA8888),
        frame_id=capture.post.frame_id,
        captured_monotonic_s=capture.post.captured_monotonic_s,
    )
    capture = replace(capture, post=aliased)
    monkeypatch.setattr(
        verifier,
        "ActionTransition",
        lambda **_kwargs: pytest.fail("transition constructed for aliased endpoint"),
    )

    with pytest.raises(ValueError, match="post endpoint aliases"):
        _verify(capture, base_specs, prior, anchors)


def test_authenticated_capture_rejects_unbound_objective_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, _base_specs, _prior, _anchors, _calls = _setup(monkeypatch)

    with pytest.raises(ValueError, match="frozen bridge source"):
        replace(capture, objective_source_sha256="0" * 64)


def test_verifier_module_has_no_camera_input_or_platform_imports() -> None:
    source_path = Path(inspect.getfile(verifier))
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    forbidden = (
        "windows_camera",
        "windows_capture",
        "CameraControl",
        "CameraPlanRunner",
        "run_fixed_camera_bridge_capture",
    )
    assert all(
        all(fragment not in imported for fragment in forbidden)
        for imported in imports
    )
