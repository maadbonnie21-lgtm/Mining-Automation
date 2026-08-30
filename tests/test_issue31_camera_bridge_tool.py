from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.validation.camera_bridge_capture import (
    CameraBridgeCaptureInputState,
    CameraBridgeCaptureTerminalReason,
)


def _load_tool() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "validate_varrock_east_camera.py"
    spec = importlib.util.spec_from_file_location("validate_varrock_east_camera_r2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> ModuleType:
    return _load_tool()


class _Backend:
    constructed = 0

    def __init__(self, **_kwargs: object) -> None:
        type(self).constructed += 1
        self.selected_window = SimpleNamespace(
            hwnd=123,
            class_name="SunAwtFrame",
            title="RuneLite - Chief Luma",
        )


class _Source:
    last: _Source | None = None

    def __init__(self, _backend: object, **_kwargs: object) -> None:
        type(self).last = self
        self.closed = False

    def open(self) -> None:
        pass

    def capture(self) -> object:
        return object()

    def close(self) -> None:
        self.closed = True
        _Lease.events.append("capture_cleanup")


class _Control:
    last: _Control | None = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        type(self).last = self
        self.released = False

    def release_all_held_keys(self) -> None:
        self.released = True
        _Lease.events.append("input_cleanup")


class _Lease:
    events: list[str] = []

    def __init__(self) -> None:
        self.acquired = False

    def __enter__(self) -> _Lease:
        self.acquired = True
        type(self).events.append("lease_acquired")
        return self

    def __exit__(self, *_args: object) -> None:
        self.acquired = False
        type(self).events.append("lease_released")


def _review_args() -> list[str]:
    return [
        "--analysis-report",
        "analysis.json",
        "--analysis-sha256",
        "1" * 64,
        "--north-report",
        "north.json",
        "--north-sha256",
        "2" * 64,
    ]


def _patch_reviewed_inputs(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frame = Frame.from_raw(
        RawFrame(
            payload=bytes((1, 2, 3, 255)),
            width=1,
            height=1,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=tool.time.monotonic(),
    )
    analysis = tool._BridgeAnalysisAuthorization(
        report_path=tmp_path / "analysis.json",
        report_sha256="1" * 64,
        r1_report_sha256="3" * 64,
        planner_id="issue31-read-only-camera-bridge-planner-r2",
        planner_version="2.0.0",
        objective_id=tool._BRIDGE_OBJECTIVE_ID,
        source_frame=frame,
        source_raw_path=tmp_path / "planner-source.raw",
        source_sha256=tool.FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    north = tool._BridgeNorthHandoff(
        report_path=tmp_path / "north.json",
        report_sha256="2" * 64,
        frame=frame,
        raw_path=tmp_path / "north.raw",
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_authorization",
        lambda *_args, **_kwargs: analysis,
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_north_handoff",
        lambda *_args, **_kwargs: north,
    )

    class RegistrationEngine:
        def analyze(self, _source: Frame, target: Frame) -> SimpleNamespace:
            digest = hashlib.sha256(target.payload).hexdigest()
            return SimpleNamespace(
                target=SimpleNamespace(payload_sha256=digest),
                as_dict=lambda: {"accepted": True, "target_sha256": digest},
            )

    monkeypatch.setattr(tool, "RobustRegistrationEngine", RegistrationEngine)
    monkeypatch.setattr(
        tool,
        "RealWindowsCameraApi",
        lambda: SimpleNamespace(declare_dpi_awareness=lambda: None),
    )
    monkeypatch.setattr(
        tool,
        "_bridge_evidence_frame",
        lambda _output_root, _evidence: frame,
    )
    monkeypatch.setattr(
        tool,
        "_require_bridge_starting_registration",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        tool,
        "_require_bridge_pointer_ownership",
        lambda *_args, **_kwargs: SimpleNamespace(
            as_dict=lambda: {"root_hwnd_matches_target": True}
        ),
    )
    monkeypatch.setattr(
        tool,
        "_require_north_bootstrap_production_identity",
        lambda *_args, **_kwargs: None,
    )

    def close(result: object, **_kwargs: object) -> tuple[object, object, object, None]:
        if not result.input_attempted:  # type: ignore[attr-defined]
            closure = tool._new_bridge_post_transition_closure(
                tool.CameraBridgePostTransitionStatus.NOT_REQUIRED,
                "test zero-input closure",
            )
            return result, closure, None, None
        closure = tool._new_bridge_post_transition_closure(
            tool.CameraBridgePostTransitionStatus.COMPLETE,
            "test closure",
            commit_sha256="4" * 64,
            post_sha256="5" * 64,
            action_bridge_receipt_proven=True,
            registration_attempted=True,
            registration_accepted=True,
            registration_bridge_observed=True,
            production_re_evaluated=True,
            production_matches_capture=True,
            production_supported_endpoint=False,
            bridge_rejected=False,
        )
        registration = SimpleNamespace(as_dict=lambda: {"accepted": True})
        return result, closure, registration, None

    monkeypatch.setattr(tool, "_evaluate_bridge_post_transition", close)


def _capture_complete_result(*, input_attempted: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        as_dict=lambda: {
            "authority": {
                "can_accept": False,
                "can_authorize_camera_input": False,
                "can_expose_resources": False,
                "can_validate_scene": False,
                "diagnostic_registration_can_override_production": False,
            },
            "bridge_capture": {
                "id": "issue31-fixed-camera-bridge-capture-r2",
                "version": "1.1.0",
                "protocol_completed": True,
                "post_production_passed": False,
            },
            "fixed_policy": {
                "caller_selectable_coordinate": False,
                "caller_selectable_direction": False,
                "caller_selectable_magnitude": False,
                "hold_seconds": 0.043,
                "key": "right",
                "maximum_physical_primitives": 1,
                "settle_seconds": 1.0,
            },
            "input": {
                "attempted": input_attempted,
                "completed": input_attempted,
                "state": "complete" if input_attempted else "none",
            },
        },
        input_state=CameraBridgeCaptureInputState.COMPLETE,
        input_attempted=input_attempted,
        input_completed=input_attempted,
        protocol_completed=True,
        terminal_reason=CameraBridgeCaptureTerminalReason.CAPTURE_COMPLETE,
    )


def _analysis_payload(tool: ModuleType) -> dict[str, object]:
    source_sha256 = tool.FROZEN_ENDPOINT_SOURCE_SHA256
    planner_authority = {
        "can_accept": False,
        "can_authorize_camera_input": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
        "diagnostic_registration_can_override_production": False,
    }
    return {
        "schema_version": 2,
        "provenance": {
            "detector_id": tool._EXPECTED_DETECTOR_ID,
            "detector_version": tool._EXPECTED_DETECTOR_VERSION,
            "git_head_sha": "a" * 40,
            "plan_id": tool._BRIDGE_ANALYSIS_PLAN_ID,
            "plan_version": tool._BRIDGE_ANALYSIS_PLAN_VERSION,
            "profile_id": tool._EXPECTED_PROFILE_ID,
            "tracked_worktree_clean": True,
        },
        "evidence": {
            "authority": {
                "diagnostic_registration_can_override_production": False,
                "live_camera_input_authorized": False,
                "live_camera_input_performed": False,
                "registration_can_authorize_camera_input": False,
                "registration_can_expose_resources": False,
                "registration_can_validate_scene": False,
            },
            "bridge_planner": {
                "authority": planner_authority,
                "current_sha256": source_sha256,
                "disposition": "missing_experiment",
                "family_evaluations": [
                    {
                        "complete": True,
                        "failure_reasons": [],
                        "family_id": tool.FROZEN_ENDPOINT_OBJECTIVE.family_id,
                    }
                ],
                "matrix_policy": {
                    "rejected_registration_matrices_used_for_control": False,
                    "rejected_registration_metrics_used_for_ranking": False,
                },
                "missing_experiment": {
                    "action_id": tool.CAMERA_BRIDGE_CAPTURE_ID,
                    "can_execute_input": False,
                    "duration_s": tool.CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
                    "experiment_id": tool._BRIDGE_OBJECTIVE_ID,
                    "family_id": tool.FROZEN_ENDPOINT_OBJECTIVE.family_id,
                    "key": "right",
                    "receipt_backing_sha256s": list(
                        sorted(tool._BRIDGE_OBJECTIVE_REPORT_SHA256S)
                    ),
                    "source_sha256": source_sha256,
                    "uses_rejected_registration_matrix": False,
                },
                "planner_id": tool.CAMERA_BRIDGE_PLANNER_ID,
                "planner_version": tool.CAMERA_BRIDGE_PLANNER_VERSION,
            },
            "corpus": {"north": {"frame": {}}},
            "result": {
                "live_input_authorized": False,
                "reacquisition_success_claimed": False,
                "selected_experiment_id": tool._BRIDGE_OBJECTIVE_ID,
            },
            "r1_source": {
                "negative_corpus": {
                    "policy_roles": ["disconnected", "risky_state_change"],
                    "supported_path_count": 0,
                },
                "report_sha256": "3" * 64,
            },
            "safe_view_graph": {
                "authority": planner_authority,
                "current_sha256": source_sha256,
                "graph_id": tool.ROBUST_VIEW_GRAPH_ID,
                "graph_version": tool.ROBUST_VIEW_GRAPH_VERSION,
            },
        },
    }


def _unit_frame(tool: ModuleType) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=bytes((1, 2, 3, 255)),
            width=1,
            height=1,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=1,
        captured_monotonic_s=0.0,
    )


def _pending_closure_result(tool: ModuleType, *, identical: bool = False) -> object:
    readiness = SimpleNamespace(safe_to_attempt_camera_input=True)
    commit_sha = "4" * 64
    post_sha = commit_sha if identical else "5" * 64
    return SimpleNamespace(
        input_attempted=True,
        input_completed=True,
        receipt=SimpleNamespace(plan=tool.camera_bridge_capture_plan()),
        commit=SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=commit_sha),
        ),
        post=SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=post_sha),
            readiness=readiness,
        ),
    )


