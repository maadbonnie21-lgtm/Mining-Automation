from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.validation.camera_bootstrap import (
    CameraNorthBootstrapInputState,
)
from mining_automation.validation.camera_bridge_capture import (
    CameraBridgeCaptureInputState,
)
from mining_automation.validation.camera_plan import (
    EXPECTED_CLIENT_HEIGHT,
    EXPECTED_CLIENT_WIDTH,
    REVIEWED_COMPASS_POINT,
    CameraActionReceipt,
    CameraInputOperation,
    CameraInputReceipt,
    CameraPlan,
    CameraPlanReceipt,
    CameraPreflightReceipt,
    CompassClick,
)


def _load_tool() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "validate_varrock_east_camera.py"
    spec = importlib.util.spec_from_file_location(
        "validate_varrock_east_camera_r2_campaign",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> ModuleType:
    return _load_tool()


def _frame(payload: bytes = bytes((1, 2, 3, 255))) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=1,
            height=1,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=7,
        captured_monotonic_s=12.5,
    )


def _registration(payload_sha256: str, *, marker: str = "reviewed") -> Any:
    payload = {
        "accepted": True,
        "marker": marker,
        "target": {"payload_sha256": payload_sha256},
    }
    return SimpleNamespace(
        target=SimpleNamespace(payload_sha256=payload_sha256),
        as_dict=lambda: payload,
    )


def _precursor(
    tool: ModuleType,
    *,
    mode: str = "zero_click",
    bootstrap: object | None = None,
    frame: Frame | None = None,
    frame_sha256: str | None = None,
    registration_sha256: str | None = None,
    window_class_name: str = "SunAwtFrame",
    window_title_sha256: str = "a" * 64,
) -> object:
    bound_frame = _frame() if frame is None else frame
    digest = hashlib.sha256(bound_frame.payload).hexdigest()
    north_qualification = (
        None
        if mode == "compass_click"
        else SimpleNamespace(
            as_dict=lambda: {
                "accepted": True,
                "exact_frozen_pixel_identity": True,
            }
        )
    )
    return tool._BridgeCampaignPrecursor(
        mode=mode,
        frame=bound_frame,
        frame_evidence=SimpleNamespace(
            artifact=SimpleNamespace(raw_sha256=frame_sha256 or digest),
        ),
        registration=_registration(registration_sha256 or digest),
        north_qualification=north_qualification,
        bootstrap=bootstrap,
        window_hwnd=123,
        window_process_id=456,
        window_thread_id=789,
        window_class_name=window_class_name,
        window_title_sha256=window_title_sha256,
    )


def _preflight() -> CameraPreflightReceipt:
    return CameraPreflightReceipt(
        focused=True,
        client_width=EXPECTED_CLIENT_WIDTH,
        client_height=EXPECTED_CLIENT_HEIGHT,
    )


def _right_receipt(tool: ModuleType) -> CameraPlanReceipt:
    plan = tool.camera_bridge_capture_plan()
    action = plan.actions[0]
    action_receipt = CameraActionReceipt(
        action_index=0,
        action=action,
        input_receipts=(
            CameraInputReceipt(CameraInputOperation.KEY_DOWN, 1, 1),
            CameraInputReceipt(CameraInputOperation.KEY_UP, 1, 1),
        ),
    )
    return CameraPlanReceipt(plan, _preflight(), (action_receipt,))


def _compass_receipt() -> tuple[CameraPlan, CameraPlanReceipt]:
    plan = CameraPlan(
        "issue31-v2-01-heading-north",
        (CompassClick(*REVIEWED_COMPASS_POINT),),
    )
    action_receipt = CameraActionReceipt(
        action_index=0,
        action=plan.actions[0],
        input_receipts=(
            CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 2),
        ),
    )
    return plan, CameraPlanReceipt(plan, _preflight(), (action_receipt,))


