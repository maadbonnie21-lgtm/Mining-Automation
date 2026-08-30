from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Lock
from types import ModuleType, SimpleNamespace

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.validation import camera_bridge_authorization as authorization
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


@pytest.fixture(autouse=True)
def isolated_host_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Keep real one-shot operations in one per-test host-global store."""

    # Keep the injected Known Folder root short enough that the fixed campaign
    # artifact names remain below legacy Win32 MAX_PATH during native tests.
    root = tmp_path.parent / (
        "h" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    )
    root.mkdir()
    monkeypatch.setattr(authorization, "_host_authority_base", lambda: root)
    return root


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
        self.target_identity = SimpleNamespace(
            process_id=456,
            thread_id=789,
            class_name="SunAwtFrame",
            title="RuneLite - Chief Luma",
        )

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
    analysis = tool._BridgeAnalysisEvidence(
        report_path=tmp_path / "analysis.json",
        report_sha256="1" * 64,
        r1_report_sha256="3" * 64,
        planner_id="issue31-read-only-camera-bridge-planner-r2",
        planner_version="2.1.0",
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
        window_hwnd=123,
        window_process_id=456,
        window_thread_id=789,
        window_class_name="SunAwtFrame",
        window_title_sha256=hashlib.sha256(
            b"RuneLite - Chief Luma"
        ).hexdigest(),
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_evidence",
        lambda *_args, **_kwargs: analysis,
    )
    monkeypatch.setattr(tool, "_BRIDGE_LIVE_INPUT_ENABLED", True)
    monkeypatch.setattr(
        tool,
        "camera_bridge_authorization_consumed",
        lambda _root: False,
    )
    authorization_evidence = tool.CameraBridgeAuthorizationEvidence(
        r1_report_sha256="3" * 64,
        r2_report_sha256="1" * 64,
        north_report_sha256="2" * 64,
        north_post_sha256=hashlib.sha256(frame.payload).hexdigest(),
        commit_sha256="4" * 64,
        target_hwnd=123,
        target_process_id=456,
        target_thread_id=789,
        target_class_name="SunAwtFrame",
        target_title_sha256=hashlib.sha256(
            b"RuneLite - Chief Luma"
        ).hexdigest(),
    )
    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        lambda *_args, **_kwargs: tool.CameraBridgeAuthorizationReservation(
            git_head_sha="a" * 40,
            host_authority_root=tmp_path,
            sentinel_path=tmp_path / "authorization.json",
            sentinel_sha256="5" * 64,
            evidence=authorization_evidence,
        ),
    )
    monkeypatch.setattr(
        tool,
        "seal_camera_bridge_completion",
        lambda *_args, **_kwargs: SimpleNamespace(seal_sha256="6" * 64),
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
            "frames": {},
            "receipt": {},
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
                "disposition": "no_safe_endpoint_evidence",
                "family_evaluations": [
                    {
                        "anchor_evaluations": [
                            {
                                "anchor_sha256": "6" * 64,
                                "complete": True,
                                "missing_edge_ids": [],
                                "verified_edge_ids": ["8" * 64, "9" * 64],
                            }
                        ],
                        "complete": False,
                        "distinct_endpoint_sha256s": ["4" * 64, "5" * 64],
                        "distinct_receipt_report_sha256s": list(
                            sorted(tool._BRIDGE_OBJECTIVE_REPORT_SHA256S)
                        ),
                        "failure_reasons": [
                            "repeat_edge_not_verified_all_zones:" + "7" * 64
                        ],
                        "family_id": tool.FROZEN_ENDPOINT_OBJECTIVE.family_id,
                        "frozen_anchor_sha256s": ["6" * 64],
                        "qualifying_common_anchor_sha256s": ["6" * 64],
                    }
                ],
                "inventory": {
                    "inventory_id": (
                        "issue31-frozen-receipt-backed-camera-primitives-r2"
                    ),
                    "inventory_version": "2.0.0",
                    "experiments": [
                        {
                            "action_id": tool.CAMERA_BRIDGE_CAPTURE_ID,
                            "duration_s": tool.CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
                            "experiment_id": tool._BRIDGE_OBJECTIVE_ID,
                            "family_id": tool.FROZEN_ENDPOINT_OBJECTIVE.family_id,
                            "key": "right",
                            "minimum_distinct_receipt_endpoints": 2,
                            "ordinal": 1,
                            "required_source_sha256": source_sha256,
                            "selection_backing_report_sha256s": list(
                                sorted(tool._BRIDGE_OBJECTIVE_REPORT_SHA256S)
                            ),
                        }
                    ]
                },
                "matrix_policy": {
                    "rejected_registration_matrices_used_for_control": False,
                    "rejected_registration_metrics_used_for_ranking": False,
                },
                "missing_experiment": None,
                "planner_id": tool.CAMERA_BRIDGE_PLANNER_ID,
                "planner_version": tool.CAMERA_BRIDGE_PLANNER_VERSION,
                "ranked_families": [],
            },
            "corpus": {"north": {"frame": {}}},
            "result": {
                "conclusion": "no safe endpoint evidence",
                "live_input_authorized": False,
                "reacquisition_success_claimed": False,
                "selected_experiment_id": None,
                "smallest_additional_evidence": (
                    tool._BRIDGE_SMALLEST_ADDITIONAL_EVIDENCE
                ),
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


def test_seal_compares_the_ordered_post_production_without_a_second_call(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = bytes((1, 2, 3, 255))
    raw = tmp_path / "r2-post.raw"
    raw.write_bytes(payload)
    readiness = SimpleNamespace(safe_to_attempt_camera_input=True)
    production = SimpleNamespace(passed=False)
    artifact = tool.CameraFrameArtifact(
        label="r2-post",
        frame_id=4,
        width=1,
        height=1,
        pixel_format=PixelFormat.BGRA8888.value,
        raw_sha256=hashlib.sha256(payload).hexdigest(),
        files=(("raw", raw.name),),
    )
    post = tool.CameraServoFrameEvidence(
        artifact=artifact,
        captured_monotonic_s=4.0,
        readiness=readiness,
        production=production,
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
        terminal_reason=tool.CameraBridgeCaptureTerminalReason.CAPTURE_COMPLETE,
    )
    detector_calls = 0

    def detector(_frame: Frame) -> object:
        nonlocal detector_calls
        detector_calls += 1
        return production

    monkeypatch.setattr(tool, "evaluate_client_input_readiness", lambda _frame: readiness)
    monkeypatch.setattr(tool, "evaluate_varrock_east_camera", detector)
    monkeypatch.setattr(
        tool,
        "_require_north_bootstrap_production_identity",
        lambda *_args, **_kwargs: None,
    )

    # This represents the sole ordered post-production call made immediately
    # after registration; sealing must compare, not invoke the detector again.
    assert detector(_unit_frame(tool)) is production
    tool._require_bridge_capture_result_identities(
        result,
        output_root=tmp_path,
        sealed_post_production=production,
        post_production_already_bound=True,
    )

    assert detector_calls == 1


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


def test_analysis_evidence_binds_exact_no_safe_replication_need(
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

    result = tool._load_bridge_analysis_evidence(
        tmp_path / "analysis.json",
        expected_sha256="1" * 64,
        expected_head="a" * 40,
    )

    assert result.objective_id == tool._BRIDGE_OBJECTIVE_ID
    assert result.source_sha256 == tool.FROZEN_ENDPOINT_SOURCE_SHA256
    assert result.source_frame is frame


def test_alternate_well_formed_analysis_digest_is_evidence_not_authority(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_path = tmp_path / "first-analysis.json"
    alternate_path = tmp_path / "alternate-analysis.json"
    first_payload = _analysis_payload(tool)
    alternate_payload = copy.deepcopy(first_payload)
    alternate_evidence = alternate_payload["evidence"]
    assert isinstance(alternate_evidence, dict)
    # Performance metadata legitimately changes canonical report bytes without
    # changing any source, objective, action, policy, or authority semantic.
    alternate_evidence["performance"] = {
        "elapsed_seconds": 12.5,
        "peak_traced_memory_bytes": 4096,
    }

    def report_sha256(payload: dict[str, object]) -> str:
        canonical = (
            json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    first_sha256 = report_sha256(first_payload)
    alternate_sha256 = report_sha256(alternate_payload)
    assert first_sha256 != alternate_sha256
    reports = {
        first_path: (first_sha256, first_payload),
        alternate_path: (alternate_sha256, alternate_payload),
    }

    def load_report(
        report: Path,
        *,
        expected_sha256: str,
    ) -> tuple[Path, dict[str, object]]:
        reviewed_path = Path(report)
        reviewed_sha256, payload = reports[reviewed_path]
        assert expected_sha256 == reviewed_sha256
        return reviewed_path, payload

    frame = _unit_frame(tool)
    monkeypatch.setattr(tool, "_load_private_bound_report", load_report)
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_source",
        lambda *_args, **_kwargs: (frame, tmp_path / "north.raw"),
    )

    first = tool._load_bridge_analysis_evidence(
        first_path,
        expected_sha256=first_sha256,
        expected_head="a" * 40,
    )
    alternate = tool._load_bridge_analysis_evidence(
        alternate_path,
        expected_sha256=alternate_sha256,
        expected_head="a" * 40,
    )

    assert first.report_sha256 != alternate.report_sha256
    assert first.source_sha256 == alternate.source_sha256
    assert first.source_sha256 == tool.FROZEN_ENDPOINT_SOURCE_SHA256
    assert first.objective_id == alternate.objective_id == tool._BRIDGE_OBJECTIVE_ID
    assert tool.FROZEN_ENDPOINT_OBJECTIVE.action_id == tool.CAMERA_BRIDGE_CAPTURE_ID
    assert tool.FROZEN_ENDPOINT_OBJECTIVE.key.value == "right"
    assert tool.FROZEN_ENDPOINT_OBJECTIVE.duration_s == 0.043

    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda path: Path(path))
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    monkeypatch.setattr(tool, "_BRIDGE_LIVE_INPUT_ENABLED", False)
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_evidence",
        lambda *_args, **_kwargs: pytest.fail(
            "an analysis digest cannot bypass the source-literal input gate"
        ),
    )
    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        lambda *_args, **_kwargs: pytest.fail(
            "an analysis digest cannot consume input authorization"
        ),
    )
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail(
            "an analysis digest cannot grant physical input"
        ),
    )

    for ordinal, (report_path, report_sha256) in enumerate(
        ((first_path, first_sha256), (alternate_path, alternate_sha256)),
        start=1,
    ):
        result = tool.main(
            [
                "bridge-capture-r2",
                "--expected-head",
                "a" * 40,
                "--analysis-report",
                str(report_path),
                "--analysis-sha256",
                report_sha256,
                "--north-report",
                "north.json",
                "--north-sha256",
                "2" * 64,
                "--output",
                str(tmp_path / f"private-{ordinal}"),
                "--case-prefix",
                f"analysis-evidence-{ordinal}",
            ]
        )
        assert result == 2

    assert tool._BRIDGE_LIVE_INPUT_ENABLED is False
    assert _Backend.constructed == 0
    assert _Control.last is None
    assert _Lease.events == []


def test_north_handoff_command_and_window_identity_are_exact(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    output = tmp_path / "diagnostics" / "north"
    report = output / "reports" / "fresh.camera.json"
    command = [
        str(Path(sys.executable).resolve()),
        str(Path(tool.__file__).resolve()),
        "north-bootstrap-v2",
        "--output",
        str(output),
        "--case-prefix",
        "fresh",
    ]
    tool._require_north_handoff_command_argv(command, report_path=report)
    binding = {
        "selected_window_binding": {
            "adapter_identity": tool._EXPECTED_WINDOWS_CAMERA_ADAPTER,
            "class_name": "SunAwtFrame",
            "hwnd": 123,
            "process_id": 456,
            "thread_id": 789,
            "title_sha256": "1" * 64,
            "title_substring": "runelite",
        }
    }
    assert tool._bridge_north_window_binding(binding) == (
        123,
        456,
        789,
        "SunAwtFrame",
        "1" * 64,
    )

    command.extend(("--title", "forged"))
    with pytest.raises(ValueError, match="override"):
        tool._require_north_handoff_command_argv(command, report_path=report)
    window = binding["selected_window_binding"]
    assert isinstance(window, dict)
    window["hwnd"] = 999
    assert tool._bridge_north_window_binding(binding)[0] == 999
    window["adapter_identity"] = "forged.adapter"
    with pytest.raises(ValueError, match="selected-window binding"):
        tool._bridge_north_window_binding(binding)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong-plan", "frozen compass primitive"),
        ("incomplete-receipt", "input receipt is incomplete"),
        ("extra-action", "input receipt is incomplete"),
        ("wrong-geometry", "reviewed geometry"),
    ],
)
def test_live_north_loader_mutation_stops_launcher_before_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    north_root = tmp_path / "north-private"
    report_path = north_root / "reports" / "fresh.camera.json"
    raw_path = north_root / "frames" / "fresh-post.raw"
    raw_path.parent.mkdir(parents=True)
    raw_payload = bytes((1, 2, 3, 255)) * (
        tool.EXPECTED_CLIENT_WIDTH * tool.EXPECTED_CLIENT_HEIGHT
    )
    raw_path.write_bytes(raw_payload)
    raw_sha256 = hashlib.sha256(raw_payload).hexdigest()
    plan = {
        "actions": [
            {
                "kind": "compass_click",
                "x": tool.REVIEWED_COMPASS_POINT[0],
                "y": tool.REVIEWED_COMPASS_POINT[1],
            }
        ],
        "name": "issue31-v2-01-heading-north",
    }
    action_receipt = {
        "action": plan["actions"][0],
        "action_index": 0,
        "input_receipts": [
            {
                "complete": True,
                "completed_events": 2,
                "operation": "compass_click",
                "requested_events": 2,
            }
        ],
    }
    baseline: dict[str, object] = {
        "schema_version": 2,
        "provenance": {
            "command_argv": [
                str(Path(sys.executable).resolve()),
                str(Path(tool.__file__).resolve()),
                "north-bootstrap-v2",
                "--output",
                str(north_root),
                "--case-prefix",
                "fresh",
            ],
            "detector_id": tool._EXPECTED_DETECTOR_ID,
            "detector_version": tool._EXPECTED_DETECTOR_VERSION,
            "git_head_sha": "a" * 40,
            "plan_id": tool._EXPECTED_GUIDANCE_V2_ID,
            "plan_version": tool._EXPECTED_GUIDANCE_V2_VERSION,
            "profile_id": tool._EXPECTED_PROFILE_ID,
            "tracked_worktree_clean": True,
        },
        "evidence": {
            "command": "north-bootstrap-v2",
            "development_only": True,
            "terminal_reason": "bootstrap_executed",
            "plan": plan,
            "receipt": {
                "actions": [action_receipt],
                "plan": plan,
                "preflight": {
                    "client_height": tool.EXPECTED_CLIENT_HEIGHT,
                    "client_width": tool.EXPECTED_CLIENT_WIDTH,
                    "focused": True,
                    "supported": True,
                },
            },
            "frames": {
                "post": {
                    "artifact": {
                        "captured_monotonic_s": tool.time.monotonic(),
                        "files": {"raw": "frames/fresh-post.raw"},
                        "frame_id": 1,
                        "height": tool.EXPECTED_CLIENT_HEIGHT,
                        "pixel_format": PixelFormat.BGRA8888.value,
                        "raw_sha256": raw_sha256,
                        "width": tool.EXPECTED_CLIENT_WIDTH,
                    },
                    "production": {"passed": False},
                    "readiness": {"safe": True},
                }
            },
            "selected_window_binding": {
                "adapter_identity": tool._EXPECTED_WINDOWS_CAMERA_ADAPTER,
                "class_name": "SunAwtFrame",
                "hwnd": 123,
                "process_id": 456,
                "thread_id": 789,
                "title_sha256": hashlib.sha256(
                    b"RuneLite - Chief Luma"
                ).hexdigest(),
                "title_substring": "runelite",
            },
        },
    }
    active_payload = [baseline]

    def load_report(
        _report: Path,
        *,
        expected_sha256: str,
    ) -> tuple[Path, dict[str, object]]:
        assert expected_sha256 == "7" * 64
        return report_path, active_payload[0]

    readiness = SimpleNamespace(safe_to_attempt_camera_input=True)
    production = SimpleNamespace(passed=False)
    monkeypatch.setattr(tool, "_load_private_bound_report", load_report)
    monkeypatch.setattr(
        tool,
        "evaluate_client_input_readiness",
        lambda _frame: readiness,
    )
    monkeypatch.setattr(tool, "evaluate_varrock_east_camera", lambda _frame: production)
    monkeypatch.setattr(tool, "_readiness_dict", lambda _value: {"safe": True})
    monkeypatch.setattr(tool, "_evaluation_dict", lambda _value: {"passed": False})
    monkeypatch.setattr(
        tool,
        "_require_north_bootstrap_production_identity",
        lambda *_args, **_kwargs: None,
    )

    valid = tool._load_bridge_north_handoff(
        report_path,
        expected_sha256="7" * 64,
        expected_head="a" * 40,
    )
    assert valid.frame.payload == raw_payload

    mutated = copy.deepcopy(baseline)
    mutated_evidence = mutated["evidence"]
    assert isinstance(mutated_evidence, dict)
    if mutation == "wrong-plan":
        mutated_plan = mutated_evidence["plan"]
        assert isinstance(mutated_plan, dict)
        mutated_plan["name"] = "forged-plan"
    else:
        receipt = mutated_evidence["receipt"]
        assert isinstance(receipt, dict)
        actions = receipt["actions"]
        assert isinstance(actions, list) and isinstance(actions[0], dict)
        if mutation == "incomplete-receipt":
            low_level = actions[0]["input_receipts"]
            assert isinstance(low_level, list) and isinstance(low_level[0], dict)
            low_level[0]["complete"] = False
        elif mutation == "extra-action":
            actions.append(copy.deepcopy(actions[0]))
        else:
            frames = mutated_evidence["frames"]
            assert isinstance(frames, dict)
            post = frames["post"]
            assert isinstance(post, dict)
            artifact = post["artifact"]
            assert isinstance(artifact, dict)
            artifact["width"] = tool.EXPECTED_CLIENT_WIDTH - 1
    active_payload[0] = mutated

    with pytest.raises(ValueError, match=message):
        tool._load_bridge_north_handoff(
            report_path,
            expected_sha256="7" * 64,
            expected_head="a" * 40,
        )

    analysis_frame = _unit_frame(tool)
    analysis = tool._BridgeAnalysisEvidence(
        report_path=tmp_path / "analysis.json",
        report_sha256="1" * 64,
        r1_report_sha256="3" * 64,
        planner_id="issue31-read-only-camera-bridge-planner-r2",
        planner_version="2.1.0",
        objective_id=tool._BRIDGE_OBJECTIVE_ID,
        source_frame=analysis_frame,
        source_raw_path=tmp_path / "planner-source.raw",
        source_sha256=tool.FROZEN_ENDPOINT_SOURCE_SHA256,
    )
    bridge_output = tmp_path / "bridge-private"
    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_BRIDGE_LIVE_INPUT_ENABLED", True)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(
        tool,
        "camera_bridge_authorization_consumed",
        lambda _root: False,
    )
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: bridge_output)
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_evidence",
        lambda *_args, **_kwargs: analysis,
    )
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid north evidence must fail before authorization reservation"
        ),
    )
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid north evidence must fail before physical input"
        ),
    )

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--analysis-report",
            "analysis.json",
            "--analysis-sha256",
            "1" * 64,
            "--north-report",
            str(report_path),
            "--north-sha256",
            "7" * 64,
            "--output",
            str(bridge_output),
            "--case-prefix",
            f"north-{mutation}",
        ]
    )

    assert result == 2
    assert _Backend.constructed == 0
    assert _Control.last is None
    assert _Lease.events == []


@pytest.mark.parametrize(
    "mutation",
    [
        "authority_extra",
        "matrix_policy",
        "action_id",
        "receipt_backing",
        "family_incomplete",
        "common_anchor",
        "source_mismatch",
        "source_not_frozen",
        "no_safe_disposition",
    ],
)
def test_analysis_evidence_rejects_forged_or_incomplete_planner_result(
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
    inventory = planner["inventory"]
    assert isinstance(inventory, dict)
    experiments = inventory["experiments"]
    assert isinstance(experiments, list) and isinstance(experiments[0], dict)
    objective = experiments[0]
    if mutation == "authority_extra":
        authority = evidence["authority"]
        assert isinstance(authority, dict)
        authority["forged"] = False
    elif mutation == "matrix_policy":
        matrix = planner["matrix_policy"]
        assert isinstance(matrix, dict)
        matrix["rejected_registration_metrics_used_for_ranking"] = True
    elif mutation == "action_id":
        objective["action_id"] = "forged"
    elif mutation == "receipt_backing":
        objective["selection_backing_report_sha256s"] = ["9" * 64]
    elif mutation == "family_incomplete":
        families = planner["family_evaluations"]
        assert isinstance(families, list) and isinstance(families[0], dict)
        families[0]["complete"] = True
        families[0]["failure_reasons"] = []
    elif mutation == "common_anchor":
        families = planner["family_evaluations"]
        assert isinstance(families, list) and isinstance(families[0], dict)
        families[0]["qualifying_common_anchor_sha256s"] = ["9" * 64]
    elif mutation == "source_mismatch":
        safe_graph = evidence["safe_view_graph"]
        assert isinstance(safe_graph, dict)
        safe_graph["current_sha256"] = "8" * 64
    elif mutation == "source_not_frozen":
        forged_source = "4" * 64
        objective["required_source_sha256"] = forged_source
        planner["current_sha256"] = forged_source
        safe_graph = evidence["safe_view_graph"]
        assert isinstance(safe_graph, dict)
        safe_graph["current_sha256"] = forged_source
    else:
        planner["disposition"] = "missing_experiment"
        planner["missing_experiment"] = {"can_execute_input": False}
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
        tool._load_bridge_analysis_evidence(
            tmp_path / "analysis.json",
            expected_sha256="1" * 64,
            expected_head="a" * 40,
        )


def test_wrong_frozen_source_report_stops_launcher_before_reservation_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(_analysis_payload(tool))
    evidence = payload["evidence"]
    assert isinstance(evidence, dict)
    planner = evidence["bridge_planner"]
    safe_graph = evidence["safe_view_graph"]
    assert isinstance(planner, dict) and isinstance(safe_graph, dict)
    inventory = planner["inventory"]
    assert isinstance(inventory, dict)
    experiments = inventory["experiments"]
    assert isinstance(experiments, list) and isinstance(experiments[0], dict)
    forged_source = "4" * 64
    experiments[0]["required_source_sha256"] = forged_source
    planner["current_sha256"] = forged_source
    safe_graph["current_sha256"] = forged_source
    report_sha256 = hashlib.sha256(
        (json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    ).hexdigest()
    report_path = tmp_path / "wrong-source-analysis.json"
    output = tmp_path / "private"

    def load_report(
        report: Path,
        *,
        expected_sha256: str,
    ) -> tuple[Path, dict[str, object]]:
        assert Path(report) == report_path
        assert expected_sha256 == report_sha256
        return report_path, payload

    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_load_private_bound_report", load_report)
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_source",
        lambda *_args, **_kwargs: pytest.fail(
            "wrong frozen source must fail before loading source pixels"
        ),
    )
    monkeypatch.setattr(tool, "_BRIDGE_LIVE_INPUT_ENABLED", True)
    monkeypatch.setattr(
        tool,
        "camera_bridge_authorization_consumed",
        lambda _root: False,
    )
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    monkeypatch.setattr(
        tool,
        "_load_bridge_north_handoff",
        lambda *_args, **_kwargs: pytest.fail(
            "wrong frozen source must fail before loading north evidence"
        ),
    )
    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        lambda *_args, **_kwargs: pytest.fail(
            "wrong frozen source must fail before authorization reservation"
        ),
    )
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail(
            "wrong frozen source must fail before physical input"
        ),
    )

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--analysis-report",
            str(report_path),
            "--analysis-sha256",
            report_sha256,
            "--north-report",
            "north.json",
            "--north-sha256",
            "2" * 64,
            "--output",
            str(output),
            "--case-prefix",
            "wrong-frozen-source",
        ]
    )

    assert result == 2
    assert _Backend.constructed == 0
    assert _Control.last is None
    assert _Lease.events == []


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
        ["--hwnd", "123"],
        ["--campaign-id", "forged"],
        ["--sentinel", "forged"],
        ["--authorization-root", "forged"],
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


def test_hostile_git_environment_cannot_redirect_launcher_provenance(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    output = tmp_path / "private"
    hostile_git_values = {
        "GIT_COMMON_DIR": str(tmp_path / "forged-common.git"),
        "GIT_CONFIG_GLOBAL": str(tmp_path / "forged-global.gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "0",
        "GIT_DIR": str(tmp_path / "forged.git"),
        "GIT_INDEX_FILE": str(tmp_path / "forged.index"),
        "GIT_OBJECT_DIRECTORY": str(tmp_path / "forged-objects"),
        "GIT_OPTIONAL_LOCKS": "1",
        "GIT_TERMINAL_PROMPT": "1",
        "GIT_WORK_TREE": str(tmp_path / "forged-worktree"),
        "HOME": str(tmp_path / "forged-home"),
        "PATH": str(tmp_path / "forged-bin"),
        "USERPROFILE": str(tmp_path / "forged-profile"),
    }
    for key, value in hostile_git_values.items():
        monkeypatch.setenv(key, value)

    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def git_run(
        command: list[str],
        **kwargs: object,
    ) -> SimpleNamespace:
        environment = kwargs.get("env")
        assert isinstance(environment, dict)
        git_environment = {
            str(key): str(value)
            for key, value in environment.items()
            if str(key).upper().startswith("GIT_")
        }
        assert git_environment == {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
        assert "HOME" not in environment
        assert "PATH" not in environment
        assert "USERPROFILE" not in environment
        normalized = tuple(str(argument) for argument in command)
        assert f"--git-dir={(repository / '.git').resolve()}" in normalized
        assert f"--work-tree={repository.resolve()}" in normalized
        assert "core.autocrlf=input" in normalized
        calls.append((normalized, environment))
        if "rev-parse" in normalized:
            # The trusted repository is deliberately the wrong reviewed head.
            return SimpleNamespace(stdout="b" * 40 + "\n", returncode=0)
        assert "status" in normalized
        return SimpleNamespace(stdout="", returncode=0)

    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_REPO_ROOT", repository)
    monkeypatch.setattr(tool, "_trusted_git_executable", lambda: "trusted-git")
    monkeypatch.setattr(tool.subprocess, "run", git_run)
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        lambda *_args, **_kwargs: pytest.fail(
            "hostile Git environment must not reach authorization reservation"
        ),
    )
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail(
            "hostile Git environment must not reach physical input"
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
            "hostile-git-environment",
            *_review_args(),
        ]
    )

    assert result == 2
    assert len(calls) == 2
    assert _Backend.constructed == 0
    assert _Control.last is None
    assert _Lease.events == []
    assert all(os.environ[key] == value for key, value in hostile_git_values.items())


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
        monkeypatch.setattr(tool, "_BRIDGE_LIVE_INPUT_ENABLED", True)
        monkeypatch.setattr(
            tool,
            "_load_bridge_analysis_evidence",
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


def test_stale_north_handoff_stops_before_reservation_backend_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "CaptureSource", _Source)
    monkeypatch.setattr(tool, "WindowsCameraControl", _Control)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    _patch_reviewed_inputs(tool, monkeypatch, tmp_path)
    north = tool._load_bridge_north_handoff(
        Path("north.json"),
        expected_sha256="2" * 64,
        expected_head="a" * 40,
    )
    stale_capture_time = (
        tool.time.monotonic() - tool._BRIDGE_NORTH_MAXIMUM_AGE_SECONDS - 1.0
    )
    stale_frame = Frame.from_raw(
        RawFrame(
            payload=north.frame.payload,
            width=north.frame.width,
            height=north.frame.height,
            pixel_format=north.frame.pixel_format,
        ),
        frame_id=north.frame.frame_id,
        captured_monotonic_s=stale_capture_time,
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_north_handoff",
        lambda *_args, **_kwargs: replace(north, frame=stale_frame),
    )
    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        lambda *_args, **_kwargs: pytest.fail(
            "stale north evidence must stop before authorization reservation"
        ),
    )
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail(
            "stale north evidence must stop before physical input"
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
            "stale-north-handoff",
            *_review_args(),
        ]
    )

    assert result == 2
    assert _Backend.constructed == 0
    assert _Control.last is None
    assert _Lease.events == ["lease_acquired", "lease_released"]


def test_bridge_launcher_is_inert_without_exact_head_lead_enablement(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _Backend.constructed = 0
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_evidence",
        lambda *_args, **_kwargs: pytest.fail(
            "an input-disabled launcher must not convert analysis into authority"
        ),
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_north_handoff",
        lambda *_args, **_kwargs: pytest.fail(
            "an input-disabled launcher must not load live handoff state"
        ),
    )
    monkeypatch.setattr(
        tool,
        "camera_bridge_authorization_consumed",
        lambda _root: pytest.fail(
            "an input-disabled launcher must not inspect or consume authorization"
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
            "lead-gate-refusal",
            *_review_args(),
        ]
    )

    assert result == 2
    assert tool._BRIDGE_LIVE_INPUT_ENABLED is False
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
    authorization_evidence = tool.CameraBridgeAuthorizationEvidence(
        r1_report_sha256="3" * 64,
        r2_report_sha256="1" * 64,
        north_report_sha256="2" * 64,
        north_post_sha256=hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest(),
        commit_sha256=hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest(),
        target_hwnd=123,
        target_process_id=456,
        target_thread_id=789,
        target_class_name="SunAwtFrame",
        target_title_sha256=hashlib.sha256(
            b"RuneLite - Chief Luma"
        ).hexdigest(),
    )

    def reserve_without_files(*_args: object, **_kwargs: object) -> object:
        _Lease.events.append("authorization_consumed")
        return tool.CameraBridgeAuthorizationReservation(
            git_head_sha="a" * 40,
            host_authority_root=tmp_path,
            sentinel_path=tmp_path / "never-written-authorization.json",
            sentinel_sha256="5" * 64,
            evidence=authorization_evidence,
        )

    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        reserve_without_files,
    )
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
        _Lease.events.append("physical_action")
        return _capture_complete_result()

    def revalidate(
        _result: object,
        *,
        output_root: Path,
        sealed_post_production: object,
        post_production_already_bound: bool,
    ) -> None:
        assert output_root == output
        assert sealed_post_production is None
        assert post_production_already_bound is False
        _Lease.events.append("revalidate")

    original_write = tool.write_camera_validation_report

    def write(*args: object, **kwargs: object) -> object:
        _Lease.events.append("publish")
        return original_write(*args, **kwargs)

    def seal(*_args: object, **kwargs: object) -> object:
        assert _Lease.events[-1] == "lease_released"
        completion_evidence = kwargs["evidence"]
        assert isinstance(completion_evidence, tool.CameraBridgeCompletionEvidence)
        assert completion_evidence.authorization_sentinel_sha256 == "5" * 64
        _Lease.events.append("completion_seal")
        return SimpleNamespace(seal_sha256="6" * 64)

    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(tool, "_require_bridge_capture_result_identities", revalidate)
    monkeypatch.setattr(tool, "write_camera_validation_report", write)
    monkeypatch.setattr(tool, "seal_camera_bridge_completion", seal)
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
        "authorization_consumed",
        "physical_action",
        "input_cleanup",
        "capture_cleanup",
        "post_registration_then_production",
        "revalidate",
        "publish",
        "lease_released",
        "completion_seal",
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


@pytest.mark.parametrize("artifact", ["reservation", "completion-pending", "completion"])
@pytest.mark.parametrize("contents", [b"", b"interrupted", b'{"tampered":true}\n'])
def test_precreated_or_tampered_campaign_blocks_every_launcher_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: str,
    contents: bytes,
) -> None:
    first_repository = tmp_path / "clone-a"
    second_repository = tmp_path / "clone-b"
    (first_repository / ".git").mkdir(parents=True)
    (second_repository / ".git").mkdir(parents=True)
    artifact_path = {
        "reservation": authorization.camera_bridge_authorization_sentinel_path(
            first_repository
        ),
        "completion-pending": authorization._completion_pending_path(first_repository),
        "completion": authorization.camera_bridge_completion_seal_path(first_repository),
    }[artifact]
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(contents)
    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_BRIDGE_LIVE_INPUT_ENABLED", True)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, True))
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda path: Path(path))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _Backend)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _Lease)
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_evidence",
        lambda *_args, **_kwargs: pytest.fail(
            "consumed campaign must stop before analysis loading"
        ),
    )
    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        lambda *_args, **_kwargs: pytest.fail(
            "precreated campaign must stop before another reservation"
        ),
    )
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail(
            "precreated campaign must stop before physical input"
        ),
    )

    for ordinal, repository in enumerate(
        (first_repository, second_repository),
        start=1,
    ):
        monkeypatch.setattr(tool, "_REPO_ROOT", repository)
        result = tool.main(
            [
                "bridge-capture-r2",
                "--expected-head",
                "a" * 40,
                "--output",
                str(tmp_path / f"private-{ordinal}"),
                "--case-prefix",
                f"precreated-campaign-{ordinal}",
                *_review_args(),
            ]
        )
        assert result == 2

    assert artifact_path.read_bytes() == contents
    assert _Backend.constructed == 0
    assert _Control.last is None
    assert _Lease.events == []


def test_interrupted_final_reservation_consumes_campaign_with_zero_retry_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_repository = tmp_path / "clone-a"
    second_repository = tmp_path / "clone-b"
    (first_repository / ".git").mkdir(parents=True)
    (second_repository / ".git").mkdir(parents=True)
    first_output = tmp_path / "first-private"
    second_output = tmp_path / "second-private"
    real_consumed = tool.camera_bridge_authorization_consumed
    real_reserve = tool.reserve_camera_bridge_authorization
    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_REPO_ROOT", first_repository)
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda path: Path(path))
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
    monkeypatch.setattr(tool, "camera_bridge_authorization_consumed", real_consumed)

    original_fsync = authorization.os.fsync

    def interrupted_reserve(*args: object, **kwargs: object) -> object:
        def fail_fsync(_descriptor: int) -> None:
            raise OSError("simulated final authorization interruption")

        authorization.os.fsync = fail_fsync
        try:
            return real_reserve(*args, **kwargs)
        finally:
            authorization.os.fsync = original_fsync

    runner_calls = 0
    physical_inputs = 0
    unit_digest = hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()

    def run(*_args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal runner_calls, physical_inputs
        runner_calls += 1
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=unit_digest),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        kwargs["final_input_guard"](evidence, evidence, evidence)
        physical_inputs += 1
        return _capture_complete_result()

    monkeypatch.setattr(tool, "reserve_camera_bridge_authorization", interrupted_reserve)
    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda *_args, **_kwargs: None,
    )

    first_result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(first_output),
            "--case-prefix",
            "interrupted-first",
            *_review_args(),
        ]
    )
    first_backend_count = _Backend.constructed
    first_lease_events = tuple(_Lease.events)
    monkeypatch.setattr(tool, "_REPO_ROOT", second_repository)
    second_result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(second_output),
            "--case-prefix",
            "interrupted-retry",
            *_review_args(),
        ]
    )

    assert first_result == 2
    assert second_result == 2
    assert runner_calls == 1
    assert physical_inputs == 0
    assert authorization.camera_bridge_authorization_consumed(second_repository)
    assert _Backend.constructed == first_backend_count
    assert tuple(_Lease.events) == first_lease_events
    assert not (
        second_output / "reports" / "interrupted-retry.camera.json"
    ).exists()


def test_interrupted_completion_seal_retracts_report_and_blocks_retry_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_repository = tmp_path / "clone-a"
    second_repository = tmp_path / "clone-b"
    (first_repository / ".git").mkdir(parents=True)
    (second_repository / ".git").mkdir(parents=True)
    first_output = tmp_path / "first-private"
    second_output = tmp_path / "second-private"
    real_consumed = tool.camera_bridge_authorization_consumed
    real_reserve = tool.reserve_camera_bridge_authorization
    real_seal = tool.seal_camera_bridge_completion
    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_REPO_ROOT", first_repository)
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda path: Path(path))
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
    monkeypatch.setattr(tool, "camera_bridge_authorization_consumed", real_consumed)
    monkeypatch.setattr(tool, "reserve_camera_bridge_authorization", real_reserve)

    original_fsync = authorization.os.fsync

    def interrupted_seal(*args: object, **kwargs: object) -> object:
        def fail_fsync(_descriptor: int) -> None:
            raise OSError("simulated completion-seal interruption")

        authorization.os.fsync = fail_fsync
        try:
            return real_seal(*args, **kwargs)
        finally:
            authorization.os.fsync = original_fsync

    runner_calls = 0
    physical_inputs = 0
    unit_digest = hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()

    def run(*_args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal runner_calls, physical_inputs
        runner_calls += 1
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=unit_digest),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        kwargs["final_input_guard"](evidence, evidence, evidence)
        physical_inputs += 1
        return _capture_complete_result()

    monkeypatch.setattr(tool, "seal_camera_bridge_completion", interrupted_seal)
    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda *_args, **_kwargs: None,
    )

    first_result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(first_output),
            "--case-prefix",
            "completion-interrupted",
            *_review_args(),
        ]
    )
    first_backend_count = _Backend.constructed
    first_lease_events = tuple(_Lease.events)
    first_report = (
        first_output / "reports" / "completion-interrupted.camera.json"
    )
    monkeypatch.setattr(tool, "_REPO_ROOT", second_repository)
    second_result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(second_output),
            "--case-prefix",
            "completion-retry",
            *_review_args(),
        ]
    )

    assert first_result == 2
    assert second_result == 2
    assert runner_calls == 1
    assert physical_inputs == 1
    assert authorization.camera_bridge_authorization_consumed(second_repository)
    assert not authorization.camera_bridge_completion_seal_path(
        second_repository
    ).exists()
    pending_path = authorization._completion_pending_path(second_repository)
    assert pending_path.exists()
    assert not first_report.exists()
    assert not first_report.with_name(f"{first_report.name}.sha256").exists()
    assert _Backend.constructed == first_backend_count
    assert tuple(_Lease.events) == first_lease_events
    assert not (
        second_output / "reports" / "completion-retry.camera.json"
    ).exists()


def test_concurrent_launchers_can_cross_final_guard_only_once(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    real_consumed = tool.camera_bridge_authorization_consumed
    real_reserve = tool.reserve_camera_bridge_authorization
    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_REPO_ROOT", repository)
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda path: Path(path))
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
    monkeypatch.setattr(tool, "reserve_camera_bridge_authorization", real_reserve)

    initial_precheck = Barrier(2)

    def synchronized_consumed(root: Path) -> bool:
        observed = real_consumed(root)
        initial_precheck.wait(timeout=5.0)
        return observed

    monkeypatch.setattr(
        tool,
        "camera_bridge_authorization_consumed",
        synchronized_consumed,
    )
    input_seam = Barrier(2)
    counter_lock = Lock()
    runner_calls = 0
    physical_inputs = 0
    unit_digest = hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()

    def run(*_args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal runner_calls, physical_inputs
        with counter_lock:
            runner_calls += 1
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=unit_digest),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        input_seam.wait(timeout=5.0)
        kwargs["final_input_guard"](evidence, evidence, evidence)
        with counter_lock:
            physical_inputs += 1
        return _capture_complete_result()

    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda *_args, **_kwargs: None,
    )

    def invoke(ordinal: int) -> int:
        return int(
            tool.main(
                [
                    "bridge-capture-r2",
                    "--expected-head",
                    "a" * 40,
                    "--output",
                    str(tmp_path / f"concurrent-private-{ordinal}"),
                    "--case-prefix",
                    f"concurrent-{ordinal}",
                    *_review_args(),
                ]
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, (1, 2)))

    assert sorted(results) == [0, 2]
    assert runner_calls == 2
    assert physical_inputs == 1
    assert authorization.camera_bridge_authorization_consumed(repository)

    monkeypatch.setattr(tool, "camera_bridge_authorization_consumed", real_consumed)
    backend_count = _Backend.constructed
    runner_count = runner_calls
    third_result = invoke(3)
    assert third_result == 2
    assert runner_calls == runner_count
    assert physical_inputs == 1
    assert _Backend.constructed == backend_count


def test_completed_clone_a_launcher_blocks_clone_b_alternate_inputs_with_real_store(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_repository = tmp_path / "clone-a"
    second_repository = tmp_path / "clone-b"
    (first_repository / ".git").mkdir(parents=True)
    (second_repository / ".git").mkdir(parents=True)
    first_output = tmp_path / "first-private"
    second_output = tmp_path / "alternate-private"
    real_consumed = tool.camera_bridge_authorization_consumed
    real_reserve = tool.reserve_camera_bridge_authorization
    real_seal = tool.seal_camera_bridge_completion
    _Backend.constructed = 0
    _Control.last = None
    _Lease.events = []
    monkeypatch.setattr(tool, "_REPO_ROOT", first_repository)
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda path: Path(path))
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
    monkeypatch.setattr(tool, "camera_bridge_authorization_consumed", real_consumed)
    monkeypatch.setattr(tool, "reserve_camera_bridge_authorization", real_reserve)
    monkeypatch.setattr(tool, "seal_camera_bridge_completion", real_seal)

    runner_calls = 0
    physical_inputs = 0
    unit_digest = hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()

    def run(*_args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal runner_calls, physical_inputs
        runner_calls += 1
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=unit_digest),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        kwargs["final_input_guard"](evidence, evidence, evidence)
        physical_inputs += 1
        return _capture_complete_result()

    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda *_args, **_kwargs: None,
    )

    first_result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(first_output),
            "--case-prefix",
            "reviewed-first",
            *_review_args(),
        ]
    )
    first_backend_count = _Backend.constructed
    first_lease_events = tuple(_Lease.events)
    assert first_result == 0
    assert runner_calls == 1
    assert physical_inputs == 1
    assert authorization.camera_bridge_authorization_consumed(first_repository)
    assert authorization.camera_bridge_completion_seal_path(first_repository).exists()

    monkeypatch.setattr(tool, "_REPO_ROOT", second_repository)
    monkeypatch.setattr(
        tool,
        "_load_bridge_analysis_evidence",
        lambda *_args, **_kwargs: pytest.fail(
            "consumed clone B must stop before alternate analysis loading"
        ),
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_north_handoff",
        lambda *_args, **_kwargs: pytest.fail(
            "consumed clone B must stop before alternate north loading"
        ),
    )
    second_result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--analysis-report",
            str(tmp_path / "alternate-analysis.json"),
            "--analysis-sha256",
            "8" * 64,
            "--north-report",
            str(tmp_path / "alternate-north.json"),
            "--north-sha256",
            "9" * 64,
            "--output",
            str(second_output),
            "--case-prefix",
            "alternate-prefix",
        ]
    )

    assert second_result == 2
    assert runner_calls == 1
    assert physical_inputs == 1
    assert _Backend.constructed == first_backend_count
    assert tuple(_Lease.events) == first_lease_events
    assert authorization.camera_bridge_authorization_consumed(second_repository)
    assert not (
        second_output / "reports" / "alternate-prefix.camera.json"
    ).exists()


@pytest.mark.parametrize("fail_after_reservation", [False, True])
@pytest.mark.parametrize(
    ("second_prefix", "reuse_first_output"),
    [
        ("alternate-prefix", True),
        ("first-prefix", False),
        ("alternate-prefix", False),
    ],
    ids=["alternate-prefix", "alternate-output", "alternate-prefix-and-output"],
)
def test_one_shot_consumption_blocks_alternate_prefix_and_output_invocation(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_after_reservation: bool,
    second_prefix: str,
    reuse_first_output: bool,
) -> None:
    first_output = tmp_path / "first-private"
    second_output = (
        first_output if reuse_first_output else tmp_path / "second-private"
    )
    _Backend.constructed = 0
    _Lease.events = []
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda path: Path(path))
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
    consumed = False
    runner_calls = 0
    unit_digest = hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()
    authorization_evidence = tool.CameraBridgeAuthorizationEvidence(
        r1_report_sha256="3" * 64,
        r2_report_sha256="1" * 64,
        north_report_sha256="2" * 64,
        north_post_sha256=unit_digest,
        commit_sha256=unit_digest,
        target_hwnd=123,
        target_process_id=456,
        target_thread_id=789,
        target_class_name="SunAwtFrame",
        target_title_sha256=hashlib.sha256(
            b"RuneLite - Chief Luma"
        ).hexdigest(),
    )

    def is_consumed(_root: Path) -> bool:
        return consumed

    def reserve(*_args: object, **_kwargs: object) -> object:
        nonlocal consumed
        if consumed:
            raise RuntimeError("already consumed")
        consumed = True
        return tool.CameraBridgeAuthorizationReservation(
            git_head_sha="a" * 40,
            host_authority_root=tmp_path,
            sentinel_path=tmp_path / "fixed-campaign.consumed.json",
            sentinel_sha256="5" * 64,
            evidence=authorization_evidence,
        )

    def run(*_args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal runner_calls
        evidence = SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=unit_digest),
            production=object(),
        )
        kwargs["pre_input_guard"](evidence, evidence, evidence)
        kwargs["final_input_guard"](evidence, evidence, evidence)
        runner_calls += 1
        if fail_after_reservation:
            raise RuntimeError("simulated partial-or-unknown physical boundary")
        return _capture_complete_result()

    monkeypatch.setattr(tool, "camera_bridge_authorization_consumed", is_consumed)
    monkeypatch.setattr(tool, "reserve_camera_bridge_authorization", reserve)
    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda *_args, **_kwargs: None,
    )

    first_result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(first_output),
            "--case-prefix",
            "first-prefix",
            *_review_args(),
        ]
    )
    first_backend_count = _Backend.constructed
    first_lease_events = tuple(_Lease.events)
    second_result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(second_output),
            "--case-prefix",
            second_prefix,
            *_review_args(),
        ]
    )

    assert first_result == (2 if fail_after_reservation else 0)
    assert second_result == 2
    assert consumed is True
    assert runner_calls == 1
    assert _Backend.constructed == first_backend_count
    assert tuple(_Lease.events) == first_lease_events
    assert not (
        second_output / "reports" / f"{second_prefix}.camera.json"
    ).exists()


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


@pytest.mark.parametrize(
    "identity_change",
    [
        {"window_hwnd": 999},
        {"window_process_id": 999},
        {"window_thread_id": 999},
    ],
)
def test_substituted_north_window_stops_before_reservation_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_change: dict[str, int],
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
    valid_north = tool._load_bridge_north_handoff(
        Path("north.json"),
        expected_sha256="2" * 64,
        expected_head="a" * 40,
    )
    monkeypatch.setattr(
        tool,
        "_load_bridge_north_handoff",
        lambda *_args, **_kwargs: replace(valid_north, **identity_change),
    )
    monkeypatch.setattr(
        tool,
        "reserve_camera_bridge_authorization",
        lambda *_args, **_kwargs: pytest.fail(
            "a substituted north window must stop before reservation"
        ),
    )
    monkeypatch.setattr(
        tool,
        "run_fixed_camera_bridge_capture",
        lambda *_args, **_kwargs: pytest.fail(
            "a substituted north window must stop before the runner"
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
            "substituted-window",
            *_review_args(),
        ]
    )

    assert result == 2
    expected_cleanup = (
        ["capture_cleanup"]
        if "window_hwnd" in identity_change
        else ["input_cleanup", "capture_cleanup"]
    )
    assert _Lease.events == [
        "lease_acquired",
        *expected_cleanup,
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
    analysis = tool._BridgeAnalysisEvidence(
        report_path=tmp_path / "analysis.json",
        report_sha256="1" * 64,
        r1_report_sha256="3" * 64,
        planner_id="issue31-read-only-camera-bridge-planner-r2",
        planner_version="2.1.0",
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
        window_hwnd=123,
        window_process_id=456,
        window_thread_id=789,
        window_class_name="SunAwtFrame",
        window_title_sha256=hashlib.sha256(
            b"RuneLite - Chief Luma"
        ).hexdigest(),
    )
    precomputed = SimpleNamespace(as_dict=lambda: {"accepted": True})
    closure = tool._new_bridge_post_transition_closure(
        tool.CameraBridgePostTransitionStatus.NOT_REQUIRED,
        "zero-input test",
    )

    evidence = tool._bridge_capture_evidence(
        _capture_complete_result(input_attempted=False),
        analysis_evidence=analysis,
        authorization_reservation=None,
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
    assert evidence["one_shot_authorization"] is None
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


def test_complete_bridge_release_failure_never_seals_even_if_retraction_fails(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    output = tmp_path / "private"
    real_reserve = tool.reserve_camera_bridge_authorization

    class FailingReleaseLease(_Lease):
        def __exit__(self, *_args: object) -> None:
            type(self).events.append("lease_release_failed")
            raise tool.CameraInputLeaseError("release failed")

    FailingReleaseLease.events = []
    monkeypatch.setattr(tool, "_REPO_ROOT", repository)
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
    monkeypatch.setattr(tool, "reserve_camera_bridge_authorization", real_reserve)
    unit_digest = hashlib.sha256(bytes((1, 2, 3, 255))).hexdigest()

    def run(*_args: object, **kwargs: object) -> SimpleNamespace:
        stage = SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=unit_digest),
            production=object(),
        )
        kwargs["pre_input_guard"](stage, stage, stage)
        kwargs["final_input_guard"](stage, stage, stage)
        return _capture_complete_result()

    monkeypatch.setattr(tool, "run_fixed_camera_bridge_capture", run)
    monkeypatch.setattr(
        tool,
        "_require_bridge_capture_result_identities",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        tool,
        "seal_camera_bridge_completion",
        lambda *_args, **_kwargs: pytest.fail(
            "completion must not seal before a successful lease exit"
        ),
    )
    monkeypatch.setattr(
        tool,
        "_retract_report_targets_after_lease_failure",
        lambda *_args, **_kwargs: ("simulated report retraction failure",),
    )

    result = tool.main(
        [
            "bridge-capture-r2",
            "--expected-head",
            "a" * 40,
            "--output",
            str(output),
            "--case-prefix",
            "release-failure-complete",
            *_review_args(),
        ]
    )

    report = output / "reports" / "release-failure-complete.camera.json"
    assert result == 2
    assert report.exists()
    assert authorization.camera_bridge_authorization_consumed(repository)
    assert not authorization.camera_bridge_completion_seal_path(repository).exists()


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
