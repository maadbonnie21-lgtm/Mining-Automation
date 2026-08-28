#!/usr/bin/env python3
"""Diagnose Varrock East drift safety and restored-view reacquisition.

Development-only. The combined command analyzes the complete 36-frame local
drift set and one restored-view candidate in a single deterministic report:

    python tools/validate_varrock_east_drift.py --drift-frames diagnostics/issue18-drift-v3 --restored-frame diagnostics/varrock-east-iron/frames/reacquire-restored-20260818.raw

The production detector remains frozen-coordinate and fail-closed. Bounded
coherent and per-landmark searches are diagnostic evidence only; they never
turn a production UNCERTAIN result into a definitive target.

The historical ``--frames <dir> [--expect uncertain|definitive]`` interface is
retained for single-set validation.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import Frame, PixelFormat, RawFrame  # noqa: E402
from mining_automation.perception import (  # noqa: E402
    DEFAULT_DIAGNOSTIC_SEARCH_RADIUS,
    RESOURCE_PROFILE_SCHEMA_VERSION,
    ProfiledResourceDetector,
    ReacquisitionConclusion,
    ResourceDetectorProfile,
    ResourceVisualState,
    SceneFrameComparison,
    SceneOffsetEvaluation,
    SceneReacquisitionAnalysis,
    analyze_scene_reacquisition,
    build_varrock_east_iron_detector,
    classify_reacquisition,
    compare_scene_frames,
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)

_REPORT_SCHEMA_VERSION = 2
_EXPECTED_DRIFT_FRAME_COUNT = 36
_DEFINITIVE = {
    ResourceVisualState.AVAILABLE.value,
    ResourceVisualState.DEPLETED.value,
}
_KNOWN_STATES = _DEFINITIVE | {ResourceVisualState.UNCERTAIN.value}


@dataclass(frozen=True, slots=True)
class FrameCheck:
    path: Path
    expectation: str
    states: dict[str, str]
    definitive_targets: tuple[str, ...]
    detector_reason: str
    detector_landmark_distances: dict[str, float]
    analysis: SceneReacquisitionAnalysis
    passed: bool


@dataclass(frozen=True, slots=True)
class NamedComparison:
    path: Path
    comparison: SceneFrameComparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--drift-frames",
        "--frames",
        dest="drift_frames",
        type=Path,
        required=True,
        help=(
            "directory containing .raw or .raw.gz frames; --frames is the "
            "backward-compatible spelling"
        ),
    )
    parser.add_argument(
        "--restored-frame",
        type=Path,
        help=(
            "restored-view .raw or .raw.gz candidate; enables the combined "
            "36-frame drift and reacquisition diagnosis"
        ),
    )
    parser.add_argument(
        "--expect",
        choices=("uncertain", "definitive"),
        help=(
            "legacy single-set expectation (default: uncertain); combined mode "
            "always expects drift UNCERTAIN and restored DEFINITIVE"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional path to write the same evidence as deterministic JSON",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="legacy single-set mode only; stop after this many sorted frames",
    )
    return parser


def _error(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _discover_frames(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ValueError(f"not a directory: {directory}")
    paths = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and (path.suffix == ".raw" or path.name.endswith(".raw.gz"))
        ),
        key=lambda path: path.name,
    )
    if not paths:
        raise ValueError(f"no .raw or .raw.gz frames found in {directory}")
    return paths


def _load_frame(path: Path, profile: ResourceDetectorProfile) -> Frame:
    if not path.is_file():
        raise ValueError(f"not a frame file: {path}")
    try:
        encoded = path.read_bytes()
        payload = gzip.decompress(encoded) if path.name.endswith(".raw.gz") else encoded
    except (EOFError, OSError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    expected = (
        profile.frame_width
        * profile.frame_height
        * profile.pixel_format.bytes_per_pixel
    )
    if len(payload) != expected:
        raise ValueError(
            f"{path.name}: payload is {len(payload)} bytes, expected {expected} "
            f"for {profile.frame_width}x{profile.frame_height} "
            f"{profile.pixel_format.name}"
        )
    return Frame.from_raw(
        RawFrame(
            payload,
            profile.frame_width,
            profile.frame_height,
            profile.pixel_format,
        ),
        frame_id=1,
        captured_monotonic_s=0.0,
    )


def _extract_states(
    observations: tuple[Any, ...], expected_ids: tuple[str, ...]
) -> dict[str, str]:
    states: dict[str, str] = {}
    for observation in observations:
        resource_id = observation.evidence.get("resource_id")
        state = observation.evidence.get("state")
        if not isinstance(resource_id, str) or resource_id not in expected_ids:
            raise ValueError(f"detector returned an unknown resource_id: {resource_id!r}")
        if resource_id in states:
            raise ValueError(f"detector returned duplicate resource_id: {resource_id!r}")
        if not isinstance(state, str) or state not in _KNOWN_STATES:
            raise ValueError(
                f"detector returned invalid state for {resource_id!r}: {state!r}"
            )
        states[resource_id] = state
    if set(states) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(states))
        raise ValueError(f"detector omitted expected resources: {missing}")
    return {resource_id: states[resource_id] for resource_id in expected_ids}


def _check_frame(
    path: Path,
    frame: Frame,
    *,
    expectation: str,
    profile: ResourceDetectorProfile,
    detector: ProfiledResourceDetector,
) -> FrameCheck:
    analysis = analyze_scene_reacquisition(
        frame,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
        search_radius=DEFAULT_DIAGNOSTIC_SEARCH_RADIUS,
        excluded_regions=varrock_east_iron_scene_excluded_regions(profile),
    )
    expected_ids = tuple(candidate.resource_id for candidate in profile.candidates)
    observations = detector.detect(frame)
    states = _extract_states(observations, expected_ids)
    detector_reason = str(observations[0].evidence.get("reason", ""))
    raw_distances = observations[0].evidence.get("landmark_distances", {})
    detector_landmark_distances = (
        {str(key): float(value) for key, value in raw_distances.items()}
        if isinstance(raw_distances, dict)
        else {}
    )
    definitive_targets = tuple(
        resource_id for resource_id, state in states.items() if state in _DEFINITIVE
    )
    if expectation == "uncertain":
        passed = (
            all(state == ResourceVisualState.UNCERTAIN.value for state in states.values())
            and not analysis.frozen.verdict.validated
        )
    else:
        passed = (
            all(state in _DEFINITIVE for state in states.values())
            and analysis.frozen.verdict.validated
        )
    return FrameCheck(
        path=path,
        expectation=expectation,
        states=states,
        definitive_targets=definitive_targets,
        detector_reason=detector_reason,
        detector_landmark_distances=detector_landmark_distances,
        analysis=analysis,
        passed=passed,
    )


def _scene_dict(
    evaluation: SceneOffsetEvaluation,
    profile: ResourceDetectorProfile,
) -> dict[str, Any]:
    landmarks = {item.landmark_id: item for item in profile.scene_landmarks}
    rows: list[dict[str, Any]] = []
    for match in evaluation.verdict.matches:
        landmark = landmarks[match.landmark_id]
        x, y, width, height = landmark.region
        rows.append(
            {
                "landmark_id": match.landmark_id,
                "zone": match.zone.value,
                "frozen_region": [x, y, width, height],
                "observed_region": [
                    x + evaluation.offset_x,
                    y + evaluation.offset_y,
                    width,
                    height,
                ],
                "offset": [evaluation.offset_x, evaluation.offset_y],
                "distance": match.distance,
                "threshold": landmark.maximum_distance,
                "matched": match.matched,
            }
        )
    verdict = evaluation.verdict
    return {
        "offset": [evaluation.offset_x, evaluation.offset_y],
        "validated": verdict.validated,
        "reason": verdict.reason.value,
        "detail": verdict.detail,
        "matched_count": verdict.matched_count,
        "landmark_count": len(verdict.matches),
        "required_quorum": verdict.required_quorum,
        "matched_zones": [zone.value for zone in verdict.matched_zones],
        "required_zones": verdict.required_zones,
        "landmarks": rows,
    }


def _analysis_dict(
    analysis: SceneReacquisitionAnalysis,
    profile: ResourceDetectorProfile,
) -> dict[str, Any]:
    return {
        "search_radius_pixels": analysis.search_radius,
        "diagnostic_search_does_not_override_production": True,
        "frozen_coordinate_scene": _scene_dict(analysis.frozen, profile),
        "bounded_coherent_search": _scene_dict(analysis.best_coherent, profile),
        "independent_local_search_diagnostic_only": [
            {
                "landmark_id": item.landmark_id,
                "zone": item.zone.value,
                "offset": [item.offset_x, item.offset_y],
                "distance": item.distance,
                "threshold": item.maximum_distance,
                "matched": item.matched,
            }
            for item in analysis.local_best
        ],
    }


def _frame_dict(check: FrameCheck, profile: ResourceDetectorProfile) -> dict[str, Any]:
    return {
        "frame": check.path.name,
        "path": str(check.path),
        "expectation": check.expectation,
        "passed": check.passed,
        "states": check.states,
        "definitive_targets": list(check.definitive_targets),
        "detector_reason": check.detector_reason,
        "scene": _analysis_dict(check.analysis, profile),
    }


def _legacy_frame_dict(
    check: FrameCheck, profile: ResourceDetectorProfile
) -> dict[str, Any]:
    verdict = check.analysis.frozen.verdict
    return {
        "frame": check.path.name,
        "ok": check.passed,
        "states": check.states,
        "definitive_targets": list(check.definitive_targets),
        "landmarks_matched": verdict.matched_count,
        "reason": check.detector_reason,
        "landmark_distances": check.detector_landmark_distances,
        "scene": _analysis_dict(check.analysis, profile),
    }


def _comparison_dict(
    named: NamedComparison,
    profile: ResourceDetectorProfile,
) -> dict[str, Any]:
    return {
        "frame": named.path.name,
        "path": str(named.path),
        "normalized_distance": named.comparison.normalized_distance,
        "scene": _scene_dict(
            SceneOffsetEvaluation(0, 0, named.comparison.verdict),
            profile,
        ),
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"\nReport written: {path}")


def _print_scene_table(
    title: str,
    evaluation: SceneOffsetEvaluation,
    profile: ResourceDetectorProfile,
) -> None:
    print(f"  {title}")
    print(
        "    landmark                  zone         offset    distance  threshold  status"
    )
    landmarks = {item.landmark_id: item for item in profile.scene_landmarks}
    for match in evaluation.verdict.matches:
        threshold = landmarks[match.landmark_id].maximum_distance
        offset = f"({evaluation.offset_x:+d},{evaluation.offset_y:+d})"
        print(
            f"    {match.landmark_id:25s} {match.zone.value:12s} {offset:9s} "
            f"{match.distance:8.6f}  {threshold:9.6f}  "
            f"{'MATCH' if match.matched else 'FAIL'}"
        )
    verdict = evaluation.verdict
    zones = ", ".join(zone.value for zone in verdict.matched_zones) or "none"
    print(f"    matched zones: {zones}")
    print(f"    scene verdict: {'VALIDATED' if verdict.validated else 'REJECTED'}")
    print(f"    reason: {verdict.detail}")


def _print_local_table(analysis: SceneReacquisitionAnalysis) -> None:
    print("  INDEPENDENT LOCAL BESTS (diagnostic-only; never combined into a verdict)")
    print("    landmark                  zone         offset    distance  threshold  status")
    for item in analysis.local_best:
        offset = f"({item.offset_x:+d},{item.offset_y:+d})"
        print(
            f"    {item.landmark_id:25s} {item.zone.value:12s} {offset:9s} "
            f"{item.distance:8.6f}  {item.maximum_distance:9.6f}  "
            f"{'MATCH' if item.matched else 'FAIL'}"
        )
    zones = ", ".join(zone.value for zone in analysis.local_matched_zones) or "none"
    print(
        f"    local matches: {analysis.local_matched_count}/{len(analysis.local_best)}; "
        f"zones: {zones}"
    )


def _print_profile(
    profile: ResourceDetectorProfile,
    detector: ProfiledResourceDetector,
) -> None:
    print(f"PROFILE: {profile.profile_id} (schema v{RESOURCE_PROFILE_SCHEMA_VERSION})")
    print(
        f"DETECTOR: {detector.metadata.detector_id}@{detector.metadata.version}"
    )
    print(
        f"LANDMARK POLICY: {len(profile.scene_landmarks)} landmarks, quorum "
        f"{profile.minimum_landmark_quorum}, zones {profile.minimum_landmark_zones}"
    )
    print(
        "BOUNDED SEARCH: +/-"
        f"{DEFAULT_DIAGNOSTIC_SEARCH_RADIUS}px, diagnostic-only; production remains "
        "frozen-coordinate"
    )


def _print_frame(check: FrameCheck, profile: ResourceDetectorProfile) -> None:
    label = "PASS" if check.passed else "FAIL"
    print(f"\n{label} {check.path.name} | expected {check.expectation.upper()}")
    _print_scene_table("FROZEN COORDINATES (production)", check.analysis.frozen, profile)
    states = ", ".join(
        f"{resource_id}={state}" for resource_id, state in check.states.items()
    )
    print(f"    production states: {states}")


def _nearest_comparison(comparisons: list[NamedComparison]) -> NamedComparison:
    return min(
        comparisons,
        key=lambda item: (
            -int(item.comparison.verdict.validated),
            -item.comparison.verdict.matched_count,
            -len(item.comparison.verdict.matched_zones),
            item.comparison.normalized_distance,
            item.path.name,
        ),
    )


def _combined_report(
    *,
    profile: ResourceDetectorProfile,
    detector: ProfiledResourceDetector,
    drift_directory: Path,
    drift_checks: list[FrameCheck],
    drift_count_ok: bool,
    restored_check: FrameCheck,
    drift_comparisons: list[NamedComparison],
    nearest_drift: NamedComparison,
    conclusion: ReacquisitionConclusion,
) -> tuple[dict[str, Any], bool]:
    false_definitive = sum(len(item.definitive_targets) for item in drift_checks)
    coherent_false_support = sum(
        item.analysis.best_coherent.verdict.validated for item in drift_checks
    )
    known_drift_matches = sum(
        item.comparison.verdict.validated for item in drift_comparisons
    )
    drift_passed = drift_count_ok and all(item.passed for item in drift_checks)
    overall_passed = drift_passed and restored_check.passed
    report = {
        "report_schema_version": _REPORT_SCHEMA_VERSION,
        "mode": "combined_drift_and_reacquisition",
        "profile": {
            "profile_id": profile.profile_id,
            "profile_schema_version": RESOURCE_PROFILE_SCHEMA_VERSION,
            "detector_id": detector.metadata.detector_id,
            "detector_version": detector.metadata.version,
            "landmark_count": len(profile.scene_landmarks),
            "required_quorum": profile.minimum_landmark_quorum,
            "required_zones": profile.minimum_landmark_zones,
            "production_coordinate_mode": "frozen",
            "landmark_maximum_distances": {
                landmark.landmark_id: landmark.maximum_distance
                for landmark in profile.scene_landmarks
            },
            "diagnostic_search_radius_pixels": DEFAULT_DIAGNOSTIC_SEARCH_RADIUS,
        },
        "drift_set": {
            "directory": str(drift_directory),
            "expected_frames": _EXPECTED_DRIFT_FRAME_COUNT,
            "frames_total": len(drift_checks),
            "frame_count_matches": drift_count_ok,
            "frames_passed": sum(item.passed for item in drift_checks),
            "false_definitive_targets": false_definitive,
            "bounded_coherent_diagnostic_false_support": coherent_false_support,
            "passed": drift_passed,
            "results": [_frame_dict(item, profile) for item in drift_checks],
        },
        "restored_frame": {
            **_frame_dict(restored_check, profile),
            "known_drift_structural_matches": known_drift_matches,
            "known_drift_comparisons": [
                {
                    "frame": item.path.name,
                    "validated": item.comparison.verdict.validated,
                    "matched_count": item.comparison.verdict.matched_count,
                    "matched_zones": [
                        zone.value for zone in item.comparison.verdict.matched_zones
                    ],
                    "normalized_distance": item.comparison.normalized_distance,
                }
                for item in drift_comparisons
            ],
            "nearest_known_drift": _comparison_dict(nearest_drift, profile),
        },
        "diagnosis": {
            "code": conclusion.diagnosis.value,
            "detail": conclusion.detail,
        },
        "overall_passed": overall_passed,
    }
    return report, overall_passed


def _run_combined(
    args: argparse.Namespace,
    paths: list[Path],
    profile: ResourceDetectorProfile,
    detector: ProfiledResourceDetector,
) -> tuple[dict[str, Any], bool]:
    restored_path: Path = args.restored_frame
    restored_frame = _load_frame(restored_path, profile)
    drift_checks: list[FrameCheck] = []
    comparisons: list[NamedComparison] = []
    for path in paths:
        drift_frame = _load_frame(path, profile)
        drift_checks.append(
            _check_frame(
                path,
                drift_frame,
                expectation="uncertain",
                profile=profile,
                detector=detector,
            )
        )
        comparisons.append(
            NamedComparison(
                path=path,
                comparison=compare_scene_frames(
                    restored_frame,
                    drift_frame,
                    profile.scene_landmarks,
                    required_quorum=profile.minimum_landmark_quorum,
                    required_zones=profile.minimum_landmark_zones,
                    frame_width=profile.frame_width,
                    frame_height=profile.frame_height,
                ),
            )
        )

    restored_check = _check_frame(
        restored_path,
        restored_frame,
        expectation="definitive",
        profile=profile,
        detector=detector,
    )
    nearest_drift = _nearest_comparison(comparisons)
    count_ok = len(paths) == _EXPECTED_DRIFT_FRAME_COUNT
    coherent_false_support = sum(
        item.analysis.best_coherent.verdict.validated for item in drift_checks
    )
    conclusion = classify_reacquisition(
        restored_check.analysis,
        matching_drift=nearest_drift.comparison,
        matching_drift_label=nearest_drift.path.name,
        bounded_drift_false_support_count=coherent_false_support,
        bounded_drift_set_complete=count_ok,
    )

    _print_profile(profile, detector)
    print(
        f"\nDRIFT SET: {len(paths)} frame(s) from {args.drift_frames}; "
        f"required count {_EXPECTED_DRIFT_FRAME_COUNT}"
    )
    for check in drift_checks:
        _print_frame(check, profile)
    false_definitive = sum(len(item.definitive_targets) for item in drift_checks)
    drift_passed = count_ok and all(item.passed for item in drift_checks)
    print(
        f"\nDRIFT-SET RESULT: {'PASS' if drift_passed else 'FAIL'} | "
        f"{sum(item.passed for item in drift_checks)}/{len(drift_checks)} frames "
        f"UNCERTAIN | false definitive targets {false_definitive} | "
        f"bounded-search supported views {coherent_false_support}"
    )

    print(f"\nRESTORED FRAME: {restored_path}")
    _print_frame(restored_check, profile)
    _print_scene_table(
        "BEST COHERENT OFFSET (diagnostic-only)",
        restored_check.analysis.best_coherent,
        profile,
    )
    _print_local_table(restored_check.analysis)
    _print_scene_table(
        f"NEAREST KNOWN DRIFT VIEW ({nearest_drift.path.name})",
        SceneOffsetEvaluation(0, 0, nearest_drift.comparison.verdict),
        profile,
    )
    print(
        "    normalized structural distance: "
        f"{nearest_drift.comparison.normalized_distance:.6f}"
    )
    known_drift_matches = sum(
        item.comparison.verdict.validated for item in comparisons
    )
    print(
        f"    restored-vs-drift structural matches: "
        f"{known_drift_matches}/{len(comparisons)}"
    )
    print(f"\nRESTORED-FRAME RESULT: {'PASS' if restored_check.passed else 'FAIL'}")
    print(f"DIAGNOSIS: {conclusion.diagnosis.value}")
    print(f"EVIDENCE: {conclusion.detail}")
    if conclusion.diagnosis.value == "camera_not_actually_restored":
        print(
            "NEXT EVIDENCE: capture one fresh frame from the reviewed supported "
            "view; do not tune landmark thresholds, quorum, or zones for this frame."
        )

    return _combined_report(
        profile=profile,
        detector=detector,
        drift_directory=args.drift_frames,
        drift_checks=drift_checks,
        drift_count_ok=count_ok,
        restored_check=restored_check,
        drift_comparisons=comparisons,
        nearest_drift=nearest_drift,
        conclusion=conclusion,
    )


def _run_legacy(
    args: argparse.Namespace,
    paths: list[Path],
    profile: ResourceDetectorProfile,
    detector: ProfiledResourceDetector,
) -> tuple[dict[str, Any], bool]:
    expectation = args.expect or "uncertain"
    checks = [
        _check_frame(
            path,
            _load_frame(path, profile),
            expectation=expectation,
            profile=profile,
            detector=detector,
        )
        for path in paths
    ]
    _print_profile(profile, detector)
    print(f"\nLEGACY FRAME SET: expected {expectation.upper()}")
    for check in checks:
        _print_frame(check, profile)
    false_definitive = (
        sum(len(item.definitive_targets) for item in checks)
        if expectation == "uncertain"
        else 0
    )
    passed = all(item.passed for item in checks)
    print(
        f"\nFRAME-SET RESULT: {'PASS' if passed else 'FAIL'} | "
        f"{sum(item.passed for item in checks)}/{len(checks)} frames | "
        f"false definitive targets {false_definitive}"
    )
    return (
        {
            "report_schema_version": _REPORT_SCHEMA_VERSION,
            "mode": "legacy_single_set",
            "profile_id": profile.profile_id,
            "detector_id": detector.metadata.detector_id,
            "detector_version": detector.metadata.version,
            "schema_version": RESOURCE_PROFILE_SCHEMA_VERSION,
            "profile_schema_version": RESOURCE_PROFILE_SCHEMA_VERSION,
            "expectation": expectation,
            "frames_total": len(checks),
            "frames_passed": sum(item.passed for item in checks),
            "false_definitive_targets": false_definitive,
            "passed": passed,
            "results": [_legacy_frame_dict(item, profile) for item in checks],
        },
        passed,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    combined = args.restored_frame is not None
    if args.limit is not None and args.limit <= 0:
        return _error("--limit must be a positive integer")
    if combined and args.limit is not None:
        return _error("--limit is not allowed in combined mode; all 36 drift frames are required")
    if combined and args.expect == "definitive":
        return _error(
            "--expect definitive conflicts with combined mode; the drift set must be UNCERTAIN"
        )

    try:
        paths = _discover_frames(args.drift_frames)
        if not combined and args.limit is not None:
            paths = paths[: args.limit]
        profile = load_varrock_east_iron_profile()
        if profile.pixel_format is not PixelFormat.BGRA8888:
            raise ValueError("Varrock East diagnostic expects the reviewed BGRA8888 profile")
        if not profile.scene_landmarks:
            raise ValueError(
                "profile has no structural landmarks; schema-v2 anchor behavior remains "
                "supported by the detector but cannot run this v3 scene diagnosis"
            )
        detector = build_varrock_east_iron_detector()
        if combined:
            report, passed = _run_combined(args, paths, profile, detector)
        else:
            report, passed = _run_legacy(args, paths, profile, detector)
    except (OSError, ValueError) as exc:
        return _error(f"validation input error: {exc}")

    print(f"\nOVERALL RESULT: {'PASS' if passed else 'FAIL'}")
    if args.report is not None:
        try:
            _write_report(args.report, report)
        except OSError as exc:
            return _error(f"cannot write report {args.report}: {exc}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
