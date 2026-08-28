from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    VARROCK_EAST_IRON_FIXED_UI_REGIONS,
    load_varrock_east_iron_profile,
)
from mining_automation.validation.camera_plan import (
    CameraHoldKey,
    CameraInputOperation,
    CameraInputReceipt,
    CameraKeyHold,
    CameraPause,
    CameraPreflightReceipt,
    CameraWheel,
    CompassClick,
    ResetZoomKey,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
)


def _load_tool() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "validate_varrock_east_camera.py"
    spec = importlib.util.spec_from_file_location("validate_varrock_east_camera", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> ModuleType:
    return _load_tool()


def _args(tool: ModuleType, *values: str) -> argparse.Namespace:
    return tool.build_parser().parse_args(list(values))


def test_reset_recipe_is_north_settle_pitch_endpoint_then_release_reset(
    tool: ModuleType,
) -> None:
    args = _args(tool, "--pitch-endpoint", "down", "--reset-zoom")

    plan = tool._build_normalization_plan(args)

    assert isinstance(plan.actions[0], CompassClick)
    assert plan.actions[0] == CompassClick(608, 49)
    assert plan.actions[1] == CameraPause(0.5)
    assert plan.actions[2] == CameraKeyHold(CameraHoldKey.DOWN, 3.0)
    assert plan.actions[3] == ResetZoomKey("control", dwell_s=0.1)


def test_wheel_recipe_saturates_then_moves_back_from_endpoint(tool: ModuleType) -> None:
    args = _args(
        tool,
        "--pitch-endpoint",
        "up",
        "--zoom-saturate-detents",
        "-96",
        "--zoom-offset-detents",
        "24",
    )

    plan = tool._build_normalization_plan(args)

    assert plan.actions[1] == CameraPause(0.5)
    assert plan.actions[2] == CameraKeyHold(CameraHoldKey.UP, 3.0)
    assert plan.actions[3:] == (
        CameraWheel(400, 50, -96),
        CameraWheel(400, 50, 24),
    )


def test_yaw_and_pitch_offsets_follow_settle_and_precede_zoom(
    tool: ModuleType,
) -> None:
    args = _args(
        tool,
        "--pitch-endpoint",
        "up",
        "--pitch-offset-hold",
        "0.55",
        "--yaw-offset-direction",
        "right",
        "--yaw-offset-hold",
        "0.05",
        "--post-compass-settle",
        "0.75",
        "--zoom-saturate-detents",
        "96",
        "--zoom-offset-detents",
        "-14",
    )

    plan = tool._build_normalization_plan(args)

    assert plan.actions == (
        CompassClick(608, 49),
        CameraPause(0.75),
        CameraKeyHold(CameraHoldKey.RIGHT, 0.05),
        CameraKeyHold(CameraHoldKey.UP, 3.0),
        CameraKeyHold(CameraHoldKey.DOWN, 0.55),
        CameraWheel(400, 50, 96),
        CameraWheel(400, 50, -14),
    )


@pytest.mark.parametrize(
    "values",
    [
        ("--pitch-endpoint", "down", "--zoom-saturate-detents", "79"),
        (
            "--pitch-endpoint",
            "down",
            "--zoom-saturate-detents",
            "96",
            "--zoom-offset-detents",
            "1",
        ),
        (
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
            "--zoom-offset-detents",
            "1",
        ),
    ],
)
def test_unsafe_or_nonendpoint_zoom_recipe_is_rejected(
    tool: ModuleType,
    values: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        tool._build_normalization_plan(_args(tool, *values))


@pytest.mark.parametrize(
    "values",
    [
        ("--pitch-offset-hold", "-0.01"),
        ("--pitch-offset-hold", "nan"),
        ("--pitch-offset-hold", "inf"),
        ("--pitch-offset-hold", "5.001"),
        ("--yaw-offset-hold", "-0.01"),
        ("--yaw-offset-hold", "nan"),
        ("--yaw-offset-hold", "inf"),
        ("--yaw-offset-hold", "5.001"),
        ("--yaw-offset-hold", "0.05"),
        ("--yaw-offset-direction", "right"),
        ("--post-compass-settle", "0"),
        ("--post-compass-settle", "-0.01"),
        ("--post-compass-settle", "nan"),
        ("--post-compass-settle", "inf"),
        ("--post-compass-settle", "2.001"),
    ],
)
def test_invalid_or_nonfinite_camera_offsets_and_settle_are_rejected(
    tool: ModuleType,
    values: tuple[str, ...],
) -> None:
    args = _args(
        tool,
        "--pitch-endpoint",
        "up",
        "--reset-zoom",
        *values,
    )

    with pytest.raises(ValueError):
        tool._build_normalization_plan(args)


def test_three_perturbations_are_distinct_and_camera_only(tool: ModuleType) -> None:
    args = _args(tool, "--pitch-endpoint", "down", "--reset-zoom")

    plans = tool._build_perturbation_plans(args)

    assert len(plans) == 3
    assert len({plan.name for plan in plans}) == 3
    assert all(
        isinstance(action, (CameraKeyHold, CameraWheel))
        for plan in plans
        for action in plan.actions
    )
    assert plans[1].actions == (CameraKeyHold(CameraHoldKey.UP, 3.0),)


def test_compass_click_is_fixed_ui_only_and_never_world_or_candidate(tool: ModuleType) -> None:
    args = _args(tool, "--pitch-endpoint", "down", "--reset-zoom")
    plan = tool._build_normalization_plan(args)
    compass = plan.actions[0]
    assert isinstance(compass, CompassClick)
    point = compass.x, compass.y
    assert any(_contains(region, point) for region in VARROCK_EAST_IRON_FIXED_UI_REGIONS)
    profile = load_varrock_east_iron_profile()
    assert not any(_contains(candidate.region, point) for candidate in profile.candidates)
    assert not any(_contains(landmark.region, point) for landmark in profile.scene_landmarks)


def test_wheel_point_is_outside_fixed_ui_candidates_and_landmarks(tool: ModuleType) -> None:
    args = _args(
        tool,
        "--pitch-endpoint",
        "down",
        "--zoom-saturate-detents",
        "96",
    )
    plan = tool._build_normalization_plan(args)
    wheel = next(action for action in plan.actions if isinstance(action, CameraWheel))
    point = wheel.x, wheel.y
    profile = load_varrock_east_iron_profile()
    assert not any(_contains(region, point) for region in VARROCK_EAST_IRON_FIXED_UI_REGIONS)
    assert not any(_contains(candidate.region, point) for candidate in profile.candidates)
    assert not any(_contains(landmark.region, point) for landmark in profile.scene_landmarks)


def test_dry_run_prints_exact_plans_without_capture(
    tool: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = tool.main(["--pitch-endpoint", "down", "--reset-zoom", "--dry-run"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["normalization"]["actions"][0] == {
        "kind": "compass_click",
        "x": 608,
        "y": 49,
    }
    assert payload["normalization"]["actions"][1:] == [
        {"duration_s": 0.5, "kind": "pause"},
        {"duration_s": 3.0, "key": "down", "kind": "key_hold"},
        {"dwell_s": 0.1, "key": "control", "kind": "reset_zoom_key"},
    ]
    assert len(payload["perturbations"]) == 3


def test_git_state_counts_untracked_code_but_ignores_private_diagnostics(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Issue 31 Test")
    _git(repo, "config", "user.email", "issue31@example.invalid")
    (repo / ".gitignore").write_text("diagnostics/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-m", "baseline")
    (repo / "diagnostics").mkdir()
    (repo / "diagnostics" / "private.raw").write_bytes(b"private")
    monkeypatch.setattr(tool, "_REPO_ROOT", repo)

    _, clean_with_only_ignored_evidence = tool._git_state()

    assert clean_with_only_ignored_evidence
    (repo / "src").mkdir()
    (repo / "src" / "untracked.py").write_text("UNTRACKED = True\n", encoding="utf-8")

    _, clean_with_untracked_code = tool._git_state()

    assert not clean_with_untracked_code


def test_private_output_must_resolve_under_ignored_diagnostics(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / ".gitignore").write_text("diagnostics/\n", encoding="utf-8")
    monkeypatch.setattr(tool, "_REPO_ROOT", repo)

    assert tool._resolve_private_output_root(Path("diagnostics/live")) == (
        repo / "diagnostics" / "live"
    ).resolve()
    with pytest.raises(ValueError, match="live pixels remain private"):
        tool._resolve_private_output_root(Path("public/live"))


class _FakeBackend:
    constructed = 0

    def __init__(self, *, title_substring: str) -> None:
        del title_substring
        type(self).constructed += 1
        self.selected_window = SimpleNamespace(hwnd=3131)


class _FakeSource:
    frames: list[Frame] = []
    last: _FakeSource | None = None

    def __init__(self, backend: object, *, max_consecutive_failures: int) -> None:
        del backend, max_consecutive_failures
        self._frames = list(type(self).frames)
        self.opened = False
        self.closed = False
        type(self).last = self

    def open(self) -> None:
        self.opened = True

    def capture(self) -> Frame:
        if not self._frames:
            raise AssertionError("unexpected capture")
        return self._frames.pop(0)

    def close(self) -> None:
        self.closed = True


class _FakeControl:
    last: _FakeControl | None = None
    cleanup_error: BaseException | None = None

    def __init__(self, hwnd: int) -> None:
        assert hwnd == 3131
        self.released = False
        type(self).last = self

    def preflight(self) -> CameraPreflightReceipt:
        return CameraPreflightReceipt(True, 1005, 1078)

    def click_compass(self, x: int, y: int) -> CameraInputReceipt:
        assert (x, y) == (608, 49)
        return CameraInputReceipt(CameraInputOperation.COMPASS_CLICK, 2, 2)

    def key_down(self, key: str) -> CameraInputReceipt:
        return CameraInputReceipt(CameraInputOperation.KEY_DOWN, 1, 1)

    def key_up(self, key: str) -> CameraInputReceipt:
        return CameraInputReceipt(CameraInputOperation.KEY_UP, 1, 1)

    def scroll_camera(self, x: int, y: int, detents: int) -> CameraInputReceipt:
        assert (x, y) == (400, 50)
        return CameraInputReceipt(
            CameraInputOperation.CAMERA_WHEEL,
            abs(detents),
            abs(detents),
        )

    def release_all_held_keys(self) -> None:
        self.released = True
        error = type(self).cleanup_error
        if error is not None:
            raise error


def test_one_command_wires_production_evaluation_artifacts_and_exact_report(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    command_args = [
        "--output",
        str(output),
        "--case-prefix",
        "integration",
        "--pitch-endpoint",
        "down",
        "--reset-zoom",
        "--settle",
        "0.001",
    ]

    exit_code = tool.main(command_args)

    assert exit_code == 0
    assert _FakeSource.last is not None and _FakeSource.last.closed
    assert _FakeControl.last is not None and _FakeControl.last.released
    report_path = output / "reports" / "integration.camera.json"
    report_bytes = report_path.read_bytes()
    payload = json.loads(report_bytes)
    assert payload["schema_version"] == 1
    assert payload["provenance"]["git_head_sha"] == "a" * 40
    assert payload["provenance"]["command_argv"] == [
        str(Path(sys.executable).resolve()),
        str(Path(tool.__file__).resolve()),
        *command_args,
    ]
    evidence = payload["evidence"]
    assert evidence["camera_protocol_passed"] is True
    assert evidence["tracked_worktree_clean"] is True
    assert evidence["camera_evidence_eligible"] is True
    assert evidence["combined_issue31_acceptance"] == {
        "complete": False,
        "reviewed_live_resource_states_included": False,
        "same_head_drift_proof_included": False,
    }
    assert evidence["initial_normalization_receipt"]["plan"] == evidence[
        "normalization_plan"
    ]
    assert evidence["camera_assumptions"] == {
        "compass_point": [608, 49],
        "wheel_point": [400, 50],
        "pitch_endpoint": "down",
        "pitch_hold_s": 3.0,
        "pitch_offset_hold_s": 0.0,
        "yaw_offset_direction": None,
        "yaw_offset_hold_s": 0.0,
        "post_compass_settle_s": 0.5,
        "zoom_mode": "reset_key",
        "zoom_saturate_detents": None,
        "zoom_offset_detents": 0,
        "wheel_delivery": "paced_individual_detents",
        "wheel_event_interval_s": 0.025,
        "diagnostics_can_override_production": False,
    }
    assert evidence["initial_normalization_receipt"]["actions"][1] == {
        "action_index": 1,
        "action": {"kind": "pause", "duration_s": 0.5},
        "input_receipts": [],
    }
    assert len(evidence["trials"]) == 3
    assert all(
        trial["expected_resource_state_vector"]
        == [
            {
                "resource_id": resource_id,
                "state": "available",
            }
            for resource_id in (
                "varrock-east-iron-northwest",
                "varrock-east-iron-southwest",
                "varrock-east-iron-center",
                "varrock-east-iron-northeast",
            )
        ]
        for trial in evidence["trials"]
    )
    assert all(
        confirmation["resource_states_match_expected"] is True
        for trial in evidence["trials"]
        for confirmation in trial["confirmations"]
    )
    records = [
        trial[key]
        for trial in evidence["trials"]
        for key in ("before", "perturbed")
    ] + [
        confirmation
        for trial in evidence["trials"]
        for confirmation in trial["confirmations"]
    ]
    assert len(records) == 12
    assert all(len(record["production"]["scene"]["landmarks"]) == 6 for record in records)
    assert all(len(record["production"]["resources"]) == 4 for record in records)
    assert all(
        record["production"]["profile_geometry"]
        == {"width": 1005, "height": 1078, "pixel_format": "bgra8888"}
        for record in records
    )
    assert all(
        record["production"]["scene"]["configured_landmarks"] == 6
        and record["production"]["scene"]["required_landmark_matches"] == 5
        and record["production"]["scene"]["required_zones"] == 3
        for record in records
    )
    assert all(
        not Path(path).is_absolute()
        for record in records
        for path in record["artifact"]["files"].values()
    )
    digest = hashlib.sha256(report_bytes).hexdigest()
    assert (Path(f"{report_path}.sha256")).read_text(encoding="ascii") == f"{digest}\n"


def test_report_records_definitive_confirmation_state_mismatch(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    frames = _passing_frames()
    mismatch = _reviewed_payload("lower-left-full-cycle-020")
    frames[3] = Frame.from_raw(
        RawFrame(mismatch, 1005, 1078, PixelFormat.BGRA8888),
        frame_id=4,
        captured_monotonic_s=4.0,
    )
    _install_main_fakes(tool, output, monkeypatch, frames)

    exit_code = tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "state-mismatch",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
            "--settle",
            "0.001",
        ]
    )

    assert exit_code == 1
    payload = json.loads(
        (output / "reports" / "state-mismatch.camera.json").read_text()
    )
    first_trial = payload["evidence"]["trials"][0]
    mismatch_confirmation = first_trial["confirmations"][0]
    assert mismatch_confirmation["production"]["passed"] is True
    assert all(
        resource["definitive"]
        for resource in mismatch_confirmation["production"]["resources"]
    )
    assert mismatch_confirmation["resource_states_match_expected"] is False
    assert first_trial["confirmations"][1]["resource_states_match_expected"] is True
    assert first_trial["passed"] is False
    assert payload["evidence"]["camera_protocol_passed"] is False


def test_dirty_development_session_cannot_serialize_acceptance_pass(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames(), clean=False)
    args = [
        "--output",
        str(output),
        "--case-prefix",
        "dirty",
        "--pitch-endpoint",
        "down",
        "--reset-zoom",
        "--allow-dirty",
        "--settle",
        "0.001",
    ]

    assert tool.main(args) == 1

    payload = json.loads((output / "reports" / "dirty.camera.json").read_text())
    assert payload["evidence"]["camera_protocol_passed"] is True
    assert payload["evidence"]["tracked_worktree_clean"] is False
    assert payload["evidence"]["camera_evidence_eligible"] is False
    assert payload["evidence"]["combined_issue31_acceptance"]["complete"] is False
    assert payload["provenance"]["tracked_worktree_clean"] is False


def test_primary_failure_and_input_cleanup_failure_are_both_reported(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    _FakeControl.cleanup_error = OSError("release still failed")

    def fail_session(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("primary session failure")

    monkeypatch.setattr(tool, "run_camera_validation_session", fail_session)

    result = tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "cleanup-failure",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
        ]
    )

    assert result == 2
    error = capsys.readouterr().err
    assert "primary session failure" in error
    assert "camera input cleanup failed: release still failed" in error
    assert _FakeSource.last is not None and _FakeSource.last.closed


def test_keyboard_interrupt_runs_cleanup_and_preserves_interruption(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    _FakeControl.cleanup_error = OSError("interrupt cleanup failed")

    def interrupt_session(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(tool, "run_camera_validation_session", interrupt_session)

    with pytest.raises(KeyboardInterrupt):
        tool.main(
            [
                "--output",
                str(output),
                "--case-prefix",
                "interrupted",
                "--pitch-endpoint",
                "down",
                "--reset-zoom",
            ]
        )

    assert "camera input cleanup failed: interrupt cleanup failed" in (
        capsys.readouterr().err
    )
    assert _FakeSource.last is not None and _FakeSource.last.closed


def test_existing_report_stops_before_capture_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    report = output / "reports" / "collision.camera.json"
    report.parent.mkdir(parents=True)
    report.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _FakeBackend)
    _FakeBackend.constructed = 0

    result = tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "collision",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
        ]
    )

    assert result == 2
    assert _FakeBackend.constructed == 0
    assert report.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("case_prefix", ["../escape", "bad/name", "-leading", "x" * 129])
def test_unsafe_case_prefix_stops_before_capture_or_file_creation(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_prefix: str,
) -> None:
    output = tmp_path / "private"
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _FakeBackend)
    _FakeBackend.constructed = 0

    result = tool.main(
        [
            "--output",
            str(output),
            f"--case-prefix={case_prefix}",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
        ]
    )

    assert result == 2
    assert _FakeBackend.constructed == 0
    assert not output.exists()


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--title", " "),
        ("--plan-id", "unsafe\nplan"),
        ("--plan-version", "version\x00suffix"),
    ],
)
def test_invalid_pre_input_metadata_stops_before_capture(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: str,
) -> None:
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _FakeBackend)
    _FakeBackend.constructed = 0

    result = tool.main(
        [
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
            option,
            value,
        ]
    )

    assert result == 2
    assert _FakeBackend.constructed == 0


def _install_main_fakes(
    tool: ModuleType,
    output: Path,
    monkeypatch: pytest.MonkeyPatch,
    frames: list[Frame],
    *,
    clean: bool = True,
) -> None:
    _FakeBackend.constructed = 0
    _FakeSource.frames = frames
    _FakeSource.last = None
    _FakeControl.last = None
    _FakeControl.cleanup_error = None
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, clean))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _FakeBackend)
    monkeypatch.setattr(tool, "CaptureSource", _FakeSource)
    monkeypatch.setattr(tool, "WindowsCameraControl", _FakeControl)
    monkeypatch.setattr(tool.time, "sleep", lambda _seconds: None)

    def write_draft(
        frame: Frame,
        root: Path,
        **kwargs: Any,
    ) -> SimpleNamespace:
        del frame
        case_id = kwargs["case_id"]
        frame_path = root / "frames" / f"{case_id}.raw"
        preview_path = root / "previews" / f"{case_id}.bmp"
        draft_path = root / "drafts" / f"{case_id}.json"
        for path in (frame_path, preview_path, draft_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"private-test-artifact")
        return SimpleNamespace(
            frame=frame_path,
            preview=preview_path,
            draft=draft_path,
        )

    monkeypatch.setattr(tool, "write_resource_fixture_draft", write_draft)


def _passing_frames() -> list[Frame]:
    reviewed = _reviewed_payload("available-01")
    unsupported = bytes(len(reviewed))
    # Window discovery happens before initial normalization. Make that frame
    # unsupported so the integration test proves it cannot be reused as the
    # post-normalization trial-one baseline.
    frames: list[Frame] = [
        Frame.from_raw(
            RawFrame(unsupported, 1005, 1078, PixelFormat.BGRA8888),
            frame_id=1,
            captured_monotonic_s=1.0,
        )
    ]
    frame_id = 2
    for _trial in range(3):
        for payload in (reviewed, unsupported, reviewed, reviewed):
            frames.append(
                Frame.from_raw(
                    RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
                    frame_id=frame_id,
                    captured_monotonic_s=float(frame_id),
                )
            )
            frame_id += 1
    return frames


def _reviewed_payload(case_id: str) -> bytes:
    return gzip.decompress(
        (FIXTURE_ROOT / "frames" / f"{case_id}.raw.gz").read_bytes()
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _contains(region: tuple[int, int, int, int], point: tuple[int, int]) -> bool:
    x, y, width, height = region
    point_x, point_y = point
    return x <= point_x < x + width and y <= point_y < y + height
