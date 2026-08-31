"""Offline independent-validation firewall for the frozen Inventory V3 candidate.

This module is intentionally absent from the inventory package exports.  It
loads one source-owned, frozen development candidate and evaluates a separate
validation package without exposing a calibration, prototype-learning,
promotion, Observation, InventoryState, controller, or input path.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

from ...capture.frame import Frame, PixelFormat, RawFrame
from .geometry import InventoryGridLayout, Region
from .localization import InventoryFrameProfile
from .positive_classifier_v3 import (
    INVENTORY_POSITIVE_V3_ANALYZER_ID,
    INVENTORY_POSITIVE_V3_ANALYZER_VERSION,
    INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID,
    INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION,
    INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID,
    INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION,
    INVENTORY_POSITIVE_V3_VALIDATION_STATUS,
    InventoryPositiveV3DevelopmentAnalyzer,
    InventoryPositiveV3DevelopmentResult,
)
from .positive_v3_prototypes import (
    DEVELOPMENT_DATASET_ID,
    DEVELOPMENT_MANIFEST_SHA256,
    FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES,
    MODEL_ARTIFACT_SHA256,
    PROTOTYPE_SOURCE_REGION_SHA256S,
    SUPPORTED_COLUMN_STRIDE,
    SUPPORTED_FRAME_HEIGHT,
    SUPPORTED_FRAME_WIDTH,
    SUPPORTED_PIXEL_FORMAT,
    SUPPORTED_PROFILE_ID,
    SUPPORTED_REFERENCE_RGB_SHA256,
    SUPPORTED_REGION,
    SUPPORTED_ROW_STRIDE,
)

__all__ = [
    "INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA",
    "INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256",
    "FrozenV3ModelBinding",
    "IndependentValidationDataset",
    "InventoryPositiveV3IndependentValidationError",
    "InventoryPositiveV3IndependentValidationReport",
    "build_inventory_positive_v3_validation_readiness_report",
    "evaluate_frozen_v3_independent_validation",
    "frozen_v3_model_binding",
    "independent_validation_preregistration",
    "load_independent_validation_dataset",
]


INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA: Final[str] = (
    "5975532b472a74d93f010e04ca44b2efa2a3ffd7"
)
INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256: Final[str] = (
    "9cd485f6b095018763ea481144ea183e089d670148ed7a79d3c11126a9736518"
)

_FROZEN_COMMITTED_AT_UTC: Final[str] = "2026-08-31T05:25:40Z"
_PREREGISTRATION_EFFECTIVE_AT_UTC: Final[str] = "2026-08-31T11:30:00Z"
_PREREGISTRATION_BASE_HEAD_SHA: Final[str] = INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA
_FROZEN_CONFIGURATION_ID: Final[str] = (
    "inventory-positive-v3-development-"
    "4ee2d01517447655700bd0d49637b3f4221edcc950a07c270e0717c52999e72d"
)
_FROZEN_MODEL_CONFIGURATION_SHA256: Final[str] = (
    "57a0ed55328e4f6994f3f099d415e223a1bebb3954cab7ef44c2938dce14b634"
)
_FROZEN_MODEL_ARTIFACT_SHA256: Final[str] = (
    "0722f1922c88ec011099fe83980b44f2b77637e2aa49a58d87245ff208cdf469"
)
_FROZEN_PROTOTYPE_OCCURRENCES_SHA256: Final[str] = (
    "3c0dce4ca58ca44839dee2d25e7d4d3d8e1182a23dd042286791424de0f2e8f8"
)
_FROZEN_PROTOTYPE_SOURCE_SET_SHA256: Final[str] = (
    "3f6c957f5805c2be7e305a7aea57e9e41f2d15d5bd2fd7195af1af1620a92aee"
)
_FROZEN_REFERENCE_REGION_FILE_SHA256: Final[str] = (
    "c46c43ecf972c05a34b968f5f232c886cb253eb511bd916f5f36670defad1df3"
)
_FROZEN_ANALYZER_RUNTIME_STATE_SHA256: Final[str] = (
    "949530fb56a6873776b64310ed223b24f53e3f99d34995c3a58f759ce69328ad"
)
_FROZEN_DEVELOPMENT_CASE_IDS_SHA256: Final[str] = (
    "d91e1b01617669d0bf44c6d0c8645070994528471c02b773310b422b812ff05a"
)
_FROZEN_DEVELOPMENT_SESSION_IDS_SHA256: Final[str] = (
    "d75ad12a7d412e244b7349a8663ce7236d5cfc0bdd7e95461e76b1ca12623701"
)
_FROZEN_DEVELOPMENT_CAPTURE_IDS_SHA256: Final[str] = (
    "728a350c0af47cc660591b2ab713218352d19d78054288181eabdab00721a313"
)
_FROZEN_GIT_BLOBS: Final[tuple[tuple[str, str], ...]] = (
    (
        "src/mining_automation/capture/frame.py",
        "21fe6b9e3421de5255f8dc5ae10c945e0459d82c",
    ),
    (
        "src/mining_automation/perception/inventory/classification.py",
        "3658faf3049e3ac16a61e2ef3d3c055f7e90ecde",
    ),
    (
        "src/mining_automation/perception/inventory/geometry.py",
        "ae067813d7885418563f3328571967b7dce7e844",
    ),
    (
        "src/mining_automation/perception/inventory/localization.py",
        "e4ccc47ddbbfaba50f446da1430b0575c147e3e0",
    ),
    (
        "src/mining_automation/perception/inventory/positive_classifier_v3.py",
        "18849334bbb34c7b1073820ab1fc2a29223662d2",
    ),
    (
        "src/mining_automation/perception/inventory/positive_v3_prototypes.py",
        "fb357ce3ad477be25fbd1653457f6b367f34ab40",
    ),
)

_DEVELOPMENT_FIXTURE: Final[PurePosixPath] = PurePosixPath(
    "tests/fixtures/perception/inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)
_DEVELOPMENT_REFERENCE_REGION: Final[PurePosixPath] = PurePosixPath(
    "frames/001-20260830T183116.108869Z-empty-reference.region.bgra"
)
_PREREGISTRATION_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/preregistration.json"
)
_APPROVAL_REGISTRY_PATH: Final[PurePosixPath] = PurePosixPath(
    "validation/inventory-positive-v3/approved-campaigns.json"
)

_PREREGISTRATION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-preregistration-v1"
)
_PACKAGE_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-package-v1"
)
_DATASET_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-dataset-v1"
)
_REVIEW_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-review-v1"
)
_SOURCE_SESSION_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-source-session-v1"
)
_SOURCE_CAPTURE_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-source-capture-v1"
)
_REPORT_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-report-v1"
)
_READINESS_REPORT_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-readiness-v1"
)
_APPROVAL_REGISTRY_SCHEMA: Final[str] = (
    "inventory-positive-v3-independent-validation-approval-registry-v1"
)
_APPROVAL_STATUS: Final[str] = "approved-for-independent-validation-conformance"
_APPROVAL_REQUIRED_STATUS: Final[str] = "approval-required"
_VALIDATION_PASSED_STATUS: Final[str] = "independent-validation-passed-nonactivating"
_VALIDATION_FAILED_STATUS: Final[str] = "independent-validation-failed"
_EVALUATOR_ID: Final[str] = "inventory-positive-v3-independent-validation"
_EVALUATOR_VERSION: Final[str] = "1.0.0"
_DATASET_ROLE: Final[str] = "independent-validation-only"
_SELECTION_POLICY: Final[str] = (
    "all-owned-captures-in-source-order-no-drop-no-replacement"
)
_OPERATOR_LABEL_STATUS: Final[str] = "operator-selected-unverified"
_TRUTH_SOURCE: Final[str] = "independent-human-review"
_PUBLICATION_FLOOR: Final[float] = 0.8
_POSITIVE_STAGES: Final[tuple[str, ...]] = (
    "empty",
    "early-partial",
    "mid-partial",
    "near-full",
    "full",
)
_NEGATIVE_STAGES: Final[tuple[str, ...]] = (
    "wrong-tab",
    "row-obstruction",
)
_REQUIRED_STAGES: Final[tuple[str, ...]] = _POSITIVE_STAGES + _NEGATIVE_STAGES
_OPTIONAL_STAGE: Final[str] = "unexpected-presentation"
_PRESENTATION_FLAGS: Final[tuple[str, ...]] = (
    "drag_visible",
    "hover_visible",
    "quantity_text_visible",
    "selected_item_visible",
)
_SHA256_LENGTH: Final[int] = 64

# These object sentinels close the ordinary runtime-rebinding path: the exact
# class imported while this reviewed evaluator module was initialized must
# still own both construction and analysis.  The source-file checks below bind
# those objects to the exact Git worktree rather than an unrelated installation.
_FROZEN_ANALYZER_CLASS: Final[type[InventoryPositiveV3DevelopmentAnalyzer]] = (
    InventoryPositiveV3DevelopmentAnalyzer
)
_FROZEN_ANALYZER_INIT_CALLABLE: Final[object] = (
    InventoryPositiveV3DevelopmentAnalyzer.__init__
)
_FROZEN_ANALYZER_ANALYZE_CALLABLE: Final[object] = (
    InventoryPositiveV3DevelopmentAnalyzer.analyze
)
_REPORT_FACTORY_TOKEN: Final[object] = object()


class InventoryPositiveV3IndependentValidationError(RuntimeError):
    """The frozen-candidate or independent-validation contract was invalid."""


@dataclass(frozen=True, slots=True)
class FrozenV3ModelBinding:
    """All identity that must remain fixed across an independent campaign."""

    candidate_head_sha: str
    configuration_id: str
    model_configuration_sha256: str
    model_artifact_sha256: str
    prototype_occurrences_sha256: str
    prototype_source_set_sha256: str
    reference_region_file_sha256: str
    reference_rgb_sha256: str
    source_git_blobs: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_head_sha": self.candidate_head_sha,
            "configuration_id": self.configuration_id,
            "model_artifact_sha256": self.model_artifact_sha256,
            "model_configuration_sha256": self.model_configuration_sha256,
            "prototype_occurrences_sha256": self.prototype_occurrences_sha256,
            "prototype_source_set_sha256": self.prototype_source_set_sha256,
            "reference_region_file_sha256": self.reference_region_file_sha256,
            "reference_rgb_sha256": self.reference_rgb_sha256,
            "source_git_blobs": [
                {"git_blob": blob, "path": path}
                for path, blob in self.source_git_blobs
            ],
        }


@dataclass(frozen=True, slots=True)
class IndependentValidationEnvironment:
    """Complete environment provenance required for a future campaign."""

    capture_build_sha: str
    capture_configuration_id: str
    runelite_build: str
    windows_version: str
    windows_scaling_percent: int
    windows_dpi: int
    client_mode: str
    theme: str
    renderer: str
    window_class: str

    def to_dict(self) -> dict[str, object]:
        return {
            "capture_build_sha": self.capture_build_sha,
            "capture_configuration_id": self.capture_configuration_id,
            "client_mode": self.client_mode,
            "frame": {
                "height": SUPPORTED_FRAME_HEIGHT,
                "pixel_format": SUPPORTED_PIXEL_FORMAT,
                "profile_id": SUPPORTED_PROFILE_ID,
                "width": SUPPORTED_FRAME_WIDTH,
            },
            "renderer": self.renderer,
            "runelite_build": self.runelite_build,
            "theme": self.theme,
            "window_class": self.window_class,
            "windows_dpi": self.windows_dpi,
            "windows_scaling_percent": self.windows_scaling_percent,
            "windows_version": self.windows_version,
        }


@dataclass(frozen=True, slots=True)
class IndependentValidationTruth:
    """Reviewer-owned truth, separate from acquisition labels."""

    case_id: str
    frame_region_sha256: str
    decision: str
    visibility: str
    occupied_slots: int | None
    ordinary_iron_only: bool
    drag_visible: bool
    hover_visible: bool
    quantity_text_visible: bool
    selected_item_visible: bool
    review_note: str | None

    @property
    def has_unsupported_presentation(self) -> bool:
        return any(
            (
                self.drag_visible,
                self.hover_visible,
                self.quantity_text_visible,
                self.selected_item_visible,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "decision": self.decision,
            "drag_visible": self.drag_visible,
            "frame_region_sha256": self.frame_region_sha256,
            "hover_visible": self.hover_visible,
            "occupied_slots": self.occupied_slots,
            "ordinary_iron_only": self.ordinary_iron_only,
            "quantity_text_visible": self.quantity_text_visible,
            "review_note": self.review_note,
            "selected_item_visible": self.selected_item_visible,
            "visibility": self.visibility,
        }


@dataclass(frozen=True, slots=True)
class IndependentValidationCase:
    """One immutable capture plus its separately loaded reviewer truth."""

    sequence_index: int
    case_id: str
    session_id: str
    capture_id: str
    planned_stage_id: str
    operator_stage_label: str
    captured_at_utc: str
    capture_report_path: str
    capture_report_sha256: str
    session_report_sha256: str
    frame_region_path: str
    frame_region_sha256: str
    frame_region_payload: bytes
    truth: IndependentValidationTruth


@dataclass(frozen=True, slots=True)
class IndependentValidationDataset:
    """One finalized, immutable, independently reviewed validation package."""

    package_directory: Path
    dataset_id: str
    campaign_id: str
    session_id: str
    operator: str
    reviewer: str
    manifest_finalized_at_utc: str
    reviewed_at_utc: str
    environment: IndependentValidationEnvironment
    cases: tuple[IndependentValidationCase, ...]
    package_sha256: str
    campaign_manifest_sha256: str
    reviewer_truth_sha256: str
    source_session_report_sha256: str
    prior_campaigns: tuple[Mapping[str, object], ...]
    _snapshots: tuple[tuple[Path, bytes], ...]


@dataclass(frozen=True, slots=True)
class _ApprovedCampaignBinding:
    """One source-reviewed approval; it can never authorize activation."""

    approval_id: str
    approved_at_utc: str
    approver: str
    operator: str
    reviewer: str
    campaign_id: str
    dataset_id: str
    package_sha256: str
    campaign_manifest_sha256: str
    reviewer_truth_sha256: str
    source_session_report_sha256: str
    registry_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "approved_at_utc": self.approved_at_utc,
            "approver": self.approver,
            "campaign_id": self.campaign_id,
            "campaign_manifest_sha256": self.campaign_manifest_sha256,
            "dataset_id": self.dataset_id,
            "operator": self.operator,
            "package_sha256": self.package_sha256,
            "reviewer": self.reviewer,
            "reviewer_truth_sha256": self.reviewer_truth_sha256,
            "source_session_report_sha256": self.source_session_report_sha256,
            "status": _APPROVAL_STATUS,
        }


@dataclass(frozen=True, slots=True)
class InventoryPositiveV3IndependentValidationCaseResult:
    """Expected-versus-actual result for one independent case."""

    case_id: str
    planned_stage_id: str
    captured_at_utc: str
    frame_region_sha256: str
    byte_identical_to_development_payload: bool
    reviewer_truth: Mapping[str, object]
    expected: Mapping[str, object]
    actual: Mapping[str, object]
    passed: bool
    failure_reason: str | None

    def __post_init__(self) -> None:
        self._assert_integrity()

    def _assert_integrity(self) -> None:
        computed, reason = _serialized_result_matches_expected(
            self.actual,
            self.expected,
            self.reviewer_truth,
        )
        if self.passed != computed or self.failure_reason != reason:
            raise InventoryPositiveV3IndependentValidationError(
                f"case result integrity mismatch for {self.case_id}"
            )

    def to_dict(self) -> dict[str, object]:
        self._assert_integrity()
        return {
            "actual": dict(self.actual),
            "byte_identical_to_development_payload": (
                self.byte_identical_to_development_payload
            ),
            "captured_at_utc": self.captured_at_utc,
            "case_id": self.case_id,
            "expected": dict(self.expected),
            "failure_reason": self.failure_reason,
            "frame_region_sha256": self.frame_region_sha256,
            "passed": self.passed,
            "planned_stage_id": self.planned_stage_id,
            "reviewer_truth": dict(self.reviewer_truth),
        }


@dataclass(frozen=True, slots=True)
class InventoryPositiveV3IndependentValidationReport:
    """Canonical, permanently non-activating independent-validation report."""

    evaluator_git_head_sha: str
    preregistration_sha256: str
    candidate_binding: FrozenV3ModelBinding
    dataset: IndependentValidationDataset
    candidate_identity_before: Mapping[str, object]
    candidate_identity_after: Mapping[str, object]
    analyzer_state_sha256_before: str
    analyzer_state_sha256_after: str
    approval_registry_sha256: str
    approval: _ApprovedCampaignBinding | None
    cases: tuple[InventoryPositiveV3IndependentValidationCaseResult, ...]
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _REPORT_FACTORY_TOKEN:
            raise InventoryPositiveV3IndependentValidationError(
                "validation reports may only be constructed by the verified evaluator"
            )
        if self.candidate_binding != frozen_v3_model_binding():
            raise InventoryPositiveV3IndependentValidationError(
                "report candidate binding differs from frozen V3"
            )
        if self.preregistration_sha256 != (
            INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256
        ):
            raise InventoryPositiveV3IndependentValidationError(
                "report preregistration differs from the frozen contract"
            )
        expected_identity = _expected_candidate_identity(self.candidate_binding)
        if (
            dict(self.candidate_identity_before) != expected_identity
            or dict(self.candidate_identity_after) != expected_identity
        ):
            raise InventoryPositiveV3IndependentValidationError(
                "report candidate identity is mutated or rebound"
            )
        _require_sha256(self.analyzer_state_sha256_before, "analyzer state before")
        _require_sha256(self.analyzer_state_sha256_after, "analyzer state after")
        _require_sha256(self.approval_registry_sha256, "approval registry")
        if (
            self.analyzer_state_sha256_before
            != _FROZEN_ANALYZER_RUNTIME_STATE_SHA256
            or self.analyzer_state_sha256_after
            != _FROZEN_ANALYZER_RUNTIME_STATE_SHA256
        ):
            raise InventoryPositiveV3IndependentValidationError(
                "report analyzer state differs from frozen V3"
            )
        if len(self.cases) != len(self.dataset.cases):
            raise InventoryPositiveV3IndependentValidationError(
                "report case count differs from the finalized dataset"
            )
        for result, case in zip(self.cases, self.dataset.cases, strict=True):
            if (
                result.case_id != case.case_id
                or result.planned_stage_id != case.planned_stage_id
                or result.captured_at_utc != case.captured_at_utc
                or result.frame_region_sha256 != case.frame_region_sha256
                or dict(result.reviewer_truth) != case.truth.to_dict()
                or dict(result.expected) != _expected_result(case)
            ):
                raise InventoryPositiveV3IndependentValidationError(
                    f"report case is rebound from its dataset: {case.case_id}"
                )
        if self.approval is not None:
            _require_approval_matches_dataset(
                self.approval,
                self.dataset,
                self.approval_registry_sha256,
            )

    @property
    def detector_conformance_passed(self) -> bool:
        for item in self.cases:
            item._assert_integrity()
        return bool(self.cases) and all(item.passed for item in self.cases)

    @property
    def validation_passed(self) -> bool:
        return self.detector_conformance_passed and self.approval is not None

    @property
    def activation_allowed(self) -> bool:
        return False

    @property
    def validation_status(self) -> str:
        if self.approval is None:
            return _APPROVAL_REQUIRED_STATUS
        if self.detector_conformance_passed:
            return _VALIDATION_PASSED_STATUS
        return _VALIDATION_FAILED_STATUS

    def to_dict(self) -> dict[str, object]:
        return {
            "action_authority": _zero_action_authority(),
            "activation_allowed": False,
            "candidate_identity_after": dict(self.candidate_identity_after),
            "candidate_identity_before": dict(self.candidate_identity_before),
            "candidate_model": self.candidate_binding.to_dict(),
            "campaign": {
                "campaign_id": self.dataset.campaign_id,
                "campaign_manifest_sha256": (
                    self.dataset.campaign_manifest_sha256
                ),
                "dataset_id": self.dataset.dataset_id,
                "manifest_finalized_at_utc": (
                    self.dataset.manifest_finalized_at_utc
                ),
                "operator": self.dataset.operator,
                "environment": self.dataset.environment.to_dict(),
                "package_sha256": self.dataset.package_sha256,
                "prior_campaigns": [
                    dict(item) for item in self.dataset.prior_campaigns
                ],
                "reviewer_truth_sha256": self.dataset.reviewer_truth_sha256,
                "reviewed_at_utc": self.dataset.reviewed_at_utc,
                "reviewer": self.dataset.reviewer,
                "session_id": self.dataset.session_id,
                "source_session_report_sha256": (
                    self.dataset.source_session_report_sha256
                ),
            },
            "cases": [item.to_dict() for item in self.cases],
            "detector_conformance_passed": self.detector_conformance_passed,
            "contamination_firewall": {
                "candidate_identity_unchanged": (
                    dict(self.candidate_identity_before)
                    == dict(self.candidate_identity_after)
                ),
                "development_and_validation_dataset_paths_are_separate": True,
                "prototype_learning_allowed": False,
                "prototypes_added": 0,
                "training_allowed": False,
                "validation_case_export_to_model_allowed": False,
            },
            "evaluator": {
                "git_head_sha": self.evaluator_git_head_sha,
                "id": _EVALUATOR_ID,
                "version": _EVALUATOR_VERSION,
            },
            "independent_validation_case_count": len(self.cases),
            "approval": None if self.approval is None else self.approval.to_dict(),
            "approval_registry_sha256": self.approval_registry_sha256,
            "analyzer_state_sha256_after": self.analyzer_state_sha256_after,
            "analyzer_state_sha256_before": self.analyzer_state_sha256_before,
            "preregistration_sha256": self.preregistration_sha256,
            "promotion_allowed": False,
            "report_schema": _REPORT_SCHEMA,
            "validation_passed": self.validation_passed,
            "validation_status": self.validation_status,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class _FrozenCandidate:
    analyzer: InventoryPositiveV3DevelopmentAnalyzer
    binding: FrozenV3ModelBinding
    development_case_ids: frozenset[str]
    development_session_ids: frozenset[str]
    development_capture_ids: frozenset[str]
    development_payload_sha256s: frozenset[str]
    development_fixture_root: Path


@dataclass(frozen=True, slots=True)
class _SourceCaptureBinding:
    capture_id: str
    captured_at_utc: str
    report_path: str
    report_sha256: str


@dataclass(frozen=True, slots=True)
class _SourceSessionBinding:
    session_id: str
    campaign_id: str
    started_at_utc: str
    completed_at_utc: str
    environment: Mapping[str, object]
    captures: tuple[_SourceCaptureBinding, ...]
    report_sha256: str


@dataclass(frozen=True, slots=True)
class _SourceRegionBinding:
    path: str
    sha256: str
    size_bytes: int


def frozen_v3_model_binding() -> FrozenV3ModelBinding:
    """Return the source-owned binding for the exact frozen V3 candidate."""
    return FrozenV3ModelBinding(
        candidate_head_sha=INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA,
        configuration_id=_FROZEN_CONFIGURATION_ID,
        model_configuration_sha256=_FROZEN_MODEL_CONFIGURATION_SHA256,
        model_artifact_sha256=_FROZEN_MODEL_ARTIFACT_SHA256,
        prototype_occurrences_sha256=_FROZEN_PROTOTYPE_OCCURRENCES_SHA256,
        prototype_source_set_sha256=_FROZEN_PROTOTYPE_SOURCE_SET_SHA256,
        reference_region_file_sha256=_FROZEN_REFERENCE_REGION_FILE_SHA256,
        reference_rgb_sha256=SUPPORTED_REFERENCE_RGB_SHA256,
        source_git_blobs=_FROZEN_GIT_BLOBS,
    )


def independent_validation_preregistration() -> Mapping[str, object]:
    """Return the immutable plan that predates every future validation pixel."""
    binding = frozen_v3_model_binding()
    return copy.deepcopy(
        {
            "activation_allowed": False,
            "candidate": {
                **binding.to_dict(),
                "analyzer": {
                    "id": INVENTORY_POSITIVE_V3_ANALYZER_ID,
                    "version": INVENTORY_POSITIVE_V3_ANALYZER_VERSION,
                },
                "classifier": {
                    "id": INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_ID,
                    "version": INVENTORY_POSITIVE_V3_CANDIDATE_CLASSIFIER_VERSION,
                },
                "detector": {
                    "id": INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_ID,
                    "version": INVENTORY_POSITIVE_V3_CANDIDATE_DETECTOR_VERSION,
                },
                "freeze_committed_at_utc": _FROZEN_COMMITTED_AT_UTC,
                "profile": {
                    "column_stride": SUPPORTED_COLUMN_STRIDE,
                    "frame_height": SUPPORTED_FRAME_HEIGHT,
                    "frame_width": SUPPORTED_FRAME_WIDTH,
                    "pixel_format": SUPPORTED_PIXEL_FORMAT,
                    "profile_id": SUPPORTED_PROFILE_ID,
                    "region": list(SUPPORTED_REGION),
                    "row_stride": SUPPORTED_ROW_STRIDE,
                },
                "publication_floor": _PUBLICATION_FLOOR,
            },
            "development_evidence": {
                "case_ids_sha256": _FROZEN_DEVELOPMENT_CASE_IDS_SHA256,
                "capture_ids_sha256": _FROZEN_DEVELOPMENT_CAPTURE_IDS_SHA256,
                "dataset_id": DEVELOPMENT_DATASET_ID,
                "dataset_role": "development-self-fit-only",
                "manifest_sha256": DEVELOPMENT_MANIFEST_SHA256,
                "session_ids_sha256": _FROZEN_DEVELOPMENT_SESSION_IDS_SHA256,
                "validation_eligible": False,
            },
            "approval_gate": {
                "approval_status": _APPROVAL_STATUS,
                "registry_path": _APPROVAL_REGISTRY_PATH.as_posix(),
                "registry_schema": _APPROVAL_REGISTRY_SCHEMA,
                "required_content_bindings": [
                    "campaign_id",
                    "dataset_id",
                    "package_sha256",
                    "campaign_manifest_sha256",
                    "reviewer_truth_sha256",
                    "source_session_report_sha256",
                ],
                "required_distinct_roles": [
                    "operator",
                    "reviewer",
                    "approver",
                ],
                "unapproved_detector_conformance_can_validate": False,
            },
            "evaluator": {
                "id": _EVALUATOR_ID,
                "version": _EVALUATOR_VERSION,
            },
            "future_campaign": {
                "count_contract": (
                    "reviewer truth must prove empty=0, "
                    "0<early-partial<mid-partial<near-full<28, full=28"
                ),
                "negative_case_sequence": list(_NEGATIVE_STAGES),
                "optional_unsupported_presentations": [
                    "drag",
                    "gapped-inventory",
                    "hover",
                    "quantity-text",
                    "selected-item",
                    "unexpected-foreign-item",
                ],
                "positive_case_sequence": list(_POSITIVE_STAGES),
                "required_environment_fields": [
                    "capture_build_sha",
                    "capture_configuration_id",
                    "client_mode",
                    "frame.height",
                    "frame.pixel_format",
                    "frame.profile_id",
                    "frame.width",
                    "renderer",
                    "runelite_build",
                    "theme",
                    "window_class",
                    "windows_dpi",
                    "windows_scaling_percent",
                    "windows_version",
                ],
                "selection_policy": _SELECTION_POLICY,
                "session_policy": (
                    "one natural-fill session; every owned capture evaluated; "
                    "aborted/restarted work receives a new disclosed campaign id"
                ),
            },
            "model_firewall": {
                "calibration_allowed": False,
                "candidate_rebinding_allowed": False,
                "model_mutation_allowed": False,
                "post_campaign_tuning_allowed": False,
                "prototype_learning_allowed": False,
                "training_allowed": False,
                "validation_case_export_to_model_allowed": False,
            },
            "promotion_allowed": False,
            "preregistration": {
                "effective_at_utc": _PREREGISTRATION_EFFECTIVE_AT_UTC,
                "repository_base_head_sha": _PREREGISTRATION_BASE_HEAD_SHA,
                "source_path": _PREREGISTRATION_PATH.as_posix(),
            },
            "schema": _PREREGISTRATION_SCHEMA,
            "validation_status": INVENTORY_POSITIVE_V3_VALIDATION_STATUS,
        }
    )


def build_inventory_positive_v3_validation_readiness_report(
    repository_root: Path,
    *,
    evaluator_git_head_sha: str,
) -> Mapping[str, object]:
    """Verify the frozen model and return a zero-live-case readiness report."""
    _require_git_sha(evaluator_git_head_sha, "evaluator_git_head_sha")
    verified_root = _verify_repository_state(repository_root, evaluator_git_head_sha)
    candidate = _load_frozen_candidate(verified_root)
    preregistration_sha = _verify_repository_preregistration(verified_root)
    approval_registry = _approval_registry_readiness(verified_root)
    identity = _candidate_identity(candidate.analyzer)
    report = {
        "action_authority": _zero_action_authority(),
        "activation_allowed": False,
        "campaign_execution_authorized": False,
        "candidate_identity": identity,
        "candidate_model": candidate.binding.to_dict(),
        "approval_registry": approval_registry,
        "contamination_firewall": {
            "development_case_count": len(candidate.development_case_ids),
            "development_dataset_role": "development-self-fit-only",
            "independent_dataset_role": _DATASET_ROLE,
            "prototype_learning_allowed": False,
            "training_allowed": False,
            "validation_case_export_to_model_allowed": False,
        },
        "evaluator": {
            "git_head_sha": evaluator_git_head_sha,
            "id": _EVALUATOR_ID,
            "version": _EVALUATOR_VERSION,
        },
        "future_campaign": copy.deepcopy(
            independent_validation_preregistration()["future_campaign"]
        ),
        "independent_validation_case_count": 0,
        "live_validation_performed": False,
        "preregistration_sha256": preregistration_sha,
        "readiness_passed": True,
        "report_schema": _READINESS_REPORT_SCHEMA,
        "validation_status": INVENTORY_POSITIVE_V3_VALIDATION_STATUS,
    }
    _verify_repository_state(repository_root, evaluator_git_head_sha)
    return report


def load_independent_validation_dataset(
    package_directory: Path,
    *,
    expected_preregistration_sha256: str = (
        INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256
    ),
) -> IndependentValidationDataset:
    """Load one finalized package without opening a model-update channel."""
    if not isinstance(package_directory, Path):
        raise TypeError("package_directory must be pathlib.Path")
    _require_sha256(
        expected_preregistration_sha256,
        "expected_preregistration_sha256",
    )
    root = package_directory.resolve(strict=True)
    snapshots: list[tuple[Path, bytes]] = []
    package, package_bytes = _read_canonical_document(
        root,
        "validation-package.json",
        _PACKAGE_SCHEMA,
        snapshots,
    )
    _require_exact_keys(
        package,
        {
            "activation_allowed",
            "campaign_manifest",
            "dataset_role",
            "preregistration_sha256",
            "prototype_eligible",
            "reviewer_truth",
            "schema",
            "training_allowed",
        },
        "validation package",
    )
    _require_nonactivating_roles(package, "validation package")
    if package.get("preregistration_sha256") != expected_preregistration_sha256:
        raise InventoryPositiveV3IndependentValidationError(
            "validation package preregistration differs from the frozen candidate"
        )
    campaign_ref = _require_object(package, "campaign_manifest")
    review_ref = _require_object(package, "reviewer_truth")
    _require_exact_keys(campaign_ref, {"path", "sha256"}, "campaign manifest ref")
    _require_exact_keys(review_ref, {"path", "sha256"}, "review truth ref")

    manifest_path = _owned_path(
        root,
        _require_text(campaign_ref, "path"),
        "campaign manifest",
    )
    review_path = _owned_path(
        root,
        _require_text(review_ref, "path"),
        "review truth",
    )
    manifest, manifest_bytes = _read_canonical_path(
        manifest_path,
        _DATASET_SCHEMA,
        "campaign manifest",
        snapshots,
        owned_root=root,
    )
    review, review_bytes = _read_canonical_path(
        review_path,
        _REVIEW_SCHEMA,
        "reviewer truth",
        snapshots,
        owned_root=root,
    )
    manifest_sha = _sha256(manifest_bytes)
    review_sha = _sha256(review_bytes)
    if campaign_ref.get("sha256") != manifest_sha:
        raise InventoryPositiveV3IndependentValidationError(
            "campaign manifest hash differs from validation package"
        )
    if review_ref.get("sha256") != review_sha:
        raise InventoryPositiveV3IndependentValidationError(
            "review truth hash differs from validation package"
        )

    dataset = _parse_dataset(
        root,
        manifest,
        manifest_sha,
        review,
        review_sha,
        snapshots,
    )
    return IndependentValidationDataset(
        package_directory=root,
        dataset_id=dataset.dataset_id,
        campaign_id=dataset.campaign_id,
        session_id=dataset.session_id,
        operator=dataset.operator,
        reviewer=dataset.reviewer,
        manifest_finalized_at_utc=dataset.manifest_finalized_at_utc,
        reviewed_at_utc=dataset.reviewed_at_utc,
        environment=dataset.environment,
        cases=dataset.cases,
        package_sha256=_sha256(package_bytes),
        campaign_manifest_sha256=manifest_sha,
        reviewer_truth_sha256=review_sha,
        source_session_report_sha256=dataset.source_session_report_sha256,
        prior_campaigns=dataset.prior_campaigns,
        _snapshots=tuple(snapshots),
    )


def evaluate_frozen_v3_independent_validation(
    package_directory: Path,
    *,
    repository_root: Path,
    evaluator_git_head_sha: str,
) -> InventoryPositiveV3IndependentValidationReport:
    """Evaluate a separate validation package with the frozen V3 candidate."""
    _require_git_sha(evaluator_git_head_sha, "evaluator_git_head_sha")
    verified_root = _verify_repository_state(repository_root, evaluator_git_head_sha)
    _verify_analyzer_runtime_sentinels(verified_root)
    # The analyzer is fully constructed and identity-checked before any
    # validation bytes or reviewer truth are opened.
    candidate = _load_frozen_candidate(verified_root)
    preregistration_sha = _verify_repository_preregistration(verified_root)
    before = _candidate_identity(candidate.analyzer)
    analyzer_state_before = _analyzer_runtime_state_sha256(candidate.analyzer)
    if analyzer_state_before != _FROZEN_ANALYZER_RUNTIME_STATE_SHA256:
        raise InventoryPositiveV3IndependentValidationError(
            "runtime V3 analyzer state differs from the frozen source-owned state"
        )
    _reject_development_path_reuse(package_directory, candidate)
    dataset = load_independent_validation_dataset(
        package_directory,
        expected_preregistration_sha256=preregistration_sha,
    )
    _reject_development_identity_reuse(dataset, candidate)
    approval, approval_registry_sha256 = _load_approved_campaign_binding(
        verified_root,
        dataset,
    )

    results: list[InventoryPositiveV3IndependentValidationCaseResult] = []
    for frame_id, item in enumerate(dataset.cases, start=1):
        frame = _frame_from_region(item.frame_region_payload, frame_id=frame_id)
        actual = candidate.analyzer.analyze(frame)
        expected = _expected_result(item)
        passed, failure_reason = _matches_expected(actual, expected, item.truth)
        results.append(
            InventoryPositiveV3IndependentValidationCaseResult(
                case_id=item.case_id,
                planned_stage_id=item.planned_stage_id,
                captured_at_utc=item.captured_at_utc,
                frame_region_sha256=item.frame_region_sha256,
                byte_identical_to_development_payload=(
                    item.frame_region_sha256
                    in candidate.development_payload_sha256s
                ),
                reviewer_truth=item.truth.to_dict(),
                expected=expected,
                actual=actual.to_dict(),
                passed=passed,
                failure_reason=failure_reason,
            )
        )

    _assert_snapshot_files_unchanged(dataset._snapshots)
    _verify_analyzer_runtime_sentinels(verified_root)
    after = _candidate_identity(candidate.analyzer)
    analyzer_state_after = _analyzer_runtime_state_sha256(candidate.analyzer)
    if before != after:
        raise InventoryPositiveV3IndependentValidationError(
            "validation evaluation mutated or rebound the frozen V3 candidate"
        )
    if analyzer_state_before != analyzer_state_after:
        raise InventoryPositiveV3IndependentValidationError(
            "validation evaluation mutated live frozen V3 analyzer state"
        )
    _verify_repository_state(repository_root, evaluator_git_head_sha)
    return InventoryPositiveV3IndependentValidationReport(
        evaluator_git_head_sha=evaluator_git_head_sha,
        preregistration_sha256=preregistration_sha,
        candidate_binding=candidate.binding,
        dataset=dataset,
        candidate_identity_before=before,
        candidate_identity_after=after,
        analyzer_state_sha256_before=analyzer_state_before,
        analyzer_state_sha256_after=analyzer_state_after,
        approval_registry_sha256=approval_registry_sha256,
        approval=approval,
        cases=tuple(results),
        _factory_token=_REPORT_FACTORY_TOKEN,
    )


def _load_frozen_candidate(repository_root: Path) -> _FrozenCandidate:
    if not isinstance(repository_root, Path):
        raise TypeError("repository_root must be pathlib.Path")
    root = repository_root.resolve(strict=True)
    fixture = root.joinpath(*_DEVELOPMENT_FIXTURE.parts)
    manifest_path = fixture / "manifest.json"
    manifest_bytes = _read_bytes(manifest_path, "frozen development manifest")
    if _sha256(manifest_bytes) != DEVELOPMENT_MANIFEST_SHA256:
        raise InventoryPositiveV3IndependentValidationError(
            "frozen development manifest differs from the candidate binding"
        )
    expected_sidecar = f"{DEVELOPMENT_MANIFEST_SHA256}  manifest.json\n"
    sidecar = _read_text(
        fixture / "manifest.json.sha256",
        "frozen development manifest sidecar",
    )
    if sidecar != expected_sidecar:
        raise InventoryPositiveV3IndependentValidationError(
            "frozen development manifest sidecar differs from its bytes"
        )
    manifest = _json_object(manifest_bytes, "frozen development manifest")
    if manifest.get("dataset_id") != DEVELOPMENT_DATASET_ID:
        raise InventoryPositiveV3IndependentValidationError(
            "frozen development dataset identity changed"
        )
    development = _development_identities(manifest)
    if _sha256(_canonical_data_bytes(sorted(development[0]))) != (
        _FROZEN_DEVELOPMENT_CASE_IDS_SHA256
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "frozen development case identities changed"
        )
    if _sha256(_canonical_data_bytes(sorted(development[1]))) != (
        _FROZEN_DEVELOPMENT_SESSION_IDS_SHA256
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "frozen development session identities changed"
        )
    if _sha256(_canonical_data_bytes(sorted(development[2]))) != (
        _FROZEN_DEVELOPMENT_CAPTURE_IDS_SHA256
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "frozen development capture identities changed"
        )

    reference_path = fixture.joinpath(*_DEVELOPMENT_REFERENCE_REGION.parts)
    reference_region = _read_bytes(reference_path, "frozen V3 reference region")
    if _sha256(reference_region) != _FROZEN_REFERENCE_REGION_FILE_SHA256:
        raise InventoryPositiveV3IndependentValidationError(
            "frozen V3 reference-region payload changed"
        )
    analyzer = InventoryPositiveV3DevelopmentAnalyzer(
        _supported_profile(),
        _frame_from_region(reference_region, frame_id=1),
    )
    binding = frozen_v3_model_binding()
    identity = _candidate_identity(analyzer)
    expected_identity = {
        "configuration_id": binding.configuration_id,
        "model_artifact_sha256": binding.model_artifact_sha256,
        "model_configuration_sha256": binding.model_configuration_sha256,
        "prototype_occurrences_sha256": binding.prototype_occurrences_sha256,
        "prototype_source_set_sha256": binding.prototype_source_set_sha256,
    }
    if identity != expected_identity:
        raise InventoryPositiveV3IndependentValidationError(
            "runtime V3 candidate differs from the preregistered frozen identity"
        )
    return _FrozenCandidate(
        analyzer=analyzer,
        binding=binding,
        development_case_ids=frozenset(development[0]),
        development_session_ids=frozenset(development[1]),
        development_capture_ids=frozenset(development[2]),
        development_payload_sha256s=frozenset(development[3]),
        development_fixture_root=fixture.resolve(strict=True),
    )


def _verify_repository_state(repository_root: Path, expected_head: str) -> Path:
    """Bind every report-producing API to one clean repository head."""
    if not isinstance(repository_root, Path):
        raise TypeError("repository_root must be pathlib.Path")
    root = repository_root.resolve(strict=True)

    def git(*arguments: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 and not allow_failure:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise InventoryPositiveV3IndependentValidationError(
                f"Git command failed: {detail}"
            )
        return completed

    actual_root = Path(git("rev-parse", "--show-toplevel").stdout.strip()).resolve(
        strict=True
    )
    if actual_root != root:
        raise InventoryPositiveV3IndependentValidationError(
            "repository_root is not the exact Git worktree root"
        )
    actual_head = git("rev-parse", "HEAD").stdout.strip()
    if actual_head != expected_head:
        raise InventoryPositiveV3IndependentValidationError(
            f"Git HEAD mismatch: expected {expected_head}, got {actual_head}"
        )
    if git("status", "--porcelain=v1").stdout.strip():
        raise InventoryPositiveV3IndependentValidationError(
            "worktree changes prevent exact-head independent-validation evidence"
        )
    ancestor = git(
        "merge-base",
        "--is-ancestor",
        INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA,
        actual_head,
        allow_failure=True,
    )
    if ancestor.returncode != 0:
        raise InventoryPositiveV3IndependentValidationError(
            "evaluator head is not descended from the frozen V3 candidate"
        )
    for path, expected_blob in _FROZEN_GIT_BLOBS:
        actual_blob = git("rev-parse", f"HEAD:{path}").stdout.strip()
        if actual_blob != expected_blob:
            raise InventoryPositiveV3IndependentValidationError(
                f"frozen V3 transitive source changed: {path}"
            )
    return root


def _supported_profile() -> InventoryFrameProfile:
    region = Region(*SUPPORTED_REGION)
    layout = InventoryGridLayout(
        profile_id=SUPPORTED_PROFILE_ID,
        column_stride=SUPPORTED_COLUMN_STRIDE,
        row_stride=SUPPORTED_ROW_STRIDE,
    )
    if layout.region_at(region.x, region.y) != region:
        raise InventoryPositiveV3IndependentValidationError(
            "frozen V3 profile geometry is internally inconsistent"
        )
    return InventoryFrameProfile(
        profile_id=SUPPORTED_PROFILE_ID,
        frame_width=SUPPORTED_FRAME_WIDTH,
        frame_height=SUPPORTED_FRAME_HEIGHT,
        region=region,
        layout=layout,
    )


def _frame_from_region(payload: bytes, *, frame_id: int) -> Frame:
    region = Region(*SUPPORTED_REGION)
    expected = region.width * region.height * PixelFormat.BGRA8888.bytes_per_pixel
    if len(payload) != expected:
        raise InventoryPositiveV3IndependentValidationError(
            f"inventory region payload has {len(payload)} bytes; expected {expected}"
        )
    frame = bytearray(
        SUPPORTED_FRAME_WIDTH
        * SUPPORTED_FRAME_HEIGHT
        * PixelFormat.BGRA8888.bytes_per_pixel
    )
    source_stride = region.width * PixelFormat.BGRA8888.bytes_per_pixel
    destination_stride = SUPPORTED_FRAME_WIDTH * PixelFormat.BGRA8888.bytes_per_pixel
    for row in range(region.height):
        source_start = row * source_stride
        destination_start = (
            (region.y + row) * destination_stride
            + region.x * PixelFormat.BGRA8888.bytes_per_pixel
        )
        frame[destination_start : destination_start + source_stride] = payload[
            source_start : source_start + source_stride
        ]
    return Frame.from_raw(
        RawFrame(
            payload=bytes(frame),
            width=SUPPORTED_FRAME_WIDTH,
            height=SUPPORTED_FRAME_HEIGHT,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _candidate_identity(
    analyzer: InventoryPositiveV3DevelopmentAnalyzer,
) -> dict[str, object]:
    model_configuration = analyzer.model_configuration
    return {
        "configuration_id": analyzer.configuration_id,
        "model_artifact_sha256": MODEL_ARTIFACT_SHA256,
        "model_configuration_sha256": _sha256(
            _canonical_data_bytes(model_configuration)
        ),
        "prototype_occurrences_sha256": _sha256(
            _canonical_data_bytes(FULL_SLOT_RGB_PROTOTYPE_OCCURRENCES)
        ),
        "prototype_source_set_sha256": _sha256(
            _canonical_data_bytes(PROTOTYPE_SOURCE_REGION_SHA256S)
        ),
    }


def _expected_candidate_identity(
    binding: FrozenV3ModelBinding,
) -> dict[str, object]:
    return {
        "configuration_id": binding.configuration_id,
        "model_artifact_sha256": binding.model_artifact_sha256,
        "model_configuration_sha256": binding.model_configuration_sha256,
        "prototype_occurrences_sha256": binding.prototype_occurrences_sha256,
        "prototype_source_set_sha256": binding.prototype_source_set_sha256,
    }


def _verify_analyzer_runtime_sentinels(repository_root: Path) -> None:
    if (
        InventoryPositiveV3DevelopmentAnalyzer is not _FROZEN_ANALYZER_CLASS
        or InventoryPositiveV3DevelopmentAnalyzer.__init__
        is not _FROZEN_ANALYZER_INIT_CALLABLE
        or InventoryPositiveV3DevelopmentAnalyzer.analyze
        is not _FROZEN_ANALYZER_ANALYZE_CALLABLE
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "runtime V3 analyzer class/callable binding changed"
        )
    expected = repository_root.resolve(strict=True) / Path(
        "src/mining_automation/perception/inventory/positive_classifier_v3.py"
    )
    for label, value in (
        ("class", InventoryPositiveV3DevelopmentAnalyzer),
        ("constructor", InventoryPositiveV3DevelopmentAnalyzer.__init__),
        ("analyze", InventoryPositiveV3DevelopmentAnalyzer.analyze),
    ):
        source = inspect.getsourcefile(value)
        if source is None or Path(source).resolve(strict=True) != expected:
            raise InventoryPositiveV3IndependentValidationError(
                f"runtime V3 analyzer {label} is not owned by the verified source"
            )


def _analyzer_runtime_state_sha256(
    analyzer: InventoryPositiveV3DevelopmentAnalyzer,
) -> str:
    if type(analyzer) is not _FROZEN_ANALYZER_CLASS:
        raise InventoryPositiveV3IndependentValidationError(
            "runtime V3 analyzer instance class changed"
        )
    baseline = analyzer._baseline
    profile = analyzer._profile
    value = {
        "candidate_identity": _candidate_identity(analyzer),
        "model_configuration": analyzer._model_configuration,
        "profile": {
            "frame_height": profile.frame_height,
            "frame_width": profile.frame_width,
            "layout": {
                "column_stride": profile.layout.column_stride,
                "profile_id": profile.layout.profile_id,
                "row_stride": profile.layout.row_stride,
            },
            "profile_id": profile.profile_id,
            "region": profile.region.as_tuple(),
        },
        "prototype_sources": analyzer._prototype_sources,
        "reference_frame": {
            "height": analyzer._reference.height,
            "payload_sha256": _sha256(analyzer._reference.payload),
            "pixel_format": analyzer._reference.pixel_format.value,
            "width": analyzer._reference.width,
        },
        "reference_slot_rgb_sha256s": [
            _sha256(payload) for payload in analyzer._reference_slot_rgb
        ],
        "baseline": {
            "configuration_id": baseline.configuration_id,
            "guard_offsets": baseline._guard_offsets,
            "layout": {
                "column_stride": baseline._layout.column_stride,
                "profile_id": baseline._layout.profile_id,
                "row_stride": baseline._layout.row_stride,
            },
            "policy": {
                "core_inset": baseline._policy.core_inset,
                "empty_max_score": baseline._policy.empty_max_score,
                "max_guard_changed_fraction": (
                    baseline._policy.max_guard_changed_fraction
                ),
                "max_row_guard_changed_fraction": (
                    baseline._policy.max_row_guard_changed_fraction
                ),
                "minimum_slot_confidence": (
                    baseline._policy.minimum_slot_confidence
                ),
                "occupied_min_score": baseline._policy.occupied_min_score,
                "pixel_difference_threshold": (
                    baseline._policy.pixel_difference_threshold
                ),
            },
            "reference_cores": baseline._reference_cores,
            "reference_guard": baseline._reference_guard,
            "reference_row_guard": baseline._reference_row_guard,
            "reference_sha256": baseline._reference_sha256,
            "row_guard_offsets": baseline._row_guard_offsets,
        },
    }
    return _sha256(_canonical_data_bytes(value))


def _verify_repository_preregistration(repository_root: Path) -> str:
    root = repository_root.resolve(strict=True)
    path = root.joinpath(*_PREREGISTRATION_PATH.parts)
    payload = _read_bytes(path, "source-owned V3 preregistration")
    expected = _canonical_bytes(independent_validation_preregistration())
    if payload != expected:
        raise InventoryPositiveV3IndependentValidationError(
            "source-owned V3 preregistration differs from the compiled contract"
        )
    digest = _sha256(payload)
    if digest != INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256:
        raise InventoryPositiveV3IndependentValidationError(
            "source-owned V3 preregistration hash differs from its frozen pin"
        )
    sidecar = _read_text(
        path.with_suffix(".sha256"),
        "source-owned V3 preregistration sidecar",
    )
    if sidecar != f"{digest}  {path.name}\n":
        raise InventoryPositiveV3IndependentValidationError(
            "source-owned V3 preregistration sidecar mismatch"
        )
    return digest


def _load_approved_campaign_binding(
    repository_root: Path,
    dataset: IndependentValidationDataset,
) -> tuple[_ApprovedCampaignBinding | None, str]:
    registry, payload, snapshots = _read_approval_registry(repository_root)
    digest = _sha256(payload)
    result = _parse_approval_registry(
        _require_list(registry, "entries"),
        dataset,
        registry_sha256=digest,
    )
    _assert_snapshot_files_unchanged(tuple(snapshots))
    return result, digest


def _approval_registry_readiness(repository_root: Path) -> dict[str, object]:
    registry, payload, snapshots = _read_approval_registry(repository_root)
    entries = _require_list(registry, "entries")
    digest = _sha256(payload)
    _parse_approval_registry(entries, None, registry_sha256=digest)
    _assert_snapshot_files_unchanged(tuple(snapshots))
    return {
        "approved_campaign_count": len(entries),
        "path": _APPROVAL_REGISTRY_PATH.as_posix(),
        "sha256": digest,
    }


def _read_approval_registry(
    repository_root: Path,
) -> tuple[dict[str, object], bytes, list[tuple[Path, bytes]]]:
    root = repository_root.resolve(strict=True)
    path = _owned_path(
        root,
        _APPROVAL_REGISTRY_PATH.as_posix(),
        "source-owned approved-campaign registry",
    )
    snapshots: list[tuple[Path, bytes]] = []
    registry, payload = _read_canonical_path(
        path,
        _APPROVAL_REGISTRY_SCHEMA,
        "source-owned approved-campaign registry",
        snapshots,
        owned_root=root,
    )
    _require_exact_keys(
        registry,
        {"activation_allowed", "entries", "promotion_allowed", "schema"},
        "approved-campaign registry",
    )
    if (
        registry.get("activation_allowed") is not False
        or registry.get("promotion_allowed") is not False
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "approved-campaign registry cannot authorize activation or promotion"
        )
    return registry, payload, snapshots


def _parse_approval_registry(
    entries: Sequence[object],
    dataset: IndependentValidationDataset | None,
    *,
    registry_sha256: str,
) -> _ApprovedCampaignBinding | None:
    _require_sha256(registry_sha256, "approval registry")
    matches: list[_ApprovedCampaignBinding] = []
    seen_approval_ids: set[str] = set()
    seen_campaigns: set[str] = set()
    seen_datasets: set[str] = set()
    expected_keys = {
        "approval_id",
        "approved_at_utc",
        "approver",
        "campaign_id",
        "campaign_manifest_sha256",
        "dataset_id",
        "operator",
        "package_sha256",
        "reviewer",
        "reviewer_truth_sha256",
        "source_session_report_sha256",
        "status",
    }
    for index, raw in enumerate(entries, start=1):
        value = _require_mapping(raw, f"approved campaign {index}")
        _require_exact_keys(value, expected_keys, f"approved campaign {index}")
        approval_id = _require_nonempty_text(value, "approval_id")
        campaign_id = _require_nonempty_text(value, "campaign_id")
        dataset_id = _require_nonempty_text(value, "dataset_id")
        if (
            approval_id in seen_approval_ids
            or campaign_id in seen_campaigns
            or dataset_id in seen_datasets
        ):
            raise InventoryPositiveV3IndependentValidationError(
                "approved-campaign registry contains duplicate identity"
            )
        seen_approval_ids.add(approval_id)
        seen_campaigns.add(campaign_id)
        seen_datasets.add(dataset_id)
        operator = _require_actor_identity(value, "operator")
        reviewer = _require_actor_identity(value, "reviewer")
        approver = _require_actor_identity(value, "approver")
        if len({operator, reviewer, approver}) != 3:
            raise InventoryPositiveV3IndependentValidationError(
                "approval operator, reviewer, and approver must be distinct"
            )
        if value.get("status") != _APPROVAL_STATUS:
            raise InventoryPositiveV3IndependentValidationError(
                "approved-campaign registry entry has unsupported status"
            )
        approved_at = _require_nonempty_text(value, "approved_at_utc")
        _parse_utc(approved_at, "approved_at_utc")
        binding = _ApprovedCampaignBinding(
            approval_id=approval_id,
            approved_at_utc=approved_at,
            approver=approver,
            operator=operator,
            reviewer=reviewer,
            campaign_id=campaign_id,
            dataset_id=dataset_id,
            package_sha256=_require_sha256_text(value, "package_sha256"),
            campaign_manifest_sha256=_require_sha256_text(
                value, "campaign_manifest_sha256"
            ),
            reviewer_truth_sha256=_require_sha256_text(
                value, "reviewer_truth_sha256"
            ),
            source_session_report_sha256=_require_sha256_text(
                value, "source_session_report_sha256"
            ),
            registry_sha256=registry_sha256,
        )
        if approval_id != _content_bound_approval_id(binding):
            raise InventoryPositiveV3IndependentValidationError(
                "approval_id is not derived from its immutable approval evidence"
            )
        relates = dataset is not None and (
            campaign_id == dataset.campaign_id or dataset_id == dataset.dataset_id
        )
        if relates:
            assert dataset is not None
            _require_approval_matches_dataset(binding, dataset, registry_sha256)
            matches.append(binding)
    if len(matches) > 1:
        raise InventoryPositiveV3IndependentValidationError(
            "multiple approvals bind the same independent campaign"
        )
    return matches[0] if matches else None


def _content_bound_approval_id(binding: _ApprovedCampaignBinding) -> str:
    value = binding.to_dict()
    value.pop("approval_id")
    return "inventory-positive-v3-approval-" + _sha256(
        _canonical_data_bytes(value)
    )[:24]


def _require_approval_matches_dataset(
    approval: _ApprovedCampaignBinding,
    dataset: IndependentValidationDataset,
    registry_sha256: str,
) -> None:
    expected = {
        "campaign_id": dataset.campaign_id,
        "campaign_manifest_sha256": dataset.campaign_manifest_sha256,
        "dataset_id": dataset.dataset_id,
        "operator": dataset.operator,
        "package_sha256": dataset.package_sha256,
        "reviewer": dataset.reviewer,
        "reviewer_truth_sha256": dataset.reviewer_truth_sha256,
        "source_session_report_sha256": dataset.source_session_report_sha256,
    }
    actual = {
        "campaign_id": approval.campaign_id,
        "campaign_manifest_sha256": approval.campaign_manifest_sha256,
        "dataset_id": approval.dataset_id,
        "operator": approval.operator,
        "package_sha256": approval.package_sha256,
        "reviewer": approval.reviewer,
        "reviewer_truth_sha256": approval.reviewer_truth_sha256,
        "source_session_report_sha256": approval.source_session_report_sha256,
    }
    if actual != expected or approval.registry_sha256 != registry_sha256:
        raise InventoryPositiveV3IndependentValidationError(
            "approval is forged or rebound from the independent campaign evidence"
        )
    if len({approval.operator, approval.reviewer, approval.approver}) != 3:
        raise InventoryPositiveV3IndependentValidationError(
            "approval operator, reviewer, and approver must be distinct"
        )
    if _parse_utc(approval.approved_at_utc, "approved_at_utc") <= _parse_utc(
        dataset.reviewed_at_utc,
        "reviewed_at_utc",
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "approval must follow finalized independent reviewer truth"
        )


def _parse_dataset(
    root: Path,
    manifest: Mapping[str, object],
    manifest_sha: str,
    review: Mapping[str, object],
    review_sha: str,
    snapshots: list[tuple[Path, bytes]],
) -> IndependentValidationDataset:
    _require_exact_keys(
        manifest,
        {
            "activation_allowed",
            "all_owned_captures_included",
            "campaign_id",
            "campaign_status",
            "candidate_head_sha",
            "capture_environment",
            "cases",
            "dataset_id",
            "dataset_role",
            "finalized_at_utc",
            "operator",
            "preregistration_sha256",
            "prior_campaigns",
            "prototype_eligible",
            "schema",
            "selection_policy",
            "session_id",
            "source_session_report",
            "training_allowed",
        },
        "campaign manifest",
    )
    _require_nonactivating_roles(manifest, "campaign manifest")
    if manifest.get("candidate_head_sha") != INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA:
        raise InventoryPositiveV3IndependentValidationError(
            "campaign candidate head differs from frozen V3"
        )
    if manifest.get("preregistration_sha256") != (
        INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "campaign preregistration hash differs from frozen V3"
        )
    if manifest.get("campaign_status") != "finalized":
        raise InventoryPositiveV3IndependentValidationError(
            "campaign must be finalized before evaluation"
        )
    if manifest.get("selection_policy") != _SELECTION_POLICY:
        raise InventoryPositiveV3IndependentValidationError(
            "campaign selection policy permits outcome-based filtering"
        )
    if manifest.get("all_owned_captures_included") is not True:
        raise InventoryPositiveV3IndependentValidationError(
            "campaign must include every completed owned capture"
        )
    dataset_id = _require_nonempty_text(manifest, "dataset_id")
    campaign_id = _require_nonempty_text(manifest, "campaign_id")
    session_id = _require_nonempty_text(manifest, "session_id")
    operator = _require_actor_identity(manifest, "operator")
    finalized_at_text = _require_nonempty_text(manifest, "finalized_at_utc")
    if dataset_id == DEVELOPMENT_DATASET_ID:
        raise InventoryPositiveV3IndependentValidationError(
            "development dataset cannot masquerade as independent validation"
        )
    environment = _parse_environment(
        _require_object(manifest, "capture_environment")
    )
    source_session_ref = _require_object(manifest, "source_session_report")
    _require_exact_keys(
        source_session_ref,
        {"path", "sha256"},
        "source session report ref",
    )
    source_session_path = _owned_path(
        root,
        _require_nonempty_text(source_session_ref, "path"),
        "source session report",
    )
    source_session_raw, source_session_bytes = _read_canonical_path(
        source_session_path,
        _SOURCE_SESSION_SCHEMA,
        "source session report",
        snapshots,
        owned_root=root,
    )
    source_session_sha = _sha256(source_session_bytes)
    if source_session_ref.get("sha256") != source_session_sha:
        raise InventoryPositiveV3IndependentValidationError(
            "source session report hash differs from campaign manifest"
        )
    source_session = _parse_source_session(
        source_session_raw,
        source_session_sha,
        campaign_id=campaign_id,
        session_id=session_id,
        operator=operator,
        environment=environment,
    )
    if campaign_id != _content_bound_campaign_id(session_id):
        raise InventoryPositiveV3IndependentValidationError(
            "campaign_id is not derived from its session and preregistration"
        )
    if dataset_id != _content_bound_dataset_id(manifest):
        raise InventoryPositiveV3IndependentValidationError(
            "dataset_id is not derived from the immutable campaign evidence"
        )
    prior_campaigns = _parse_prior_campaigns(
        _require_list(manifest, "prior_campaigns"),
        campaign_id,
    )
    raw_cases = _require_list(manifest, "cases")
    if not raw_cases:
        raise InventoryPositiveV3IndependentValidationError(
            "independent validation dataset has no cases"
        )

    _require_exact_keys(
        review,
        {
            "activation_allowed",
            "campaign_id",
            "campaign_manifest_sha256",
            "cases",
            "dataset_id",
            "reviewed_at_utc",
            "reviewer",
            "schema",
            "truth_source",
        },
        "reviewer truth",
    )
    if review.get("activation_allowed") is not False:
        raise InventoryPositiveV3IndependentValidationError(
            "reviewer truth cannot authorize activation"
        )
    if review.get("campaign_manifest_sha256") != manifest_sha:
        raise InventoryPositiveV3IndependentValidationError(
            "reviewer truth is not bound to this campaign manifest"
        )
    if review.get("dataset_id") != dataset_id or review.get("campaign_id") != campaign_id:
        raise InventoryPositiveV3IndependentValidationError(
            "reviewer truth dataset/campaign identity mismatch"
        )
    if review.get("truth_source") != _TRUTH_SOURCE:
        raise InventoryPositiveV3IndependentValidationError(
            "operator labels cannot populate independent reviewer truth"
        )
    reviewer = _require_actor_identity(review, "reviewer")
    if reviewer == operator:
        raise InventoryPositiveV3IndependentValidationError(
            "independent reviewer must be distinct from the capture operator"
        )
    reviewed_at = _parse_utc(_require_text(review, "reviewed_at_utc"), "reviewed_at_utc")
    truths = _parse_truths(_require_list(review, "cases"))

    parsed_cases: list[IndependentValidationCase] = []
    seen_case_ids: set[str] = set()
    seen_capture_ids: set[str] = set()
    sessions: set[str] = set()
    captured_times: list[datetime] = []
    for position, value in enumerate(raw_cases, start=1):
        case = _require_mapping(value, f"campaign case {position}")
        if position > len(source_session.captures):
            raise InventoryPositiveV3IndependentValidationError(
                "campaign manifest contains a capture absent from the source session"
            )
        parsed = _parse_case(
            root,
            case,
            position,
            truths,
            source_session.captures[position - 1],
            source_session.report_sha256,
            environment,
            snapshots,
        )
        if parsed.case_id in seen_case_ids:
            raise InventoryPositiveV3IndependentValidationError(
                f"duplicate independent case id: {parsed.case_id}"
            )
        if parsed.capture_id in seen_capture_ids:
            raise InventoryPositiveV3IndependentValidationError(
                f"duplicate independent capture id: {parsed.capture_id}"
            )
        seen_case_ids.add(parsed.case_id)
        seen_capture_ids.add(parsed.capture_id)
        sessions.add(parsed.session_id)
        captured_times.append(
            _parse_utc(parsed.captured_at_utc, f"{parsed.case_id} captured_at_utc")
        )
        parsed_cases.append(parsed)
    if set(truths) != seen_case_ids:
        missing = sorted(seen_case_ids - set(truths))
        foreign = sorted(set(truths) - seen_case_ids)
        raise InventoryPositiveV3IndependentValidationError(
            f"reviewer truth coverage mismatch: missing={missing}, foreign={foreign}"
        )
    if len(sessions) != 1:
        raise InventoryPositiveV3IndependentValidationError(
            "one independent validation package must own exactly one session"
        )
    if sessions != {session_id}:
        raise InventoryPositiveV3IndependentValidationError(
            "campaign cases differ from the source-owned session identity"
        )
    if len(parsed_cases) != len(source_session.captures):
        raise InventoryPositiveV3IndependentValidationError(
            "campaign omits completed captures from the durable source session"
        )
    if captured_times != sorted(captured_times) or len(set(captured_times)) != len(
        captured_times
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "capture timestamps must be unique and strictly source ordered"
        )
    preregistration_effective = _parse_utc(
        _PREREGISTRATION_EFFECTIVE_AT_UTC,
        "preregistration effective time",
    )
    session_started = _parse_utc(
        source_session.started_at_utc,
        "source session started_at_utc",
    )
    session_completed = _parse_utc(
        source_session.completed_at_utc,
        "source session completed_at_utc",
    )
    manifest_finalized = _parse_utc(finalized_at_text, "finalized_at_utc")
    if session_started <= preregistration_effective:
        raise InventoryPositiveV3IndependentValidationError(
            "source session must begin after the preregistration became effective"
        )
    if any(item <= preregistration_effective for item in captured_times):
        raise InventoryPositiveV3IndependentValidationError(
            "pre-preregistration evidence cannot be independent validation"
        )
    if not session_started < min(captured_times) <= max(captured_times) < session_completed:
        raise InventoryPositiveV3IndependentValidationError(
            "source session timing does not contain every ordered capture"
        )
    if not session_completed < manifest_finalized < reviewed_at:
        raise InventoryPositiveV3IndependentValidationError(
            "manifest finalization must follow session completion and precede review"
        )
    _validate_stage_matrix(tuple(parsed_cases))
    return IndependentValidationDataset(
        package_directory=root,
        dataset_id=dataset_id,
        campaign_id=campaign_id,
        session_id=next(iter(sessions)),
        operator=operator,
        reviewer=reviewer,
        manifest_finalized_at_utc=finalized_at_text,
        reviewed_at_utc=_require_text(review, "reviewed_at_utc"),
        environment=environment,
        cases=tuple(parsed_cases),
        package_sha256="",
        campaign_manifest_sha256=manifest_sha,
        reviewer_truth_sha256=review_sha,
        source_session_report_sha256=source_session.report_sha256,
        prior_campaigns=prior_campaigns,
        _snapshots=(),
    )


def _parse_environment(
    value: Mapping[str, object],
) -> IndependentValidationEnvironment:
    _require_exact_keys(
        value,
        {
            "capture_build_sha",
            "capture_configuration_id",
            "client_mode",
            "frame",
            "renderer",
            "runelite_build",
            "theme",
            "window_class",
            "windows_dpi",
            "windows_scaling_percent",
            "windows_version",
        },
        "capture environment",
    )
    frame = _require_object(value, "frame")
    _require_exact_keys(
        frame,
        {"height", "pixel_format", "profile_id", "width"},
        "capture frame",
    )
    if (
        frame.get("width") != SUPPORTED_FRAME_WIDTH
        or frame.get("height") != SUPPORTED_FRAME_HEIGHT
        or frame.get("pixel_format") != SUPPORTED_PIXEL_FORMAT
        or frame.get("profile_id") != SUPPORTED_PROFILE_ID
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "capture frame/profile differs from the preregistered V3 envelope"
        )
    capture_build_sha = _require_text(value, "capture_build_sha")
    _require_git_sha(capture_build_sha, "capture_build_sha")
    scaling = _require_positive_int(value, "windows_scaling_percent")
    dpi = _require_positive_int(value, "windows_dpi")
    return IndependentValidationEnvironment(
        capture_build_sha=capture_build_sha,
        capture_configuration_id=_require_nonempty_text(
            value, "capture_configuration_id"
        ),
        runelite_build=_require_nonempty_text(value, "runelite_build"),
        windows_version=_require_nonempty_text(value, "windows_version"),
        windows_scaling_percent=scaling,
        windows_dpi=dpi,
        client_mode=_require_nonempty_text(value, "client_mode"),
        theme=_require_nonempty_text(value, "theme"),
        renderer=_require_nonempty_text(value, "renderer"),
        window_class=_require_nonempty_text(value, "window_class"),
    )


def _parse_source_session(
    value: Mapping[str, object],
    report_sha256: str,
    *,
    campaign_id: str,
    session_id: str,
    operator: str,
    environment: IndependentValidationEnvironment,
) -> _SourceSessionBinding:
    _require_exact_keys(
        value,
        {
            "activation_allowed",
            "all_owned_captures_included",
            "campaign_id",
            "capture_environment",
            "captures",
            "completed_at_utc",
            "operator",
            "schema",
            "session_id",
            "started_at_utc",
        },
        "source session report",
    )
    if value.get("activation_allowed") is not False:
        raise InventoryPositiveV3IndependentValidationError(
            "source session report cannot authorize activation"
        )
    if value.get("all_owned_captures_included") is not True:
        raise InventoryPositiveV3IndependentValidationError(
            "source session report must finalize every owned capture"
        )
    if value.get("campaign_id") != campaign_id or value.get("session_id") != session_id:
        raise InventoryPositiveV3IndependentValidationError(
            "source session identity differs from the campaign manifest"
        )
    if value.get("operator") != operator:
        raise InventoryPositiveV3IndependentValidationError(
            "source session operator differs from the campaign manifest"
        )
    source_environment = _require_object(value, "capture_environment")
    if source_environment != environment.to_dict():
        raise InventoryPositiveV3IndependentValidationError(
            "source session environment differs from the campaign manifest"
        )
    captures: list[_SourceCaptureBinding] = []
    seen: set[str] = set()
    for index, raw in enumerate(_require_list(value, "captures"), start=1):
        item = _require_mapping(raw, f"source session capture {index}")
        _require_exact_keys(
            item,
            {"capture_id", "captured_at_utc", "capture_report"},
            f"source session capture {index}",
        )
        capture_id = _require_nonempty_text(item, "capture_id")
        if capture_id in seen:
            raise InventoryPositiveV3IndependentValidationError(
                f"source session repeats capture id: {capture_id}"
            )
        seen.add(capture_id)
        report = _require_object(item, "capture_report")
        _require_exact_keys(
            report,
            {"path", "sha256"},
            f"source capture report ref {capture_id}",
        )
        captures.append(
            _SourceCaptureBinding(
                capture_id=capture_id,
                captured_at_utc=_require_nonempty_text(item, "captured_at_utc"),
                report_path=_require_nonempty_text(report, "path"),
                report_sha256=_require_sha256_text(report, "sha256"),
            )
        )
    if not captures:
        raise InventoryPositiveV3IndependentValidationError(
            "source session report contains no captures"
        )
    return _SourceSessionBinding(
        session_id=session_id,
        campaign_id=campaign_id,
        started_at_utc=_require_nonempty_text(value, "started_at_utc"),
        completed_at_utc=_require_nonempty_text(value, "completed_at_utc"),
        environment=copy.deepcopy(source_environment),
        captures=tuple(captures),
        report_sha256=report_sha256,
    )


def _content_bound_campaign_id(session_id: str) -> str:
    identity = {
        "preregistration_sha256": INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256,
        "session_id": session_id,
    }
    return "inventory-positive-v3-campaign-" + _sha256(
        _canonical_data_bytes(identity)
    )[:24]


def _content_bound_dataset_id(manifest: Mapping[str, object]) -> str:
    identity = copy.deepcopy(dict(manifest))
    identity.pop("dataset_id", None)
    return "inventory-positive-v3-independent-" + _sha256(
        _canonical_data_bytes(identity)
    )[:24]


def _parse_prior_campaigns(
    values: Sequence[object],
    current_campaign_id: str,
) -> tuple[Mapping[str, object], ...]:
    parsed: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for index, value in enumerate(values, start=1):
        item = _require_mapping(value, f"prior campaign {index}")
        _require_exact_keys(
            item,
            {"campaign_id", "manifest_sha256", "status"},
            f"prior campaign {index}",
        )
        campaign_id = _require_text(item, "campaign_id")
        if campaign_id == current_campaign_id or campaign_id in seen:
            raise InventoryPositiveV3IndependentValidationError(
                "prior campaign disclosure has duplicate/current identity"
            )
        seen.add(campaign_id)
        _require_sha256(_require_text(item, "manifest_sha256"), "prior manifest")
        if item.get("status") not in {"aborted", "failed", "superseded"}:
            raise InventoryPositiveV3IndependentValidationError(
                "prior campaign status must disclose an unfinished/failed run"
            )
        parsed.append(copy.deepcopy(dict(item)))
    return tuple(parsed)


def _parse_truths(
    values: Sequence[object],
) -> dict[str, IndependentValidationTruth]:
    result: dict[str, IndependentValidationTruth] = {}
    expected_keys = {
        "case_id",
        "decision",
        "drag_visible",
        "frame_region_sha256",
        "hover_visible",
        "occupied_slots",
        "ordinary_iron_only",
        "quantity_text_visible",
        "review_note",
        "selected_item_visible",
        "visibility",
    }
    for index, value in enumerate(values, start=1):
        item = _require_mapping(value, f"review truth case {index}")
        _require_exact_keys(item, expected_keys, f"review truth case {index}")
        case_id = _require_text(item, "case_id")
        if case_id in result:
            raise InventoryPositiveV3IndependentValidationError(
                f"duplicate reviewer truth case: {case_id}"
            )
        decision = _require_text(item, "decision")
        if decision not in {"approved", "rejected"}:
            raise InventoryPositiveV3IndependentValidationError(
                f"unsupported reviewer decision for {case_id}"
            )
        visibility = _require_text(item, "visibility")
        if visibility not in {
            "inventory-obstructed",
            "inventory-visible",
            "wrong-tab-visible",
        }:
            raise InventoryPositiveV3IndependentValidationError(
                f"unsupported reviewer visibility for {case_id}"
            )
        occupied = item.get("occupied_slots")
        if occupied is not None and (
            not isinstance(occupied, int)
            or isinstance(occupied, bool)
            or not 0 <= occupied <= 28
        ):
            raise InventoryPositiveV3IndependentValidationError(
                f"invalid reviewer occupied count for {case_id}"
            )
        flags = {
            key: _require_bool(item, key)
            for key in ("ordinary_iron_only", *_PRESENTATION_FLAGS)
        }
        note = item.get("review_note")
        if note is not None and not isinstance(note, str):
            raise InventoryPositiveV3IndependentValidationError(
                f"review_note must be text/null for {case_id}"
            )
        result[case_id] = IndependentValidationTruth(
            case_id=case_id,
            frame_region_sha256=_require_sha256_text(
                item, "frame_region_sha256"
            ),
            decision=decision,
            visibility=visibility,
            occupied_slots=occupied,
            ordinary_iron_only=flags["ordinary_iron_only"],
            drag_visible=flags["drag_visible"],
            hover_visible=flags["hover_visible"],
            quantity_text_visible=flags["quantity_text_visible"],
            selected_item_visible=flags["selected_item_visible"],
            review_note=note,
        )
    return result


def _parse_case(
    root: Path,
    value: Mapping[str, object],
    position: int,
    truths: Mapping[str, IndependentValidationTruth],
    source_capture: _SourceCaptureBinding,
    source_session_report_sha256: str,
    environment: IndependentValidationEnvironment,
    snapshots: list[tuple[Path, bytes]],
) -> IndependentValidationCase:
    _require_exact_keys(
        value,
        {
            "capture_id",
            "captured_at_utc",
            "case_id",
            "frame_region",
            "operator_label_status",
            "operator_stage_label",
            "planned_stage_id",
            "sequence_index",
            "session_id",
            "source",
        },
        f"campaign case {position}",
    )
    if value.get("sequence_index") != position:
        raise InventoryPositiveV3IndependentValidationError(
            "campaign sequence indexes must be contiguous and source ordered"
        )
    if value.get("operator_label_status") != _OPERATOR_LABEL_STATUS:
        raise InventoryPositiveV3IndependentValidationError(
            "operator stage label must remain explicitly unverified"
        )
    case_id = _require_nonempty_text(value, "case_id")
    session_id = _require_nonempty_text(value, "session_id")
    capture_id = _require_nonempty_text(value, "capture_id")
    if case_id != f"{session_id}/{capture_id}":
        raise InventoryPositiveV3IndependentValidationError(
            f"case identity is not owned by its session/capture: {case_id}"
        )
    planned_stage = _require_nonempty_text(value, "planned_stage_id")
    if planned_stage not in {*_REQUIRED_STAGES, _OPTIONAL_STAGE}:
        raise InventoryPositiveV3IndependentValidationError(
            f"unsupported preregistered stage: {planned_stage}"
        )
    captured_at_utc = _require_nonempty_text(value, "captured_at_utc")
    if (
        source_capture.capture_id != capture_id
        or source_capture.captured_at_utc != captured_at_utc
    ):
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} differs from the source-session capture order"
        )
    source = _require_object(value, "source")
    _require_exact_keys(source, {"capture_report"}, f"{case_id} source")
    capture_report_ref = _require_object(source, "capture_report")
    _require_exact_keys(
        capture_report_ref,
        {"path", "sha256"},
        f"{case_id} capture report ref",
    )
    capture_report_path_text = _require_nonempty_text(
        capture_report_ref, "path"
    )
    capture_report_sha256 = _require_sha256_text(
        capture_report_ref, "sha256"
    )
    if (
        capture_report_path_text != source_capture.report_path
        or capture_report_sha256 != source_capture.report_sha256
    ):
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} capture report differs from the source-session binding"
        )
    capture_report_path = _owned_path(
        root,
        capture_report_path_text,
        f"{case_id} source capture report",
    )
    capture_report, capture_report_bytes = _read_canonical_path(
        capture_report_path,
        _SOURCE_CAPTURE_SCHEMA,
        f"{case_id} source capture report",
        snapshots,
        owned_root=root,
    )
    if _sha256(capture_report_bytes) != capture_report_sha256:
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} source capture report SHA-256 mismatch"
        )
    source_region = _parse_source_capture_report(
        capture_report,
        case_id=case_id,
        session_id=session_id,
        capture_id=capture_id,
        captured_at_utc=captured_at_utc,
        environment=environment,
    )
    frame_region = _require_object(value, "frame_region")
    _require_exact_keys(
        frame_region,
        {"path", "sha256", "size_bytes"},
        f"{case_id} frame region",
    )
    path_text = _require_nonempty_text(frame_region, "path")
    if (
        path_text != source_region.path
        or frame_region.get("sha256") != source_region.sha256
        or frame_region.get("size_bytes") != source_region.size_bytes
    ):
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} region differs from its durable source capture report"
        )
    path = _owned_path(root, path_text, f"{case_id} frame region")
    payload = _read_bytes(path, f"{case_id} frame region")
    snapshots.append((path, payload))
    expected_size = (
        SUPPORTED_REGION[2]
        * SUPPORTED_REGION[3]
        * PixelFormat.BGRA8888.bytes_per_pixel
    )
    if frame_region.get("size_bytes") != expected_size or len(payload) != expected_size:
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} frame region size differs from frozen profile"
        )
    digest = _sha256(payload)
    if frame_region.get("sha256") != digest:
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} frame region SHA-256 mismatch"
        )
    truth = truths.get(case_id)
    if truth is None:
        raise InventoryPositiveV3IndependentValidationError(
            f"operator label cannot substitute for missing reviewer truth: {case_id}"
        )
    if truth.frame_region_sha256 != digest:
        raise InventoryPositiveV3IndependentValidationError(
            f"reviewer truth is not bound to {case_id} pixels"
        )
    return IndependentValidationCase(
        sequence_index=position,
        case_id=case_id,
        session_id=session_id,
        capture_id=capture_id,
        planned_stage_id=planned_stage,
        operator_stage_label=_require_nonempty_text(value, "operator_stage_label"),
        captured_at_utc=captured_at_utc,
        capture_report_path=capture_report_path_text,
        capture_report_sha256=capture_report_sha256,
        session_report_sha256=source_session_report_sha256,
        frame_region_path=path_text,
        frame_region_sha256=digest,
        frame_region_payload=payload,
        truth=truth,
    )


def _parse_source_capture_report(
    value: Mapping[str, object],
    *,
    case_id: str,
    session_id: str,
    capture_id: str,
    captured_at_utc: str,
    environment: IndependentValidationEnvironment,
) -> _SourceRegionBinding:
    _require_exact_keys(
        value,
        {
            "activation_allowed",
            "capture_environment",
            "capture_id",
            "captured_at_utc",
            "inventory_region",
            "schema",
            "session_id",
        },
        f"{case_id} source capture report",
    )
    if value.get("activation_allowed") is not False:
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} source capture report cannot authorize activation"
        )
    if value.get("session_id") != session_id:
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} source capture report has a foreign session"
        )
    if value.get("capture_id") != capture_id:
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} source capture report has a foreign capture"
        )
    if value.get("captured_at_utc") != captured_at_utc:
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} source capture report timestamp mismatch"
        )
    if _require_object(value, "capture_environment") != environment.to_dict():
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} source capture environment mismatch"
        )
    region = _require_object(value, "inventory_region")
    _require_exact_keys(
        region,
        {"path", "region", "sha256", "size_bytes"},
        f"{case_id} source inventory region",
    )
    if region.get("region") != list(SUPPORTED_REGION):
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} source capture region differs from the frozen profile"
        )
    expected_size = (
        SUPPORTED_REGION[2]
        * SUPPORTED_REGION[3]
        * PixelFormat.BGRA8888.bytes_per_pixel
    )
    size_bytes = _require_positive_int(region, "size_bytes")
    if size_bytes != expected_size:
        raise InventoryPositiveV3IndependentValidationError(
            f"{case_id} source capture region size differs from the frozen profile"
        )
    return _SourceRegionBinding(
        path=_require_nonempty_text(region, "path"),
        sha256=_require_sha256_text(region, "sha256"),
        size_bytes=size_bytes,
    )


def _validate_stage_matrix(cases: tuple[IndependentValidationCase, ...]) -> None:
    stage_positions: dict[str, int] = {}
    for position, item in enumerate(cases):
        if item.planned_stage_id in _REQUIRED_STAGES:
            if item.planned_stage_id in stage_positions:
                raise InventoryPositiveV3IndependentValidationError(
                    f"required stage is duplicated: {item.planned_stage_id}"
                )
            stage_positions[item.planned_stage_id] = position
    missing = [stage for stage in _REQUIRED_STAGES if stage not in stage_positions]
    if missing:
        raise InventoryPositiveV3IndependentValidationError(
            f"independent campaign is missing preregistered stages: {missing}"
        )
    if [stage_positions[stage] for stage in _REQUIRED_STAGES] != sorted(
        stage_positions[stage] for stage in _REQUIRED_STAGES
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "preregistered campaign stages are out of order"
        )
    by_stage = {
        item.planned_stage_id: item for item in cases if item.planned_stage_id in _REQUIRED_STAGES
    }
    positive = [by_stage[stage] for stage in _POSITIVE_STAGES]
    if any(item.truth.decision != "approved" for item in positive):
        raise InventoryPositiveV3IndependentValidationError(
            "every natural-fill checkpoint requires approved reviewer truth"
        )
    if any(
        item.truth.visibility != "inventory-visible"
        or not item.truth.ordinary_iron_only
        or item.truth.has_unsupported_presentation
        for item in positive
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "natural-fill checkpoints must be clean ordinary-iron inventory views"
        )
    counts = [item.truth.occupied_slots for item in positive]
    if any(count is None for count in counts):
        raise InventoryPositiveV3IndependentValidationError(
            "natural-fill reviewer truth requires exact counts"
        )
    known_counts = [int(count) for count in counts if count is not None]
    if not (
        known_counts[0] == 0
        and 0 < known_counts[1] < known_counts[2] < known_counts[3] < 28
        and known_counts[4] == 28
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "natural-fill counts violate 0 < early < mid < near < 28"
        )
    wrong_tab = by_stage["wrong-tab"].truth
    obstruction = by_stage["row-obstruction"].truth
    if (
        wrong_tab.decision != "approved"
        or wrong_tab.visibility != "wrong-tab-visible"
        or wrong_tab.occupied_slots is not None
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "wrong-tab truth must be approved UNKNOWN/no-count evidence"
        )
    if (
        obstruction.decision != "approved"
        or obstruction.visibility != "inventory-obstructed"
        or obstruction.occupied_slots is not None
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "row-obstruction truth must be approved UNKNOWN/no-count evidence"
        )


def _reject_development_identity_reuse(
    dataset: IndependentValidationDataset,
    candidate: _FrozenCandidate,
) -> None:
    if dataset.dataset_id == DEVELOPMENT_DATASET_ID:
        raise InventoryPositiveV3IndependentValidationError(
            "development dataset cannot be validation evidence"
        )
    if dataset.session_id in candidate.development_session_ids:
        raise InventoryPositiveV3IndependentValidationError(
            "development session cannot be validation evidence"
        )
    for item in dataset.cases:
        if item.case_id in candidate.development_case_ids:
            raise InventoryPositiveV3IndependentValidationError(
                f"development case cannot be validation evidence: {item.case_id}"
            )
        if item.capture_id in candidate.development_capture_ids:
            raise InventoryPositiveV3IndependentValidationError(
                f"development capture cannot be validation evidence: {item.capture_id}"
            )


def _reject_development_path_reuse(
    package_directory: Path,
    candidate: _FrozenCandidate,
) -> None:
    """Reject overlap before any validation package artifact is opened."""
    if not isinstance(package_directory, Path):
        raise TypeError("package_directory must be pathlib.Path")
    package_root = package_directory.resolve(strict=True)
    development_root = candidate.development_fixture_root
    if (
        package_root == development_root
        or package_root.is_relative_to(development_root)
        or development_root.is_relative_to(package_root)
    ):
        raise InventoryPositiveV3IndependentValidationError(
            "independent validation package path overlaps the development fixture"
        )


def _expected_result(item: IndependentValidationCase) -> dict[str, object]:
    truth = item.truth
    if (
        item.planned_stage_id in _POSITIVE_STAGES
        and truth.decision == "approved"
        and truth.visibility == "inventory-visible"
        and truth.ordinary_iron_only
        and not truth.has_unsupported_presentation
        and truth.occupied_slots is not None
    ):
        occupied = truth.occupied_slots
        return {
            "confidence_at_least": _PUBLICATION_FLOOR,
            "label": (
                "empty" if occupied == 0 else "full" if occupied == 28 else "partial"
            ),
            "occupied_slots": occupied,
        }
    return {
        "confidence": 0.0,
        "label": "unknown",
        "occupied_slots": None,
    }


def _matches_expected(
    actual: InventoryPositiveV3DevelopmentResult,
    expected: Mapping[str, object],
    truth: IndependentValidationTruth,
) -> tuple[bool, str | None]:
    if truth.decision != "approved":
        return False, "reviewer_rejected_case_cannot_pass_validation"
    if expected.get("occupied_slots") is None:
        passed = (
            actual.occupied_slots is None
            and actual.label == "unknown"
            and actual.confidence == 0.0
        )
    else:
        passed = (
            actual.occupied_slots == expected.get("occupied_slots")
            and actual.label == expected.get("label")
            and actual.confidence >= _PUBLICATION_FLOOR
        )
    if passed:
        return True, None
    return False, "frozen_v3_result_disagrees_with_preregistered_reviewer_truth"


def _serialized_result_matches_expected(
    actual: Mapping[str, object],
    expected: Mapping[str, object],
    reviewer_truth: Mapping[str, object],
) -> tuple[bool, str | None]:
    """Recompute case truth without trusting stored pass/failure fields."""
    if reviewer_truth.get("decision") != "approved":
        return False, "reviewer_rejected_case_cannot_pass_validation"
    occupied = expected.get("occupied_slots")
    if occupied is None:
        passed = (
            actual.get("occupied_slots") is None
            and actual.get("label") == "unknown"
            and actual.get("confidence") == 0.0
        )
    else:
        confidence = actual.get("confidence")
        passed = (
            actual.get("occupied_slots") == occupied
            and actual.get("label") == expected.get("label")
            and isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and float(confidence) >= _PUBLICATION_FLOOR
        )
    if passed:
        return True, None
    return False, "frozen_v3_result_disagrees_with_preregistered_reviewer_truth"


def _development_identities(
    manifest: Mapping[str, object],
) -> tuple[set[str], set[str], set[str], set[str]]:
    cases = _require_list(manifest, "cases")
    case_ids: set[str] = set()
    session_ids: set[str] = set()
    capture_ids: set[str] = set()
    payload_sha256s: set[str] = set()
    for index, value in enumerate(cases, start=1):
        item = _require_mapping(value, f"development case {index}")
        truth = _require_object(item, "review_truth")
        region = _require_object(item, "frame_region")
        case_ids.add(_require_text(item, "case_id"))
        session_ids.add(_require_text(truth, "session_id"))
        capture_ids.add(_require_text(truth, "capture_id"))
        payload_sha256s.add(_require_sha256_text(region, "sha256"))
    return case_ids, session_ids, capture_ids, payload_sha256s


def _read_canonical_document(
    root: Path,
    filename: str,
    schema: str,
    snapshots: list[tuple[Path, bytes]],
) -> tuple[dict[str, object], bytes]:
    path = _owned_path(root, filename, filename)
    return _read_canonical_path(
        path,
        schema,
        filename,
        snapshots,
        owned_root=root,
    )


def _read_canonical_path(
    path: Path,
    schema: str,
    label: str,
    snapshots: list[tuple[Path, bytes]],
    *,
    owned_root: Path,
) -> tuple[dict[str, object], bytes]:
    payload = _read_bytes(path, label)
    snapshots.append((path, payload))
    sidecar_candidate = path.with_suffix(path.suffix + ".sha256")
    try:
        sidecar_relative = sidecar_candidate.relative_to(owned_root).as_posix()
    except ValueError as exc:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} sidecar path escapes its owned root"
        ) from exc
    sidecar_path = _owned_path(
        owned_root,
        sidecar_relative,
        f"{label} sidecar",
    )
    sidecar = _read_bytes(sidecar_path, f"{label} sidecar")
    snapshots.append((sidecar_path, sidecar))
    digest = _sha256(payload)
    expected_sidecar = f"{digest}  {path.name}\n".encode("ascii")
    if sidecar != expected_sidecar:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} SHA-256 sidecar mismatch"
        )
    decoded = _json_object(payload, label)
    if decoded.get("schema") != schema:
        raise InventoryPositiveV3IndependentValidationError(
            f"unsupported {label} schema"
        )
    if payload != _canonical_bytes(decoded):
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} is not canonical JSON"
        )
    return decoded, payload


def _owned_path(root: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} path is not a safe relative POSIX path"
        )
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise InventoryPositiveV3IndependentValidationError(
                f"{label} path cannot traverse a symbolic link"
            )
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise InventoryPositiveV3IndependentValidationError(
            f"cannot resolve {label}: {exc}"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} path escapes the campaign directory"
        ) from exc
    if not resolved.is_file():
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} is not a regular file"
        )
    return resolved


def _assert_snapshot_files_unchanged(
    snapshots: Sequence[tuple[Path, bytes]],
) -> None:
    for path, before in snapshots:
        after = _read_bytes(path, f"immutable campaign artifact {path.name}")
        if after != before:
            raise InventoryPositiveV3IndependentValidationError(
                f"campaign artifact changed during evaluation: {path.name}"
            )


def _require_nonactivating_roles(
    value: Mapping[str, object],
    label: str,
) -> None:
    if value.get("activation_allowed") is not False:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} cannot authorize activation"
        )
    if value.get("dataset_role") != _DATASET_ROLE:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} has the wrong dataset role"
        )
    if value.get("training_allowed") is not False:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} cannot allow training"
        )
    if value.get("prototype_eligible") is not False:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} cannot mark validation data prototype-eligible"
        )


def _zero_action_authority() -> dict[str, object]:
    return {
        "banking_authority": False,
        "click_authority": False,
        "mining_authority": False,
        "reason": "validation_readiness_is_not_a_production_perception_snapshot",
        "target_ids": [],
    }


def _json_object(payload: bytes, label: str) -> dict[str, object]:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise InventoryPositiveV3IndependentValidationError(
                    f"{label} contains duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    try:
        decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} is not valid UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(decoded, dict):
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} must be a JSON object"
        )
    return decoded


def _canonical_data_bytes(value: object) -> bytes:
    """Return the frozen V3 model's canonical in-memory identity encoding."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _canonical_bytes(value: object) -> bytes:
    """Return canonical on-disk JSON, including its required final newline."""

    return _canonical_data_bytes(value) + b"\n"