@pytest.mark.parametrize(
    ("registration_mode", "expected_status"),
    [
        ("accepted", "complete"),
        ("rejected", "registration_rejected"),
        ("exception", "registration_exception"),
    ],
)
def test_post_transition_closure_orders_registration_before_production(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    registration_mode: str,
    expected_status: str,
) -> None:
    result = _pending_closure_result(tool)
    commit = _unit_frame(tool)
    post = Frame.from_raw(
        RawFrame(bytes((9, 8, 7, 255)), 1, 1, PixelFormat.BGRA8888),
        frame_id=2,
        captured_monotonic_s=2.0,
    )
    events: list[str] = []
    registration = SimpleNamespace(as_dict=lambda: {"accepted": True})

    class Engine:
        def analyze(self, _source: Frame, _target: Frame) -> object:
            events.append("registration")
            if registration_mode == "exception":
                raise RuntimeError("registration exploded")
            return registration

    monkeypatch.setattr(
        tool,
        "_bridge_evidence_frame",
        lambda _root, evidence: (
            commit if evidence is result.commit else post
        ),
    )
    monkeypatch.setattr(
        tool,
        "evaluate_client_input_readiness",
        lambda _frame: result.post.readiness,
    )

    def require_registration(*_args: object, **_kwargs: object) -> None:
        if registration_mode == "rejected":
            raise ValueError("registration rejected")

    monkeypatch.setattr(tool, "_require_bridge_starting_registration", require_registration)
    production = SimpleNamespace(passed=False)
    finalized = SimpleNamespace(protocol_completed=True)

    def finalize(_result: object, _post: Frame) -> tuple[object, object]:
        events.append("production")
        return finalized, production

    monkeypatch.setattr(tool, "_finalize_camera_bridge_post_production", finalize)
    monkeypatch.setattr(
        tool,
        "_require_north_bootstrap_production_identity",
        lambda *_args, **_kwargs: None,
    )

    observed, closure, observed_registration, observed_production = (
        tool._evaluate_bridge_post_transition(
            result,
            output_root=Path("unused"),
            registration_engine=Engine(),
        )
    )

    assert observed is finalized
    assert events == ["registration", "production"]
    assert closure.status.value == expected_status
    assert closure.action_bridge_receipt_proven is True
    assert closure.production_re_evaluated is True
    assert closure.production_matches_capture is True
    assert closure.bridge_rejected is (registration_mode != "accepted")
    assert observed_registration is (registration if registration_mode != "exception" else None)
    assert observed_production is production
    semantics = closure.as_dict()["semantic_states"]
    assert semantics["ACTION_BRIDGE_RECEIPT_PROVEN"] is True
    assert semantics["BRIDGE_REJECTED"] is (registration_mode != "accepted")
    assert closure.as_dict()["action_transition_emitted"] is False
    assert closure.as_dict()["authenticated_ingestion_required"] is True


