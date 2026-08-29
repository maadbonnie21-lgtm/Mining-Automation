#!/usr/bin/env python3
"""Build deterministic read-only evidence for Issue #31 camera-servo inputs.

The tool reads reviewed replay fixtures plus labeled private/diagnostic raw
frames.  It evaluates three deliberately separate paths without instantiating
or executing any input adapter:

* fixed-client-chrome input readiness (veto only),
* the unchanged production camera/resource decision (only acceptance authority),
* world-only camera guidance (diagnostic direction only).

Raw pixels are never copied to an output directory or embedded in the report.
Only hashes, scalar measurements, typed outcomes, and aggregate counts are
written.  A report is created exclusively with an adjacent SHA-256 sidecar.

A proof-eligible run must include ``drift`` with exactly 36 discovered frames,
``--expect drift=fail-closed``, ``--expect-readiness drift=ready``, and
``--require-count drift=36``.  Every additional external label also requires
all three expectation/count declarations; otherwise it remains useful
observational evidence but makes the report ineligible as a complete proof.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.capture import (  # noqa: E402
    CaptureError,
    Frame,
    PixelFormat,
    RawFrame,
)
from mining_automation.perception import (  # noqa: E402
    MAXIMUM_WIDE_REGISTRATION_RADIUS,
    PerceptionError,
    ResourceVisualState,
    load_fixture_manifest,
    load_varrock_east_iron_profile,
    varrock_east_iron_scene_excluded_regions,
)
from mining_automation.perception.replay import FixtureCase  # noqa: E402
from mining_automation.validation.camera_evaluation import (  # noqa: E402
    CameraEvaluation,
    evaluate_varrock_east_camera,
)
from mining_automation.validation.camera_guidance import (  # noqa: E402
    _MAXIMUM_POINT_RESIDUAL_PX,
    _MAXIMUM_RMS_RESIDUAL_PX,
    _MINIMUM_LOG_SCALE_ERROR,
    _REQUIRED_LANDMARKS,
    _REQUIRED_ZONES,
    _ROTATION_SCORE_UNIT_DEGREES,
    _SCALE_DOMINANCE_RATIO,
    _TRANSLATION_SCORE_UNIT_PX,
    CAMERA_GUIDANCE_ID,
    CAMERA_GUIDANCE_VERSION,
    WorldCameraGuidance,
    evaluate_varrock_east_camera_guidance,
)
from mining_automation.validation.camera_servo import (  # noqa: E402
    ABSOLUTE_MAX_SERVO_ELAPSED_SECONDS,
    ABSOLUTE_MAX_SERVO_PRIMITIVES,
    DEFAULT_MAX_SERVO_ELAPSED_SECONDS,
    DEFAULT_MAX_SERVO_PRIMITIVES,
    MAXIMUM_CONSECUTIVE_STAGNANT_STEPS,
    MAXIMUM_SERVO_SETTLE_SECONDS,
    WORLD_EFFECT_DESCRIPTOR_EPSILON,
    WORLD_EFFECT_REQUIRED_LANDMARKS,
    WORLD_EFFECT_REQUIRED_ZONES,
    ZOOM_ERROR_PROGRESS_TOLERANCE,
)
from mining_automation.validation.client_readiness import (  # noqa: E402
    _DARK_LUMA_MAXIMUM,
    _EDGE_LUMA_DELTA,
    CLIENT_INPUT_READINESS_ID,
    CLIENT_INPUT_READINESS_VERSION,
    GAMEPLAY_CHROME_POLICIES,
    ClientInputReadiness,
    evaluate_client_input_readiness,
)

_REPORT_SCHEMA_VERSION = 1
_TOOL_ID = "issue31-servo-offline-proof"
_TOOL_VERSION = "1.0.0"
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_RAW_SUFFIXES = (".raw", ".raw.gz")

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class ExpectedOutcome(StrEnum):
    """Optional production truth attached to one frame group."""

    PASS = "pass"
    FAIL_CLOSED = "fail-closed"
    UNLABELED = "unlabeled"


class ActualOutcome(StrEnum):
    """Production result, separating a safe refusal from a partial exposure."""

    PASS = "pass"
    FAIL_CLOSED = "fail-closed"
    REJECT_NOT_FAIL_CLOSED = "reject-not-fail-closed"


class ExpectedReadiness(StrEnum):
    """Optional veto-only readiness truth attached to a frame group."""

    READY = "ready"
    STOP = "stop"
    UNLABELED = "unlabeled"


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    """Exact repository identity sampled around one read-only analysis."""

    head_sha: str
    tracked_worktree_clean: bool

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{40}", self.head_sha) is None:
            raise ValueError("Git HEAD must be a full lowercase 40-character SHA")
        if type(self.tracked_worktree_clean) is not bool:
            raise ValueError("tracked_worktree_clean must be a bool")


@dataclass(frozen=True, slots=True)
class GroupSpec:
    """One user-labeled raw-file source."""

    label: str
    path: Path
    expectation: ExpectedOutcome
    readiness_expectation: ExpectedReadiness
    required_count: int | None


@dataclass(frozen=True, slots=True)
class FrameSpec:
    """One discovered payload and its report-safe identity."""

    label: str
    source_kind: str
    case_id: str
    relative_path: str
    payload_path: Path
    expectation: ExpectedOutcome
    readiness_expectation: ExpectedReadiness
    expected_resource_states: tuple[tuple[str, ResourceVisualState], ...]


def build_parser() -> argparse.ArgumentParser:
    """Return the offline-only command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--fixture-manifest",
        required=True,
        type=Path,
        help="committed replay-schema-v1 manifest for reviewed supported fixtures",
    )
    parser.add_argument(
        "--fixture-root",
        required=True,
        type=Path,
        help="read-only root containing the manifest's .raw or .raw.gz payloads",
    )
    parser.add_argument(
        "--frames",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="labeled .raw/.raw.gz file or directory; repeat for each evidence set",
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="LABEL=pass|fail-closed",
        help=(
            "expected production result for a proof label; fail-closed also "
            "requires scene rejection, all resources UNCERTAIN, and zero "
            "definitive target IDs"
        ),
    )
    parser.add_argument(
        "--expect-readiness",
        action="append",
        default=[],
        metavar="LABEL=ready|stop",
        help="expected veto-only readiness result required for each proof label",
    )
    parser.add_argument(
        "--require-count",
        action="append",
        default=[],
        metavar="LABEL=N",
        help="exact frame count required for each proof label (drift must be 36)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="write canonical JSON plus <name>.sha256 exclusively; otherwise print JSON",
    )
    return parser


