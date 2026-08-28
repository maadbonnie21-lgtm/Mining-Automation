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
    MAX_CAMERA_DRAG_PIXELS,
    MAX_CAMERA_DRAG_STEP_PIXELS,
    REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT,
    REVIEWED_CAMERA_DRAG_POINT,
    CameraDragAxis,
    CameraHoldKey,
    CameraInputOperation,
    CameraInputReceipt,
    CameraKeyHold,
    CameraMiddleDrag,
    CameraPause,
    CameraPreflightReceipt,
    CameraWheel,
    CompassClick,
    ResetZoomKey,
    camera_drag_path,
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
        "--yaw-drag-pixels",
        "8",
        "--pitch-drag-pixels",
        "-5",
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
        CameraMiddleDrag(CameraDragAxis.HORIZONTAL, 8),
        CameraKeyHold(CameraHoldKey.UP, 3.0),
        CameraKeyHold(CameraHoldKey.DOWN, 0.55),
        CameraMiddleDrag(CameraDragAxis.VERTICAL, -5),
        CameraWheel(400, 50, 96),
        CameraWheel(400, 50, -14),
    )

    assert tool._action_dict(plan.actions[3]) == {
        "kind": "camera_middle_drag",
        "axis": "horizontal",
        "pixels": 8,
        "coordinate_space": "runelite_target_logical_client",
        "start": [200, 600],
        "reviewed_open_viewport": {
            "left": 0,
            "top": 34,
            "right_exclusive": 520,
            "bottom_exclusive": 850,
        },
        "path": [[204, 600], [208, 600]],
        "step_count": 2,
        "max_step_pixels": 4,
        "arming_settle_s": 1.0,
        "post_move_settle_s": 0.05,
        "final_move_settle_included": True,
        "post_release_settle_s": 1.0,
        "post_release_verification": (
            "middle_up_focus_geometry_cursor_and_target_root"
        ),
    }
    assert tool._plan_input_event_count(plan) == 126


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
        ("--yaw-drag-pixels", "257"),
        ("--yaw-drag-pixels", "-257"),
        ("--pitch-drag-pixels", "257"),
        ("--pitch-drag-pixels", "-257"),
        ("--pitch-drag-pixels", "250"),
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


def test_default_single_plan_version_records_drag_capability(tool: ModuleType) -> None:
    args = _args(tool, "--pitch-endpoint", "up", "--reset-zoom")

    assert args.plan_version == "0.3.0"


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


def test_drag_start_is_reviewed_open_viewport_not_ui_candidate_or_landmark() -> None:
    point = REVIEWED_CAMERA_DRAG_POINT
    profile = load_varrock_east_iron_profile()

    assert not any(_contains(region, point) for region in VARROCK_EAST_IRON_FIXED_UI_REGIONS)
    assert not any(_contains(candidate.region, point) for candidate in profile.candidates)
    assert not any(_contains(landmark.region, point) for landmark in profile.scene_landmarks)


def test_every_accepted_drag_corridor_avoids_all_reviewed_fixed_ui() -> None:
    accepted = 0
    for axis in CameraDragAxis:
        for pixels in range(-MAX_CAMERA_DRAG_PIXELS, MAX_CAMERA_DRAG_PIXELS + 1):
            try:
                action = CameraMiddleDrag(axis, pixels)
            except ValueError:
                continue
            accepted += 1
            corridor = (
                (action.start_x, action.start_y),
                *camera_drag_path(action),
            )
            assert all(
                not any(
                    _contains(fixed_ui_region, point)
                    for fixed_ui_region in VARROCK_EAST_IRON_FIXED_UI_REGIONS
                )
                for point in corridor
            )

    assert accepted == 961


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
        {
            "duration_s": 3.0,
            "key": "down",
            "kind": "key_hold",
            "post_release_settle_s": 1.0,
            "post_release_verification": (
                "semantic_client_consumption_wait_then_observable_key_up_and_"
                "target_focus_identity_geometry"
            ),
        },
        {
            "dwell_s": 0.1,
            "key": "control",
            "kind": "reset_zoom_key",
            "post_release_settle_s": 1.0,
            "post_release_verification": (
                "semantic_client_consumption_wait_then_observable_key_up_and_"
                "target_focus_identity_geometry"
            ),
        },
    ]
    assert len(payload["perturbations"]) == 3


