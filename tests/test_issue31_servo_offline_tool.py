"""Deterministic read-only contract tests for the Issue #31 offline proof tool."""

from __future__ import annotations

import ast
import gzip
import hashlib
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from mining_automation.capture import Frame, RawFrame
from mining_automation.perception import (
    RESOURCE_PROFILE_SCHEMA_VERSION,
    ResourceVisualState,
    WideLandmarkSearch,
    WideRegistrationDiagnosis,
    WideSceneRegistrationAnalysis,
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation.camera_evaluation import (
    CameraEvaluation,
    CameraLandmarkEvaluation,
    CameraResourceEvaluation,
)
from mining_automation.validation.camera_guidance import (
    CAMERA_GUIDANCE_ID,
    CAMERA_GUIDANCE_VERSION,
    CameraGuidanceAxis,
    CameraGuidanceDirection,
    CameraGuidanceDisposition,
    CameraGuidanceReason,
    CameraSimilarityFit,
    WorldCameraGuidance,
)
from mining_automation.validation.client_readiness import (
    CLIENT_INPUT_READINESS_ID,
    CLIENT_INPUT_READINESS_VERSION,
    ClientInputReadiness,
    ClientReadinessAnchorEvaluation,
    ClientReadinessAnchorPolicy,
    ClientReadinessReason,
)

_FRAME_BYTES = 1005 * 1078 * 4
_TOOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "analyze_issue31_servo_offline.py"
)


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "analyze_issue31_servo_offline",
        _TOOL_PATH,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> ModuleType:
    return _load_tool()


def _payload(marker: int) -> bytes:
    return bytes((marker, 0, 0, 255)) + bytes(_FRAME_BYTES - 4)


def _write_dataset(root: Path) -> tuple[Path, Path, Path]:
    profile = load_varrock_east_iron_profile()
    fixture_root = root / "fixtures"
    fixture_payload = fixture_root / "frames" / "reviewed.raw.gz"
    fixture_payload.parent.mkdir(parents=True)
    fixture_payload.write_bytes(gzip.compress(_payload(1), mtime=0))
    manifest = {
        "schema_version": 1,
        "dataset_id": "reviewed-supported",
        "cases": [
            {
                "case_id": "reviewed",
                "frame": {
                    "path": "frames/reviewed.raw",
                    "width": profile.frame_width,
                    "height": profile.frame_height,
                    "pixel_format": profile.pixel_format.value,
                },
                "expected_observations": [
                    {
                        "kind": "resource.available",
                        "label": profile.ore_label,
                        "region": list(candidate.region),
                    }
                    for candidate in profile.candidates
                ],
            }
        ],
    }
    manifest_path = fixture_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    external_root = root / "external"
    external_root.mkdir()
    (external_root / "drift.raw").write_bytes(_payload(2))
    risky_path = root / "risky.raw.gz"
    risky_path.write_bytes(gzip.compress(_payload(3), mtime=0))
    return manifest_path, fixture_root, risky_path


def _fake_readiness(frame: Frame) -> ClientInputReadiness:
    ready = frame.payload[0] != 2
    policy = ClientReadinessAnchorPolicy(
        "test-chrome",
        (0, 34, 2, 2),
        minimum_edge_density=0.1,
        maximum_dark_fraction=0.5,
    )
    anchor = ClientReadinessAnchorEvaluation(
        policy=policy,
        luma_stddev=12.5,
        edge_density=0.25 if ready else 0.0,
        dark_fraction=0.1 if ready else 1.0,
        matched=ready,
    )
    return ClientInputReadiness(
        evaluator_id=CLIENT_INPUT_READINESS_ID,
        evaluator_version=CLIENT_INPUT_READINESS_VERSION,
        reason=(
            ClientReadinessReason.READY
            if ready
            else ClientReadinessReason.GAMEPLAY_CHROME_MISMATCH
        ),
        detail="test readiness evidence",
        anchors=(anchor,),
        safe_to_attempt_camera_input=ready,
    )