def _bridge_result(tool: ModuleType, *, input_attempted: bool = True) -> object:
    return SimpleNamespace(
        input_attempted=input_attempted,
        input_state=(
            CameraBridgeCaptureInputState.COMPLETE
            if input_attempted
            else CameraBridgeCaptureInputState.NONE
        ),
        receipt=_right_receipt(tool) if input_attempted else None,
        commit=(
            SimpleNamespace(artifact=SimpleNamespace(raw_sha256="c" * 64))
            if input_attempted
            else None
        ),
        post=(
            SimpleNamespace(artifact=SimpleNamespace(raw_sha256="d" * 64))
            if input_attempted
            else None
        ),
        input_start_clock_s=30.0 if input_attempted else None,
        input_receipt_clock_s=30.043 if input_attempted else None,
    )


def _compass_bootstrap(*, commit: object = True, post: object = True) -> object:
    _plan, receipt = _compass_receipt()
    return SimpleNamespace(
        commit=(
            SimpleNamespace(artifact=SimpleNamespace(raw_sha256="b" * 64))
            if commit is True
            else commit
        ),
        post=(
            SimpleNamespace(artifact=SimpleNamespace(raw_sha256="e" * 64))
            if post is True
            else post
        ),
        receipt=receipt,
        input_state=CameraNorthBootstrapInputState.COMPLETE,
        input_start_clock_s=20.0,
        input_receipt_clock_s=20.01,
    )


def _reservation(
    *,
    precursor_mode: str = "zero_click",
    precursor_commit_sha256: str | None = None,
) -> object:
    return SimpleNamespace(
        sentinel_sha256="9" * 64,
        evidence=SimpleNamespace(
            precursor_mode=precursor_mode,
            precursor_commit_sha256=(
                hashlib.sha256(_frame().payload).hexdigest()
                if precursor_commit_sha256 is None
                else precursor_commit_sha256
            ),
        ),
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"mode": "diagonal"}, "mode is invalid"),
        ({"mode": "compass_click", "bootstrap": None}, "bootstrap evidence disagree"),
        ({"mode": "zero_click", "bootstrap": object()}, "bootstrap evidence disagree"),
        ({"frame_sha256": "0" * 64}, "exact frame/window"),
        ({"registration_sha256": "0" * 64}, "exact frame/window"),
        ({"window_class_name": ""}, "exact frame/window"),
        ({"window_title_sha256": ""}, "exact frame/window"),
    ],
)
def test_campaign_precursor_rejects_mode_and_binding_mismatches(
    tool: ModuleType,
    changes: dict[str, object],
    message: str,
) -> None:
    kwargs: dict[str, object] = {"mode": "zero_click", "bootstrap": None}
    kwargs.update(changes)

    with pytest.raises(ValueError, match=message):
        _precursor(tool, **kwargs)


def test_campaign_precursor_is_immutable(tool: ModuleType) -> None:
    precursor = _precursor(tool)

    with pytest.raises(FrozenInstanceError):
        precursor.mode = "compass_click"


def test_zero_click_receipt_is_an_authenticated_observation_stage(
    tool: ModuleType,
) -> None:
    precursor = _precursor(tool)
    reservation = _reservation()

    receipt = tool._ordered_campaign_receipt(
        precursor,
        _bridge_result(tool),
        reservation=reservation,
        reservation_completed_clock_s=18.25,
    )

    assert receipt["campaign_id"] == tool.CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID
    assert receipt["reservation_id"] == reservation.sentinel_sha256
    assert receipt["reservation_completed_clock_s"] == 18.25
    assert receipt["maximum_physical_primitives"] == 2
    assert receipt["actual_physical_primitives"] == 1
    assert receipt["allowed_order"] == [
        {
            "ordinal": 0,
            "stage": "north_precursor",
            "kind": "compass_click",
            "logical_client_point": list(REVIEWED_COMPASS_POINT),
            "zero_click_requires_exact_frozen_north_pixels": True,
        },
        {
            "ordinal": 1,
            "stage": "bridge",
            "kind": "key_hold",
            "key": "right",
            "hold_seconds": tool.CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS,
        },
    ]
    precursor_stage, bridge_stage = receipt["stages"]
    frame_sha256 = hashlib.sha256(precursor.frame.payload).hexdigest()
    assert precursor_stage == {
        "ordinal": 0,
        "stage": "north_precursor",
        "mode": "zero_click",
        "commit_sha256": frame_sha256,
        "post_sha256": frame_sha256,
        "input_state": "none",
        "receipt": {
            "kind": "zero_click_observation",
            "physical_input_attempted": False,
            "physical_input_completed": False,
            "frame_sha256": frame_sha256,
                "source_registration_sha256": (
                    tool.canonical_camera_bridge_component_sha256(
                        precursor.registration.as_dict()
                    )
                ),
                "north_qualification_sha256": (
                    tool.canonical_camera_bridge_component_sha256(
                        precursor.north_qualification.as_dict()
                    )
                ),
            },
        "start_clock_s": None,
        "receipt_clock_s": None,
    }
    assert bridge_stage["ordinal"] == 1
    assert bridge_stage["stage"] == "bridge"
    assert bridge_stage["mode"] == "fixed_right_hold"
    bridge_action = bridge_stage["receipt"]["plan"]["actions"][0]
    assert bridge_action["kind"] == "key_hold"
    assert bridge_action["key"] == "right"
    assert bridge_action["duration_s"] == tool.CAMERA_BRIDGE_CAPTURE_HOLD_SECONDS