def test_post_transition_identical_pixels_are_measured_then_rejected(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _pending_closure_result(tool, identical=True)
    frame = _unit_frame(tool)
    events: list[str] = []
    monkeypatch.setattr(tool, "_bridge_evidence_frame", lambda *_args: frame)
    monkeypatch.setattr(
        tool,
        "evaluate_client_input_readiness",
        lambda _frame: result.post.readiness,
    )
    monkeypatch.setattr(
        tool,
        "_require_bridge_starting_registration",
        lambda *_args, **_kwargs: None,
    )

    class Engine:
        def analyze(self, _source: Frame, _target: Frame) -> object:
            events.append("registration")
            return SimpleNamespace(as_dict=dict)

    production = SimpleNamespace(passed=False)
    monkeypatch.setattr(
        tool,
        "_finalize_camera_bridge_post_production",
        lambda *_args: (
            events.append("production") or SimpleNamespace(protocol_completed=True),
            production,
        ),
    )
    monkeypatch.setattr(
        tool,
        "_require_north_bootstrap_production_identity",
        lambda *_args, **_kwargs: None,
    )

    _finalized, closure, _registration, _production = (
        tool._evaluate_bridge_post_transition(
            result,
            output_root=Path("unused"),
            registration_engine=Engine(),
        )
    )

    assert events == ["registration", "production"]
    assert closure.status.value == "no_distinct_endpoint"
    assert closure.registration_bridge_observed is True
    assert closure.bridge_rejected is True
    assert closure.completed is False


def test_post_transition_zero_input_performs_no_registration_or_production(
    tool: ModuleType,
) -> None:
    result = SimpleNamespace(input_attempted=False)

    class Engine:
        def analyze(self, *_args: object) -> object:
            raise AssertionError("zero-input closure must not register")

    observed, closure, registration, production = (
        tool._evaluate_bridge_post_transition(
            result,
            output_root=Path("unused"),
            registration_engine=Engine(),
        )
    )

    assert observed is result
    assert closure.status.value == "not_required"
    assert closure.as_dict()["semantic_states"] == {
        "ACTION_BRIDGE_RECEIPT_PROVEN": False,
        "BRIDGE_REJECTED": False,
        "PRODUCTION_SUPPORTED_ENDPOINT": False,
        "REGISTRATION_BRIDGE_OBSERVED": False,
    }
    assert registration is None
    assert production is None


def test_post_missing_retains_complete_physical_receipt_semantic(
    tool: ModuleType,
) -> None:
    result = SimpleNamespace(
        input_attempted=True,
        input_completed=True,
        receipt=SimpleNamespace(plan=tool.camera_bridge_capture_plan()),
        commit=SimpleNamespace(artifact=SimpleNamespace(raw_sha256="4" * 64)),
        post=None,
    )

    class Engine:
        def analyze(self, *_args: object) -> object:
            raise AssertionError("missing post cannot register")

    observed, closure, registration, production = (
        tool._evaluate_bridge_post_transition(
            result,
            output_root=Path("unused"),
            registration_engine=Engine(),
        )
    )

    assert observed is result
    assert closure.status.value == "physical_capture_incomplete"
    assert closure.action_bridge_receipt_proven is True
    assert closure.bridge_rejected is True
    assert registration is None
    assert production is None


def test_post_transition_fail_open_production_rejects_the_bridge(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _pending_closure_result(tool)
    commit = _unit_frame(tool)
    post = Frame.from_raw(
        RawFrame(bytes((9, 8, 7, 255)), 1, 1, PixelFormat.BGRA8888),
        frame_id=2,
        captured_monotonic_s=2.0,
    )
    monkeypatch.setattr(
        tool,
        "_bridge_evidence_frame",
        lambda _root, evidence: commit if evidence is result.commit else post,
    )
    monkeypatch.setattr(
        tool,
        "evaluate_client_input_readiness",
        lambda _frame: result.post.readiness,
    )
    monkeypatch.setattr(
        tool,
        "_require_bridge_starting_registration",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        tool,
        "_require_north_bootstrap_production_identity",
        lambda *_args, **_kwargs: None,
    )
    production = SimpleNamespace(passed=False)
    monkeypatch.setattr(
        tool,
        "_finalize_camera_bridge_post_production",
        lambda *_args: (SimpleNamespace(protocol_completed=False), production),
    )

    _finalized, closure, registration, observed_production = (
        tool._evaluate_bridge_post_transition(
            result,
            output_root=Path("unused"),
            registration_engine=SimpleNamespace(
                analyze=lambda *_args: SimpleNamespace(as_dict=dict)
            ),
        )
    )

    assert closure.status.value == "production_rejected"
    assert closure.action_bridge_receipt_proven is True
    assert closure.registration_bridge_observed is True
    assert closure.production_re_evaluated is True
    assert closure.bridge_rejected is True
    assert closure.completed is False
    assert registration is not None
    assert observed_production is production


def test_post_transition_artifact_error_retains_exact_receipt(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _pending_closure_result(tool)
    monkeypatch.setattr(
        tool,
        "_bridge_evidence_frame",
        lambda *_args: (_ for _ in ()).throw(OSError("raw payload missing")),
    )

    _result, closure, registration, production = (
        tool._evaluate_bridge_post_transition(
            result,
            output_root=Path("unused"),
            registration_engine=SimpleNamespace(
                analyze=lambda *_args: pytest.fail("registration must not run")
            ),
        )
    )

    assert closure.status.value == "artifact_error"
    assert closure.action_bridge_receipt_proven is True
    assert closure.artifact_exception is not None
    assert closure.bridge_rejected is True
    assert registration is None
    assert production is None


def test_post_transition_production_exception_retains_registration_and_receipt(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _pending_closure_result(tool)
    commit = _unit_frame(tool)
    post = Frame.from_raw(
        RawFrame(bytes((9, 8, 7, 255)), 1, 1, PixelFormat.BGRA8888),
        frame_id=2,
        captured_monotonic_s=2.0,
    )
    registration = SimpleNamespace(as_dict=dict)
    monkeypatch.setattr(
        tool,
        "_bridge_evidence_frame",
        lambda _root, evidence: commit if evidence is result.commit else post,
    )
    monkeypatch.setattr(
        tool,
        "evaluate_client_input_readiness",
        lambda _frame: result.post.readiness,
    )
    monkeypatch.setattr(
        tool,
        "_require_bridge_starting_registration",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        tool,
        "_finalize_camera_bridge_post_production",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("detector failed")),
    )

    _result, closure, observed_registration, production = (
        tool._evaluate_bridge_post_transition(
            result,
            output_root=Path("unused"),
            registration_engine=SimpleNamespace(
                analyze=lambda *_args: registration
            ),
        )
    )

    assert closure.status.value == "production_exception"
    assert closure.action_bridge_receipt_proven is True
    assert closure.registration_bridge_observed is True
    assert closure.production_exception is not None
    assert closure.bridge_rejected is True
    assert observed_registration is registration
    assert production is None


def test_pending_post_identity_revalidation_does_not_run_production_early(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = bytes((1, 2, 3, 255))
    raw = tmp_path / "r2-post.raw"
    raw.write_bytes(payload)
    readiness = SimpleNamespace(safe_to_attempt_camera_input=True)
    post = SimpleNamespace(
        artifact=SimpleNamespace(
            files=(("raw", raw.name),),
            frame_id=4,
            height=1,
            label="r2-post",
            pixel_format=PixelFormat.BGRA8888.value,
            raw_sha256=hashlib.sha256(payload).hexdigest(),
            width=1,
        ),
        captured_monotonic_s=4.0,
        readiness=readiness,
    )
    result = SimpleNamespace(
        plan=tool.camera_bridge_capture_plan(),
        can_accept=False,
        can_authorize_camera_input=False,
        can_expose_resources=False,
        can_validate_scene=False,
        diagnostic_registration_can_override_production=False,
        decision=None,
        arm=None,
        commit=None,
        post=post,
        terminal_reason=(
            tool.CameraBridgeCaptureTerminalReason.POST_CAPTURE_PENDING_CLOSURE
        ),
    )
    monkeypatch.setattr(
        tool,
        "evaluate_client_input_readiness",
        lambda _frame: readiness,
    )
    monkeypatch.setattr(
        tool,
        "evaluate_varrock_east_camera",
        lambda _frame: pytest.fail("pending post production must remain deferred"),
    )

    tool._require_bridge_capture_result_identities(result, output_root=tmp_path)


def test_live_bridge_objective_exactly_matches_frozen_planner_objective(
    tool: ModuleType,
) -> None:
    tool._require_bridge_capture_runtime_identities()
    objective = tool.FROZEN_ENDPOINT_OBJECTIVE
    assert objective.experiment_id == tool._BRIDGE_OBJECTIVE_ID
    assert objective.action_id == tool.CAMERA_BRIDGE_CAPTURE_ID
    assert objective.family_id == "north-up-p610-y043-reset"
    assert objective.key.value == "right"
    assert objective.duration_s == 0.043
    assert tuple(sorted(objective.selection_backing_report_sha256s)) == tuple(
        sorted(tool._BRIDGE_OBJECTIVE_REPORT_SHA256S)
    )


def test_analysis_authorization_binds_exact_complete_planner_result(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _analysis_payload(tool)
    frame = _unit_frame(tool)
    monkeypatch.setattr(
        tool,
        "_load_private_bound_report",
        lambda *_args, **_kwargs: (tmp_path / "analysis.json", payload),
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_source",
        lambda *_args, **_kwargs: (frame, tmp_path / "north.raw"),
    )

    result = tool._load_bridge_analysis_authorization(
        tmp_path / "analysis.json",
        expected_sha256="1" * 64,
        expected_head="a" * 40,
    )

    assert result.objective_id == tool._BRIDGE_OBJECTIVE_ID
    assert result.source_sha256 == tool.FROZEN_ENDPOINT_SOURCE_SHA256
    assert result.source_frame is frame


@pytest.mark.parametrize(
    "mutation",
    [
        "authority_extra",
        "matrix_policy",
        "action_id",
        "receipt_backing",
        "family_incomplete",
        "source_mismatch",
        "source_not_frozen",
        "no_safe_disposition",
    ],
)
def test_analysis_authorization_rejects_forged_or_incomplete_planner_result(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    payload = copy.deepcopy(_analysis_payload(tool))
    evidence = payload["evidence"]
    assert isinstance(evidence, dict)
    planner = evidence["bridge_planner"]
    assert isinstance(planner, dict)
    missing = planner["missing_experiment"]
    assert isinstance(missing, dict)
    if mutation == "authority_extra":
        authority = evidence["authority"]
        assert isinstance(authority, dict)
        authority["forged"] = False
    elif mutation == "matrix_policy":
        matrix = planner["matrix_policy"]
        assert isinstance(matrix, dict)
        matrix["rejected_registration_metrics_used_for_ranking"] = True
    elif mutation == "action_id":
        missing["action_id"] = "forged"
    elif mutation == "receipt_backing":
        missing["receipt_backing_sha256s"] = ["9" * 64]
    elif mutation == "family_incomplete":
        families = planner["family_evaluations"]
        assert isinstance(families, list) and isinstance(families[0], dict)
        families[0]["complete"] = False
        families[0]["failure_reasons"] = ["repeat_edge_not_verified"]
    elif mutation == "source_mismatch":
        safe_graph = evidence["safe_view_graph"]
        assert isinstance(safe_graph, dict)
        safe_graph["current_sha256"] = "8" * 64
    elif mutation == "source_not_frozen":
        forged_source = "4" * 64
        missing["source_sha256"] = forged_source
        planner["current_sha256"] = forged_source
        safe_graph = evidence["safe_view_graph"]
        assert isinstance(safe_graph, dict)
        safe_graph["current_sha256"] = forged_source
    else:
        planner["disposition"] = "no_safe_endpoint_evidence"
        planner["missing_experiment"] = None
    monkeypatch.setattr(
        tool,
        "_load_private_bound_report",
        lambda *_args, **_kwargs: (tmp_path / "analysis.json", payload),
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_source",
        lambda *_args, **_kwargs: (_unit_frame(tool), tmp_path / "north.raw"),
    )

    with pytest.raises(ValueError):
        tool._load_bridge_analysis_authorization(
            tmp_path / "analysis.json",
            expected_sha256="1" * 64,
            expected_head="a" * 40,
        )


@pytest.mark.parametrize(
    "override",
    [
        ["--allow-dirty"],
        ["--dry-run"],
        ["--title", "forged"],
        ["--plan-id", "forged"],
        ["--plan-version", "forged"],
        ["--settle", "0.01"],
        ["--axis", "vertical"],
        ["--direction", "left"],
        ["--duration", "0.1"],
        ["--detents", "1"],
        ["--x", "200"],
        ["--y", "600"],
    ],
)
def test_bridge_parser_rejects_every_control_override(
    tool: ModuleType,
    override: list[str],
) -> None:
    _Backend.constructed = 0
    with pytest.raises(SystemExit) as raised:
        tool.main(
            [
                "bridge-capture-r2",
                "--expected-head",
                "a" * 40,
                "--case-prefix",
                "parser-refusal",
                *_review_args(),
                *override,
            ]
        )
    assert raised.value.code == 2
    assert _Backend.constructed == 0


@pytest.mark.parametrize(
    ("head", "clean"),
    [("b" * 40, True), ("a" * 40, False)],
)
def test_bridge_exact_head_and_cleanliness_stop_before_lease_or_capture(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    head: str,
    clean: bool,
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: (head, clean))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "provenance-refusal",
            *_review_args(),
        ]
    )

    assert result == 2
    assert _Backend.constructed == 0
    assert _Lease.events == []


@pytest.mark.parametrize("invalid_input", ["analysis", "north"])
def test_invalid_reviewed_report_stops_before_lease_backend_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_input: str,
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    if invalid_input == "north":
        _patch_reviewed_inputs(tool, monkeypatch, tmp_path)
        monkeypatch.setattr(
            tool,
            "_load_bridge_north_handoff",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("forged north")
            ),
        )
    else:
        monkeypatch.setattr(
            tool,
            "_load_bridge_analysis_authorization",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("forged analysis")
            ),
        )

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "invalid-reviewed-input",
            *_review_args(),
        ]
    )

    assert result == 2
    assert _Backend.constructed == 0
    assert _Lease.events == []