def _canonical_json(value: object) -> str:
    return _canonical_bytes(value).decode("utf-8")


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return _io_path(path).read_bytes()
    except OSError as exc:
        raise InventoryPositiveV3IndependentValidationError(
            f"cannot read {label}: {exc}"
        ) from exc


def _read_text(path: Path, label: str) -> str:
    try:
        return _io_path(path).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise InventoryPositiveV3IndependentValidationError(
            f"cannot read {label}: {exc}"
        ) from exc


def _io_path(path: Path) -> Path:
    """Preserve ordinary paths while supporting long absolute paths on Windows."""

    if os.name != "nt":
        return path
    value = str(path.resolve(strict=False))
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _parse_utc(value: str, label: str) -> datetime:
    if not value.endswith("Z"):
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} must use canonical UTC Z notation"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} is not an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo != UTC:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} must be UTC"
        )
    return parsed


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} must be an object"
        )
    return value


def _require_object(mapping: Mapping[str, object], key: str) -> dict[str, object]:
    return _require_mapping(mapping.get(key), key)


def _require_list(mapping: Mapping[str, object], key: str) -> list[object]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise InventoryPositiveV3IndependentValidationError(f"{key} must be a list")
    return value


def _require_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise InventoryPositiveV3IndependentValidationError(f"{key} must be text")
    return value


