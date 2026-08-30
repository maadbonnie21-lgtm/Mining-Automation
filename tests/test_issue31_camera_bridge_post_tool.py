"""Strict offline-ingestion tests for the Issue #31 R2 post verifier."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.validation.camera_bridge_authorization import (
    CameraBridgeAuthorizationEvidence,
    CameraBridgeAuthorizationReservation,
)

_TOOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "verify_issue31_bridge_r2_post.py"
)
_HEAD = "a" * 40
_R1_SHA = "1" * 64
_R2_SHA = "2" * 64


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "verify_issue31_bridge_r2_post_test", _TOOL_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> ModuleType:
    return _load_tool()


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, sort_keys=True) + "\n").encode()
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(f"{path.name}.sha256").write_text(
        f"{digest}\n", encoding="ascii", newline="\n"
    )
    return digest


def _arguments() -> list[str]:
    return [
        "--expected-head",
        _HEAD,
        "--r1-report",
        "r1.json",
        "--r1-sha256",
        _R1_SHA,
        "--r2-report",
        "r2.json",
        "--r2-sha256",
        _R2_SHA,
        "--capture-report",
        "capture.json",
        "--capture-sha256",
        "3" * 64,
        "--completion-sha256",
        "4" * 64,
        "--report",
        "result.json",
    ]


def _receipt(tool: ModuleType) -> dict[str, object]:
    plan = tool.camera_bridge_capture_plan()
    return {
        "actions": [
            {
                "action_index": 0,
                "input_receipts": [
                    {
                        "complete": True,
                        "completed_events": 1,
                        "operation": "key_down",
                        "requested_events": 1,
                    },
                    {
                        "complete": True,
                        "completed_events": 1,
                        "operation": "key_up",
                        "requested_events": 1,
                    },
                ],
            }
        ],
        "plan": {
            "actions": [
                {"duration_s": 0.043, "key": "right", "kind": "key_hold"}
            ],
            "name": plan.name,
        },
        "preflight": {
            "client_height": 1078,
            "client_width": 1005,
            "focused": True,
            "supported": True,
        },
    }


def _capture_envelope(tool: ModuleType) -> dict[str, object]:
    return {
        "action_transition_emitted": False,
        "analysis_evidence": {
            "objective_id": tool.FROZEN_ENDPOINT_OBJECTIVE_ID,
            "planner_id": tool.CAMERA_BRIDGE_PLANNER_ID,
            "planner_version": tool.CAMERA_BRIDGE_PLANNER_VERSION,
            "r1_report_sha256": _R1_SHA,
            "report_sha256": _R2_SHA,
            "source_sha256": tool.FROZEN_ENDPOINT_SOURCE_SHA256,
        },
        "authenticated_ingestion_required": True,
        "authority": {
            "can_accept": False,
            "can_authorize_camera_input": False,
            "can_expose_resources": False,
            "can_validate_scene": False,
            "diagnostic_registration_can_override_production": False,
            "input_receipt_is_scene_acceptance": False,
            "production_remains_sole_scene_authority": True,
        },
        "bridge_capture": {
            "id": tool.CAMERA_BRIDGE_CAPTURE_ID,
            "version": tool.CAMERA_BRIDGE_CAPTURE_VERSION,
            "physical_capture_protocol_completed": True,
            "post_production_passed": False,
            "protocol_completed": True,
            "post_transition_closure_completed": True,
        },
        "bridge_objective": {
            "first_missing_primitive": {
                "duration_seconds": 0.043,
                "key": "right",
            },
            "id": tool.FROZEN_ENDPOINT_OBJECTIVE_ID,
        },
        "command": "bridge-capture-r2",
        "development_only": True,
        "exception": None,
        "fixed_policy": {
            "caller_selectable_axis": False,
            "caller_selectable_coordinate": False,
            "caller_selectable_direction": False,
            "caller_selectable_evaluator": False,
            "caller_selectable_magnitude": False,
            "caller_selectable_plan": False,
            "hold_seconds": 0.043,
            "key": "right",
            "maximum_physical_primitives": 1,
            "post_action_settle_seconds": 1.0,
        },
        "input": {
            "attempted": True,
            "completed": True,
            "delivery_duration_s": 0.6 - 0.5,
            "receipt_clock_s": 0.6,
            "start_clock_s": 0.5,
            "state": "complete",
        },
        "arm_age": {
            "age_s": 0.5,
            "final_clock_s": 0.5,
            "maximum_age_s": 1.0,
            "origin_clock_s": 0.0,
            "status": "within_limit",
        },
        "new_live_input_from_robust_registration": False,
        "plan": {
            "actions": [
                {"duration_s": 0.043, "key": "right", "kind": "key_hold"}
            ],
            "name": tool.camera_bridge_capture_plan().name,
        },
        "post_capture_registration_required": True,
        "preflight": {
            "client_height": 1078,
            "client_width": 1005,
            "focused": True,
            "supported": True,
        },
        "production_detector_remains_sole_scene_authority": True,
        "registration_execution": {
            "north_to_commit_executed_in_input_seam": True,
            "planner_source_to_north_precomputed_before_arm": True,
            "post_transition_registration_performed": True,
            "post_transition_registration_stage": (
                "same_transaction_before_production_re_evaluation_and_report_seal"
            ),
            "production_re_evaluated_after_registration": True,
        },
        "robust_registration_can_authorize_input_alone": False,
        "same_transaction_closure_completed": True,
        "terminal_reason": "capture_complete",
        "tracked_worktree_clean": True,
        "transition_candidate_eligible": False,
    }


def _timeline_frame(marker: int, frame_id: int, captured: float) -> Frame:
    return Frame.from_raw(
        RawFrame(bytes((marker, 0, 0, 255)), 1, 1, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=captured,
    )


def _provenance(tool: ModuleType) -> dict[str, object]:
    return {
        "detector_id": "profiled-resource:varrock-east-iron-v1",
        "detector_version": "2.1.0",
        "git_head_sha": _HEAD,
        "plan_id": tool.CAMERA_BRIDGE_CAPTURE_ID,
        "plan_version": tool.CAMERA_BRIDGE_CAPTURE_VERSION,
        "profile_id": "varrock-east-iron-v1",
        "tracked_worktree_clean": True,
    }


def test_parser_exposes_only_fixed_offline_evidence_inputs(tool: ModuleType) -> None:
    parsed = tool.parse_args(_arguments())
    assert parsed.expected_head == _HEAD
    assert parsed.capture_sha256 == "3" * 64
    assert parsed.completion_sha256 == "4" * 64
    with pytest.raises(SystemExit):
        tool.parse_args([*_arguments(), "--key", "left"])


def test_report_loader_authenticates_exact_bytes_and_sidecar(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    path = tmp_path / "report.json"
    digest = _write_json(path, {"schema_version": 2})
    resolved, payload = tool._load_report(path, digest)
    assert resolved == path.resolve()
    assert payload == {"schema_version": 2}


def test_report_loader_rejects_sidecar_mismatch(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    path = tmp_path / "report.json"
    digest = _write_json(path, {})
    path.with_name(f"{path.name}.sha256").write_text("0" * 64 + "\n")
    with pytest.raises(ValueError, match="sidecar"):
        tool._load_report(path, digest)


def test_report_loader_rejects_duplicate_keys(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    path = tmp_path / "report.json"
    data = b'{"a":1,"a":2}\n'
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(f"{path.name}.sha256").write_text(
        digest + "\n", newline="\n"
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        tool._load_report(path, digest)


def test_report_loader_rejects_nonfinite_json(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    path = tmp_path / "report.json"
    data = b'{"a":NaN}\n'
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(f"{path.name}.sha256").write_text(
        digest + "\n", newline="\n"
    )
    with pytest.raises(ValueError, match="non-standard JSON"):
        tool._load_report(path, digest)


def test_fixed_receipt_reconstructs_one_complete_right_hold(tool: ModuleType) -> None:
    receipt = tool._typed_receipt(_receipt(tool))
    assert receipt.plan is tool.camera_bridge_capture_plan()
    assert len(receipt.action_receipts) == 1
    assert [item.operation.value for item in receipt.action_receipts[0].input_receipts] == [
        "key_down",
        "key_up",
    ]


def test_partial_receipt_is_rejected_before_core_construction(tool: ModuleType) -> None:
    value = _receipt(tool)
    actions = value["actions"]
    assert isinstance(actions, list)
    action = actions[0]
    assert isinstance(action, dict)
    inputs = action["input_receipts"]
    assert isinstance(inputs, list)
    assert isinstance(inputs[0], dict)
    inputs[0]["completed_events"] = 0
    inputs[0]["complete"] = False
    with pytest.raises(ValueError, match="partial|fixed action"):
        tool._typed_receipt(value)


def test_capture_envelope_rejects_wrong_r2_binding(tool: ModuleType) -> None:
    evidence = _capture_envelope(tool)
    analysis = evidence["analysis_evidence"]
    assert isinstance(analysis, dict)
    analysis["report_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="exact R1/R2"):
        tool._validate_capture_envelope(
            evidence,
            expected_r1_sha256=_R1_SHA,
            expected_r2_sha256=_R2_SHA,
        )


def test_capture_envelope_accepts_only_complete_fixed_transaction(
    tool: ModuleType,
) -> None:
    assert tool._validate_capture_envelope(
        _capture_envelope(tool),
        expected_r1_sha256=_R1_SHA,
        expected_r2_sha256=_R2_SHA,
    ) == (0.5, 0.6, 0.0)


def test_capture_envelope_rejects_nonfrozen_arm_age_limit(tool: ModuleType) -> None:
    evidence = _capture_envelope(tool)
    arm_age = evidence["arm_age"]
    assert isinstance(arm_age, dict)
    arm_age["maximum_age_s"] = 2.0
    with pytest.raises(ValueError, match="arm age"):
        tool._validate_capture_envelope(
            evidence,
            expected_r1_sha256=_R1_SHA,
            expected_r2_sha256=_R2_SHA,
        )


def test_capture_envelope_rejects_sub_hold_delivery(tool: ModuleType) -> None:
    evidence = _capture_envelope(tool)
    input_evidence = evidence["input"]
    assert isinstance(input_evidence, dict)
    input_evidence["receipt_clock_s"] = 0.52
    input_evidence["delivery_duration_s"] = 0.52 - 0.5
    with pytest.raises(ValueError, match="input timing"):
        tool._validate_capture_envelope(
            evidence,
            expected_r1_sha256=_R1_SHA,
            expected_r2_sha256=_R2_SHA,
        )


def test_capture_chronology_authenticates_full_live_sequence(
    tool: ModuleType,
) -> None:
    tool._validate_capture_input_chronology(
        north=_timeline_frame(1, 1, 0.1),
        decision=_timeline_frame(2, 2, 0.1),
        arm=_timeline_frame(3, 3, 0.2),
        arm_origin=0.3,
        commit=_timeline_frame(4, 4, 0.4),
        input_start=0.5,
        input_receipt=0.6,
        post=_timeline_frame(5, 5, 1.6),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("north", 0.11),
        ("decision", 0.2),
        ("arm_origin", 0.19),
        ("arm_origin", 0.41),
        ("commit", 0.51),
        ("input_start", 0.61),
    ],
)
def test_capture_chronology_rejects_every_out_of_order_seam(
    tool: ModuleType,
    field: str,
    value: float,
) -> None:
    times = {
        "north": 0.0,
        "decision": 0.1,
        "arm": 0.2,
        "arm_origin": 0.3,
        "commit": 0.4,
        "input_start": 0.5,
        "input_receipt": 0.6,
        "post": 1.6,
    }
    times[field] = value
    with pytest.raises(ValueError, match="north <= decision < arm"):
        tool._validate_capture_input_chronology(
            north=_timeline_frame(1, 1, times["north"]),
            decision=_timeline_frame(2, 2, times["decision"]),
            arm=_timeline_frame(3, 3, times["arm"]),
            arm_origin=times["arm_origin"],
            commit=_timeline_frame(4, 4, times["commit"]),
            input_start=times["input_start"],
            input_receipt=times["input_receipt"],
            post=_timeline_frame(5, 5, times["post"]),
        )


def test_capture_chronology_rejects_north_at_exclusive_age_limit(
    tool: ModuleType,
) -> None:
    with pytest.raises(ValueError, match="north handoff.*exclusive age"):
        tool._validate_capture_input_chronology(
            north=_timeline_frame(1, 1, 0.0),
            decision=_timeline_frame(2, 2, 29.1),
            arm=_timeline_frame(3, 3, 29.2),
            arm_origin=29.3,
            commit=_timeline_frame(4, 4, 29.4),
            input_start=30.0,
            input_receipt=30.1,
            post=_timeline_frame(5, 5, 31.1),
        )


def test_capture_chronology_rejects_post_before_fixed_settle(
    tool: ModuleType,
) -> None:
    with pytest.raises(ValueError, match="fixed settle interval"):
        tool._validate_capture_input_chronology(
            north=_timeline_frame(1, 1, 0.0),
            decision=_timeline_frame(2, 2, 0.1),
            arm=_timeline_frame(3, 3, 0.2),
            arm_origin=0.3,
            commit=_timeline_frame(4, 4, 0.4),
            input_start=0.5,
            input_receipt=0.6,
            post=_timeline_frame(5, 5, 1.599),
        )


def test_capture_pointer_mapping_requires_exact_round_trip_and_owner(
    tool: ModuleType,
) -> None:
    value = {
        "adapter_identity": tool._EXPECTED_WINDOWS_CAMERA_ADAPTER,
        "evidence": {
            "exact_round_trip": True,
            "logical_client": list(tool.REVIEWED_CAMERA_WHEEL_POINT),
            "physical_screen": [600, 100],
            "reverse_logical_client": list(tool.REVIEWED_CAMERA_WHEEL_POINT),
            "root_hwnd_matches_target": True,
        },
        "numeric_mapping_captured": True,
        "pointer_primitive_required": False,
        "reviewed_logical_point": list(tool.REVIEWED_CAMERA_WHEEL_POINT),
        "selected_window_class_name": "SunAwtFrame",
        "selected_window_title_sha256": "a" * 64,
    }
    tool._validate_capture_pointer_mapping(value)
    evidence = value["evidence"]
    assert isinstance(evidence, dict)
    evidence["root_hwnd_matches_target"] = False
    with pytest.raises(ValueError, match="pointer mapping/ownership"):
        tool._validate_capture_pointer_mapping(value)


def test_post_ingestion_reauthenticates_fixed_one_shot_sentinel(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = Frame.from_raw(
        RawFrame(b"\x01\x02\x03\xff", 1, 1, PixelFormat.BGRA8888),
        frame_id=4,
        captured_monotonic_s=4.0,
    )
    north = tool._AuthenticatedNorthHandoff(
        report_path=tmp_path / "north.camera.json",
        report_sha256="3" * 64,
        raw_path=tmp_path / "north.raw",
        frame=frame,
        window_hwnd=123,
        window_process_id=456,
        window_thread_id=789,
        window_class_name="SunAwtFrame",
        window_title_sha256="4" * 64,
    )
    dynamic = CameraBridgeAuthorizationEvidence(
        r1_report_sha256=_R1_SHA,
        r2_report_sha256=_R2_SHA,
        north_report_sha256="3" * 64,
        north_post_sha256=hashlib.sha256(frame.payload).hexdigest(),
        commit_sha256="5" * 64,
        target_hwnd=123,
        target_process_id=456,
        target_thread_id=789,
        target_class_name="SunAwtFrame",
        target_title_sha256="4" * 64,
    )
    reservation = CameraBridgeAuthorizationReservation(
        git_head_sha=_HEAD,
        host_authority_root=tmp_path,
        sentinel_path=tmp_path / "fixed.consumed.json",
        sentinel_sha256="6" * 64,
        evidence=dynamic,
    )
    calls: list[dict[str, object]] = []

    def authenticate(_root: Path, **kwargs: object) -> object:
        calls.append(kwargs)
        assert kwargs == {
            "git_head_sha": _HEAD,
            "expected_sentinel_sha256": "6" * 64,
            "evidence": dynamic,
        }
        return reservation

    monkeypatch.setattr(tool, "authenticate_camera_bridge_authorization", authenticate)
    report_value = reservation.as_dict()
    assert report_value == {
        "schema_version": 2,
        "authorization_id": "issue31-r2-one-shot-bridge-authorization",
        "authorization_version": "2.2.1",
        "campaign_id": "issue31-r2-north-up-p610-y043-reset-right-0043-v1",
        "repository_id": "maadbonnie21-lgtm/Mining-Automation",
        "authority_scope": "persistent_per_user_host_global",
        "authority_provider_id": "windows-known-folder-localappdata-v1",
        "state": "consumed_at_final_pre_input_seam",
        "authorization_authority": "source_literal_gate_only",
        "source_gate_enabled_at_consumption": True,
        "git_head_sha": _HEAD,
        "objective_id": "north-up-p610-y043-reset:right-key-hold-0.043s",
        "required_source_sha256": (
            "c1cb6fe144600ce153b1ceb2e90d6e375d42babea1eda6a08120efbc7ed2a4cd"
        ),
        "action_id": "issue31-fixed-camera-bridge-capture-r2",
        "action_family": "north-up-p610-y043-reset",
        "key": "right",
        "hold_seconds": 0.043,
        "maximum_physical_primitives": 1,
        "target_policy": {
            "camera_adapter": (
                "mining_automation.validation.windows_camera.WindowsCameraControl"
            ),
            "client_height": 1078,
            "client_width": 1005,
            "input_lease": (
                "mining_automation.validation.camera_input_lease."
                "WindowsCameraInputLease"
            ),
            "reviewed_pointer_logical_client": [400, 50],
            "title_substring": "runelite",
        },
        "authenticated_evidence_not_authority": dynamic.as_dict(),
        "owner": "Mining-Automation Issue #31 R2 bridge launcher",
        "sentinel_relative_to_host_authority_root": "fixed.consumed.json",
        "sentinel_sha256": "6" * 64,
        "source_owned_namespace": True,
        "persistent_per_user_host_global_authority": True,
        "independent_repository_clone_can_bypass": False,
        "caller_can_select_campaign": False,
        "caller_can_select_action_or_target": False,
        "alternate_output_or_case_prefix_can_bypass": False,
        "second_invocation_can_send_input": False,
    }
    assert "sentinel_relative_to_common_git_dir" not in report_value
    tool._authenticate_capture_one_shot_authorization(
        report_value,
        expected_head=_HEAD,
        expected_r1_sha256=_R1_SHA,
        expected_r2_sha256=_R2_SHA,
        north=north,
        commit_sha256="5" * 64,
    )
    assert len(calls) == 1

    forged = dict(report_value)
    forged["state"] = "available"
    with pytest.raises(ValueError, match="does not bind the fixed sentinel"):
        tool._authenticate_capture_one_shot_authorization(
            forged,
            expected_head=_HEAD,
            expected_r1_sha256=_R1_SHA,
            expected_r2_sha256=_R2_SHA,
            north=north,
            commit_sha256="5" * 64,
        )

    tampered_bindings = (
        ("sentinel_relative_to_host_authority_root", "other.consumed.json"),
        ("authority_provider_id", "repository-common-git-v1"),
        ("repository_id", "attacker/other-repository"),
    )
    for field_name, field_value in tampered_bindings:
        forged = dict(report_value)
        forged[field_name] = field_value
        with pytest.raises(ValueError, match="does not bind the fixed sentinel"):
            tool._authenticate_capture_one_shot_authorization(
                forged,
                expected_head=_HEAD,
                expected_r1_sha256=_R1_SHA,
                expected_r2_sha256=_R2_SHA,
                north=north,
                commit_sha256="5" * 64,
            )

    retained_common_git_authority = dict(report_value)
    retained_common_git_authority["sentinel_relative_to_common_git_dir"] = (
        "mining-automation-authorizations/issue31-camera-bridge/fixed.consumed.json"
    )
    with pytest.raises(ValueError, match="does not bind the fixed sentinel"):
        tool._authenticate_capture_one_shot_authorization(
            retained_common_git_authority,
            expected_head=_HEAD,
            expected_r1_sha256=_R1_SHA,
            expected_r2_sha256=_R2_SHA,
            north=north,
            commit_sha256="5" * 64,
        )


def test_post_ingestion_requires_exact_completion_seal(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization = CameraBridgeAuthorizationReservation(
        git_head_sha=_HEAD,
        host_authority_root=tmp_path,
        sentinel_path=tmp_path / "fixed.consumed.json",
        sentinel_sha256="6" * 64,
        evidence=CameraBridgeAuthorizationEvidence(
            r1_report_sha256=_R1_SHA,
            r2_report_sha256=_R2_SHA,
            north_report_sha256="3" * 64,
            north_post_sha256="4" * 64,
            commit_sha256="5" * 64,
            target_hwnd=123,
            target_process_id=456,
            target_thread_id=789,
            target_class_name="SunAwtFrame",
            target_title_sha256="7" * 64,
        ),
    )
    evidence: dict[str, object] = {
        "receipt": _receipt(tool),
        "frames": {stage: {"stage": stage} for stage in tool._STAGES},
        "arm_age": {"age_s": 0.5},
        "guards": {"all": "exact"},
        "input": {"attempted": True, "completed": True},
        "preflight": {"supported": True},
        "pointer_mapping": {"root_hwnd_matches_target": True},
        "compass_north_handoff": {"report_sha256": "3" * 64},
        "compass_north_registration": {"accepted": True},
        "planner_source_registration": {"accepted": True},
        "post_transition_registration": {"accepted": True},
        "post_transition_closure": {"completed": True},
    }
    observed: list[object] = []

    def authenticate(_root: Path, **kwargs: object) -> object:
        observed.append(kwargs["evidence"])
        assert kwargs["git_head_sha"] == _HEAD
        assert kwargs["expected_seal_sha256"] == "8" * 64
        return object()

    monkeypatch.setattr(tool, "authenticate_camera_bridge_completion", authenticate)
    tool._authenticate_capture_completion_seal(
        evidence,
        expected_head=_HEAD,
        expected_seal_sha256="8" * 64,
        capture_report_sha256="9" * 64,
        authorization=authorization,
        commit_sha256="a" * 64,
        post_sha256="b" * 64,
    )
    first = observed[-1]
    evidence["receipt"] = {"forged": True}
    tool._authenticate_capture_completion_seal(
        evidence,
        expected_head=_HEAD,
        expected_seal_sha256="8" * 64,
        capture_report_sha256="9" * 64,
        authorization=authorization,
        commit_sha256="a" * 64,
        post_sha256="b" * 64,
    )
    second = observed[-1]

    assert first != second
    assert first.authorization_sentinel_sha256 == "6" * 64
    assert first.capture_report_sha256 == "9" * 64


def test_missing_completion_seal_rejects_offline_ingestion(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization = CameraBridgeAuthorizationReservation(
        git_head_sha=_HEAD,
        host_authority_root=tmp_path,
        sentinel_path=tmp_path / "fixed.consumed.json",
        sentinel_sha256="6" * 64,
        evidence=CameraBridgeAuthorizationEvidence(
            r1_report_sha256=_R1_SHA,
            r2_report_sha256=_R2_SHA,
            north_report_sha256="3" * 64,
            north_post_sha256="4" * 64,
            commit_sha256="5" * 64,
            target_hwnd=123,
            target_process_id=456,
            target_thread_id=789,
            target_class_name="SunAwtFrame",
            target_title_sha256="7" * 64,
        ),
    )
    evidence = {
        "receipt": {},
        "frames": {},
        "pointer_mapping": {},
        "post_transition_closure": {},
    }
    monkeypatch.setattr(
        tool,
        "authenticate_camera_bridge_completion",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("cannot read source-owned bridge completion seal")
        ),
    )

    with pytest.raises(ValueError, match="completion seal"):
        tool._authenticate_capture_completion_seal(
            evidence,
            expected_head=_HEAD,
            expected_seal_sha256="8" * 64,
            capture_report_sha256="9" * 64,
            authorization=authorization,
            commit_sha256="a" * 64,
            post_sha256="b" * 64,
        )


def test_post_ingestion_rejects_missing_one_shot_authorization(
    tool: ModuleType,
    tmp_path: Path,
) -> None:
    frame = Frame.from_raw(
        RawFrame(b"\x01\x02\x03\xff", 1, 1, PixelFormat.BGRA8888),
        frame_id=4,
        captured_monotonic_s=4.0,
    )
    north = tool._AuthenticatedNorthHandoff(
        report_path=tmp_path / "north.camera.json",
        report_sha256="3" * 64,
        raw_path=tmp_path / "north.raw",
        frame=frame,
        window_hwnd=123,
        window_process_id=456,
        window_thread_id=789,
        window_class_name="SunAwtFrame",
        window_title_sha256="4" * 64,
    )
    with pytest.raises(ValueError, match="one-shot authorization"):
        tool._authenticate_capture_one_shot_authorization(
            None,
            expected_head=_HEAD,
            expected_r1_sha256=_R1_SHA,
            expected_r2_sha256=_R2_SHA,
            north=north,
            commit_sha256="5" * 64,
        )


def test_provenance_command_requires_exact_single_subcommand(tool: ModuleType) -> None:
    provenance = {
        "command_argv": [
            "python",
            str(tool._REPO_ROOT / "tools" / "validate_varrock_east_camera.py"),
            "bridge-capture-r2",
            "--case-prefix",
            "case",
        ]
    }
    assert tool._command_options(provenance, "bridge-capture-r2") == {
        "--case-prefix": "case"
    }
    provenance["command_argv"].append("bridge-capture-r2")
    with pytest.raises(ValueError, match="exact subcommand"):
        tool._command_options(provenance, "bridge-capture-r2")


def test_capture_command_binds_analysis_north_output_and_case(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    output = tmp_path / "diagnostics" / "bridge"
    capture_report = output / "reports" / "case.camera.json"
    r2_report = tmp_path / "diagnostics" / "r2.json"
    north_report = tmp_path / "diagnostics" / "north" / "reports" / "north.camera.json"
    north = SimpleNamespace(
        report_path=north_report.resolve(),
        report_sha256="b" * 64,
    )
    provenance = {
        "command_argv": [
            "python",
            str(tmp_path / "tools" / "validate_varrock_east_camera.py"),
            "bridge-capture-r2",
            "--expected-head",
            _HEAD,
            "--analysis-report",
            str(r2_report),
            "--analysis-sha256",
            _R2_SHA,
            "--north-report",
            str(north_report),
            "--north-sha256",
            "b" * 64,
            "--output",
            str(output),
            "--case-prefix",
            "case",
        ]
    }
    tool._validate_capture_command_argv(
        provenance,
        expected_head=_HEAD,
        expected_r2_sha256=_R2_SHA,
        expected_r2_report_path=r2_report,
        expected_north=north,
        capture_report_path=capture_report,
    )
    provenance["command_argv"][8] = "c" * 64
    with pytest.raises(ValueError, match="exact live inputs"):
        tool._validate_capture_command_argv(
            provenance,
            expected_head=_HEAD,
            expected_r2_sha256=_R2_SHA,
            expected_r2_report_path=r2_report,
            expected_north=north,
            capture_report_path=capture_report,
        )


def test_pre_input_registration_is_recomputed_from_exact_endpoint_pixels(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Frame.from_raw(
        RawFrame(b"\x01\x00\x00\xff", 1, 1, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=1.0,
    )
    target = Frame.from_raw(
        RawFrame(b"\x02\x00\x00\xff", 1, 1, PixelFormat.BGRA8888),
        frame_id=2,
        captured_monotonic_s=2.0,
    )
    reported = {
        "accepted": True,
        "authority": {
            "can_accept": False,
            "can_expose_resources": False,
            "can_validate_scene": False,
            "diagnostic_registration_can_override_production": False,
        },
        "required_zones": ["north_west", "north_east", "south_west"],
        "source": {"payload_sha256": hashlib.sha256(source.payload).hexdigest()},
        "target": {"payload_sha256": hashlib.sha256(target.payload).hexdigest()},
    }
    analyzed: list[tuple[Frame, Frame]] = []

    def analyze(first: Frame, second: Frame) -> SimpleNamespace:
        analyzed.append((first, second))
        return SimpleNamespace(as_dict=lambda: reported)

    monkeypatch.setattr(
        tool,
        "RobustRegistrationEngine",
        lambda: SimpleNamespace(analyze=analyze),
    )
    tool._require_exact_registration(
        source,
        target,
        reported,
        context="test chain",
    )
    assert analyzed == [(source, target)]
    reported["target"] = {"payload_sha256": "f" * 64}
    with pytest.raises(ValueError, match="test chain registration is not exact"):
        tool._require_exact_registration(
            source,
            target,
            reported,
            context="test chain",
        )


def test_raw_reference_must_remain_below_capture_report_grandparent(
    tool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    report = (
        tmp_path / "diagnostics" / "case" / "reports" / "capture.camera.json"
    )
    report.parent.mkdir(parents=True)
    escaped = tmp_path / "diagnostics" / "escape.raw"
    escaped.parent.mkdir(parents=True, exist_ok=True)
    escaped.write_bytes(b"x")
    evidence = {
        "artifact": {
            "files": {"raw": "../escape.raw"},
            "frame_id": 1,
            "height": 1078,
            "label": "r2-commit",
            "pixel_format": "bgra8888",
            "raw_sha256": hashlib.sha256(b"x").hexdigest(),
            "width": 1005,
        },
        "captured_monotonic_s": 1.0,
        "production": {},
        "readiness": {},
    }
    with pytest.raises(ValueError, match="fixed artifact|escaped|missing"):
        tool._load_frame_evidence(report, "commit", evidence)


def test_complete_closure_binds_honest_commit_and_post_hashes(tool: ModuleType) -> None:
    commit = "4" * 64
    post = "5" * 64
    closure = {
        "action_transition_emitted": False,
        "artifact_exception": None,
        "authenticated_ingestion_required": True,
        "binding": {
            "action_id": tool.CAMERA_BRIDGE_CAPTURE_ID,
            "action_version": tool.CAMERA_BRIDGE_CAPTURE_VERSION,
            "objective_id": tool.FROZEN_ENDPOINT_OBJECTIVE_ID,
            "objective_source_sha256": tool.FROZEN_ENDPOINT_SOURCE_SHA256,
            "plan_name": tool.camera_bridge_capture_plan().name,
        },
        "commit_sha256": commit,
        "completed": True,
        "post_sha256": post,
        "production_exception": None,
        "production_matches_capture": True,
        "production_re_evaluated": True,
        "registration_accepted": True,
        "registration_attempted": True,
        "registration_exception": None,
        "seal_exception": None,
        "semantic_states": {
            "ACTION_BRIDGE_RECEIPT_PROVEN": True,
            "BRIDGE_REJECTED": False,
            "PRODUCTION_SUPPORTED_ENDPOINT": False,
            "REGISTRATION_BRIDGE_OBSERVED": True,
        },
        "status": "complete",
        "transition_candidate_eligible": False,
    }
    tool._validate_closure(closure, commit_sha=commit, post_sha=post)
    closure["commit_sha256"] = tool.FROZEN_ENDPOINT_SOURCE_SHA256
    with pytest.raises(ValueError, match="closure"):
        tool._validate_closure(closure, commit_sha=commit, post_sha=post)


def test_report_evidence_is_input_inert_and_deterministic(tool: ModuleType) -> None:
    verification = SimpleNamespace(
        capture_report_sha256="6" * 64,
        verified=False,
        as_dict=lambda: {"verified": False, "failure_reasons": ["missing"]},
    )
    frozen = SimpleNamespace(r1_report_sha256=_R1_SHA, r2_report_sha256=_R2_SHA)
    first = tool._report_evidence(
        verification,
        frozen=frozen,
        before_head=_HEAD,
        after_head=_HEAD,
        completion_seal_sha256="4" * 64,
    )
    second = tool._report_evidence(
        verification,
        frozen=frozen,
        before_head=_HEAD,
        after_head=_HEAD,
        completion_seal_sha256="4" * 64,
    )
    assert first == second
    assert all(value is False for value in first["authority"].values())
    assert first["result"] == {
        "conclusion": "insufficient graph evidence; stop after one sample",
        "second_live_action_authorized": False,
        "stop_after_single_sample": True,
        "verified": False,
    }


def test_publication_state_query_failure_retracts_report_pair(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    frozen = SimpleNamespace(
        base_specs=(),
        corpus=SimpleNamespace(
            anchors=(SimpleNamespace(sha256="6" * 64),),
            resets=(
                SimpleNamespace(named_frame=SimpleNamespace(sha256="4" * 64)),
                SimpleNamespace(named_frame=SimpleNamespace(sha256="5" * 64)),
            ),
            r1=SimpleNamespace(reviewed_manifest_sha256="e" * 64),
        ),
        r1_report_sha256=_R1_SHA,
        r2_report_sha256=_R2_SHA,
        r2_report_path=tmp_path / "r2.json",
    )
    frozen.corpus.north = SimpleNamespace(
        named_frame=SimpleNamespace(
            frame=Frame.from_raw(
                RawFrame(b"\x00\x00\x00\xff", 1, 1, PixelFormat.BGRA8888),
                frame_id=1,
                captured_monotonic_s=1.0,
            ),
            path="source.raw",
        )
    )
    verification = SimpleNamespace(
        capture_report_sha256="3" * 64,
        verified=False,
        as_dict=lambda: {"failure_reasons": ["missing"], "verified": False},
    )
    monkeypatch.setattr(tool, "_load_frozen_inputs", lambda **_kwargs: frozen)
    monkeypatch.setattr(
        tool,
        "_load_authenticated_capture",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        tool,
        "verify_camera_bridge_post",
        lambda *_args, **_kwargs: verification,
    )
    git_states: list[object] = [
        (_HEAD, True),
        (_HEAD, True),
        subprocess.CalledProcessError(1, ("git", "status")),
    ]

    def git_state(_repo_root: Path) -> tuple[str, bool]:
        state = git_states.pop(0)
        if isinstance(state, BaseException):
            raise state
        assert isinstance(state, tuple)
        return state

    monkeypatch.setattr(tool, "_git_state", git_state)
    arguments = _arguments()
    arguments[-1] = "diagnostics/result.json"

    assert tool.main(arguments) == 2
    report = tmp_path / "diagnostics" / "result.json"
    assert not report.exists()
    assert not report.with_name(f"{report.name}.sha256").exists()
    assert git_states == []


def test_tool_source_has_no_windows_or_input_control_imports() -> None:
    source = _TOOL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert all("windows_camera" not in module for module in imported_modules)
    assert "WindowsCaptureBackend" not in source
    assert "run_fixed_camera_bridge_capture" not in source


def test_sealed_post_uses_nested_evaluator_schema(tool: ModuleType) -> None:
    landmark = SimpleNamespace(
        landmark_id="landmark",
        distance=1.0,
        threshold=2.0,
        matched=True,
        zone=SimpleNamespace(value="north_west"),
    )
    resource = SimpleNamespace(
        resource_id="rock-1",
        state=SimpleNamespace(value="uncertain"),
        confidence=0.0,
        definitive=False,
    )
    evaluation = SimpleNamespace(
        detector_id="detector",
        detector_version="1",
        profile_id="profile",
        profile_schema_version=3,
        profile_frame_width=1005,
        profile_frame_height=1078,
        profile_pixel_format=SimpleNamespace(value="bgra8888"),
        frame_geometry_supported=True,
        scene_validated=False,
        scene_reason="unsupported",
        matched_landmark_count=1,
        required_landmark_count=6,
        required_landmark_matches=5,
        matched_zones=(SimpleNamespace(value="north_west"),),
        required_matched_zones=3,
        landmarks=(landmark,),
        resource_states=(resource,),
        definitive_target_ids=(),
        passed=False,
    )
    nested = tool._evaluation_dict(evaluation)
    assert nested["scene"]["landmarks"][0]["landmark_id"] == "landmark"
    assert nested["resources"][0]["definitive"] is False
    assert "resource_states" not in nested


def test_mismatched_recomputed_registration_is_rejected(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = Frame.from_raw(
        RawFrame(b"\x01\x02\x03\xff", 1, 1, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=1.0,
    )
    engine = SimpleNamespace(
        analyze=lambda _source, _target: SimpleNamespace(
            as_dict=lambda: {"accepted": False}
        )
    )
    monkeypatch.setattr(tool, "RobustRegistrationEngine", lambda: engine)
    with pytest.raises(ValueError, match="registration does not match"):
        tool._require_recomputed_registration(frame, frame, {"accepted": True})


def test_dummy_frame_helper_remains_exactly_pixel_bound() -> None:
    frame = Frame.from_raw(
        RawFrame(b"\x01\x02\x03\xff", 1, 1, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=1.0,
    )
    assert hashlib.sha256(frame.payload).hexdigest() == hashlib.sha256(
        b"\x01\x02\x03\xff"
    ).hexdigest()