def test_production_gated_strategy_has_exact_independent_candidate_order(
    tool: ModuleType,
) -> None:
    args = _args(
        tool,
        "--normalization-strategy",
        "varrock-east-production-gated-search-v1",
    )

    candidates = tool._build_normalization_candidates(args)
    perturbations = tool._build_perturbation_plans(args)

    assert len(candidates) == 11
    assert len({candidate.actions for candidate in candidates}) == 11
    assert [
        (candidate.actions[4].duration_s, candidate.actions[2].duration_s)
        for candidate in candidates
    ] == [
        (0.60, 0.05),
        (0.58, 0.05),
        (0.62, 0.05),
        (0.56, 0.05),
        (0.64, 0.05),
        (0.60, 0.04),
        (0.58, 0.04),
        (0.62, 0.04),
        (0.60, 0.06),
        (0.58, 0.06),
        (0.62, 0.06),
    ]
    for candidate in candidates:
        assert candidate.actions[:4] == (
            CompassClick(608, 49),
            CameraPause(0.5),
            CameraKeyHold(CameraHoldKey.RIGHT, candidate.actions[2].duration_s),
            CameraKeyHold(CameraHoldKey.UP, 3.0),
        )
        assert isinstance(candidate.actions[4], CameraKeyHold)
        assert candidate.actions[4].key is CameraHoldKey.DOWN
        assert candidate.actions[5:] == (
            CameraWheel(400, 50, 96),
            CameraWheel(400, 50, -17),
        )
    assert tool._worst_case_bounds(
        candidates,
        perturbations,
        confirmations=2,
    ) == {
        "normalization_candidates_per_boundary": 11,
        "normalization_boundaries": 4,
        "normalization_plan_executions": 44,
        "normalization_input_events": 5324,
        "perturbation_input_events": 18,
        "total_input_events": 5342,
        "candidate_evaluation_frames": 44,
        "required_confirmation_frames": 6,
        "maximum_protocol_frames": 56,
    }


def test_production_gated_strategy_rejects_single_plan_overrides(
    tool: ModuleType,
) -> None:
    args = _args(
        tool,
        "--normalization-strategy",
        "varrock-east-production-gated-search-v1",
        "--pitch-endpoint",
        "up",
        "--reset-zoom",
    )

    with pytest.raises(ValueError, match="frozen candidate ladder"):
        tool._build_normalization_candidates(args)


def test_production_gated_strategy_rejects_unreviewed_drag_overrides(
    tool: ModuleType,
) -> None:
    args = _args(
        tool,
        "--normalization-strategy",
        "varrock-east-production-gated-search-v1",
        "--yaw-drag-pixels",
        "4",
    )

    with pytest.raises(ValueError, match="frozen candidate ladder"):
        tool._build_normalization_candidates(args)