def _fake_production(frame: Frame) -> CameraEvaluation:
    profile = load_varrock_east_iron_profile()
    marker = frame.payload[0]
    if marker == 1:
        states = tuple(ResourceVisualState.AVAILABLE for _ in profile.candidates)
        matched_count = 6
    elif marker == 2:
        states = tuple(ResourceVisualState.UNCERTAIN for _ in profile.candidates)
        matched_count = 0
    else:
        states = (
            ResourceVisualState.AVAILABLE,
            *(ResourceVisualState.UNCERTAIN for _ in profile.candidates[1:]),
        )
        matched_count = 5
    landmarks = tuple(
        CameraLandmarkEvaluation(
            landmark_id=landmark.landmark_id,
            distance=landmark.maximum_distance / 2.0,
            threshold=landmark.maximum_distance,
            matched=index < matched_count,
            zone=landmark.macro_zone,
        )
        for index, landmark in enumerate(profile.scene_landmarks)
    )
    resources = tuple(
        CameraResourceEvaluation(
            resource_id=candidate.resource_id,
            state=state,
            confidence=0.9,
        )
        for candidate, state in zip(profile.candidates, states, strict=True)
    )
    zones = tuple(
        zone
        for zone in MacroZone
        if any(item.matched and item.zone is zone for item in landmarks)
    )
    definitive_ids = tuple(
        resource.resource_id for resource in resources if resource.definitive
    )
    passed = marker == 1
    return CameraEvaluation(
        detector_id="profiled-resource:varrock-east-iron-v1",
        detector_version="2.1.0",
        profile_id=profile.profile_id,
        profile_schema_version=RESOURCE_PROFILE_SCHEMA_VERSION,
        profile_frame_width=profile.frame_width,
        profile_frame_height=profile.frame_height,
        profile_pixel_format=profile.pixel_format,
        frame_geometry_supported=True,
        landmarks=landmarks,
        matched_landmark_count=matched_count,
        required_landmark_count=6,
        required_landmark_matches=5,
        matched_zones=zones,
        required_matched_zones=3,
        scene_reason="scene_validated" if marker != 2 else "insufficient_landmark_quorum",
        scene_validated=marker != 2,
        resource_states=resources,
        definitive_target_ids=definitive_ids,
        passed=passed,
    )


def _fake_guidance(frame: Frame) -> WorldCameraGuidance:
    profile = load_varrock_east_iron_profile()
    marker = frame.payload[0]
    matched_count = 0 if marker == 2 else 6
    searches = tuple(
        WideLandmarkSearch(
            landmark_id=landmark.landmark_id,
            offset_x=index,
            offset_y=-index,
            distance=landmark.maximum_distance / 2.0,
            maximum_distance=landmark.maximum_distance,
            matched=index < matched_count,
            zone=landmark.macro_zone,
            searched_offsets=25,
        )
        for index, landmark in enumerate(profile.scene_landmarks)
    )
    analysis = WideSceneRegistrationAnalysis(
        landmarks=searches,
        best_shared=None,
        diagnosis=(
            WideRegistrationDiagnosis.INSUFFICIENT_REGISTRATION_EVIDENCE
            if marker == 2
            else WideRegistrationDiagnosis.CAMERA_TRANSFORM_NOT_TRANSLATION
        ),
        detail="deterministic test guidance evidence",
        search_radius=96,
        coarse_step=4,
        refinement_radius=3,
    )
    fit = None
    if marker != 2:
        fit = CameraSimilarityFit(
            scale=1.05 if marker == 3 else 1.0,
            rotation_degrees=0.0,
            centre_shift_x=0.0,
            centre_shift_y=0.0,
            rms_residual_px=0.5,
            maximum_residual_px=1.0,
            landmark_count=6,
            matched_zones=analysis.matched_zones,
        )
    actionable = marker == 3
    return WorldCameraGuidance(
        selector_id=CAMERA_GUIDANCE_ID,
        selector_version=CAMERA_GUIDANCE_VERSION,
        disposition=(
            CameraGuidanceDisposition.ACTIONABLE
            if actionable
            else CameraGuidanceDisposition.INSUFFICIENT_GUIDANCE
        ),
        reason=(
            CameraGuidanceReason.ZOOM_SCALE_HIGH
            if actionable
            else (
                CameraGuidanceReason.INSUFFICIENT_DISTRIBUTED_LANDMARKS
                if marker == 2
                else CameraGuidanceReason.WITHIN_DEADBAND
            )
        ),
        detail="diagnostic only",
        axis=CameraGuidanceAxis.ZOOM if actionable else None,
        direction=CameraGuidanceDirection.NEGATIVE if actionable else None,
        fit=fit,
        analysis=analysis,
        excluded_regions=varrock_east_iron_scene_excluded_regions(profile),
    )