def _parse_assignment(value: str, *, option: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(f"{option} requires LABEL=VALUE, got {value!r}")
    label, raw_value = value.split("=", 1)
    if _LABEL.fullmatch(label) is None:
        raise ValueError(
            f"{option} label must match {_LABEL.pattern!r}, got {label!r}"
        )
    if not raw_value:
        raise ValueError(f"{option} value must not be empty for label {label!r}")
    return label, raw_value


def _parse_groups(
    raw_groups: list[str],
    raw_expectations: list[str],
    raw_readiness_expectations: list[str],
    raw_required_counts: list[str],
    *,
    reserved_label: str,
) -> tuple[GroupSpec, ...]:
    expectations: dict[str, ExpectedOutcome] = {}
    for item in raw_expectations:
        label, raw_value = _parse_assignment(item, option="--expect")
        if label in expectations:
            raise ValueError(f"duplicate --expect label: {label!r}")
        try:
            expectation = ExpectedOutcome(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"--expect for {label!r} must be 'pass' or 'fail-closed'"
            ) from exc
        if expectation is ExpectedOutcome.UNLABELED:  # pragma: no cover - enum guard
            raise ValueError("--expect cannot explicitly select 'unlabeled'")
        expectations[label] = expectation

    readiness_expectations: dict[str, ExpectedReadiness] = {}
    for item in raw_readiness_expectations:
        label, raw_value = _parse_assignment(item, option="--expect-readiness")
        if label in readiness_expectations:
            raise ValueError(f"duplicate --expect-readiness label: {label!r}")
        try:
            readiness_expectation = ExpectedReadiness(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"--expect-readiness for {label!r} must be 'ready' or 'stop'"
            ) from exc
        if (
            readiness_expectation is ExpectedReadiness.UNLABELED
        ):  # pragma: no cover - enum guard
            raise ValueError("--expect-readiness cannot explicitly select 'unlabeled'")
        readiness_expectations[label] = readiness_expectation

    required_counts: dict[str, int] = {}
    for item in raw_required_counts:
        label, raw_value = _parse_assignment(item, option="--require-count")
        if label in required_counts:
            raise ValueError(f"duplicate --require-count label: {label!r}")
        try:
            count = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"--require-count for {label!r} must be a positive integer"
            ) from exc
        if str(count) != raw_value or count <= 0:
            raise ValueError(
                f"--require-count for {label!r} must be a positive integer"
            )
        required_counts[label] = count

    paths: dict[str, Path] = {}
    for item in raw_groups:
        label, raw_path = _parse_assignment(item, option="--frames")
        if label == reserved_label:
            raise ValueError(
                f"--frames label {label!r} duplicates the fixture dataset label"
            )
        if label in paths:
            raise ValueError(f"duplicate --frames label: {label!r}")
        paths[label] = Path(raw_path)

    referenced_labels = set(expectations) | set(readiness_expectations) | set(required_counts)
    unknown = sorted(referenced_labels - set(paths))
    if unknown:
        raise ValueError("options name unknown --frames labels: " + ", ".join(unknown))
    return tuple(
        GroupSpec(
            label=label,
            path=paths[label],
            expectation=expectations.get(label, ExpectedOutcome.UNLABELED),
            readiness_expectation=readiness_expectations.get(
                label,
                ExpectedReadiness.UNLABELED,
            ),
            required_count=required_counts.get(label),
        )
        for label in sorted(paths)
    )


def _normal_path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve(strict=True)))
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot resolve input path {path}: {exc}") from exc


def _is_raw_path(path: Path) -> bool:
    return path.name.endswith(_RAW_SUFFIXES)


def _discover_external_frames(group: GroupSpec) -> tuple[FrameSpec, ...]:
    path = group.path
    discovered: tuple[tuple[str, Path], ...]
    if path.is_file():
        if not _is_raw_path(path):
            raise ValueError(f"labeled frame is not .raw or .raw.gz: {path}")
        discovered = ((path.name, path),)
    elif path.is_dir():
        discovered = tuple(
            (candidate.name, candidate)
            for candidate in sorted(path.iterdir(), key=lambda item: item.name)
            if candidate.is_file() and _is_raw_path(candidate)
        )
        if not discovered:
            raise ValueError(f"no .raw or .raw.gz frames found in {path}")
    else:
        raise ValueError(f"labeled frame path is not a file or directory: {path}")

    return tuple(
        FrameSpec(
            label=group.label,
            source_kind="labeled_input",
            case_id=_case_id_from_path(relative_path),
            relative_path=PurePosixPath(relative_path).as_posix(),
            payload_path=payload_path,
            expectation=group.expectation,
            readiness_expectation=group.readiness_expectation,
            expected_resource_states=(),
        )
        for relative_path, payload_path in discovered
    )


def _case_id_from_path(value: str) -> str:
    return value.removesuffix(".gz").removesuffix(".raw")


def _fixture_payload_path(root: Path, case: FixtureCase) -> Path:
    relative = Path(*PurePosixPath(case.frame.path).parts)
    raw = root / relative
    compressed = root / f"{case.frame.path}.gz"
    raw_exists = raw.is_file()
    compressed_exists = compressed.is_file()
    if raw_exists and compressed_exists:
        raise ValueError(
            f"fixture {case.case_id!r} has ambiguous raw and gzip payloads"
        )
    if raw_exists:
        candidate = raw
    elif compressed_exists:
        candidate = compressed
    else:
        raise ValueError(
            f"fixture {case.case_id!r} payload is missing: {case.frame.path}[.gz]"
        )
    root_resolved = root.resolve(strict=True)
    candidate_resolved = candidate.resolve(strict=True)
    if not candidate_resolved.is_relative_to(root_resolved):
        raise ValueError(f"fixture {case.case_id!r} payload escapes fixture root")
    return candidate