def test_production_gated_dry_run_is_no_input_and_records_full_bound(
    tool: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = tool.main(
        [
            "--normalization-strategy",
            "varrock-east-production-gated-search-v1",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["normalization_strategy"] == {
        "id": "varrock-east-production-gated-search-v1",
        "version": "1.0.0",
        "selection_authority": "unchanged_production_camera_evaluation",
        "diagnostic_registration_used": False,
    }
    assert len(payload["normalization_candidates"]) == 11
    assert payload["worst_case_bounds"]["total_input_events"] == 5342
    assert "normalization" not in payload


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
        self.selected_window = SimpleNamespace(
            hwnd=3131,
            class_name="SunAwtFrame",
            title="RuneLite - Chief Luma",
        )


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

    def __init__(
        self,
        hwnd: int,
        *,
        expected_class_name: str,
        expected_title: str,
    ) -> None:
        assert hwnd == 3131
        assert expected_class_name == "SunAwtFrame"
        assert expected_title == "RuneLite - Chief Luma"
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

    def drag_camera(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int,
    ) -> tuple[CameraInputReceipt, CameraInputReceipt, CameraInputReceipt]:
        assert (x, y) == REVIEWED_CAMERA_DRAG_POINT
        assert (delta_x == 0) != (delta_y == 0)
        step_count = (
            abs(delta_x or delta_y) + MAX_CAMERA_DRAG_STEP_PIXELS - 1
        ) // MAX_CAMERA_DRAG_STEP_PIXELS
        return (
            CameraInputReceipt(CameraInputOperation.MIDDLE_DOWN, 1, 1),
            CameraInputReceipt(
                CameraInputOperation.CAMERA_DRAG_MOVE,
                step_count,
                step_count,
            ),
            CameraInputReceipt(CameraInputOperation.MIDDLE_UP, 1, 1),
        )

    def release_all_held_keys(self) -> None:
        self.released = True
        error = type(self).cleanup_error
        if error is not None:
            raise error


class _FakeInputLease:
    active = False
    events: list[str] | None = None

    @property
    def acquired(self) -> bool:
        return type(self).active

    def __enter__(self) -> _FakeInputLease:
        assert not type(self).active
        type(self).active = True
        if type(self).events is not None:
            type(self).events.append("lease_acquired")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback
        if type(self).events is not None:
            type(self).events.append("lease_released")
        type(self).active = False


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
        "--yaw-drag-pixels",
        "8",
        "--pitch-drag-pixels",
        "-5",
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
    assert payload["schema_version"] == 2
    assert payload["provenance"]["git_head_sha"] == "a" * 40
    assert payload["provenance"]["plan_version"] == "0.3.0"
    assert payload["provenance"]["command_argv"] == [
        str(Path(sys.executable).resolve()),
        str(Path(tool.__file__).resolve()),
        *command_args,
    ]
    reservation_path = (
        output / "reservations" / "integration.camera-reservation.json"
    )
    assert json.loads(reservation_path.read_text(encoding="utf-8")) == {
        "case_prefix": "integration",
        "git_head_sha": "a" * 40,
        "owner": "validate_varrock_east_camera.py",
        "schema_version": 1,
    }
    evidence = payload["evidence"]
    assert evidence["camera_protocol_passed"] is True
    assert evidence["tracked_worktree_clean"] is True
    assert evidence["camera_evidence_eligible"] is True
    assert evidence["combined_issue31_acceptance"] == {
        "complete": False,
        "reviewed_live_resource_states_included": False,
        "same_head_drift_proof_included": False,
    }
    initial = evidence["initial_normalization"]
    assert initial["selected_candidate_index_1_based"] == 1
    assert initial["selected_identity"] == "varrock-east-camera-endpoint"
    assert initial["production_gate_passed"] is True
    assert initial["attempts"][0]["plan"] == initial["attempts"][0]["receipt"][
        "plan"
    ]
    assert initial["attempts"][0]["counts_as_confirmation"] is False
    assert evidence["normalization_strategy"]["selection_authority"] == (
        "unchanged_production_camera_evaluation"
    )
    assert evidence["normalization_strategy"]["diagnostic_registration_used"] is False
    assert evidence["pre_perturbation_failure"] is None
    assert evidence["camera_assumptions"] == {
        "compass_point": [608, 49],
        "drag_point": [200, 600],
        "wheel_point": [400, 50],
        "pointer_coordinate_space": "runelite_target_logical_client",
        "compass_click_dwell_s": 0.1,
        "key_release_settle_s": 1.0,
        "key_release_verification": (
            "semantic_client_consumption_wait_then_observable_key_up_and_"
            "target_focus_identity_geometry"
        ),
        "pitch_endpoint": "down",
        "pitch_hold_s": 3.0,
        "pitch_offset_hold_s": 0.0,
        "yaw_offset_direction": None,
        "yaw_offset_hold_s": 0.0,
        "yaw_drag_pixels": 8,
        "pitch_drag_pixels": -5,
        "drag_delivery": (
            "preflight_complete_logical_corridor_then_middle_down_arming_"
            "and_post_move_settle_including_final"
        ),
        "drag_coordinate_space": "runelite_target_logical_client",
        "drag_open_viewport": {
            "left": REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT[0],
            "top": REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT[1],
            "right_exclusive": REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT[2],
            "bottom_exclusive": REVIEWED_CAMERA_DRAG_OPEN_VIEWPORT[3],
        },
        "drag_max_pixels": MAX_CAMERA_DRAG_PIXELS,
        "drag_max_step_pixels": 4,
        "drag_path_excludes_start": True,
        "drag_arming_settle_s": 1.0,
        "drag_post_move_settle_s": 0.05,
        "drag_final_move_settle_included": True,
        "drag_post_release_settle_s": 1.0,
        "drag_post_release_verification": (
            "middle_up_focus_geometry_cursor_and_target_root"
        ),
        "post_compass_settle_s": 0.5,
        "zoom_mode": "reset_key",
        "zoom_saturate_detents": None,
        "zoom_offset_detents": 0,
        "wheel_delivery": "paced_individual_detents",
        "wheel_pointer_button_gate": (
            "left_and_middle_before_and_after_relocation"
        ),
        "wheel_event_interval_s": 0.025,
        "diagnostics_can_override_production": False,
    }
    assert initial["attempts"][0]["receipt"]["actions"][1] == {
        "action_index": 1,
        "action": {"kind": "pause", "duration_s": 0.5},
        "input_receipts": [],
    }
    assert initial["attempts"][0]["receipt"]["actions"][2] == {
        "action_index": 2,
        "action": {
            "arming_settle_s": 1.0,
            "axis": "horizontal",
            "coordinate_space": "runelite_target_logical_client",
            "final_move_settle_included": True,
            "kind": "camera_middle_drag",
            "max_step_pixels": 4,
            "path": [[204, 600], [208, 600]],
            "pixels": 8,
            "post_move_settle_s": 0.05,
            "post_release_settle_s": 1.0,
            "post_release_verification": (
                "middle_up_focus_geometry_cursor_and_target_root"
            ),
            "reviewed_open_viewport": {
                "left": 0,
                "top": 34,
                "right_exclusive": 520,
                "bottom_exclusive": 850,
            },
            "start": [200, 600],
            "step_count": 2,
        },
        "input_receipts": [
            {
                "complete": True,
                "completed_events": 1,
                "operation": "middle_down",
                "requested_events": 1,
            },
            {
                "complete": True,
                "completed_events": 2,
                "operation": "camera_drag_move",
                "requested_events": 2,
            },
            {
                "complete": True,
                "completed_events": 1,
                "operation": "middle_up",
                "requested_events": 1,
            },
        ],
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


def test_input_lease_remains_held_through_cleanup_and_report_publication(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    events: list[str] = []
    _FakeInputLease.events = events
    original_control_cleanup = _FakeControl.release_all_held_keys
    original_source_cleanup = _FakeSource.close
    original_report_writer = tool.write_camera_validation_report

    def control_cleanup(control: _FakeControl) -> None:
        assert _FakeInputLease.active
        events.append("input_cleanup")
        original_control_cleanup(control)

    def source_cleanup(source: _FakeSource) -> None:
        assert _FakeInputLease.active
        events.append("capture_cleanup")
        original_source_cleanup(source)

    def report_writer(*args: object, **kwargs: object) -> object:
        assert _FakeInputLease.active
        events.append("report_published")
        return original_report_writer(*args, **kwargs)

    monkeypatch.setattr(_FakeControl, "release_all_held_keys", control_cleanup)
    monkeypatch.setattr(_FakeSource, "close", source_cleanup)
    monkeypatch.setattr(tool, "write_camera_validation_report", report_writer)

    assert tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "lease-lifetime",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
            "--settle",
            "0.001",
        ]
    ) == 0

    assert events == [
        "lease_acquired",
        "input_cleanup",
        "capture_cleanup",
        "report_published",
        "lease_released",
    ]
    assert _FakeInputLease.active is False


def test_second_invocation_with_held_lease_sends_no_capture_focus_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())

    class HeldInputLease:
        def __enter__(self) -> HeldInputLease:
            raise tool.CameraInputLeaseError(
                "another camera validator owns the lease; no capture, focus, or "
                "input was attempted"
            )

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> None:
            del exc_type, exc, traceback

    monkeypatch.setattr(tool, "WindowsCameraInputLease", HeldInputLease)

    result = tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "lease-contender",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
        ]
    )

    assert result == 2
    assert _FakeBackend.constructed == 0
    assert _FakeSource.last is None
    assert _FakeControl.last is None
    assert not (output / "reports" / "lease-contender.camera.json").exists()
    assert "no capture, focus, or input was attempted" in capsys.readouterr().err