def test_bridge_lease_spans_runner_cleanup_revalidation_and_publication(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Source.last = None
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)
    close = tool._evaluate_bridge_post_transition

    def ordered_close(*args: object, **kwargs: object) -> object:
        _Lease.events.append("post_registration_then_production")
        return close(*args, **kwargs)

    monkeypatch.setattr(tool, "_evaluate_bridge_post_transition", ordered_close)
    registration_events: list[str] = []

    class RecordingRegistrationEngine:
        def analyze(self, _source: Frame, target: Frame) -> SimpleNamespace:
            registration_events.append("analyze")
            digest = hashlib.sha256(target.payload).hexdigest()
            return SimpleNamespace(
                target=SimpleNamespace(payload_sha256=digest),
                as_dict=lambda: {"accepted": True, "target_sha256": digest},
            )

    monkeypatch.setattr(
        tool,
        "RobustRegistrationEngine",
        RecordingRegistrationEngine,
    )

    def run(*_args: object, **kwargs: object) -> SimpleNamespace:
        _Lease.events.append("runner")
        assert registration_events == ["analyze"]
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(
                raw_sha256=hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()
            ),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        assert registration_events == ["analyze", "analyze"]
        kwargs["final_input_guard"](evidence, evidence, evidence)
        assert registration_events == ["analyze", "analyze"]
        return _capture_complete_result()

    def revalidate(_result: object, *, output_root: Path) -> None:
        assert output_root == output
        _Lease.events.append("revalidate")

    original_write = tool.write_camera_validation_report

    def write(*args: object, **kwargs: object) -> object:
        _Lease.events.append("publish")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(tool, "_require_bridge_capture_result_identities", revalidate)
    monkeypatch.setattr(tool, "write_camera_validation_report", write)
    command = [
        "bridge-capture-r2",
        "--expected-head",
        "a" * 40,
        "--output",
        str(output),
        "--case-prefix",
        "bridge-integration",
        *_review_args(),
    ]

    assert tool.main(command) == 0
    assert _Lease.events == [
        "lease_acquired",
        "runner",
        "input_cleanup",
        "capture_cleanup",
        "post_registration_then_production",
        "revalidate",
        "publish",
        "lease_released",
    ]
    assert _Source.last is not None and _Source.last.closed
    assert _Control.last is not None and _Control.last.released
    report = output / "reports" / "bridge-integration.camera.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["provenance"]["git_head_sha"] == "a" * 40
    assert payload["provenance"]["plan_id"] == (
        "issue31-fixed-camera-bridge-capture-r2"
    )
    assert payload["provenance"]["plan_version"] == "1.1.0"
    evidence = payload["evidence"]
    assert evidence["development_only"] is True
    assert evidence["production_detector_remains_sole_scene_authority"] is True
    assert evidence["robust_registration_executed_in_input_seam"] is True
    assert evidence["registration_execution"] == {
        "north_to_commit_executed_in_input_seam": True,
        "planner_source_to_north_precomputed_before_arm": True,
        "post_transition_registration_performed": True,
        "post_transition_registration_stage": (
            "same_transaction_before_production_re_evaluation_and_report_seal"
        ),
        "production_re_evaluated_after_registration": True,
    }
    assert evidence["transition_candidate_eligible"] is False
    assert evidence["action_transition_emitted"] is False
    assert evidence["authenticated_ingestion_required"] is True
    assert evidence["same_transaction_closure_completed"] is True
    assert evidence["new_live_input_from_robust_registration"] is False
    assert evidence["bridge_objective"]["first_missing_primitive"] == {
        "duration_seconds": 0.043,
        "key": "right",
    }
    assert evidence["pointer_mapping"]["numeric_mapping_captured"] is True
    assert evidence["pointer_mapping"]["pointer_primitive_required"] is False
    digest = report.with_name(f"{report.name}.sha256").read_text().strip()
    assert digest == hashlib.sha256(report.read_bytes()).hexdigest()