def _manifest_expected_states(
    case: FixtureCase,
    *,
    candidate_regions: dict[tuple[int, int, int, int], str],
) -> tuple[tuple[str, ResourceVisualState], ...]:
    states: dict[str, ResourceVisualState] = {}
    for expectation in case.expected_observations:
        if expectation.region is None or expectation.region not in candidate_regions:
            raise ValueError(
                f"fixture {case.case_id!r} has an expectation outside packaged candidates"
            )
        resource_id = candidate_regions[expectation.region]
        if resource_id in states:
            raise ValueError(
                f"fixture {case.case_id!r} repeats candidate {resource_id!r}"
            )
        prefix = "resource."
        if not expectation.kind.startswith(prefix):
            raise ValueError(
                f"fixture {case.case_id!r} has non-resource expectation "
                f"{expectation.kind!r}"
            )
        try:
            state = ResourceVisualState(expectation.kind.removeprefix(prefix))
        except ValueError as exc:
            raise ValueError(
                f"fixture {case.case_id!r} has unsupported resource expectation "
                f"{expectation.kind!r}"
            ) from exc
        states[resource_id] = state
    expected_ids = set(candidate_regions.values())
    if set(states) != expected_ids:
        missing = sorted(expected_ids - set(states))
        raise ValueError(
            f"fixture {case.case_id!r} omits packaged candidates: {missing}"
        )
    return tuple(sorted(states.items()))


def _fixture_frames(
    manifest_path: Path,
    fixture_root: Path,
) -> tuple[str, int, str, tuple[FrameSpec, ...]]:
    manifest = load_fixture_manifest(manifest_path)
    if not fixture_root.is_dir():
        raise ValueError(f"fixture root is not a directory: {fixture_root}")
    profile = load_varrock_east_iron_profile()
    candidate_regions = {
        candidate.region: candidate.resource_id for candidate in profile.candidates
    }
    frames: list[FrameSpec] = []
    for case in manifest.cases:
        if (
            case.frame.width != profile.frame_width
            or case.frame.height != profile.frame_height
            or case.frame.pixel_format is not profile.pixel_format
        ):
            raise ValueError(
                f"fixture {case.case_id!r} is not the reviewed "
                f"{profile.frame_width}x{profile.frame_height} "
                f"{profile.pixel_format.value} format"
            )
        frames.append(
            FrameSpec(
                label=manifest.dataset_id,
                source_kind="reviewed_fixture",
                case_id=case.case_id,
                relative_path=case.frame.path,
                payload_path=_fixture_payload_path(fixture_root, case),
                expectation=ExpectedOutcome.PASS,
                readiness_expectation=ExpectedReadiness.READY,
                expected_resource_states=_manifest_expected_states(
                    case,
                    candidate_regions=candidate_regions,
                ),
            )
        )
    if not frames:  # pragma: no cover - replay manifest contract is non-empty
        raise ValueError("fixture manifest must contain at least one reviewed case")
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return manifest.dataset_id, manifest.schema_version, manifest_sha, tuple(frames)


def _reject_duplicate_paths(frames: tuple[FrameSpec, ...]) -> None:
    seen: dict[str, FrameSpec] = {}
    for frame in frames:
        key = _normal_path_key(frame.payload_path)
        previous = seen.get(key)
        if previous is not None:
            raise ValueError(
                "duplicate frame path across inputs: "
                f"{previous.label}/{previous.relative_path} and "
                f"{frame.label}/{frame.relative_path}"
            )
        seen[key] = frame


def _read_frame(spec: FrameSpec, *, frame_id: int) -> tuple[Frame, str]:
    try:
        encoded = spec.payload_path.read_bytes()
        payload = gzip.decompress(encoded) if spec.payload_path.name.endswith(".gz") else encoded
    except (EOFError, OSError) as exc:
        raise ValueError(
            f"cannot decode frame {spec.label}/{spec.relative_path}: {exc}"
        ) from exc
    profile = load_varrock_east_iron_profile()
    expected_bytes = (
        profile.frame_width
        * profile.frame_height
        * profile.pixel_format.bytes_per_pixel
    )
    if len(payload) != expected_bytes:
        raise ValueError(
            f"malformed frame {spec.label}/{spec.relative_path}: "
            f"{len(payload)} bytes, expected {expected_bytes} for "
            f"{profile.frame_width}x{profile.frame_height} "
            f"{profile.pixel_format.value}"
        )
    frame = Frame.from_raw(
        RawFrame(
            payload=payload,
            width=profile.frame_width,
            height=profile.frame_height,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id - 1),
    )
    return frame, hashlib.sha256(payload).hexdigest()


def _readiness_dict(value: ClientInputReadiness) -> dict[str, JsonValue]:
    return {
        "acceptance_authority": False,
        "can_accept": value.can_accept,
        "can_expose_resources": value.can_expose_resources,
        "can_validate_scene": value.can_validate_scene,
        "detail": value.detail,
        "evaluator_id": value.evaluator_id,
        "evaluator_version": value.evaluator_version,
        "reason": value.reason.value,
        "safe_to_attempt_camera_input": value.safe_to_attempt_camera_input,
        "anchors": [
            {
                "anchor_id": anchor.policy.anchor_id,
                "region": list(anchor.policy.region),
                "thresholds": {
                    "maximum_dark_fraction": anchor.policy.maximum_dark_fraction,
                    "minimum_edge_density": anchor.policy.minimum_edge_density,
                    "minimum_luma_stddev": anchor.policy.minimum_luma_stddev,
                },
                "metrics": {
                    "dark_fraction": anchor.dark_fraction,
                    "edge_density": anchor.edge_density,
                    "luma_stddev": anchor.luma_stddev,
                },
                "matched": anchor.matched,
            }
            for anchor in value.anchors
        ],
    }


