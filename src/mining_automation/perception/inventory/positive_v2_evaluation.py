"""Deterministic calibration/held-out evaluation for inventory classifier V2."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ...capture import Frame
from .adapter import inventory_detection_from_observation
from .classification import InventoryObstructionError, SlotOccupancy, _read_core
from .configuration import inventory_positive_detector_v2_from_profile
from .detector import InventoryDetection
from .geometry import InventoryGridLayout, Region
from .localization import InventoryFrameProfile
from .positive_classifier_v2 import (
    INVENTORY_POSITIVE_V2_CALIBRATION_SHA256,
    PositiveReferenceInventoryClassifierV2,
    PositiveSlotFeaturesV2,
    _slot_features,
)
from .positive_v2_calibration import (
    INVENTORY_POSITIVE_V2_CALIBRATION_SESSION_ID,
    INVENTORY_POSITIVE_V2_HELD_OUT_SESSION_ID,
    InventoryPositiveV2CalibrationError,
    compute_inventory_positive_v2_calibration_sha256,
)
from .sanitized_replay import (
    InventorySanitizedReplayError,
    InventorySanitizedReplayReport,
    _json_object,
    _object_value,
    _owned_path,
    _read_bytes,
    _reconstructed_frame,
    _region_value,
    _required_list,
    _required_object,
    _required_positive_int,
    _required_relative_path,
    _required_sha256,
    _required_text,
    _sha256,
    replay_inventory_sanitized_fixture,
)

__all__ = [
    "INVENTORY_POSITIVE_V2_CALIBRATION_SESSION_ID",
    "INVENTORY_POSITIVE_V2_HELD_OUT_SESSION_ID",
    "INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA",
    "InventoryPositiveV2CaseResult",
    "InventoryPositiveV2EvaluationError",
    "InventoryPositiveV2EvaluationReport",
    "compute_inventory_positive_v2_calibration_sha256",
    "evaluate_inventory_positive_v2",
]


INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA: Final[str] = (
    "620dcde6a476b5f458f6736e990f4d4e578791c4"
)
_REPORT_SCHEMA_VERSION: Final[int] = 1
_PUBLICATION_FLOOR: Final[float] = 0.8


class InventoryPositiveV2EvaluationError(RuntimeError):
    """The frozen V2 campaign or its integrity contract was invalid."""


@dataclass(frozen=True, slots=True)
class InventoryPositiveV2CaseResult:
    """V1 root cause and V2 outcome for one reviewed sanitized case."""

    case_id: str
    campaign_partition: str
    reviewer_truth: Mapping[str, object]
    v1_actual: Mapping[str, object]
    v2_actual: Mapping[str, object]
    expected_v2: Mapping[str, object]
    slot_root_cause: tuple[Mapping[str, object], ...]
    v2_analysis_failure_reason: str | None
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "campaign_partition": self.campaign_partition,
            "case_id": self.case_id,
            "expected_v2": dict(self.expected_v2),
            "passed": self.passed,
            "reviewer_truth": dict(self.reviewer_truth),
            "slot_root_cause": [dict(item) for item in self.slot_root_cause],
            "v1_actual": dict(self.v1_actual),
            "v2_actual": dict(self.v2_actual),
            "v2_analysis_failure_reason": self.v2_analysis_failure_reason,
        }


@dataclass(frozen=True, slots=True)
class InventoryPositiveV2EvaluationReport:
    """Canonical non-activating V2 calibration and held-out report."""

    git_head_sha: str
    model_freeze_git_sha: str
    fixture_dataset_id: str
    fixture_manifest_sha256: str
    fixture_generator_head_sha: str | None
    calibration_evidence_sha256: str
    model_configuration_sha256: str
    model_configuration: Mapping[str, object]
    v1_detector: Mapping[str, object]
    v2_detector: Mapping[str, object]
    cases: tuple[InventoryPositiveV2CaseResult, ...]

    @property
    def activation_allowed(self) -> bool:
        return False

    @property
    def passed(self) -> bool:
        return (
            bool(self.cases)
            and self.calibration_evidence_sha256
            == INVENTORY_POSITIVE_V2_CALIBRATION_SHA256
            and all(item.passed for item in self.cases)
        )

    @property
    def calibration_cases(self) -> tuple[InventoryPositiveV2CaseResult, ...]:
        return tuple(
            item for item in self.cases if item.campaign_partition == "calibration"
        )

    @property
    def held_out_cases(self) -> tuple[InventoryPositiveV2CaseResult, ...]:
        return tuple(
            item for item in self.cases if item.campaign_partition == "held-out"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "activation_allowed": False,
            "acceptance_failures": [
                {
                    "case_id": item.case_id,
                    "expected_v2": dict(item.expected_v2),
                    "v2_actual": dict(item.v2_actual),
                    "v2_analysis_failure_reason": (
                        item.v2_analysis_failure_reason
                    ),
                }
                for item in self.cases
                if not item.passed
            ],
            "calibration": {
                "case_ids": [item.case_id for item in self.calibration_cases],
                "evidence_sha256": self.calibration_evidence_sha256,
                "frozen_before_held_out_evaluation": True,
                "model_freeze_git_sha": self.model_freeze_git_sha,
                "session_id": INVENTORY_POSITIVE_V2_CALIBRATION_SESSION_ID,
            },
            "cases": [item.to_dict() for item in self.cases],
            "fixture": {
                "dataset_id": self.fixture_dataset_id,
                "generator_head_sha": self.fixture_generator_head_sha,
                "manifest_sha256": self.fixture_manifest_sha256,
            },
            "git_head_sha": self.git_head_sha,
            "held_out": {
                "case_ids": [item.case_id for item in self.held_out_cases],
                "evaluated_model_freeze_git_sha": self.model_freeze_git_sha,
                "session_id": INVENTORY_POSITIVE_V2_HELD_OUT_SESSION_ID,
                "tuning_after_evaluation_allowed": False,
            },
            "held_out_conclusion": (
                "The frozen spatial confidence feature clears the 0.8 floor "
                "for every clean held-out occupied slot, but the first-batch "
                "slot-perimeter presentation guard rejects clean varied art. "
                "The candidate remains non-activating and this second batch "
                "must not be used for post-hoc tuning."
            ),
            "model": {
                "configuration": dict(self.model_configuration),
                "configuration_sha256": self.model_configuration_sha256,
            },
            "passed": self.passed,
            "report_schema_version": _REPORT_SCHEMA_VERSION,
            "root_cause": (
                "V1 clean occupied slots already clear the unchanged raw occupied "
                "threshold, but its theoretical-range confidence curve leaves ordinary "
                "sprites below the independent 0.8 detector publication floor. V2 "
                "requires the same raw occupied decision, distributed slot-core "
                "support, and a first-batch-frozen presentation envelope before "
                "deriving confidence from spatial support."
            ),
            "slot_expectation_policy": {
                "basis": "reviewed occupied count plus frozen row-major prefix",
                "explicit_per_slot_reviewer_truth": False,
            },
            "v1_detector": dict(self.v1_detector),
            "v2_detector": dict(self.v2_detector),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class _CampaignCase:
    case_id: str
    session_id: str
    payload: bytes
    payload_sha256: str
    reviewer_truth: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _Campaign:
    v1_report: InventorySanitizedReplayReport
    profile: InventoryFrameProfile
    reference_payload: bytes
    cases: tuple[_CampaignCase, ...]


def evaluate_inventory_positive_v2(
    fixture_directory: Path,
    *,
    git_head_sha: str,
) -> InventoryPositiveV2EvaluationReport:
    """Evaluate the frozen V2 candidate without changing V1 fixture semantics."""
    if (
        not isinstance(git_head_sha, str)
        or len(git_head_sha) != 40
        or any(character not in "0123456789abcdef" for character in git_head_sha)
    ):
        raise InventoryPositiveV2EvaluationError(
            "git_head_sha must be an exact lowercase 40-character Git SHA"
        )
    campaign = _load_campaign(fixture_directory)
    try:
        calibration_sha256 = compute_inventory_positive_v2_calibration_sha256(
            fixture_directory
        )
    except InventoryPositiveV2CalibrationError as exc:
        raise InventoryPositiveV2EvaluationError(str(exc)) from exc
    if calibration_sha256 != INVENTORY_POSITIVE_V2_CALIBRATION_SHA256:
        raise InventoryPositiveV2EvaluationError(
            "first-campaign evidence differs from the frozen V2 calibration: "
            f"{calibration_sha256}"
        )
    reference = _reconstructed_frame(
        campaign.reference_payload,
        campaign.profile.region,
        campaign.profile.frame_width,
        campaign.profile.frame_height,
        frame_id=1,
    )
    detector = inventory_positive_detector_v2_from_profile(campaign.profile, reference)
    classifier = detector.classifier
    if not isinstance(classifier, PositiveReferenceInventoryClassifierV2):
        raise InventoryPositiveV2EvaluationError("V2 factory returned the wrong classifier")

    v1_by_id = {item.case_id: item for item in campaign.v1_report.cases}
    results: list[InventoryPositiveV2CaseResult] = []
    for frame_id, item in enumerate(campaign.cases, start=1):
        partition = _campaign_partition(item.session_id)
        frame = _reconstructed_frame(
            item.payload,
            campaign.profile.region,
            campaign.profile.frame_width,
            campaign.profile.frame_height,
            frame_id=frame_id,
        )
        detection = inventory_detection_from_observation(detector.detect(frame)[0])
        v2_actual = _detection_dict(detection)
        expected_v2 = _expected_v2(item.reviewer_truth)
        passed = _matches_expected(detection, expected_v2)
        clean = _is_clean_inventory(item.reviewer_truth)
        features: tuple[PositiveSlotFeaturesV2, ...] = ()
        analysis_failure_reason: str | None = None
        try:
            features = classifier.analyze(frame, campaign.profile.region)
        except InventoryObstructionError as exc:
            analysis_failure_reason = str(exc)
            if clean:
                features = _diagnostic_slot_features(
                    classifier,
                    reference,
                    frame,
                    campaign.profile.region,
                )
        slot_report = (
            _slot_root_cause(features, item.reviewer_truth, detection)
            if clean
            else ()
        )
        v1_case = v1_by_id.get(item.case_id)
        if v1_case is None:
            raise InventoryPositiveV2EvaluationError(
                f"V1 replay omitted campaign case: {item.case_id}"
            )
        results.append(
            InventoryPositiveV2CaseResult(
                case_id=item.case_id,
                campaign_partition=partition,
                reviewer_truth=item.reviewer_truth,
                v1_actual=v1_case.actual,
                v2_actual=v2_actual,
                expected_v2=expected_v2,
                slot_root_cause=slot_report,
                v2_analysis_failure_reason=analysis_failure_reason,
                passed=passed,
            )
        )
    if len(results) != 16:
        raise InventoryPositiveV2EvaluationError(
            "V2 campaign requires exactly 16 reviewed cases"
        )
    if any(item.campaign_partition != "calibration" for item in results[:8]) or any(
        item.campaign_partition != "held-out" for item in results[8:]
    ):
        raise InventoryPositiveV2EvaluationError(
            "V2 campaign sessions are reordered or interleaved"
        )

    model_configuration = classifier.model_configuration
    model_configuration_sha256 = _sha256(_canonical_bytes(model_configuration))
    return InventoryPositiveV2EvaluationReport(
        git_head_sha=git_head_sha,
        model_freeze_git_sha=INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA,
        fixture_dataset_id=campaign.v1_report.dataset_id,
        fixture_manifest_sha256=campaign.v1_report.fixture_manifest_sha256,
        fixture_generator_head_sha=campaign.v1_report.generator_head_sha,
        calibration_evidence_sha256=calibration_sha256,
        model_configuration_sha256=model_configuration_sha256,
        model_configuration=model_configuration,
        v1_detector={
            "configuration_id": campaign.v1_report.configuration_id,
            "detector_id": campaign.v1_report.detector_id,
            "detector_version": campaign.v1_report.detector_version,
        },
        v2_detector={
            "configuration_id": detector.configuration_id,
            "detector_id": detector.metadata.detector_id,
            "detector_version": detector.metadata.version,
            "minimum_slot_confidence": detector.minimum_slot_confidence,
            "profile_id": classifier.profile_id,
        },
        cases=tuple(results),
    )


def _load_campaign(fixture_directory: Path) -> _Campaign:
    if not isinstance(fixture_directory, Path):
        raise TypeError("fixture_directory must be pathlib.Path")
    manifest_path = fixture_directory / "manifest.json"
    manifest_before = _read_bytes(manifest_path, "fixture manifest")
    try:
        v1_report = replay_inventory_sanitized_fixture(fixture_directory)
    except InventorySanitizedReplayError as exc:
        raise InventoryPositiveV2EvaluationError(
            f"V1 fixture integrity/replay failed: {exc}"
        ) from exc
    if _read_bytes(manifest_path, "fixture manifest") != manifest_before:
        raise InventoryPositiveV2EvaluationError(
            "fixture manifest changed during V2 evaluation"
        )
    manifest = _json_object(manifest_before, "fixture manifest")
    candidate = _required_object(manifest, "candidate")
    profile_raw = _required_object(candidate, "profile")
    reconstruction = _required_object(manifest, "frame_reconstruction")
    width = _required_positive_int(reconstruction, "width")
    height = _required_positive_int(reconstruction, "height")
    region = _region_value(reconstruction.get("region"), "reconstruction region")
    layout = InventoryGridLayout(
        profile_id=_required_text(profile_raw, "profile_id"),
        column_stride=_required_positive_int(profile_raw, "column_stride"),
        row_stride=_required_positive_int(profile_raw, "row_stride"),
    )
    profile = InventoryFrameProfile(
        profile_id=layout.profile_id,
        frame_width=width,
        frame_height=height,
        region=region,
        layout=layout,
    )
    candidate_evidence = _required_object(candidate, "evidence")
    reference_case_id = (
        _required_text(candidate_evidence, "reference_session_id")
        + "/"
        + _required_text(candidate_evidence, "reference_capture_id")
    )
    parsed: list[_CampaignCase] = []
    for value in _required_list(manifest, "cases"):
        case = _object_value(value, "sanitized fixture case")
        case_id = _required_text(case, "case_id")
        artifact = _required_object(case, "frame_region")
        path = _owned_path(
            fixture_directory,
            _required_relative_path(artifact, "path"),
            "sanitized frame region",
        )
        payload = _read_bytes(path, "sanitized frame region")
        payload_sha256 = _sha256(payload)
        if payload_sha256 != _required_sha256(artifact, "sha256"):
            raise InventoryPositiveV2EvaluationError(
                f"sanitized frame region changed after V1 verification: {case_id}"
            )
        truth = _required_object(case, "review_truth")
        parsed.append(
            _CampaignCase(
                case_id=case_id,
                session_id=_required_text(truth, "session_id"),
                payload=payload,
                payload_sha256=payload_sha256,
                reviewer_truth=truth,
            )
        )
    references = tuple(item for item in parsed if item.case_id == reference_case_id)
    if len(references) != 1:
        raise InventoryPositiveV2EvaluationError(
            "V2 campaign cannot resolve the exact candidate reference"
        )
    return _Campaign(
        v1_report=v1_report,
        profile=profile,
        reference_payload=references[0].payload,
        cases=tuple(parsed),
    )


def _campaign_partition(session_id: str) -> str:
    if session_id == INVENTORY_POSITIVE_V2_CALIBRATION_SESSION_ID:
        return "calibration"
    if session_id == INVENTORY_POSITIVE_V2_HELD_OUT_SESSION_ID:
        return "held-out"
    raise InventoryPositiveV2EvaluationError(
        f"case belongs to an unreviewed V2 campaign session: {session_id}"
    )


def _is_clean_inventory(truth: Mapping[str, object]) -> bool:
    return (
        truth.get("visibility") == "inventory-visible"
        and all(
            truth.get(name) is False
            for name in (
                "drag_visible",
                "hover_visible",
                "quantity_text_visible",
                "selected_item_visible",
            )
        )
        and isinstance(truth.get("occupied_slots"), int)
        and not isinstance(truth.get("occupied_slots"), bool)
    )


def _expected_v2(truth: Mapping[str, object]) -> dict[str, object]:
    visibility = truth.get("visibility")
    occupied = truth.get("occupied_slots")
    if visibility in ("wrong-tab-visible", "inventory-obstructed"):
        return {"confidence": 0.0, "label": "unknown", "occupied_slots": None}
    if (
        visibility != "inventory-visible"
        or not isinstance(occupied, int)
        or isinstance(occupied, bool)
        or not 0 <= occupied <= 28
    ):
        raise InventoryPositiveV2EvaluationError("reviewer truth is invalid for V2")
    if any(
        truth.get(name) is True
        for name in (
            "drag_visible",
            "hover_visible",
            "quantity_text_visible",
            "selected_item_visible",
        )
    ):
        return {"confidence": 0.0, "label": "unknown", "occupied_slots": None}
    label = "empty" if occupied == 0 else "full" if occupied == 28 else "partial"
    return {
        "confidence_min": _PUBLICATION_FLOOR,
        "label": label,
        "occupied_slots": occupied,
    }


def _matches_expected(
    detection: InventoryDetection,
    expected: Mapping[str, object],
) -> bool:
    if detection.label != expected.get("label") or (
        detection.occupied_slots != expected.get("occupied_slots")
    ):
        return False
    if expected.get("label") == "unknown":
        return detection.confidence == 0.0 and bool(detection.reason)
    minimum = expected.get("confidence_min")
    return (
        isinstance(minimum, float)
        and detection.confidence >= minimum
        and detection.reason is None
        and all(item.confidence >= minimum for item in detection.slots)
    )


def _slot_root_cause(
    features: tuple[PositiveSlotFeaturesV2, ...],
    truth: Mapping[str, object],
    detection: InventoryDetection,
) -> tuple[Mapping[str, object], ...]:
    if len(features) != 28:
        raise InventoryPositiveV2EvaluationError(
            "clean inventory did not produce all 28 V2 feature rows"
        )
    occupied = truth.get("occupied_slots")
    if not isinstance(occupied, int) or isinstance(occupied, bool):
        raise InventoryPositiveV2EvaluationError(
            "clean inventory requires exact reviewer occupied truth"
        )
    rows: list[Mapping[str, object]] = []
    for item in features:
        reviewer_state = "occupied" if item.index < occupied else "empty"
        rows.append(
            {
                "active_spatial_cells": item.active_spatial_cells,
                "active_coarse_columns": list(item.active_coarse_columns),
                "active_coarse_rows": list(item.active_coarse_rows),
                "authoritative_for_publication": detection.occupied_slots is not None,
                "changed_fraction": item.changed_fraction,
                "changed_fraction_component": item.changed_fraction_component,
                "column": item.column,
                "index": item.index,
                "mean_color_component": item.mean_color_component,
                "mean_normalized_l1_delta": item.mean_normalized_l1_delta,
                "raw_score": item.raw_score,
                "region": list(item.region.as_tuple()),
                "expected_slot_state_from_reviewed_count_and_prefix_policy": (
                    reviewer_state
                ),
                "row": item.row,
                "spatial_cell_active": list(item.spatial_cell_active),
                "spatial_cell_changed_pixels": list(
                    item.spatial_cell_changed_pixels
                ),
                "spatial_support": item.spatial_support,
                "distributed_support": item.distributed_support,
                "v1_confidence": item.v1_confidence,
                "v1_distance_to_publication_floor": (
                    item.v1_confidence - _PUBLICATION_FLOOR
                ),
                "v1_meets_publication_floor": (
                    item.v1_state is not SlotOccupancy.UNCERTAIN
                    and item.v1_confidence >= _PUBLICATION_FLOOR
                ),
                "v1_state": item.v1_state.value,
                "v2_confidence": item.v2_confidence,
                "v2_distance_to_publication_floor": (
                    item.v2_confidence - _PUBLICATION_FLOOR
                ),
                "v2_meets_publication_floor": (
                    item.v2_state is not SlotOccupancy.UNCERTAIN
                    and item.v2_confidence >= _PUBLICATION_FLOOR
                ),
                "v2_state": item.v2_state.value,
            }
        )
    return tuple(rows)


def _diagnostic_slot_features(
    classifier: PositiveReferenceInventoryClassifierV2,
    reference: Frame,
    candidate: Frame,
    region: Region,
) -> tuple[PositiveSlotFeaturesV2, ...]:
    """Compute non-authoritative root-cause rows after a fail-closed guard."""
    slots = classifier.layout.all_slot_regions(region)
    return tuple(
        _slot_features(
            index,
            slot,
            _read_core(reference, slot, classifier.policy.core_inset),
            _read_core(candidate, slot, classifier.policy.core_inset),
            classifier.policy,
        )
        for index, slot in enumerate(slots)
    )


def _detection_dict(detection: InventoryDetection) -> dict[str, object]:
    return {
        "confidence": detection.confidence,
        "configuration_id": detection.configuration_id,
        "label": detection.label,
        "occupied_slots": detection.occupied_slots,
        "profile_id": detection.profile_id,
        "reason": detection.reason,
        "slots": [
            {
                "changed_fraction": item.changed_fraction,
                "confidence": item.confidence,
                "index": item.index,
                "score": item.score,
                "state": item.state.value,
            }
            for item in detection.slots
        ],
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _canonical_json(value: object) -> str:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