def _require_nonempty_text(mapping: Mapping[str, object], key: str) -> str:
    value = _require_text(mapping, key)
    if not value.strip():
        raise InventoryPositiveV3IndependentValidationError(f"{key} is empty")
    return value


def _require_actor_identity(mapping: Mapping[str, object], key: str) -> str:
    value = _require_nonempty_text(mapping, key)
    if value != value.strip():
        raise InventoryPositiveV3IndependentValidationError(
            f"{key} identity cannot contain surrounding whitespace"
        )
    return value


def _require_bool(mapping: Mapping[str, object], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise InventoryPositiveV3IndependentValidationError(f"{key} must be boolean")
    return value


def _require_positive_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InventoryPositiveV3IndependentValidationError(
            f"{key} must be a positive integer"
        )
    return value


def _require_sha256_text(mapping: Mapping[str, object], key: str) -> str:
    value = _require_text(mapping, key)
    _require_sha256(value, key)
    return value


def _require_sha256(value: str, label: str) -> None:
    if len(value) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} must be an exact lowercase SHA-256"
        )


def _require_git_sha(value: str, label: str) -> None:
    if len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} must be an exact lowercase 40-character Git SHA"
        )


def _require_exact_keys(
    mapping: Mapping[str, object],
    expected: set[str],
    label: str,
) -> None:
    actual = set(mapping)
    if actual != expected:
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