def test_bridge_adapter_identity_mismatch_stops_before_runner(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    monkeypatch.setattr(tool, "_EXPECTED_WINDOWS_CAMERA_ADAPTER", "wrong.adapter")
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "adapter-refusal",
            *_review_args(),
        ]
    )

    assert result == 2
    assert _Lease.events == [
        "lease_acquired",
        "input_cleanup",
        "capture_cleanup",
        "lease_released",
    ]


def test_final_production_identity_mismatch_seals_rejected_receipt(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)

    def run(*_args: object, **kwargs: object) -> object:
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(
                raw_sha256=hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()
            ),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        kwargs["final_input_guard"](evidence, evidence, evidence)
        return _capture_complete_result()

    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("production mismatch")
        ),
    )

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "production-mismatch",
            *_review_args(),
        ]
    )

    assert result == 1
    report = output / "reports" / "production-mismatch.camera.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    evidence = payload["evidence"]
    assert evidence["input"]["attempted"] is True
    closure = evidence["post_transition_closure"]
    assert closure["status"] == "seal_revalidation_error"
    assert closure["semantic_states"] == {
        "ACTION_BRIDGE_RECEIPT_PROVEN": True,
        "BRIDGE_REJECTED": True,
        "PRODUCTION_SUPPORTED_ENDPOINT": False,
        "REGISTRATION_BRIDGE_OBSERVED": True,
    }
    assert evidence["transition_candidate_eligible"] is False
    assert evidence["action_transition_emitted"] is False


