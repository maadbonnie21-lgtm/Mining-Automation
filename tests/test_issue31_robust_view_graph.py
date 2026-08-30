"""Deterministic safety and reachability tests for the read-only R1 view graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.resource import ResourceVisualState
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation import robust_view_graph as view_graph
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    CameraLandmarkEvaluation,
    CameraResourceEvaluation,
)
from mining_automation.validation.client_readiness import (
    ClientInputReadiness,
    ClientReadinessAnchorEvaluation,
    ClientReadinessAnchorPolicy,
    ClientReadinessReason,
)
from mining_automation.validation.robust_registration import (
    Matrix3,
    RobustRegistrationEngine,
)
from mining_automation.validation.robust_view_graph import (
    ActionTransition,
    GraphPolicy,
    ReadOnlyViewGraph,
    ViewNodeSpec,
    ViewRole,
    build_read_only_view_graph,
)

_MANIFEST_SHA256 = hashlib.sha256(b"reviewed-manifest").hexdigest()
_READY_POLICY = ClientReadinessAnchorPolicy(
    "test-gameplay-chrome",
    (0, 0, 1, 1),
    minimum_edge_density=0.1,
)


@dataclass(frozen=True, slots=True)
class _FakeModel:
    forward_matrix: Matrix3
    reverse_matrix: Matrix3


@dataclass(frozen=True, slots=True)
class _FakeRegistration:
    accepted: bool
    selected_model: _FakeModel | None

    def as_dict(self) -> dict[str, object]:
        model = self.selected_model
        return {
            "accepted": self.accepted,
            "authority": {
                "can_accept": False,
                "can_expose_resources": False,
                "can_validate_scene": False,
                "diagnostic_registration_can_override_production": False,
            },
            "selected_model": (
                None
                if model is None
                else {
                    "forward_matrix": [list(row) for row in model.forward_matrix],
                    "reverse_matrix": [list(row) for row in model.reverse_matrix],
                }
            ),
        }


class _DeterministicEngine:
    """Pair-controlled translation engine for graph policy tests."""

    def __init__(
        self,
        positions: dict[str, tuple[float, float]],
        *,
        accepted_pairs: frozenset[tuple[str, str]] | None = None,
        offset_overrides: dict[tuple[str, str], tuple[float, float]] | None = None,
    ) -> None:
        self._positions = positions
        self._accepted_pairs = accepted_pairs
        self._offset_overrides = offset_overrides or {}
        self.calls: list[tuple[str, str]] = []

    def analyze(self, source: Frame, target: Frame) -> _FakeRegistration:
        source_sha256 = _sha256(source)
        target_sha256 = _sha256(target)
        pair = _pair(source_sha256, target_sha256)
        self.calls.append((source_sha256, target_sha256))
        if self._accepted_pairs is not None and pair not in self._accepted_pairs:
            return _FakeRegistration(False, None)

        if pair in self._offset_overrides:
            canonical_dx, canonical_dy = self._offset_overrides[pair]
            if (source_sha256, target_sha256) == pair:
                dx, dy = canonical_dx, canonical_dy
            else:
                dx, dy = -canonical_dx, -canonical_dy
        else:
            source_x, source_y = self._positions[source_sha256]
            target_x, target_y = self._positions[target_sha256]
            dx, dy = target_x - source_x, target_y - source_y
        forward = _translation(dx, dy)
        reverse = _translation(-dx, -dy)
        return _FakeRegistration(True, _FakeModel(forward, reverse))


def _frame(seed: int, *, frame_id: int | None = None) -> Frame:
    pixel = bytes((seed, seed ^ 0x55, seed ^ 0xAA, 255))
    return Frame.from_raw(
        RawFrame(pixel * 16, 4, 4, PixelFormat.BGRA8888),
        frame_id=seed if frame_id is None else frame_id,
        captured_monotonic_s=float(seed if frame_id is None else frame_id),
    )


def _alias(frame: Frame, *, frame_id: int) -> Frame:
    return Frame.from_raw(
        RawFrame(frame.payload, frame.width, frame.height, frame.pixel_format),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _sha256(frame: Frame) -> str:
    return hashlib.sha256(frame.payload).hexdigest()


def _pair(first: str, second: str) -> tuple[str, str]:
    source, target = sorted((first, second))
    return source, target


def _translation(dx: float, dy: float) -> Matrix3:
    return (
        (1.0, 0.0, dx),
        (0.0, 1.0, dy),
        (0.0, 0.0, 1.0),
    )


def _positions(*frames: Frame) -> dict[str, tuple[float, float]]:
    return {
        _sha256(frame): (float(index * 2), float(index % 2))
        for index, frame in enumerate(frames)
    }


def _ready_result(ready: bool) -> ClientInputReadiness:
    anchor = ClientReadinessAnchorEvaluation(
        policy=_READY_POLICY,
        luma_stddev=20.0 if ready else 0.0,
        edge_density=0.5 if ready else 0.0,
        dark_fraction=0.0 if ready else 1.0,
        matched=ready,
    )
    return ClientInputReadiness(
        evaluator_id="test-readiness",
        evaluator_version="1.0.0",
        reason=(
            ClientReadinessReason.READY
            if ready
            else ClientReadinessReason.GAMEPLAY_CHROME_MISMATCH
        ),
        detail="deterministic test readiness",
        anchors=(anchor,),
        safe_to_attempt_camera_input=ready,
    )


def _production_result(frame: Frame, passed: bool) -> CameraEvaluation:
    zones = (
        MacroZone.NORTH_WEST,
        MacroZone.NORTH_EAST,
        MacroZone.SOUTH_WEST,
        MacroZone.NORTH_WEST,
        MacroZone.NORTH_EAST,
        MacroZone.SOUTH_WEST,
    )
    landmarks = tuple(
        CameraLandmarkEvaluation(
            landmark_id=f"landmark-{index}",
            distance=0.1 if passed and index < 5 else 2.0,
            threshold=1.0,
            matched=passed and index < 5,
            zone=zone,
        )
        for index, zone in enumerate(zones)
    )
    resource_state = (
        ResourceVisualState.AVAILABLE if passed else ResourceVisualState.UNCERTAIN
    )
    return CameraEvaluation(
        detector_id="test-production-detector",
        detector_version="1.0.0",
        profile_id="test-reviewed-profile",
        profile_schema_version=2,
        profile_frame_width=frame.width,
        profile_frame_height=frame.height,
        profile_pixel_format=frame.pixel_format,
        frame_geometry_supported=True,
        landmarks=landmarks,
        matched_landmark_count=5 if passed else 0,
        required_landmark_count=6,
        required_landmark_matches=5,
        matched_zones=(
            (
                MacroZone.NORTH_WEST,
                MacroZone.NORTH_EAST,
                MacroZone.SOUTH_WEST,
            )
            if passed
            else ()
        ),
        required_matched_zones=3,
        scene_reason="validated" if passed else "insufficient_landmark_quorum",
        scene_validated=passed,
        resource_states=(
            CameraResourceEvaluation("test-resource", resource_state, 1.0),
        ),
        definitive_target_ids=("test-resource",) if passed else (),
        passed=passed,
    )


def _install_evaluators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ready_sha256s: frozenset[str],
    supported_sha256s: frozenset[str],
) -> None:
    monkeypatch.setattr(
        view_graph,
        "evaluate_client_input_readiness",
        lambda frame: _ready_result(_sha256(frame) in ready_sha256s),
    )
    monkeypatch.setattr(
        view_graph,
        "evaluate_varrock_east_camera",
        lambda frame: _production_result(
            frame, _sha256(frame) in supported_sha256s
        ),
    )


def _build(
    specs: tuple[ViewNodeSpec, ...],
    *,
    current: Frame,
    anchor: Frame,
    engine: _DeterministicEngine,
    transitions: tuple[ActionTransition, ...] = (),
) -> ReadOnlyViewGraph:
    return build_read_only_view_graph(
        specs,
        current_sha256=_sha256(current),
        reviewed_manifest_sha256=_MANIFEST_SHA256,
        reviewed_anchor_sha256s=frozenset((_sha256(anchor),)),
        action_transitions=transitions,
        registration_engine=cast(RobustRegistrationEngine, engine),
    )


def _transition(action_id: str, source: Frame, target: Frame) -> ActionTransition:
    return ActionTransition(
        action_id=action_id,
        source_sha256=_sha256(source),
        target_sha256=_sha256(target),
        evidence_report_sha256=hashlib.sha256(
            f"receipt:{action_id}".encode()
        ).hexdigest(),
        receipt_verified=True,
    )


def test_exact_payload_aliases_collapse_without_losing_labels_or_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = _frame(1)
    anchor_alias = _alias(anchor, frame_id=101)
    middle = _frame(2)
    current = _frame(3)
    digests = frozenset(_sha256(frame) for frame in (anchor, middle, current))
    _install_evaluators(
        monkeypatch,
        ready_sha256s=digests,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    graph = _build(
        (
            ViewNodeSpec("z-anchor-alias", anchor_alias, ViewRole.REAL_DRIFT),
            ViewNodeSpec("anchor", anchor, ViewRole.REVIEWED_SUPPORTED),
            ViewNodeSpec("middle", middle, ViewRole.OTHER_UNSUPPORTED),
            ViewNodeSpec("current", current, ViewRole.SYSTEM_IDENTIFICATION),
        ),
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(_positions(anchor, middle, current)),
    )

    assert len(graph.nodes) == 3
    collapsed = next(node for node in graph.nodes if node.sha256 == _sha256(anchor))
    assert collapsed.labels == ("anchor", "z-anchor-alias")
    assert set(collapsed.roles) == {ViewRole.REAL_DRIFT, ViewRole.REVIEWED_SUPPORTED}
    assert collapsed.reviewed_supported_anchor
    assert not collapsed.current
    assert sum(node.current for node in graph.nodes) == 1


def test_payload_aliases_with_conflicting_endpoint_metadata_are_rejected() -> None:
    anchor = _frame(4)
    same_bytes_different_endpoint = Frame.from_raw(
        RawFrame(anchor.payload, 8, 8, PixelFormat.GRAY8),
        frame_id=104,
        captured_monotonic_s=104.0,
    )
    anchor_sha256 = _sha256(anchor)

    with pytest.raises(ValueError, match="payload or endpoint metadata"):
        build_read_only_view_graph(
            (
                ViewNodeSpec("anchor", anchor, ViewRole.REVIEWED_SUPPORTED),
                ViewNodeSpec(
                    "conflicting-alias",
                    same_bytes_different_endpoint,
                    ViewRole.OTHER_UNSUPPORTED,
                ),
            ),
            current_sha256=anchor_sha256,
            reviewed_manifest_sha256=_MANIFEST_SHA256,
            reviewed_anchor_sha256s=frozenset((anchor_sha256,)),
            registration_engine=cast(
                RobustRegistrationEngine,
                _DeterministicEngine(_positions(anchor)),
            ),
        )


def test_coherent_complete_triangle_is_cycle_backed_and_visually_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, middle, current = (_frame(10), _frame(11), _frame(12))
    frames = (anchor, middle, current)
    digests = frozenset(_sha256(frame) for frame in frames)
    _install_evaluators(
        monkeypatch,
        ready_sha256s=digests,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    graph = _build(
        tuple(
            ViewNodeSpec(f"view-{index}", frame, role)
            for index, (frame, role) in enumerate(
                zip(
                    frames,
                    (
                        ViewRole.REVIEWED_SUPPORTED,
                        ViewRole.REAL_DRIFT,
                        ViewRole.SYSTEM_IDENTIFICATION,
                    ),
                    strict=True,
                )
            )
        ),
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(_positions(*frames)),
    )

    assert len(graph.cycles) == 1
    assert graph.cycles[0].passed
    assert graph.cycles[0].median_error_px == pytest.approx(0.0)
    assert graph.cycles[0].p90_error_px == pytest.approx(0.0)
    assert all(edge.registration_accepted for edge in graph.edges)
    assert all(edge.verified(graph.policy) for edge in graph.edges)
    assert graph.visual_path_to_supported == (_sha256(current), _sha256(anchor))
    assert graph.action_path_to_supported is None


def test_inconsistent_triangle_cannot_verify_any_pairwise_accepted_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, middle, current = (_frame(20), _frame(21), _frame(22))
    frames = (anchor, middle, current)
    digests = frozenset(_sha256(frame) for frame in frames)
    _install_evaluators(
        monkeypatch,
        ready_sha256s=digests,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    graph = _build(
        (
            ViewNodeSpec("anchor", anchor, ViewRole.REVIEWED_SUPPORTED),
            ViewNodeSpec("middle", middle, ViewRole.OTHER_UNSUPPORTED),
            ViewNodeSpec("current", current, ViewRole.SYSTEM_IDENTIFICATION),
        ),
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(
            _positions(*frames),
            offset_overrides={_pair(_sha256(anchor), _sha256(middle)): (50.0, 0.0)},
        ),
    )

    assert all(edge.registration_accepted for edge in graph.edges)
    assert len(graph.cycles) == 1
    assert not graph.cycles[0].passed
    assert not any(edge.verified(graph.policy) for edge in graph.edges)
    assert graph.visual_path_to_supported is None
    assert graph.action_path_to_supported is None
    assert len(graph.components) == 3


def test_readiness_veto_keeps_disconnected_pixels_out_of_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, first, current, disconnected = (
        _frame(30),
        _frame(31),
        _frame(32),
        _frame(33),
    )
    ready = frozenset(_sha256(frame) for frame in (anchor, first, current))
    _install_evaluators(
        monkeypatch,
        ready_sha256s=ready,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    engine = _DeterministicEngine(_positions(anchor, first, current, disconnected))
    graph = _build(
        (
            ViewNodeSpec("anchor", anchor, ViewRole.REVIEWED_SUPPORTED),
            ViewNodeSpec("first", first, ViewRole.REAL_DRIFT),
            ViewNodeSpec("current", current, ViewRole.SYSTEM_IDENTIFICATION),
            ViewNodeSpec("disconnect", disconnected, ViewRole.DISCONNECTED),
        ),
        current=current,
        anchor=anchor,
        engine=engine,
    )

    disconnected_sha256 = _sha256(disconnected)
    disconnected_node = next(
        node for node in graph.nodes if node.sha256 == disconnected_sha256
    )
    disconnected_edges = tuple(
        edge
        for edge in graph.edges
        if disconnected_sha256 in (edge.source_sha256, edge.target_sha256)
    )
    assert disconnected_node.explicitly_disconnected
    assert not disconnected_node.registration_eligible
    assert all(edge.registration is None for edge in disconnected_edges)
    assert all(
        edge.pre_registration_rejection
        == "readiness_veto: one or both endpoints are not gameplay-ready"
        for edge in disconnected_edges
    )
    assert all(disconnected_sha256 not in call for call in engine.calls)
    assert graph.false_edge_count == 0


def test_receipt_label_cannot_promote_an_edge_without_cycle_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, current = _frame(40), _frame(41)
    digests = frozenset((_sha256(anchor), _sha256(current)))
    _install_evaluators(
        monkeypatch,
        ready_sha256s=digests,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    transition = _transition("claimed-current-to-anchor", current, anchor)
    graph = _build(
        (
            ViewNodeSpec("anchor", anchor, ViewRole.REVIEWED_SUPPORTED),
            ViewNodeSpec("current", current, ViewRole.SYSTEM_IDENTIFICATION),
        ),
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(_positions(anchor, current)),
        transitions=(transition,),
    )

    assert len(graph.edges) == 1
    assert graph.edges[0].registration_accepted
    assert graph.edges[0].action_ids == (transition.action_id,)
    assert not graph.edges[0].verified(graph.policy)
    assert graph.visual_path_to_supported is None
    assert graph.action_path_to_supported is None
    assert not graph.offline_controller_path_available
    assert graph.conclusion.startswith("missing graph link:")

    with pytest.raises(ValueError, match="exact complete receipt"):
        ActionTransition(
            action_id="forged-receipt",
            source_sha256=_sha256(current),
            target_sha256=_sha256(anchor),
            evidence_report_sha256=hashlib.sha256(b"forged").hexdigest(),
            receipt_verified=False,
        )


def test_action_reachability_is_directed_and_distinct_from_visual_reachability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, middle, current = (_frame(50), _frame(51), _frame(52))
    frames = (anchor, middle, current)
    digests = frozenset(_sha256(frame) for frame in frames)
    _install_evaluators(
        monkeypatch,
        ready_sha256s=digests,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    specs = (
        ViewNodeSpec("anchor", anchor, ViewRole.REVIEWED_SUPPORTED),
        ViewNodeSpec("middle", middle, ViewRole.SYSTEM_IDENTIFICATION),
        ViewNodeSpec("current", current, ViewRole.SYSTEM_IDENTIFICATION),
    )
    reverse_only = _build(
        specs,
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(_positions(*frames)),
        transitions=(
            _transition("anchor-to-middle", anchor, middle),
            _transition("middle-to-current", middle, current),
        ),
    )
    directed_to_anchor = _build(
        specs,
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(_positions(*frames)),
        transitions=(
            _transition("current-to-middle", current, middle),
            _transition("middle-to-anchor", middle, anchor),
        ),
    )

    assert reverse_only.visual_path_to_supported == (
        _sha256(current),
        _sha256(anchor),
    )
    assert reverse_only.action_path_to_supported is None
    assert not reverse_only.offline_controller_path_available
    assert directed_to_anchor.visual_path_to_supported == (
        _sha256(current),
        _sha256(anchor),
    )
    assert directed_to_anchor.action_path_to_supported == (
        _sha256(current),
        _sha256(middle),
        _sha256(anchor),
    )
    assert directed_to_anchor.offline_controller_path_available
    assert directed_to_anchor.conclusion == "offline controller path available"


def test_single_pairwise_cross_component_match_cannot_create_false_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, supported_b, supported_c, current, current_b, current_c = (
        _frame(60),
        _frame(61),
        _frame(62),
        _frame(63),
        _frame(64),
        _frame(65),
    )
    frames = (anchor, supported_b, supported_c, current, current_b, current_c)
    digests = frozenset(_sha256(frame) for frame in frames)
    _install_evaluators(
        monkeypatch,
        ready_sha256s=digests,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    supported_triangle = (anchor, supported_b, supported_c)
    current_triangle = (current, current_b, current_c)
    accepted_pairs = {
        _pair(_sha256(first), _sha256(second))
        for triangle in (supported_triangle, current_triangle)
        for index, first in enumerate(triangle)
        for second in triangle[index + 1 :]
    }
    bridge_pair = _pair(_sha256(anchor), _sha256(current))
    accepted_pairs.add(bridge_pair)
    graph = _build(
        tuple(
            ViewNodeSpec(
                f"view-{index}",
                frame,
                (
                    ViewRole.REVIEWED_SUPPORTED
                    if frame is anchor
                    else ViewRole.OTHER_UNSUPPORTED
                ),
            )
            for index, frame in enumerate(frames)
        ),
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(
            _positions(*frames),
            accepted_pairs=frozenset(accepted_pairs),
        ),
    )

    bridge = next(
        edge
        for edge in graph.edges
        if _pair(edge.source_sha256, edge.target_sha256) == bridge_pair
    )
    assert bridge.registration_accepted
    assert bridge.supporting_cycle_ids == ()
    assert not bridge.verified(graph.policy)
    assert len(graph.components) == 2
    assert graph.visual_path_to_supported is None
    assert graph.action_path_to_supported is None
    assert sum(edge.verified(graph.policy) for edge in graph.edges) == 6


def test_shuffled_specs_and_actions_serialize_to_identical_canonical_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, first, second, current = (
        _frame(70),
        _frame(71),
        _frame(72),
        _frame(73),
    )
    frames = (anchor, first, second, current)
    digests = frozenset(_sha256(frame) for frame in frames)
    _install_evaluators(
        monkeypatch,
        ready_sha256s=digests,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    specs = (
        ViewNodeSpec("anchor-z", _alias(anchor, frame_id=170), ViewRole.REAL_DRIFT),
        ViewNodeSpec("anchor-a", anchor, ViewRole.REVIEWED_SUPPORTED),
        ViewNodeSpec("first", first, ViewRole.SYSTEM_IDENTIFICATION),
        ViewNodeSpec("second", second, ViewRole.REAL_DRIFT),
        ViewNodeSpec("current", current, ViewRole.SYSTEM_IDENTIFICATION),
    )
    transitions = (
        _transition("step-3", second, anchor),
        _transition("step-1", current, first),
        _transition("step-2", first, second),
    )
    first_graph = _build(
        specs,
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(_positions(*frames)),
        transitions=transitions,
    )
    second_graph = _build(
        tuple(reversed(specs)),
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(_positions(*frames)),
        transitions=tuple(reversed(transitions)),
    )

    assert first_graph.as_dict() == second_graph.as_dict()
    assert json.dumps(
        first_graph.as_dict(), sort_keys=True, separators=(",", ":")
    ) == json.dumps(second_graph.as_dict(), sort_keys=True, separators=(",", ":"))
    assert first_graph.action_path_to_supported == (
        _sha256(current),
        _sha256(first),
        _sha256(second),
        _sha256(anchor),
    )
    assert [item.action_id for item in first_graph.action_transitions] == [
        "step-1",
        "step-2",
        "step-3",
    ]


def test_view_graph_never_inherits_production_or_input_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, middle, current = (_frame(80), _frame(81), _frame(82))
    frames = (anchor, middle, current)
    digests = frozenset(_sha256(frame) for frame in frames)
    _install_evaluators(
        monkeypatch,
        ready_sha256s=digests,
        supported_sha256s=frozenset((_sha256(anchor),)),
    )
    graph = _build(
        (
            ViewNodeSpec("anchor", anchor, ViewRole.REVIEWED_SUPPORTED),
            ViewNodeSpec("middle", middle, ViewRole.SYSTEM_IDENTIFICATION),
            ViewNodeSpec("current", current, ViewRole.SYSTEM_IDENTIFICATION),
        ),
        current=current,
        anchor=anchor,
        engine=_DeterministicEngine(_positions(*frames)),
        transitions=(
            _transition("current-to-middle", current, middle),
            _transition("middle-to-anchor", middle, anchor),
        ),
    )

    assert graph.offline_controller_path_available
    assert graph.visual_path_to_supported is not None
    assert not graph.can_accept
    assert not graph.can_validate_scene
    assert not graph.can_expose_resources
    assert not graph.can_authorize_camera_input
    assert not graph.diagnostic_registration_can_override_production
    assert graph.as_dict()["authority"] == {
        "can_accept": False,
        "can_authorize_camera_input": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
        "diagnostic_registration_can_override_production": False,
    }


def test_graph_policy_requires_at_least_one_supporting_cycle() -> None:
    with pytest.raises(ValueError, match="positive"):
        GraphPolicy(minimum_supporting_cycles_per_edge=0)