def _install_fake_evaluators(tool: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "evaluate_client_input_readiness", _fake_readiness)
    monkeypatch.setattr(tool, "evaluate_varrock_east_camera", _fake_production)
    monkeypatch.setattr(tool, "evaluate_varrock_east_camera_guidance", _fake_guidance)
    snapshot = tool.GitSnapshot("a" * 40, True)
    monkeypatch.setattr(tool, "_capture_git_snapshot", lambda: snapshot)


def _analyze(
    tool: ModuleType,
    manifest: Path,
    fixture_root: Path,
    risky_path: Path,
    *,
    reverse_groups: bool = False,
) -> dict[str, object]:
    drift = risky_path.parent / "external"
    groups = [f"drift={drift}", f"risky={risky_path}"]
    if reverse_groups:
        groups.reverse()
    return tool.analyze_inputs(
        manifest_path=manifest,
        fixture_root=fixture_root,
        raw_groups=groups,
        raw_expectations=["drift=fail-closed"],
        raw_readiness_expectations=["drift=stop"],
        raw_required_counts=["drift=1"],
        command_argv=(
            "tools/analyze_issue31_servo_offline.py",
            "--fixture-manifest",
            "fixture-manifest.json",
            "--frames",
            "drift=drift",
            "--expect",
            "drift=fail-closed",
            "--expect-readiness",
            "drift=stop",
            "--require-count",
            "drift=1",
        ),
    )


def test_canonical_bytes_sidecar_and_input_order_are_deterministic(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, fixture_root, risky_path = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)

    first = _analyze(tool, manifest, fixture_root, risky_path)
    second = _analyze(
        tool,
        manifest,
        fixture_root,
        risky_path,
        reverse_groups=True,
    )
    first_bytes = tool.canonical_report_bytes(first)
    second_bytes = tool.canonical_report_bytes(second)

    assert first_bytes == second_bytes
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_digest = tool._write_exclusive_report(first_path, first_bytes)
    second_digest = tool._write_exclusive_report(second_path, second_bytes)
    assert first_digest == second_digest == hashlib.sha256(first_bytes).hexdigest()
    assert first_path.read_bytes() == second_path.read_bytes()
    assert Path(f"{first_path}.sha256").read_text(encoding="ascii") == f"{first_digest}\n"
    with pytest.raises(FileExistsError):
        tool._write_exclusive_report(first_path, first_bytes)


def test_report_preserves_authority_separation_and_distributed_evidence(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, fixture_root, risky_path = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)

    report = _analyze(tool, manifest, fixture_root, risky_path)
    frames = report["frames"]
    assert isinstance(frames, list)
    for frame in frames:
        assert frame["readiness"]["acceptance_authority"] is False
        assert frame["readiness"]["can_accept"] is False
        assert frame["production"]["acceptance_authority"] is True
        assert frame["guidance"]["acceptance_authority"] is False
        assert frame["guidance"]["can_accept"] is False
        assert frame["guidance"]["can_validate_scene"] is False
        assert frame["guidance"]["can_expose_resources"] is False
        assert frame["guidance"]["distributed_evidence"] is not None
        assert frame["guidance"]["evidence_policy"] == {
            "candidate_and_fixed_ui_excluded": True,
            "excluded_regions": [
                list(region)
                for region in varrock_east_iron_scene_excluded_regions(
                    load_varrock_east_iron_profile()
                )
            ],
            "independent_local_minima_cannot_accept": True,
            "world_only": True,
        }
    assert report["authority"] == {
        "acceptance_path": "unchanged_production_camera_evaluation",
        "arm_guard_can_accept": False,
        "diagnostics_can_expose_resources": False,
        "guidance_can_accept": False,
        "production_acceptance_only": True,
        "readiness_can_accept": False,
        "invariants_passed": True,
    }


def test_aggregate_confusion_counts_pass_fail_closed_and_partial_exposure(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, fixture_root, risky_path = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)

    report = _analyze(tool, manifest, fixture_root, risky_path)
    aggregate = report["aggregate"]

    assert report["overall_passed"] is False
    assert report["proof"]["complete"] is False
    assert report["proof"]["eligible"] is False
    assert aggregate["frames_total"] == 3
    assert aggregate["expectations"] == {
        "checked": 2,
        "matched": 2,
        "mismatched": 0,
        "unlabeled": 1,
    }
    assert aggregate["confusion"] == {
        "expected_fail-closed__actual_fail-closed": 1,
        "expected_pass__actual_pass": 1,
        "expected_unlabeled__actual_reject-not-fail-closed": 1,
    }
    assert aggregate["production"] == {
        "actual_outcomes": {
            "fail-closed": 1,
            "pass": 1,
            "reject-not-fail-closed": 1,
        },
        "definitive_target_count": 5,
        "frames_with_definitive_targets": 2,
        "resource_expectations_checked": 1,
        "resource_expectations_matched": 1,
        "resource_expectations_mismatched": 0,
    }
    assert aggregate["guidance"]["actionable"] == 1
    assert aggregate["guidance"]["insufficient"] == 2