def test_persistent_artifact_failure_keeps_artifact_status_and_receipt(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)
    def run(*_args: object, **kwargs: object) -> object:
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(
                raw_sha256=hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()
            ),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        kwargs["final_input_guard"](evidence, evidence, evidence)
        return _capture_complete_result()

    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)

    def artifact_closure(result: object, **_kwargs: object) -> tuple[object, object, None, None]:
        closure = tool._new_bridge_post_transition_closure(
            tool.CameraBridgePostTransitionStatus.ARTIFACT_ERROR,
            "raw artifact missing",
            commit_sha256="4" * 64,
            post_sha256="5" * 64,
            action_bridge_receipt_proven=True,
            bridge_rejected=True,
            artifact_exception=tool._bridge_closure_exception(
                OSError("raw artifact missing")
            ),
        )
        return result, closure, None, None

    monkeypatch.setattr(tool, "_evaluate_bridge_post_transition", artifact_closure)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("raw artifact still missing")
        ),
    )

    exit_code = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "artifact-persistent",
            *_review_args(),
        ]
    )

    assert exit_code == 1
    report = output / "reports" / "artifact-persistent.camera.json"
    evidence = json.loads(report.read_text(encoding="utf-8"))["evidence"]
    closure = evidence["post_transition_closure"]
    assert evidence["input"]["completed"] is True
    assert closure["status"] == "artifact_error"
    assert closure["artifact_exception"]["type"] == "OSError"
    assert closure["seal_exception"]["type"] == "OSError"
    assert closure["semantic_states"]["ACTION_BRIDGE_RECEIPT_PROVEN"] is True
    assert closure["semantic_states"]["BRIDGE_REJECTED"] is True
    assert evidence["transition_candidate_eligible"] is False