def test_compass_then_right_receipt_records_exact_two_stage_order(
    tool: ModuleType,
) -> None:
    bootstrap = _compass_bootstrap()
    precursor = _precursor(tool, mode="compass_click", bootstrap=bootstrap)

    receipt = tool._ordered_campaign_receipt(
        precursor,
        _bridge_result(tool),
        reservation=_reservation(
            precursor_mode="compass_click",
            precursor_commit_sha256="b" * 64,
        ),
        reservation_completed_clock_s=19.0,
    )

    assert receipt["maximum_physical_primitives"] == 2
    assert receipt["actual_physical_primitives"] == 2
    precursor_stage, bridge_stage = receipt["stages"]
    assert [precursor_stage["ordinal"], bridge_stage["ordinal"]] == [0, 1]
    assert [precursor_stage["stage"], bridge_stage["stage"]] == [
        "north_precursor",
        "bridge",
    ]
    assert precursor_stage["mode"] == "compass_click"
    assert precursor_stage["commit_sha256"] == "b" * 64
    assert precursor_stage["post_sha256"] == "e" * 64
    assert precursor_stage["receipt"]["plan"]["actions"] == [
        {
            "kind": "compass_click",
            "x": REVIEWED_COMPASS_POINT[0],
            "y": REVIEWED_COMPASS_POINT[1],
        }
    ]
    assert precursor_stage["start_clock_s"] == 20.0
    assert precursor_stage["receipt_clock_s"] == 20.01
    assert bridge_stage["mode"] == "fixed_right_hold"


@pytest.mark.parametrize(("missing",), [("commit",), ("post",)])
def test_ordered_receipt_rejects_missing_compass_stage_frame(
    tool: ModuleType,
    missing: str,
) -> None:
    bootstrap = _compass_bootstrap(
        commit=None if missing == "commit" else True,
        post=None if missing == "post" else True,
    )
    precursor = _precursor(tool, mode="compass_click", bootstrap=bootstrap)

    with pytest.raises(RuntimeError, match="lacks exact commit/post evidence"):
        tool._ordered_campaign_receipt(
            precursor,
            _bridge_result(tool),
            reservation=_reservation(),
            reservation_completed_clock_s=19.0,
        )


def test_receipt_constructors_reject_missing_swapped_and_substituted_evidence(
    tool: ModuleType,
) -> None:
    right_plan = tool.camera_bridge_capture_plan()
    right_action = right_plan.actions[0]

    with pytest.raises(ValueError, match="missing required input acknowledgements"):
        CameraActionReceipt(0, right_action, ())
    with pytest.raises(ValueError, match="unexpected input operation"):
        CameraActionReceipt(
            0,
            right_action,
            (
                CameraInputReceipt(CameraInputOperation.KEY_UP, 1, 1),
                CameraInputReceipt(CameraInputOperation.KEY_DOWN, 1, 1),
            ),
        )

    compass_plan, compass_receipt = _compass_receipt()
    with pytest.raises(ValueError, match="action order does not match"):
        CameraPlanReceipt(
            compass_plan,
            _preflight(),
            _right_receipt(tool).action_receipts,
        )
    with pytest.raises(ValueError, match="does not cover every plan action"):
        CameraPlanReceipt(compass_plan, _preflight(), ())
    assert compass_receipt.action_receipts[0].action == compass_plan.actions[0]


