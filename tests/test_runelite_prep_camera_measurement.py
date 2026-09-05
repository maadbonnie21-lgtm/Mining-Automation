"""Synthetic operator-bound single camera measurement; never a mining authorization."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_runelite_prep_live_cli import HEAD, _forbid_real_backend, _read_result, prep_live


@pytest.mark.parametrize("step", [item.value for item in prep_live.PrepCameraStep])
def test_camera_measurement_requires_apply(step: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        prep_live._parse_args(["--camera-step", step])
    assert stopped.value.code == 2


@pytest.mark.parametrize(
    ("camera_step", "initial_supported", "final_supported", "expected_camera_count", "expected_rc"),
    [
        (None, False, False, 0, 2),
        ("compass_reset", True, True, 0, 0),
        ("compass_reset", False, True, 1, 0),
        ("compass_reset", False, False, 1, 2),
        ("pitch_up_50ms", True, True, 0, 0),
        ("pitch_up_50ms", False, True, 1, 0),
        ("pitch_up_50ms", False, False, 1, 2),
    ],
)
def test_single_camera_measurement_stops_or_reevaluates_without_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    camera_step: str | None,
    initial_supported: bool,
    final_supported: bool,
    expected_camera_count: int,
    expected_rc: int,
) -> None:
    from test_runelite_prep_live_boundary import InnerBackend, _observation

    inner = InnerBackend()
    observations = []

    def observe():
        has_camera = any(item.startswith("camera:") for item in inner.mutations)
        supported = final_supported if has_camera else initial_supported
        frame_id = 10 + len(observations)
        observation = replace(
            _observation(), frame_id=frame_id,
            frame_sha256=f"{frame_id:064x}",
            resource_supported=supported,
            resource_view="supported" if supported else "unsupported",
            matched_landmarks=6 if supported else 0,
            matched_zones=_observation().matched_zones if supported else (),
            landmark_distances=tuple(
                (f"landmark-{i}", 0.01 if supported else 0.5) for i in range(6)
            ),
        )
        observations.append(observation)
        return observation

    monkeypatch.setattr(inner, "observe", observe)
    monkeypatch.setattr(prep_live.legacy_prep, "RealPrepBackend", lambda **kwargs: inner)
    monkeypatch.setattr(prep_live.legacy_prep, "_exact_git_sha", lambda: HEAD)
    monkeypatch.setattr(prep_live.legacy_prep, "_checkout_clean", lambda: True)
    output = tmp_path / "measurement"
    args = [
        "--apply", "--authorize-execution-sha", HEAD, "--hwnd", "42",
        "--confirm", prep_live.PREP_CONFIRMATION, "--output", str(output),
    ]
    if camera_step is not None:
        args.extend(["--camera-step", camera_step])
    rc = prep_live.main(args)
    result = _read_result(output)
    calls = [item for item in inner.mutations if item.startswith("camera:")]
    assert len(calls) == expected_camera_count
    assert rc == expected_rc
    assert result["ready_for_mining"] is (expected_rc == 0)
    assert result["mining_input_authority"] is False
    assert result["navigation_authority"] is False
    assert result["banking_authority"] is False
    if expected_rc == 0:
        assert result["observations"][-1]["frame_id"] == observations[-1].frame_id
    elif camera_step is not None:
        assert result["stop_reason"] == "camera_search_exhausted"


def test_camera_measurement_missing_sha_is_zero_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(prep_live.legacy_prep, "_exact_git_sha", lambda: HEAD)
    monkeypatch.setattr(prep_live.legacy_prep, "_checkout_clean", lambda: True)
    calls = _forbid_real_backend(monkeypatch)
    output = tmp_path / "missing-camera-sha"
    rc = prep_live.main([
        "--apply", "--hwnd", "42", "--camera-step", "pitch_up_50ms",
        "--confirm", prep_live.PREP_CONFIRMATION, "--output", str(output),
    ])
    assert rc == 2
    assert calls == []
    assert _read_result(output)["ready_for_mining"] is False


@pytest.mark.parametrize("completed", [0, 1, 2])
def test_compass_adapter_uses_only_reviewed_compass_point(
    completed: int,
) -> None:
    from unittest.mock import Mock

    from mining_automation.validation.camera_plan import (
        REVIEWED_COMPASS_POINT,
        CameraInputOperation,
        CameraInputReceipt,
    )

    control = Mock()
    control.click_compass.return_value = CameraInputReceipt(
        CameraInputOperation.COMPASS_CLICK, 2, completed,
    )
    # Bypass construction only: no Win32 API or live input in this offline test.
    backend = object.__new__(prep_live.legacy_prep.RealPrepBackend)
    backend.camera_control = control
    receipts = backend.camera_action(prep_live.PrepCameraStep.COMPASS_RESET)
    control.click_compass.assert_called_once_with(*REVIEWED_COMPASS_POINT)
    control.key_down.assert_not_called()
    control.scroll_camera.assert_not_called()
    assert len(receipts) == 1
    assert receipts[0].action == "compass_click"
    assert receipts[0].detail == "compass_reset"
    assert receipts[0].requested_events == 2
    assert receipts[0].completed_events == completed
    assert receipts[0].complete is (completed == 2)


def test_compass_short_receipt_stops_before_another_observation() -> None:
    from test_runelite_prep_live_boundary import InnerBackend, _observation

    class ShortCompassBackend(InnerBackend):
        observations = 0

        def observe(self):
            self.observations += 1
            return replace(
                _observation(), resource_supported=False, resource_view="unsupported",
            )

        def camera_action(self, step):
            assert step is prep_live.PrepCameraStep.COMPASS_RESET
            self.mutations.append("compass")
            return (prep_live.PrepActionReceipt("compass_click", 2, 1),)

    inner = ShortCompassBackend()
    backend = prep_live.ExactHwndPrepBackend(inner, expected_hwnd=42)
    result = prep_live.run_runelite_prep(
        backend, mode=prep_live.PrepMode.APPLY, git_sha=HEAD,
        prep_session_id="compass-short-test", confirm=prep_live.PREP_CONFIRMATION,
        camera_steps=(prep_live.PrepCameraStep.COMPASS_RESET,),
    )
    assert result.ready_for_mining is False
    assert result.stop_reason is prep_live.PrepStopReason.CAMERA_RECEIPT_INCOMPLETE
    assert inner.observations == 1
    assert inner.mutations.count("compass") == 1
    assert result.mining_input_authority is False