def test_cleanup_failure_vetoes_post_closure_and_publication(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"

    class FailingCleanupControl(_Control):
        def release_all_held_keys(self) -> None:
            self.released = True
            _Lease.events.append("input_cleanup_failed")
            raise RuntimeError("release state unknown")

    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", FailingCleanupControl)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{FailingCleanupControl.__module__}.{FailingCleanupControl.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: _capture_complete_result(),
    )
    monkeypatch.setattr(
        tool,
        "_evaluate_bridge_post_transition",
        lambda *_args, **_kwargs: pytest.fail(
            "cleanup failure must veto post registration and production"
        ),
    )

    exit_code = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "cleanup-veto",
            *_review_args(),
        ]
    )

    assert exit_code == 2
    assert "input_cleanup_failed" in _Lease.events
    assert "capture_cleanup" in _Lease.events
    report = output / "reports" / "cleanup-veto.camera.json"
    assert not report.exists()
    assert not report.with_name(f"{report.name}.sha256").exists()


def test_bridge_prearm_registration_failure_stops_before_runner_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Source.last = None
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)

    class RejectingRegistrationEngine:
        def analyze(self, _source: Frame, _target: Frame) -> object:
            raise RuntimeError("stable source relationship rejected")

    monkeypatch.setattr(
        tool,
        "RobustRegistrationEngine",
        RejectingRegistrationEngine,
    )
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    assert tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "prearm-registration-refusal",
            *_review_args(),
        ]
    ) == 2
    assert _Lease.events == [
        "lease_acquired",
        "input_cleanup",
        "capture_cleanup",
        "lease_released",
    ]