def _actual_outcome(value: CameraEvaluation) -> ActualOutcome:
    if value.passed:
        return ActualOutcome.PASS
    if (
        not value.scene_validated
        and not value.definitive_target_ids
        and bool(value.resource_states)
        and all(
            resource.state is ResourceVisualState.UNCERTAIN
            for resource in value.resource_states
        )
    ):
        return ActualOutcome.FAIL_CLOSED
    return ActualOutcome.REJECT_NOT_FAIL_CLOSED


def _production_dict(
    value: CameraEvaluation,
    expected_states: tuple[tuple[str, ResourceVisualState], ...],
) -> tuple[dict[str, JsonValue], bool | None, ActualOutcome]:
    observed_states = {resource.resource_id: resource.state for resource in value.resource_states}
    expected_state_match = (
        None
        if not expected_states
        else all(observed_states.get(resource_id) is state for resource_id, state in expected_states)
    )
    actual = _actual_outcome(value)
    return (
        {
            "acceptance_authority": True,
            "detector_id": value.detector_id,
            "detector_version": value.detector_version,
            "profile_id": value.profile_id,
            "profile_schema_version": value.profile_schema_version,
            "profile_geometry": {
                "height": value.profile_frame_height,
                "pixel_format": value.profile_pixel_format.value,
                "width": value.profile_frame_width,
            },
            "frame_geometry_supported": value.frame_geometry_supported,
            "passed": value.passed,
            "actual_outcome": actual.value,
            "scene": {
                "validated": value.scene_validated,
                "reason": value.scene_reason,
                "matched_landmark_count": value.matched_landmark_count,
                "required_landmark_count": value.required_landmark_count,
                "required_landmark_matches": value.required_landmark_matches,
                "matched_zones": [zone.value for zone in value.matched_zones],
                "required_matched_zones": value.required_matched_zones,
                "landmarks": [
                    {
                        "distance": landmark.distance,
                        "landmark_id": landmark.landmark_id,
                        "matched": landmark.matched,
                        "threshold": landmark.threshold,
                        "zone": landmark.zone.value,
                    }
                    for landmark in value.landmarks
                ],
            },
            "resources": [
                {
                    "confidence": resource.confidence,
                    "definitive": resource.definitive,
                    "resource_id": resource.resource_id,
                    "state": resource.state.value,
                }
                for resource in value.resource_states
            ],
            "definitive_target_ids": list(value.definitive_target_ids),
            "expected_resource_states": {
                resource_id: state.value for resource_id, state in expected_states
            },
            "resource_states_match_expectation": expected_state_match,
        },
        expected_state_match,
        actual,
    )


def _guidance_dict(value: WorldCameraGuidance) -> dict[str, JsonValue]:
    fit = value.fit
    analysis = value.analysis
    if fit is None:
        fit_dict: JsonValue = None
    else:
        fit_dict = {
            "centre_shift_x": fit.centre_shift_x,
            "centre_shift_y": fit.centre_shift_y,
            "landmark_count": fit.landmark_count,
            "matched_zones": [zone.value for zone in fit.matched_zones],
            "maximum_residual_px": fit.maximum_residual_px,
            "rms_residual_px": fit.rms_residual_px,
            "rotation_degrees": fit.rotation_degrees,
            "scale": fit.scale,
        }

    if analysis is None:
        analysis_dict: JsonValue = None
    else:
        shared = analysis.best_shared
        shared_dict: JsonValue
        if shared is None:
            shared_dict = None
        else:
            shared_dict = {
                "matched_count": shared.matched_count,
                "matched_zones": [zone.value for zone in shared.matched_zones],
                "normalized_distance_sum": shared.normalized_distance_sum,
                "offset": [shared.offset_x, shared.offset_y],
                "required_quorum": shared.required_quorum,
                "required_zones": shared.required_zones,
                "valid_landmark_count": shared.valid_landmark_count,
                "validated_diagnostic_only": shared.validated,
            }
        analysis_dict = {
            "best_shared": shared_dict,
            "coarse_step": analysis.coarse_step,
            "detail": analysis.detail,
            "diagnosis": analysis.diagnosis.value,
            "independent_landmark_searches": [
                {
                    "distance": landmark.distance,
                    "landmark_id": landmark.landmark_id,
                    "matched": landmark.matched,
                    "normalized_distance": landmark.normalized_distance,
                    "offset": [landmark.offset_x, landmark.offset_y],
                    "searched_offsets": landmark.searched_offsets,
                    "threshold": landmark.maximum_distance,
                    "zone": landmark.zone.value,
                }
                for landmark in analysis.landmarks
            ],
            "matched_count": analysis.matched_count,
            "matched_zones": [zone.value for zone in analysis.matched_zones],
            "refinement_radius": analysis.refinement_radius,
            "search_radius": analysis.search_radius,
        }
    return {
        "acceptance_authority": False,
        "can_accept": value.can_accept,
        "can_expose_resources": value.can_expose_resources,
        "can_validate_scene": value.can_validate_scene,
        "selector_id": value.selector_id,
        "selector_version": value.selector_version,
        "disposition": value.disposition.value,
        "reason": value.reason.value,
        "detail": value.detail,
        "axis": None if value.axis is None else value.axis.value,
        "direction": None if value.direction is None else value.direction.value,
        "fit": fit_dict,
        "distributed_evidence": analysis_dict,
        "evidence_policy": {
            "candidate_and_fixed_ui_excluded": True,
            "independent_local_minima_cannot_accept": True,
            "world_only": True,
            "excluded_regions": [list(region) for region in value.excluded_regions],
        },
    }