def test_same_prefix_collision_created_during_lease_entry_sends_no_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    report_path = output / "reports" / "lease-race.camera.json"

    class RacingInputLease(_FakeInputLease):
        def __enter__(self) -> RacingInputLease:
            super().__enter__()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("winner", encoding="utf-8")
            return self

    monkeypatch.setattr(tool, "WindowsCameraInputLease", RacingInputLease)

    assert tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "lease-race",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
        ]
    ) == 2
    assert _FakeBackend.constructed == 0
    assert _FakeSource.last is None
    assert _FakeControl.last is None
    assert report_path.read_text(encoding="utf-8") == "winner"


def test_case_prefix_is_reserved_inside_lease_before_backend_construction(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    reservation_path = (
        output / "reservations" / "reservation-order.camera-reservation.json"
    )

    def guarded_backend(*, title_substring: str) -> _FakeBackend:
        assert _FakeInputLease.active
        assert reservation_path.exists()
        return _FakeBackend(title_substring=title_substring)

    monkeypatch.setattr(tool, "WindowsCaptureBackend", guarded_backend)

    assert tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "reservation-order",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
            "--settle",
            "0.001",
        ]
    ) == 0


def test_release_failure_retracts_published_canonical_report(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())

    class ReleaseFailingInputLease(_FakeInputLease):
        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> None:
            del exc_type, exc, traceback
            # Deliberately retain active/acquired state, mirroring the real
            # poison-on-ReleaseMutex-failure behavior.
            raise tool.CameraInputLeaseError("ReleaseMutex failed; process poisoned")

    monkeypatch.setattr(
        tool,
        "WindowsCameraInputLease",
        ReleaseFailingInputLease,
    )

    assert tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "lease-release-failure",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
            "--settle",
            "0.001",
        ]
    ) == 2
    report_path = output / "reports" / "lease-release-failure.camera.json"
    assert not report_path.exists()
    assert not Path(f"{report_path}.sha256").exists()
    assert "process poisoned" in capsys.readouterr().err


