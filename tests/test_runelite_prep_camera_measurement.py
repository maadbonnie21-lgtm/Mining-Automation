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