def _expectation_match(
    expected: ExpectedOutcome,
    actual: ActualOutcome,
    expected_state_match: bool | None,
) -> bool | None:
    if expected is ExpectedOutcome.UNLABELED:
        return None
    if expected is ExpectedOutcome.PASS:
        return actual is ActualOutcome.PASS and expected_state_match is not False
    return actual is ActualOutcome.FAIL_CLOSED


def _readiness_expectation_match(
    expected: ExpectedReadiness,
    readiness: ClientInputReadiness,
) -> bool | None:
    if expected is ExpectedReadiness.UNLABELED:
        return None
    if expected is ExpectedReadiness.READY:
        return readiness.safe_to_attempt_camera_input
    return not readiness.safe_to_attempt_camera_input


def _analyze_frame(spec: FrameSpec, *, frame_id: int) -> dict[str, JsonValue]:
    frame, raw_sha256 = _read_frame(spec, frame_id=frame_id)
    readiness = evaluate_client_input_readiness(frame)
    production = evaluate_varrock_east_camera(frame)
    guidance = evaluate_varrock_east_camera_guidance(frame)
    production_dict, state_match, actual = _production_dict(
        production,
        spec.expected_resource_states,
    )
    return {
        "case_id": spec.case_id,
        "expected_outcome": spec.expectation.value,
        "expectation_match": _expectation_match(spec.expectation, actual, state_match),
        "expected_readiness": spec.readiness_expectation.value,
        "readiness_expectation_match": _readiness_expectation_match(
            spec.readiness_expectation,
            readiness,
        ),
        "label": spec.label,
        "raw_sha256": raw_sha256,
        "readiness": _readiness_dict(readiness),
        "relative_path": spec.relative_path,
        "source_kind": spec.source_kind,
        "production": production_dict,
        "guidance": _guidance_dict(guidance),
    }


def _aggregate(frames: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    readiness_reasons: Counter[str] = Counter()
    guidance_reasons: Counter[str] = Counter()
    guidance_directions: Counter[str] = Counter()
    actual_outcomes: Counter[str] = Counter()
    confusion: Counter[str] = Counter()
    readiness_confusion: Counter[str] = Counter()
    labels: dict[str, Counter[str]] = {}
    readiness_ready = 0
    guidance_actionable = 0
    definitive_target_count = 0
    frames_with_definitive_targets = 0
    resource_expectations_checked = 0
    resource_expectations_matched = 0

    for frame in frames:
        readiness = frame["readiness"]
        production = frame["production"]
        guidance = frame["guidance"]
        assert isinstance(readiness, dict)
        assert isinstance(production, dict)
        assert isinstance(guidance, dict)
        label = str(frame["label"])
        label_counts = labels.setdefault(label, Counter())

        ready = bool(readiness["safe_to_attempt_camera_input"])
        readiness_ready += ready
        readiness_reasons[str(readiness["reason"])] += 1
        label_counts["readiness_ready" if ready else "readiness_stop"] += 1

        actual = str(production["actual_outcome"])
        actual_outcomes[actual] += 1
        label_counts[f"production_{actual}"] += 1
        targets = production["definitive_target_ids"]
        assert isinstance(targets, list)
        definitive_target_count += len(targets)
        frames_with_definitive_targets += bool(targets)
        state_match = production["resource_states_match_expectation"]
        if state_match is not None:
            resource_expectations_checked += 1
            resource_expectations_matched += bool(state_match)

        disposition = str(guidance["disposition"])
        actionable = disposition == "actionable"
        guidance_actionable += actionable
        guidance_reasons[str(guidance["reason"])] += 1
        label_counts["guidance_actionable" if actionable else "guidance_insufficient"] += 1
        if actionable:
            guidance_directions[f"{guidance['axis']}:{guidance['direction']}"] += 1

        expected = str(frame["expected_outcome"])
        confusion[f"expected_{expected}__actual_{actual}"] += 1
        expected_readiness = str(frame["expected_readiness"])
        actual_readiness = "ready" if ready else "stop"
        readiness_confusion[
            f"expected_{expected_readiness}__actual_{actual_readiness}"
        ] += 1

    total = len(frames)
    matched_expectations = sum(frame["expectation_match"] is True for frame in frames)
    mismatched_expectations = sum(frame["expectation_match"] is False for frame in frames)
    matched_readiness = sum(
        frame["readiness_expectation_match"] is True for frame in frames
    )
    mismatched_readiness = sum(
        frame["readiness_expectation_match"] is False for frame in frames
    )
    return {
        "frames_total": total,
        "expectations": {
            "checked": matched_expectations + mismatched_expectations,
            "matched": matched_expectations,
            "mismatched": mismatched_expectations,
            "unlabeled": sum(frame["expectation_match"] is None for frame in frames),
        },
        "readiness": {
            "ready": readiness_ready,
            "stop": total - readiness_ready,
            "reasons": dict(sorted(readiness_reasons.items())),
            "expectations": {
                "checked": matched_readiness + mismatched_readiness,
                "matched": matched_readiness,
                "mismatched": mismatched_readiness,
                "unlabeled": sum(
                    frame["readiness_expectation_match"] is None for frame in frames
                ),
            },
            "confusion": dict(sorted(readiness_confusion.items())),
        },
        "production": {
            "actual_outcomes": dict(sorted(actual_outcomes.items())),
            "definitive_target_count": definitive_target_count,
            "frames_with_definitive_targets": frames_with_definitive_targets,
            "resource_expectations_checked": resource_expectations_checked,
            "resource_expectations_matched": resource_expectations_matched,
            "resource_expectations_mismatched": (
                resource_expectations_checked - resource_expectations_matched
            ),
        },
        "guidance": {
            "actionable": guidance_actionable,
            "insufficient": total - guidance_actionable,
            "directions": dict(sorted(guidance_directions.items())),
            "reasons": dict(sorted(guidance_reasons.items())),
        },
        "confusion": dict(sorted(confusion.items())),
        "by_label": {
            label: dict(sorted(counts.items())) for label, counts in sorted(labels.items())
        },
    }


def _capture_git_snapshot(repo_root: Path | None = None) -> GitSnapshot:
    """Read exact HEAD and tracked cleanliness without invoking a shell."""

    root = Path(__file__).resolve().parents[1] if repo_root is None else repo_root
    try:
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"cannot capture Git proof provenance: {exc}") from exc
    return GitSnapshot(
        head_sha=head_result.stdout.strip(),
        tracked_worktree_clean=not status_result.stdout,
    )