def test_body_and_release_failure_still_retract_published_report(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())

    class DualFailingInputLease(_FakeInputLease):
        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> None:
            del exc_type, traceback
            assert isinstance(exc, KeyboardInterrupt)
            raise tool.CameraInputLeaseError(
                "ReleaseMutex failed after summary; process poisoned"
            ) from exc

    def interrupt_summary(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt("summary interrupted")

    monkeypatch.setattr(tool, "WindowsCameraInputLease", DualFailingInputLease)
    monkeypatch.setattr(tool, "_print_summary", interrupt_summary)

    assert tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "dual-failure",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
            "--settle",
            "0.001",
        ]
    ) == 2
    report_path = output / "reports" / "dual-failure.camera.json"
    assert not report_path.exists()
    assert not Path(f"{report_path}.sha256").exists()
    assert (
        output / "reservations" / "dual-failure.camera-reservation.json"
    ).exists()
    assert "process poisoned" in capsys.readouterr().err


def test_outer_guard_retracts_if_context_manager_preserves_body_error(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())

    class NoteOnlyFailingInputLease(_FakeInputLease):
        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> None:
            del exc_type, traceback
            assert isinstance(exc, KeyboardInterrupt)
            exc.add_note("ReleaseMutex failed; ownership retained")
            # Deliberately retain active/acquired state while preserving the
            # body exception, modeling a faulty alternate context boundary.

    def interrupt_summary(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt("summary interrupted")

    monkeypatch.setattr(
        tool,
        "WindowsCameraInputLease",
        NoteOnlyFailingInputLease,
    )
    monkeypatch.setattr(tool, "_print_summary", interrupt_summary)

    with pytest.raises(KeyboardInterrupt, match="summary interrupted"):
        tool.main(
            [
                "--output",
                str(output),
                "--case-prefix",
                "fallback-dual-failure",
                "--pitch-endpoint",
                "down",
                "--reset-zoom",
                "--settle",
                "0.001",
            ]
        )
    report_path = output / "reports" / "fallback-dual-failure.camera.json"
    assert not report_path.exists()
    assert not Path(f"{report_path}.sha256").exists()
    assert (
        output
        / "reservations"
        / "fallback-dual-failure.camera-reservation.json"
    ).exists()


def test_release_failure_never_retracts_another_invocation_report(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    report_path = output / "reports" / "lease-race-release.camera.json"
    digest_path = Path(f"{report_path}.sha256")

    class RacingReleaseFailingLease(_FakeInputLease):
        def __enter__(self) -> RacingReleaseFailingLease:
            super().__enter__()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("winner-report", encoding="utf-8")
            digest_path.write_text("winner-digest", encoding="ascii")
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> None:
            del exc_type, exc, traceback
            raise tool.CameraInputLeaseError("ReleaseMutex failed; process poisoned")

    monkeypatch.setattr(
        tool,
        "WindowsCameraInputLease",
        RacingReleaseFailingLease,
    )

    assert tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "lease-race-release",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
        ]
    ) == 2
    assert _FakeBackend.constructed == 0
    assert report_path.read_text(encoding="utf-8") == "winner-report"
    assert digest_path.read_text(encoding="ascii") == "winner-digest"