def test_fail_closed_requires_scene_rejection_and_all_uncertain(tool: ModuleType) -> None:
    frame = Frame.from_raw(
        RawFrame(_payload(2), 1005, 1078),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    rejected = _fake_production(frame)

    assert tool._actual_outcome(rejected).value == "fail-closed"
    assert (
        tool._actual_outcome(
            replace(rejected, scene_validated=True, scene_reason="scene_validated")
        ).value
        == "reject-not-fail-closed"
    )


def test_report_binds_exact_git_command_and_frozen_policy(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, fixture_root, risky_path = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)
    command = ("offline-proof", "--require-count", "drift=1")

    report = tool.analyze_inputs(
        manifest_path=manifest,
        fixture_root=fixture_root,
        raw_groups=[f"drift={risky_path.parent / 'external'}"],
        raw_expectations=["drift=fail-closed"],
        raw_readiness_expectations=["drift=stop"],
        raw_required_counts=["drift=1"],
        command_argv=command,
    )

    assert report["report_schema_version"] == 2
    assert report["tool"]["version"] == "1.1.0"
    assert report["overall_passed"] is False
    assert report["proof"] == {
        "authority_invariants_passed": True,
        "complete": False,
        "count_gate_passed": True,
        "eligible": False,
        "git_provenance_eligible": True,
        "production_expectations_passed": True,
        "readiness_expectations_passed": True,
    }
    assert report["provenance"]["command_argv"] == list(command)
    assert report["provenance"]["git_before"] == {
        "head_sha": "a" * 40,
        "tracked_worktree_clean": True,
    }
    assert report["provenance"]["git_after"] == {
        "head_sha": "a" * 40,
        "tracked_worktree_clean": True,
    }
    assert report["configuration"]["guidance"]["required_landmarks"] == 5
    assert report["configuration"]["guidance"]["required_zones"] == 3
    assert report["configuration"]["guidance"]["translation_vector_norm"] == "euclidean"
    assert (
        report["configuration"]["guidance"]["competing_axis_combination"]
        == "root_sum_square"
    )
    assert report["configuration"]["readiness"]["edge_luma_delta"] == 20
    assert report["configuration"]["readiness"]["dark_luma_maximum"] == 16
    assert report["configuration"]["servo"]["absolute_max_primitives"] == 8
    assert report["configuration"]["servo"]["absolute_max_arm_attempts"] == 16
    assert report["configuration"]["arm_guard"] == {
        "acceptance_authority": False,
        "can_accept": False,
        "can_expose_resources": False,
        "can_validate_scene": False,
        "decision_authority": "retain_or_discard_pending_guidance_only",
        "discard_requires_new_full_cycle_and_fresh_arm": True,
        "excluded_regions": [
            [263, 409, 20, 20],
            [295, 490, 20, 20],
            [405, 424, 20, 20],
            [590, 365, 20, 20],
            [0, 0, 1005, 34],
            [545, 34, 222, 220],
            [767, 34, 238, 816],
            [520, 500, 485, 350],
            [0, 850, 1005, 228],
            [588, 34, 40, 40],
            [628, 74, 139, 180],
            [0, 834, 520, 16],
        ],
        "freshness_policy": {
            "arm_frame_id_strictly_greater": True,
            "arm_timestamp_strictly_greater": True,
            "dedicated_capture_after_guidance": True,
        },
        "guard_id": "issue31-world-only-arm-guard",
        "guard_version": "1.0.0",
        "material_channel_delta": 24,
        "maximum_changed_pixel_fraction_exclusive": 0.08,
        "maximum_mean_channel_delta_exclusive": 4.0,
        "minimum_region_coverage": 0.75,
        "required_stable_landmarks": 6,
        "required_stable_zones": 3,
        "single_outlier_discards": True,
        "structural_regions": [
            {
                "landmark_id": "west-ridge",
                "region": [6, 376, 48, 48],
                "zone": "north_west",
            },
            {
                "landmark_id": "west-lower-ridge",
                "region": [6, 448, 48, 48],
                "zone": "north_west",
            },
            {
                "landmark_id": "south-path",
                "region": [258, 784, 48, 48],
                "zone": "south_west",
            },
            {
                "landmark_id": "south-central-edge",
                "region": [426, 736, 48, 48],
                "zone": "south_west",
            },
            {
                "landmark_id": "north-east-wall",
                "region": [689, 299, 48, 48],
                "zone": "north_east",
            },
            {
                "landmark_id": "east-bank-edge",
                "region": [678, 448, 48, 48],
                "zone": "north_east",
            },
        ],
        "threshold_equality_discards": True,
    }
    assert report["profile"]["arm_guard_id"] == "issue31-world-only-arm-guard"
    assert report["profile"]["arm_guard_version"] == "1.0.0"


def test_exact_count_gate_distinguishes_35_from_36_and_blocks_mismatch(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert tool._required_count_matches(36, 36) is True
    assert tool._required_count_matches(36, 35) is False
    assert tool._required_count_matches(None, 35) is None
    canonical = tool.GroupSpec(
        "drift",
        Path("drift"),
        tool.ExpectedOutcome.FAIL_CLOSED,
        tool.ExpectedReadiness.READY,
        36,
    )
    declared_35 = tool.GroupSpec(
        "drift",
        Path("drift"),
        tool.ExpectedOutcome.FAIL_CLOSED,
        tool.ExpectedReadiness.READY,
        35,
    )
    assert tool._canonical_drift_gate(((canonical, (None,) * 36),))["passed"] is True
    assert tool._canonical_drift_gate(((canonical, (None,) * 35),))["passed"] is False
    assert tool._canonical_drift_gate(((declared_35, (None,) * 35),))["passed"] is False
    assert tool._canonical_drift_gate(())["passed"] is False
    manifest, fixture_root, risky_path = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)

    report = tool.analyze_inputs(
        manifest_path=manifest,
        fixture_root=fixture_root,
        raw_groups=[f"drift={risky_path.parent / 'external'}"],
        raw_expectations=["drift=fail-closed"],
        raw_readiness_expectations=["drift=stop"],
        raw_required_counts=["drift=2"],
        command_argv=("offline-proof",),
    )

    assert report["input_groups"][1]["frames_discovered"] == 1
    assert report["input_groups"][1]["required_count"] == 2
    assert report["input_groups"][1]["count_matches"] is False
    assert report["proof"]["complete"] is False
    assert report["proof"]["count_gate_passed"] is False
    assert report["overall_passed"] is False


def test_readiness_mismatch_and_unlabeled_group_make_proof_ineligible(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, fixture_root, risky_path = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)
    drift = risky_path.parent / "external"

    mismatch = tool.analyze_inputs(
        manifest_path=manifest,
        fixture_root=fixture_root,
        raw_groups=[f"drift={drift}"],
        raw_expectations=["drift=fail-closed"],
        raw_readiness_expectations=["drift=ready"],
        raw_required_counts=["drift=1"],
        command_argv=("offline-proof",),
    )
    incomplete = tool.analyze_inputs(
        manifest_path=manifest,
        fixture_root=fixture_root,
        raw_groups=[f"risky={risky_path}"],
        raw_expectations=[],
        raw_readiness_expectations=[],
        raw_required_counts=[],
        command_argv=("offline-proof",),
    )

    assert mismatch["aggregate"]["readiness"]["expectations"]["mismatched"] == 1
    assert mismatch["proof"]["readiness_expectations_passed"] is False
    assert mismatch["overall_passed"] is False
    assert incomplete["proof"]["complete"] is False
    assert incomplete["proof"]["eligible"] is False
    assert incomplete["overall_passed"] is False


@pytest.mark.parametrize(
    "snapshots",
    [
        (("a" * 40, True), ("b" * 40, True)),
        (("a" * 40, False), ("a" * 40, True)),
    ],
)
def test_head_change_or_dirty_tracked_state_makes_proof_ineligible(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshots: tuple[tuple[str, bool], tuple[str, bool]],
) -> None:
    manifest, fixture_root, risky_path = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)
    values = iter(tool.GitSnapshot(head, clean) for head, clean in snapshots)
    monkeypatch.setattr(tool, "_capture_git_snapshot", lambda: next(values))

    report = tool.analyze_inputs(
        manifest_path=manifest,
        fixture_root=fixture_root,
        raw_groups=[f"drift={risky_path.parent / 'external'}"],
        raw_expectations=["drift=fail-closed"],
        raw_readiness_expectations=["drift=stop"],
        raw_required_counts=["drift=1"],
        command_argv=("offline-proof",),
    )

    assert report["proof"]["complete"] is False
    assert report["proof"]["git_provenance_eligible"] is False
    assert report["proof"]["eligible"] is False
    assert report["overall_passed"] is False