def _validate_report_target(path: Path, repo_root: Path | None = None) -> None:
    """Reject report outputs that could invalidate the recorded clean tree."""

    root = (
        Path(__file__).resolve().parents[1]
        if repo_root is None
        else repo_root.resolve(strict=True)
    )
    root = root.resolve(strict=True)
    report = path.resolve(strict=False)
    digest = report.with_name(f"{report.name}.sha256")
    for candidate in (report, digest):
        if not candidate.is_relative_to(root):
            continue
        relative = candidate.relative_to(root)
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", str(relative)],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise ValueError(f"cannot validate report ignore policy: {exc}") from exc
        if result.returncode == 0:
            continue
        if result.returncode == 1:
            raise ValueError(
                "in-repository report and sidecar must both be Git-ignored: "
                f"{relative}"
            )
        raise ValueError(
            "git check-ignore failed while validating report target "
            f"{relative}: {result.stderr.strip()}"
        )


def _validate_command_argv(command_argv: tuple[str, ...]) -> None:
    if not command_argv:
        raise ValueError("command_argv must not be empty")
    for index, argument in enumerate(command_argv):
        if not isinstance(argument, str) or not argument:
            raise ValueError(f"command_argv[{index}] must be a non-empty string")
        if any(character in argument for character in ("\x00", "\r", "\n")):
            raise ValueError(f"command_argv[{index}] contains a control separator")


def _policy_dict() -> dict[str, JsonValue]:
    """Bind every offline result to the frozen production/Phase-1 policies."""

    profile = load_varrock_east_iron_profile()
    exclusions = tuple(
        dict.fromkeys(
            (
                *varrock_east_iron_scene_excluded_regions(profile),
                *(policy.region for policy in GAMEPLAY_CHROME_POLICIES),
            )
        )
    )
    return {
        "production": {
            "profile_id": profile.profile_id,
            "geometry": {
                "height": profile.frame_height,
                "pixel_format": profile.pixel_format.value,
                "width": profile.frame_width,
            },
            "minimum_landmark_quorum": profile.minimum_landmark_quorum,
            "minimum_landmark_zones": profile.minimum_landmark_zones,
            "landmarks": [
                {
                    "landmark_id": landmark.landmark_id,
                    "maximum_distance": landmark.maximum_distance,
                    "region": list(landmark.region),
                    "zone": landmark.macro_zone.value,
                }
                for landmark in profile.scene_landmarks
            ],
            "candidates": [
                {
                    "region": list(candidate.region),
                    "resource_id": candidate.resource_id,
                }
                for candidate in profile.candidates
            ],
        },
        "readiness": {
            "evaluator_id": CLIENT_INPUT_READINESS_ID,
            "evaluator_version": CLIENT_INPUT_READINESS_VERSION,
            "acceptance_authority": False,
            "dark_luma_maximum": _DARK_LUMA_MAXIMUM,
            "edge_luma_delta": _EDGE_LUMA_DELTA,
            "anchors": [
                {
                    "anchor_id": policy.anchor_id,
                    "maximum_dark_fraction": policy.maximum_dark_fraction,
                    "minimum_edge_density": policy.minimum_edge_density,
                    "minimum_luma_stddev": policy.minimum_luma_stddev,
                    "region": list(policy.region),
                }
                for policy in GAMEPLAY_CHROME_POLICIES
            ],
        },
        "guidance": {
            "selector_id": CAMERA_GUIDANCE_ID,
            "selector_version": CAMERA_GUIDANCE_VERSION,
            "acceptance_authority": False,
            "required_landmarks": _REQUIRED_LANDMARKS,
            "required_zones": _REQUIRED_ZONES,
            "maximum_registration_radius": MAXIMUM_WIDE_REGISTRATION_RADIUS,
            "maximum_rms_residual_px": _MAXIMUM_RMS_RESIDUAL_PX,
            "maximum_point_residual_px": _MAXIMUM_POINT_RESIDUAL_PX,
            "minimum_log_scale_error": _MINIMUM_LOG_SCALE_ERROR,
            "scale_dominance_ratio": _SCALE_DOMINANCE_RATIO,
            "rotation_score_unit_degrees": _ROTATION_SCORE_UNIT_DEGREES,
            "translation_score_unit_px": _TRANSLATION_SCORE_UNIT_PX,
            "translation_vector_norm": "euclidean",
            "competing_axis_combination": "root_sum_square",
            "excluded_regions": [list(region) for region in exclusions],
        },
        "servo": {
            "module": "mining_automation.validation.camera_servo",
            "production_acceptance_only": True,
            "default_max_primitives": DEFAULT_MAX_SERVO_PRIMITIVES,
            "absolute_max_primitives": ABSOLUTE_MAX_SERVO_PRIMITIVES,
            "default_max_elapsed_seconds": DEFAULT_MAX_SERVO_ELAPSED_SECONDS,
            "absolute_max_elapsed_seconds": ABSOLUTE_MAX_SERVO_ELAPSED_SECONDS,
            "maximum_settle_seconds": MAXIMUM_SERVO_SETTLE_SECONDS,
            "world_effect_descriptor_epsilon": WORLD_EFFECT_DESCRIPTOR_EPSILON,
            "world_effect_required_landmarks": WORLD_EFFECT_REQUIRED_LANDMARKS,
            "world_effect_required_zones": WORLD_EFFECT_REQUIRED_ZONES,
            "zoom_error_progress_tolerance": ZOOM_ERROR_PROGRESS_TOLERANCE,
            "maximum_consecutive_stagnant_steps": (
                MAXIMUM_CONSECUTIVE_STAGNANT_STEPS
            ),
        },
    }