def test_canonical_production_gated_command_reports_selected_candidates(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _search_second_candidate_frames())
    command_args = [
        "--output",
        str(output),
        "--case-prefix",
        "production-gated",
        "--normalization-strategy",
        "varrock-east-production-gated-search-v1",
        "--settle",
        "0.001",
    ]

    assert tool.main(command_args) == 0

    payload = json.loads(
        (output / "reports" / "production-gated.camera.json").read_text()
    )
    evidence = payload["evidence"]
    assert payload["provenance"]["plan_id"] == (
        "varrock-east-production-gated-search-v1"
    )
    assert payload["provenance"]["plan_version"] == "1.0.0"
    assert evidence["normalization_strategy"]["id"] == (
        "varrock-east-production-gated-search-v1"
    )
    normalizations = [evidence["initial_normalization"]] + [
        trial["normalization"] for trial in evidence["trials"]
    ]
    assert len(normalizations) == 4
    assert all(
        normalization["selected_candidate_index_1_based"] == 2
        and len(normalization["attempts"]) == 2
        and normalization["attempts"][0]["production_gate_passed"] is False
        and normalization["attempts"][1]["production_gate_passed"] is True
        and all(
            attempt["counts_as_confirmation"] is False
            for attempt in normalization["attempts"]
        )
        for normalization in normalizations
    )
    assert all(
        len(trial["confirmations"]) == 2 for trial in evidence["trials"]
    )
    assert evidence["camera_protocol_passed"] is True