@pytest.mark.parametrize(
    "post_precompute_state",
    [("b" * 40, True), ("a" * 40, False)],
)
def test_bridge_rechecks_head_and_cleanliness_after_prearm_registration(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    post_precompute_state: tuple[str, bool],
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Source.last = None
    _Control.last = None
    _Lease.events = []
    states = iter(
        [
            ("a" * 40, True),
            ("a" * 40, True),
            post_precompute_state,
        ]
    )
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: next(states))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail("runner must not start"),
    )

    assert tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "post-precompute-provenance-refusal",
            *_review_args(),
        ]
    ) == 2
    assert _Lease.events == [
        "lease_acquired",
        "input_cleanup",
        "capture_cleanup",
        "lease_released",
    ]


def test_bridge_report_truthfully_distinguishes_prearm_and_input_seam_checks(
    tool: ModuleType,
    tmp_path: Path,
) -> None:
    frame = _unit_frame(tool)
    analysis = tool._BridgeAnalysisAuthorization(
        report_path=tmp_path / "analysis.json",
        report_sha256="1" * 64,
        r1_report_sha256="3" * 64,
        planner_id="issue31-read-only-camera-bridge-planner-r2",
        planner_version="2.0.0",
        objective_id=tool._BRIDGE_OBJECTIVE_ID,
        source_frame=frame,
        source_raw_path=tmp_path / "source.raw",
        source_sha256=tool.FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    north = tool._BridgeNorthHandoff(
        report_path=tmp_path / "north.json",
        report_sha256="2" * 64,
        frame=frame,
        raw_path=tmp_path / "north.raw",
    )
    precomputed = SimpleNamespace(as_dict=lambda: {"accepted": True})
    closure = tool._new_bridge_post_transition_closure(
        tool.CameraBridgePostTransitionStatus.NOT_REQUIRED,
        "zero-input test",
    )

    evidence = tool._bridge_capture_evidence(
        _capture_complete_result(input_attempted=False),
        analysis_authorization=analysis,
        adapter_identity="adapter",
        north_handoff=north,
        north_registration=None,
        planner_source_registration=precomputed,
        post_transition_closure=closure,
        post_transition_production=None,
        post_transition_registration=None,
        pointer_evidence=None,
        selected_class_name="SunAwtFrame",
        selected_title="RuneLite",
    )

    assert evidence["robust_registration_executed_in_input_seam"] is False
    assert evidence["registration_execution"] == {
        "north_to_commit_executed_in_input_seam": False,
        "planner_source_to_north_precomputed_before_arm": True,
        "post_transition_registration_performed": False,
        "post_transition_registration_stage": (
            "not_applicable_without_complete_physical_receipt"
        ),
        "production_re_evaluated_after_registration": False,
    }


def test_bridge_release_failure_retracts_only_this_invocations_report(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    unrelated = output / "reports" / "unrelated.camera.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")

    class FailingReleaseLease(_Lease):
        def __exit__(self, *_args: object) -> None:
            type(self).events.append("lease_release_failed")
            raise tool.CameraInputLeaseError("release failed")

    FailingReleaseLease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", FailingReleaseLease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: _capture_complete_result(input_attempted=False),
    )
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda _result, *, output_root: None,
    )

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "release-failure",
            *_review_args(),
        ]
    )

    report = output / "reports" / "release-failure.camera.json"
    assert result == 2
    assert not report.exists()
    assert not report.with_name(f"{report.name}.sha256").exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_bridge_postwrite_git_change_retracts_only_new_report(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    unrelated = output / "reports" / "unrelated.camera.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")
    git_calls = 0

    def git_state() -> tuple[str, bool]:
        nonlocal git_calls
        git_calls += 1
        return (("b" * 40, True) if git_calls == 6 else ("a" * 40, True))

    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", git_state)
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(
        tool,
        "_EXPECTED_WINDOWS_CAMERA_ADAPTER",
        f"{_Control.__module__}.{_Control.__qualname__}",
    )
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)

    def run(*_args: object, **kwargs: object) -> SimpleNamespace:
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256="5" * 64),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        kwargs["final_input_guard"](evidence, evidence, evidence)
        return _capture_complete_result()

    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda _result, *, output_root: None,
    )

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "postwrite-mutation",
            *_review_args(),
        ]
    )

    report = output / "reports" / "postwrite-mutation.camera.json"
    assert result == 2
    assert git_calls == 6
    assert not report.exists()
    assert not report.with_name(f"{report.name}.sha256").exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