def _authority_invariants_hold(frames: list[dict[str, JsonValue]]) -> bool:
    """Reject any result shape that grants diagnostics production authority."""

    for frame in frames:
        readiness = frame["readiness"]
        production = frame["production"]
        guidance = frame["guidance"]
        if not isinstance(readiness, dict):
            return False
        if not isinstance(production, dict):
            return False
        if not isinstance(guidance, dict):
            return False
        if any(
            readiness.get(key) is not False
            for key in ("acceptance_authority", "can_accept", "can_validate_scene", "can_expose_resources")
        ):
            return False
        if production.get("acceptance_authority") is not True:
            return False
        if any(
            guidance.get(key) is not False
            for key in ("acceptance_authority", "can_accept", "can_validate_scene", "can_expose_resources")
        ):
            return False
        if guidance.get("disposition") != "actionable":
            continue
        fit = guidance.get("fit")
        analysis = guidance.get("distributed_evidence")
        if (
            guidance.get("axis") != "zoom"
            or guidance.get("direction") not in ("negative", "positive")
            or not isinstance(fit, dict)
            or not isinstance(analysis, dict)
        ):
            return False
        fit_count = fit.get("landmark_count")
        analysis_count = analysis.get("matched_count")
        fit_zones = fit.get("matched_zones")
        analysis_zones = analysis.get("matched_zones")
        if (
            not isinstance(fit_count, int)
            or isinstance(fit_count, bool)
            or fit_count < _REQUIRED_LANDMARKS
            or not isinstance(fit_zones, list)
            or len(fit_zones) < _REQUIRED_ZONES
            or not isinstance(analysis_count, int)
            or isinstance(analysis_count, bool)
            or analysis_count < _REQUIRED_LANDMARKS
            or not isinstance(analysis_zones, list)
            or len(analysis_zones) < _REQUIRED_ZONES
        ):
            return False
    return True


def _required_count_matches(required: int | None, discovered: int) -> bool | None:
    """Return the exact cardinality gate, or None when no gate was requested."""

    if required is None:
        return None
    return discovered == required


def _canonical_drift_gate(
    discovered_groups: tuple[tuple[GroupSpec, tuple[FrameSpec, ...]], ...],
) -> dict[str, JsonValue]:
    """Freeze the Issue #31 36-frame real-drift proof contract."""

    drift = next(
        ((group, frames) for group, frames in discovered_groups if group.label == "drift"),
        None,
    )
    if drift is None:
        return {
            "label": "drift",
            "present": False,
            "frames_discovered": 0,
            "required_count": 36,
            "declared_required_count": None,
            "expected_production": ExpectedOutcome.FAIL_CLOSED.value,
            "declared_production": None,
            "expected_readiness": ExpectedReadiness.READY.value,
            "declared_readiness": None,
            "passed": False,
        }
    group, frames = drift
    passed = (
        group.required_count == 36
        and len(frames) == 36
        and group.expectation is ExpectedOutcome.FAIL_CLOSED
        and group.readiness_expectation is ExpectedReadiness.READY
    )
    return {
        "label": "drift",
        "present": True,
        "frames_discovered": len(frames),
        "required_count": 36,
        "declared_required_count": group.required_count,
        "expected_production": ExpectedOutcome.FAIL_CLOSED.value,
        "declared_production": group.expectation.value,
        "expected_readiness": ExpectedReadiness.READY.value,
        "declared_readiness": group.readiness_expectation.value,
        "passed": passed,
    }