def test_report_records_definitive_confirmation_state_mismatch(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    frames = _passing_frames()
    mismatch = _reviewed_payload("lower-left-full-cycle-020")
    frames[5] = Frame.from_raw(
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


@pytest.mark.parametrize(
    ("directory", "filename"),
    [
        ("frames", "stale-before-normalization.raw"),
        ("previews", "stale-before-normalization.bmp"),
        ("drafts", "stale-before-normalization.json"),
    ],
)
def test_stale_case_artifact_stops_before_capture_focus_or_input(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory: str,
    filename: str,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    stale = output / directory / filename
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"prior-private-artifact")

    result = tool.main(
        [
            "--output",
            str(output),
            "--case-prefix",
            "stale",
            "--pitch-endpoint",
            "down",
            "--reset-zoom",
        ]
    )

    assert result == 2
    assert _FakeBackend.constructed == 0
    assert _FakeSource.last is None
    assert _FakeControl.last is None
    assert stale.read_bytes() == b"prior-private-artifact"
    assert not (
        output / "reservations" / "stale.camera-reservation.json"
    ).exists()


def test_case_prefix_is_permanently_single_use_after_attempt(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    arguments = [
        "--output",
        str(output),
        "--case-prefix",
        "single-use",
        "--pitch-endpoint",
        "down",
        "--reset-zoom",
        "--settle",
        "0.001",
    ]

    assert tool.main(arguments) == 0
    reservation = (
        output / "reservations" / "single-use.camera-reservation.json"
    )
    original_reservation = reservation.read_bytes()
    _FakeBackend.constructed = 0
    _FakeSource.last = None
    _FakeControl.last = None

    assert tool.main(arguments) == 2
    assert _FakeBackend.constructed == 0
    assert _FakeSource.last is None
    assert _FakeControl.last is None
    assert reservation.read_bytes() == original_reservation


def test_failed_attempt_permanently_consumes_case_prefix_before_capture(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "private"
    _install_main_fakes(tool, output, monkeypatch, _passing_frames())
    construction_attempts = 0

    def fail_backend(*, title_substring: str) -> None:
        nonlocal construction_attempts
        del title_substring
        construction_attempts += 1
        raise RuntimeError("capture backend construction failed")

    monkeypatch.setattr(tool, "WindowsCaptureBackend", fail_backend)
    arguments = [
        "--output",
        str(output),
        "--case-prefix",
        "failed-single-use",
        "--pitch-endpoint",
        "down",
        "--reset-zoom",
    ]

    assert tool.main(arguments) == 2
    reservation = (
        output / "reservations" / "failed-single-use.camera-reservation.json"
    )
    original_reservation = reservation.read_bytes()
    assert construction_attempts == 1

    assert tool.main(arguments) == 2
    assert construction_attempts == 1
    assert reservation.read_bytes() == original_reservation


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
    _FakeInputLease.active = False
    _FakeInputLease.events = None
    monkeypatch.setattr(tool, "_resolve_private_output_root", lambda _path: output)
    monkeypatch.setattr(tool, "_git_state", lambda: ("a" * 40, clean))
    monkeypatch.setattr(tool, "WindowsCaptureBackend", _FakeBackend)
    monkeypatch.setattr(tool, "CaptureSource", _FakeSource)
    monkeypatch.setattr(tool, "WindowsCameraControl", _FakeControl)
    monkeypatch.setattr(tool, "WindowsCameraInputLease", _FakeInputLease)
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
    frames.append(
        Frame.from_raw(
            RawFrame(reviewed, 1005, 1078, PixelFormat.BGRA8888),
            frame_id=frame_id,
            captured_monotonic_s=float(frame_id),
        )
    )
    frame_id += 1
    for _trial in range(3):
        for payload in (reviewed, unsupported, reviewed, reviewed, reviewed):
            frames.append(
                Frame.from_raw(
                    RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
                    frame_id=frame_id,
                    captured_monotonic_s=float(frame_id),
                )
            )
            frame_id += 1
    return frames


def _search_second_candidate_frames() -> list[Frame]:
    reviewed = _reviewed_payload("available-01")
    unsupported = bytes(len(reviewed))
    payloads = [unsupported, unsupported, reviewed]
    for _trial in range(3):
        payloads.extend(
            (reviewed, unsupported, unsupported, reviewed, reviewed, reviewed)
        )
    return [
        Frame.from_raw(
            RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
            frame_id=index,
            captured_monotonic_s=float(index),
        )
        for index, payload in enumerate(payloads, start=1)
    ]


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