def test_tool_delegates_guidance_exclusions_and_directly_imports_no_input_adapter() -> None:
    source = _TOOL_PATH.read_text(encoding="utf-8")
    assert "evaluate_varrock_east_camera_guidance(frame)" in source
    assert "analyze_wide_scene_registration" not in source
    assert "windows_camera" not in source
    assert "CameraPlanRunner" not in source
    assert "SendInput" not in source
    imported_modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(
        module.endswith("windows_camera")
        or module.startswith("mining_automation.interaction")
        for module in imported_modules
    )


def test_duplicate_labels_and_paths_are_rejected_before_evaluation(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, fixture_root, risky_path = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)

    with pytest.raises(ValueError, match="duplicate --frames label"):
        tool.analyze_inputs(
            manifest_path=manifest,
            fixture_root=fixture_root,
            raw_groups=[f"same={risky_path}", f"same={risky_path}"],
            raw_expectations=[],
            raw_readiness_expectations=[],
            raw_required_counts=[],
            command_argv=("offline",),
        )
    with pytest.raises(ValueError, match="duplicate frame path across inputs"):
        tool.analyze_inputs(
            manifest_path=manifest,
            fixture_root=fixture_root,
            raw_groups=[f"first={risky_path}", f"second={risky_path}"],
            raw_expectations=[],
            raw_readiness_expectations=[],
            raw_required_counts=[],
            command_argv=("offline",),
        )
    fixture_payload = fixture_root / "frames" / "reviewed.raw.gz"
    with pytest.raises(ValueError, match="duplicate frame path across inputs"):
        tool.analyze_inputs(
            manifest_path=manifest,
            fixture_root=fixture_root,
            raw_groups=[f"fixture-alias={fixture_payload}"],
            raw_expectations=[],
            raw_readiness_expectations=[],
            raw_required_counts=[],
            command_argv=("offline",),
        )