def analyze_inputs(
    *,
    manifest_path: Path,
    fixture_root: Path,
    raw_groups: list[str],
    raw_expectations: list[str],
    raw_readiness_expectations: list[str],
    raw_required_counts: list[str],
    command_argv: tuple[str, ...],
) -> dict[str, JsonValue]:
    """Analyze every input in canonical order without any input side effects."""

    _validate_command_argv(command_argv)
    git_before = _capture_git_snapshot()
    dataset_id, manifest_version, manifest_sha, fixtures = _fixture_frames(
        manifest_path,
        fixture_root,
    )
    groups = _parse_groups(
        raw_groups,
        raw_expectations,
        raw_readiness_expectations,
        raw_required_counts,
        reserved_label=dataset_id,
    )
    discovered_groups = tuple(
        (group, _discover_external_frames(group)) for group in groups
    )
    external = tuple(frame for _, discovered in discovered_groups for frame in discovered)
    specs = fixtures + external
    _reject_duplicate_paths(specs)
    frames = [
        _analyze_frame(spec, frame_id=index)
        for index, spec in enumerate(specs, start=1)
    ]
    aggregate = _aggregate(frames)
    expectations = aggregate["expectations"]
    readiness_aggregate = aggregate["readiness"]
    assert isinstance(expectations, dict)
    assert isinstance(readiness_aggregate, dict)
    readiness_expectations = readiness_aggregate["expectations"]
    assert isinstance(readiness_expectations, dict)
    profile = load_varrock_east_iron_profile()
    first_production = frames[0]["production"]
    first_guidance = frames[0]["guidance"]
    first_readiness = frames[0]["readiness"]
    assert isinstance(first_production, dict)
    assert isinstance(first_guidance, dict)
    assert isinstance(first_readiness, dict)
    input_groups: list[dict[str, JsonValue]] = [
        {
            "label": dataset_id,
            "source_kind": "reviewed_fixture",
            "frames_discovered": len(fixtures),
            "required_count": len(fixtures),
            "count_matches": True,
            "count_requirement_present": True,
            "expected_production": ExpectedOutcome.PASS.value,
            "expected_readiness": ExpectedReadiness.READY.value,
            "expectations_complete": True,
        }
    ]
    for group, discovered in discovered_groups:
        count_matches = _required_count_matches(
            group.required_count,
            len(discovered),
        )
        input_groups.append(
            {
                "label": group.label,
                "source_kind": "labeled_input",
                "frames_discovered": len(discovered),
                "required_count": group.required_count,
                "count_matches": count_matches,
                "count_requirement_present": group.required_count is not None,
                "expected_production": group.expectation.value,
                "expected_readiness": group.readiness_expectation.value,
                "expectations_complete": (
                    group.expectation is not ExpectedOutcome.UNLABELED
                    and group.readiness_expectation is not ExpectedReadiness.UNLABELED
                    and group.required_count is not None
                ),
            }
        )
    proof_complete = all(
        group["expectations_complete"] is True for group in input_groups
    )
    canonical_drift_gate = _canonical_drift_gate(discovered_groups)
    proof_complete = proof_complete and canonical_drift_gate["passed"] is True
    count_gate_passed = all(
        group["count_matches"] is True for group in input_groups
    )
    configuration = _policy_dict()
    # This is deliberately the last read before report construction/writing.
    git_after = _capture_git_snapshot()
    head_unchanged = git_before.head_sha == git_after.head_sha
    git_provenance_eligible = (
        head_unchanged
        and git_before.tracked_worktree_clean
        and git_after.tracked_worktree_clean
    )
    authority_invariants_passed = _authority_invariants_hold(frames)
    production_expectations_passed = expectations["mismatched"] == 0
    readiness_expectations_passed = readiness_expectations["mismatched"] == 0
    proof_eligible = (
        proof_complete
        and count_gate_passed
        and git_provenance_eligible
        and authority_invariants_passed
    )
    overall_passed = (
        proof_eligible
        and production_expectations_passed
        and readiness_expectations_passed
    )
    return {
        "report_schema_version": _REPORT_SCHEMA_VERSION,
        "tool": {
            "id": _TOOL_ID,
            "version": _TOOL_VERSION,
            "read_only": True,
            "executes_input_adapters": False,
            "writes_or_copies_pixels": False,
        },
        "authority": {
            "acceptance_path": "unchanged_production_camera_evaluation",
            "production_acceptance_only": True,
            "readiness_can_accept": False,
            "guidance_can_accept": False,
            "diagnostics_can_expose_resources": False,
            "invariants_passed": authority_invariants_passed,
        },
        "provenance": {
            "command_argv": list(command_argv),
            "git_before": {
                "head_sha": git_before.head_sha,
                "tracked_worktree_clean": git_before.tracked_worktree_clean,
            },
            "git_after": {
                "head_sha": git_after.head_sha,
                "tracked_worktree_clean": git_after.tracked_worktree_clean,
            },
            "head_unchanged": head_unchanged,
            "tracked_state_clean_before_and_after": (
                git_before.tracked_worktree_clean
                and git_after.tracked_worktree_clean
            ),
        },
        "configuration": configuration,
        "profile": {
            "profile_id": profile.profile_id,
            "profile_schema_version": first_production["profile_schema_version"],
            "detector_id": first_production["detector_id"],
            "detector_version": first_production["detector_version"],
            "readiness_id": first_readiness["evaluator_id"],
            "readiness_version": first_readiness["evaluator_version"],
            "guidance_id": first_guidance["selector_id"],
            "guidance_version": first_guidance["selector_version"],
        },
        "fixture_manifest": {
            "dataset_id": dataset_id,
            "schema_version": manifest_version,
            "sha256": manifest_sha,
            "cases_total": len(fixtures),
        },
        "input_groups": cast(JsonValue, input_groups),
        "canonical_drift_gate": canonical_drift_gate,
        "frames": cast(JsonValue, frames),
        "aggregate": aggregate,
        "proof": {
            "complete": proof_complete,
            "eligible": proof_eligible,
            "count_gate_passed": count_gate_passed,
            "git_provenance_eligible": git_provenance_eligible,
            "authority_invariants_passed": authority_invariants_passed,
            "production_expectations_passed": production_expectations_passed,
            "readiness_expectations_passed": readiness_expectations_passed,
        },
        "overall_passed": overall_passed,
    }


def canonical_report_bytes(report: dict[str, JsonValue]) -> bytes:
    """Return strict, stable UTF-8 JSON for one offline report."""

    return (
        json.dumps(report, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_exclusive_report(path: Path, payload: bytes) -> str:
    digest_path = path.with_name(f"{path.name}.sha256")
    if path.exists():
        raise FileExistsError(path)
    if digest_path.exists():
        raise FileExistsError(digest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    created_report = False
    created_digest = False
    try:
        with path.open("xb") as report_file:
            created_report = True
            report_file.write(payload)
        with digest_path.open("xb") as digest_file:
            created_digest = True
            digest_file.write(f"{digest}\n".encode("ascii"))
    except BaseException:
        if created_digest:
            digest_path.unlink(missing_ok=True)
        if created_report:
            path.unlink(missing_ok=True)
        raise
    return digest


def _error(message: str) -> int:
    print(f"offline analysis input error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Run the offline proof; return 1 only for a completed expectation mismatch."""

    raw_argv = sys.argv[1:] if argv is None else argv
    command_argv = (
        tuple(sys.argv)
        if argv is None
        else ("tools/analyze_issue31_servo_offline.py", *argv)
    )
    arguments = build_parser().parse_args(raw_argv)
    try:
        if arguments.report is not None:
            _validate_report_target(arguments.report)
        report = analyze_inputs(
            manifest_path=arguments.fixture_manifest,
            fixture_root=arguments.fixture_root,
            raw_groups=arguments.frames,
            raw_expectations=arguments.expect,
            raw_readiness_expectations=arguments.expect_readiness,
            raw_required_counts=arguments.require_count,
            command_argv=command_argv,
        )
        payload = canonical_report_bytes(report)
        if arguments.report is None:
            sys.stdout.buffer.write(payload)
        else:
            digest = _write_exclusive_report(arguments.report, payload)
            print(f"Report: {arguments.report}")
            print(f"SHA-256: {digest}")
    except (CaptureError, PerceptionError, OSError, ValueError) as exc:
        return _error(str(exc))
    return 0 if report["overall_passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