def test_ordered_receipt_has_no_caller_selected_campaign_controls(
    tool: ModuleType,
) -> None:
    parameters = inspect.signature(tool._ordered_campaign_receipt).parameters
    assert set(parameters) == {
        "precursor",
        "result",
        "reservation",
        "reservation_completed_clock_s",
    }
    prohibited = {
        "order",
        "key",
        "duration",
        "hold_seconds",
        "coordinate",
        "compass_point",
        "budget",
        "maximum_physical_primitives",
    }
    assert prohibited.isdisjoint(parameters)

    precursor = _precursor(tool)
    for name in sorted(prohibited):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            tool._ordered_campaign_receipt(
                precursor,
                _bridge_result(tool),
                reservation=_reservation(),
                reservation_completed_clock_s=19.0,
                **{name: object()},
            )


def test_receipt_output_tampering_cannot_change_a_fresh_frozen_receipt(
    tool: ModuleType,
) -> None:
    precursor = _precursor(tool)
    result = _bridge_result(tool)
    reservation = _reservation()
    first = tool._ordered_campaign_receipt(
        precursor,
        result,
        reservation=reservation,
        reservation_completed_clock_s=19.0,
    )
    first["allowed_order"].reverse()
    first["stages"].pop()

    fresh = tool._ordered_campaign_receipt(
        precursor,
        result,
        reservation=reservation,
        reservation_completed_clock_s=19.0,
    )

    assert [item["ordinal"] for item in fresh["allowed_order"]] == [0, 1]
    assert [item["ordinal"] for item in fresh["stages"]] == [0, 1]
    assert fresh["maximum_physical_primitives"] == 2


def test_consumed_precursor_failure_forbids_right_and_cannot_be_sealed(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plan, compass_receipt = _compass_receipt()
    result = SimpleNamespace(
        commit=SimpleNamespace(artifact=SimpleNamespace(raw_sha256="b" * 64)),
        post=None,
        receipt=compass_receipt,
        input_attempted=True,
        input_state=CameraNorthBootstrapInputState.COMPLETE,
        input_start_clock_s=20.0,
        input_receipt_clock_s=20.01,
    )
    reservation = SimpleNamespace(
        sentinel_sha256="9" * 64,
        as_dict=lambda: {
            "campaign_id": tool.CAMERA_BRIDGE_AUTHORIZATION_CAMPAIGN_ID,
            "reservation_id": "9" * 64,
        },
    )
    captured: dict[str, object] = {}

    def fake_write(
        path: Path,
        evidence: dict[str, object],
        provenance: object,
    ) -> object:
        captured.update(path=path, evidence=evidence, provenance=provenance)
        return SimpleNamespace(sha256="f" * 64)

    monkeypatch.setattr(tool, "_bootstrap_result_dict", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tool, "write_camera_validation_report", fake_write)

    digest = tool._write_consumed_precursor_failure_report(
        result=result,
        reservation=reservation,
        reservation_completed_clock_s=18.25,
        report_path=tmp_path / "consumed.camera.json",
        expected_head="1" * 40,
        command_argv=("bridge-capture-r2",),
        selected_hwnd=123,
        selected_process_id=456,
        selected_thread_id=789,
        selected_class_name="SunAwtFrame",
        selected_title_sha256="a" * 64,
        detail="compass post observation failed closed",
    )

    assert digest == "f" * 64
    evidence = captured["evidence"]
    assert evidence["terminal_reason"] == "campaign_precursor_failed"
    assert evidence["right_input_attempted"] is False
    assert evidence["right_input_forbidden"] is True
    assert evidence["completion_seal_eligible"] is False
    ordered = evidence["ordered_campaign_receipt"]
    assert ordered["reservation_id"] == reservation.sentinel_sha256
    assert ordered["reservation_completed_clock_s"] == 18.25
    assert ordered["campaign_completed"] is False
    assert ordered["actual_physical_primitives"] == 1
    assert ordered["stages"][1] == {
        "ordinal": 1,
        "stage": "bridge",
        "mode": "fixed_right_hold",
        "status": "forbidden_after_precursor_failure",
        "receipt": None,
    }