def test_malformed_raw_frame_fails_without_report_or_pixel_copy(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, fixture_root, _ = _write_dataset(tmp_path)
    _install_fake_evaluators(tool, monkeypatch)
    malformed = tmp_path / "malformed.raw"
    malformed.write_bytes(b"not-a-frame")
    report_path = tmp_path / "must-not-exist.json"

    result = tool.main(
        [
            "--fixture-manifest",
            str(manifest),
            "--fixture-root",
            str(fixture_root),
            "--frames",
            f"bad={malformed}",
            "--report",
            str(report_path),
        ]
    )

    assert result == 2
    assert not report_path.exists()
    assert not Path(f"{report_path}.sha256").exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "external",
        "fixtures",
        "malformed.raw",
        "risky.raw.gz",
    ]


def test_partial_sidecar_failure_removes_both_outputs(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "evidence.json"
    digest_path = Path(f"{report_path}.sha256")
    original_open = Path.open

    class BrokenSidecar:
        def __enter__(self):  # type: ignore[no-untyped-def]
            self.output = original_open(digest_path, "xb")
            return self

        def write(self, payload: bytes) -> int:
            self.output.write(payload[:1])
            raise OSError("simulated sidecar write failure")

        def __exit__(self, *args: object) -> None:
            self.output.close()

    def failing_open(path: Path, mode: str = "r", *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if path == digest_path and mode == "xb":
            return BrokenSidecar()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    with pytest.raises(OSError, match="simulated sidecar"):
        tool._write_exclusive_report(report_path, b"{}\n")
    assert not report_path.exists()
    assert not digest_path.exists()


def test_report_target_must_be_outside_repo_or_git_ignored(
    tool: ModuleType,
    tmp_path: Path,
) -> None:
    repo_root = _TOOL_PATH.parents[1]

    with pytest.raises(ValueError, match="must both be Git-ignored"):
        tool._validate_report_target(
            repo_root / "issue31-proof-must-not-be-untracked.json",
            repo_root,
        )
    tool._validate_report_target(
        repo_root / "diagnostics" / "issue31-servo-proof.json",
        repo_root,
    )
    tool._validate_report_target(tmp_path / "outside-repo.json", repo_root)
