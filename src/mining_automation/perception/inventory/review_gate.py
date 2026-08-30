"""Deterministic privacy-safe review and replay for live inventory evidence.

This module deliberately separates three authorities:

* the operator-selected capture label is acquisition metadata only;
* an explicit reviewer record establishes visual truth; and
* the unchanged production detector establishes the replay result.

No result from this development gate activates a production profile.  A
candidate remains non-activating until a separate lead-owned release decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from ...capture import Frame, PixelFormat, RawFrame
from .adapter import inventory_detection_from_observation
from .configuration import inventory_detector_from_profile
from .detector import InventoryDetection, InventoryDetector
from .geometry import (
    INVENTORY_CAPACITY,
    INVENTORY_COLUMNS,
    INVENTORY_ROWS,
    INVENTORY_SLOT_SIZE,
    InventoryGridLayout,
    Region,
)
from .live_validation_session import (
    InventoryValidationSessionRecord,
    InventoryValidationSessionReport,
    load_inventory_validation_session,
)
from .localization import InventoryFrameProfile

__all__ = [
    "CandidateInventoryProfile",
    "InventoryCaseReview",
    "InventoryEvidenceVisibility",
    "InventoryReviewDecision",
    "InventoryReviewGateError",
    "InventoryReviewPackage",
    "InventoryReviewPackageCase",
    "InventoryReviewRecord",
    "InventoryReviewReplayReport",
    "InventoryReviewSourceSession",
    "InventoryValidationSplit",
    "load_inventory_review_package",
    "load_inventory_review_record",
    "prepare_inventory_review_package",
    "run_inventory_review_replay_gate",
]

REVIEW_PACKAGE_SCHEMA_VERSION: Final[int] = 1
REVIEW_RECORD_SCHEMA_VERSION: Final[int] = 1
REPLAY_REPORT_SCHEMA_VERSION: Final[int] = 1
SANITIZED_FIXTURE_SCHEMA_VERSION: Final[int] = 2
_PACKAGE_MANIFEST_NAME: Final[str] = "review-package.json"
_PACKAGE_SHA_NAME: Final[str] = "review-package.sha256"
_REVIEW_TEMPLATE_NAME: Final[str] = "review-record.template.json"
_CANDIDATE_NAME: Final[str] = "candidate-profile.json"
_REPLAY_REPORT_NAME: Final[str] = "review-replay-report.json"
_REPLAY_REPORT_SHA_NAME: Final[str] = "review-replay-report.sha256"
_ARTIFACT_DIRECTORY: Final[str] = "panel-artifacts"
_REPLAY_DIRECTORY: Final[str] = "sanitized-replay"
_FIXTURE_MANIFEST_NAME: Final[str] = "manifest.json"
_REVIEW_PANEL_WIDTH: Final[int] = 288
_REVIEW_PANEL_HEIGHT: Final[int] = 360
_COMPONENT_MIN_AREA: Final[int] = 64
_PIXEL_DIFFERENCE_THRESHOLD: Final[int] = 24


class InventoryReviewGateError(RuntimeError):
    """Review/replay evidence violated a deterministic safety contract."""


class InventoryReviewDecision(StrEnum):
    """An explicit reviewer decision; package templates never preselect one."""

    APPROVED = "approved"
    REJECTED = "rejected"


class InventoryValidationSplit(StrEnum):
    """Reviewer-owned role of one capture in the evidence campaign."""

    REFERENCE = "reference"
    CALIBRATION = "calibration"
    HELD_OUT = "held-out"
    NEGATIVE = "negative"
    ADVERSARIAL = "adversarial"


class InventoryEvidenceVisibility(StrEnum):
    """What the reviewer can actually see, independent of operator intent."""

    INVENTORY = "inventory-visible"
    WRONG_TAB = "wrong-tab-visible"
    OBSTRUCTED = "inventory-obstructed"


@dataclass(frozen=True, slots=True)
class InventoryReviewPackageCase:
    """One source-linked, privacy-cropped review artifact."""

    order: int
    session_id: str
    capture_id: str
    operator_label: str
    source_report_path: str
    source_report_sha256: str
    source_payload_sha256: str
    frame_width: int
    frame_height: int
    pixel_format: str
    reported_dpi: int | None
    window_class: str
    panel_raw_path: str
    panel_raw_sha256: str
    panel_bmp_path: str
    panel_bmp_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "artifacts": {
                "panel_bgra": {
                    "path": self.panel_raw_path,
                    "sha256": self.panel_raw_sha256,
                },
                "panel_bmp": {
                    "path": self.panel_bmp_path,
                    "sha256": self.panel_bmp_sha256,
                },
            },
            "capture_id": self.capture_id,
            "session_id": self.session_id,
            "frame": {
                "height": self.frame_height,
                "pixel_format": self.pixel_format,
                "reported_dpi": self.reported_dpi,
                "width": self.frame_width,
                "window_class": self.window_class,
            },
            "operator_selection": {
                "label": self.operator_label,
                "truth_status": "operator-selected-unverified",
            },
            "order": self.order,
            "source": {
                "payload_sha256": self.source_payload_sha256,
                "report_path": self.source_report_path,
                "report_sha256": self.source_report_sha256,
            },
        }


@dataclass(frozen=True, slots=True)
class InventoryReviewSourceSession:
    """One immutable acquisition-session provenance binding."""

    session_id: str
    session_report_sha256: str
    capture_build: str | None
    runelite_build: str | None
    windows_scaling_percent: int | None
    client_mode: str | None
    runelite_theme: str | None
    renderer: str | None
    capture_configuration_id: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "capture_build": self.capture_build,
            "capture_configuration_id": self.capture_configuration_id,
            "client_mode": self.client_mode,
            "provenance_status": "operator-reported-bound",
            "renderer": self.renderer,
            "runelite_build": self.runelite_build,
            "runelite_theme": self.runelite_theme,
            "session_id": self.session_id,
            "session_report_sha256": self.session_report_sha256,
            "windows_scaling_percent": self.windows_scaling_percent,
        }


@dataclass(frozen=True, slots=True)
class InventoryReviewPackage:
    """Cryptographically linked, privacy-safe material for human review."""

    package_directory: Path
    generator_head_sha: str
    source_sessions: tuple[InventoryReviewSourceSession, ...]
    review_region: Region
    cases: tuple[InventoryReviewPackageCase, ...]

    @property
    def manifest_path(self) -> Path:
        return self.package_directory / _PACKAGE_MANIFEST_NAME

    @property
    def template_path(self) -> Path:
        return self.package_directory / _REVIEW_TEMPLATE_NAME

    def as_dict(self) -> dict[str, object]:
        return {
            "cases": [case.as_dict() for case in self.cases],
            "generator": {"git_head_sha": self.generator_head_sha},
            "package_kind": "inventory-privacy-review-package",
            "privacy": {
                "full_frames_included": False,
                "free_form_notes_included": False,
                "review_region": list(self.review_region.as_tuple()),
                "window_titles_included": False,
            },
            "schema_version": REVIEW_PACKAGE_SCHEMA_VERSION,
            "source_sessions": [item.as_dict() for item in self.source_sessions],
        }

    def to_json(self) -> str:
        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class InventoryCaseReview:
    """Reviewer truth for a capture, never inferred from its operator label."""

    session_id: str
    capture_id: str
    panel_raw_sha256: str
    decision: InventoryReviewDecision
    validation_split: InventoryValidationSplit
    visibility: InventoryEvidenceVisibility
    occupied_slots: int | None
    operator_intent_confirmed: bool
    selected_item_visible: bool
    drag_visible: bool
    quantity_text_visible: bool
    geometry_source: bool
    artwork_tags: tuple[str, ...]
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.session_id, "session_id")
        _require_text(self.capture_id, "capture_id")
        _require_sha256(self.panel_raw_sha256, "panel_raw_sha256")
        if not isinstance(self.decision, InventoryReviewDecision):
            raise TypeError("decision must be InventoryReviewDecision")
        if not isinstance(self.validation_split, InventoryValidationSplit):
            raise TypeError("validation_split must be InventoryValidationSplit")
        if not isinstance(self.visibility, InventoryEvidenceVisibility):
            raise TypeError("visibility must be InventoryEvidenceVisibility")
        if self.occupied_slots is not None and (
            not _strict_int(self.occupied_slots)
            or not 0 <= self.occupied_slots <= INVENTORY_CAPACITY
        ):
            raise ValueError("occupied_slots must be null or an integer in [0, 28]")
        for name, value in (
            ("operator_intent_confirmed", self.operator_intent_confirmed),
            ("selected_item_visible", self.selected_item_visible),
            ("drag_visible", self.drag_visible),
            ("quantity_text_visible", self.quantity_text_visible),
            ("geometry_source", self.geometry_source),
        ):
            if not isinstance(value, bool):
                raise TypeError(f"{name} must be bool")
        if any(not isinstance(tag, str) or not tag.strip() for tag in self.artwork_tags):
            raise ValueError("artwork_tags must contain non-empty strings")
        if len(set(self.artwork_tags)) != len(self.artwork_tags):
            raise ValueError("artwork_tags must not contain duplicates")
        if self.decision is InventoryReviewDecision.REJECTED:
            _require_text(self.rejection_reason, "rejection_reason")
        elif self.rejection_reason is not None:
            raise ValueError("approved review cannot contain a rejection_reason")
        if self.visibility is InventoryEvidenceVisibility.INVENTORY:
            if self.occupied_slots is None:
                raise ValueError("visible inventory review requires an occupied slot count")
            if self.validation_split is InventoryValidationSplit.NEGATIVE:
                raise ValueError("visible unobstructed inventory cannot use negative split")
        elif self.occupied_slots is not None:
            raise ValueError("wrong-tab/obstructed review must keep occupied_slots null")
        if (
            self.visibility is not InventoryEvidenceVisibility.INVENTORY
            and (self.selected_item_visible or self.drag_visible or self.quantity_text_visible)
        ):
            raise ValueError("adversarial item flags require a visible inventory")
        if self.geometry_source and (
            self.decision is not InventoryReviewDecision.APPROVED
            or self.visibility is not InventoryEvidenceVisibility.INVENTORY
            or self.occupied_slots != INVENTORY_CAPACITY
            or self.validation_split is not InventoryValidationSplit.CALIBRATION
            or self.selected_item_visible
            or self.drag_visible
            or self.quantity_text_visible
        ):
            raise ValueError(
                "geometry_source requires a clean approved calibration 28-slot inventory"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "artwork_tags": list(self.artwork_tags),
            "capture_id": self.capture_id,
            "decision": self.decision.value,
            "drag_visible": self.drag_visible,
            "geometry_source": self.geometry_source,
            "occupied_slots": self.occupied_slots,
            "operator_intent_confirmed": self.operator_intent_confirmed,
            "panel_raw_sha256": self.panel_raw_sha256,
            "quantity_text_visible": self.quantity_text_visible,
            "rejection_reason": self.rejection_reason,
            "selected_item_visible": self.selected_item_visible,
            "session_id": self.session_id,
            "validation_split": self.validation_split.value,
            "visibility": self.visibility.value,
        }


@dataclass(frozen=True, slots=True)
class InventoryReviewRecord:
    """Explicit review authority bound to one immutable package manifest."""

    package_manifest_sha256: str
    reviewer: str
    reviewed_at_utc: str
    cases: tuple[InventoryCaseReview, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.package_manifest_sha256, "package_manifest_sha256")
        _require_text(self.reviewer, "reviewer")
        _require_utc_timestamp(self.reviewed_at_utc, "reviewed_at_utc")
        if not self.cases:
            raise ValueError("review record must contain at least one case")
        if len({(case.session_id, case.capture_id) for case in self.cases}) != len(
            self.cases
        ):
            raise ValueError("review record session/capture identities must be unique")

    def as_dict(self) -> dict[str, object]:
        return {
            "cases": [case.as_dict() for case in self.cases],
            "package_manifest_sha256": self.package_manifest_sha256,
            "review_kind": "inventory-evidence-review",
            "reviewed_at_utc": self.reviewed_at_utc,
            "reviewer": self.reviewer,
            "schema_version": REVIEW_RECORD_SCHEMA_VERSION,
        }

    def to_json(self) -> str:
        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class CandidateInventoryProfile:
    """A reviewed-evidence-derived candidate which cannot self-activate."""

    profile: InventoryFrameProfile
    reference_session_id: str
    reference_capture_id: str
    reference_payload_sha256: str
    reference_region_sha256: str
    package_manifest_sha256: str
    review_record_sha256: str

    def as_dict(self, detector: InventoryDetector) -> dict[str, object]:
        layout = self.profile.layout
        return {
            "activation_allowed": False,
            "candidate_kind": "inventory-live-profile-candidate",
            "candidate_schema_version": 1,
            "detector": {
                "configuration_id": detector.configuration_id,
                "detector_id": detector.metadata.detector_id,
                "detector_version": detector.metadata.version,
                "minimum_slot_confidence": detector.minimum_slot_confidence,
            },
            "evidence": {
                "package_manifest_sha256": self.package_manifest_sha256,
                "reference_capture_id": self.reference_capture_id,
                "reference_session_id": self.reference_session_id,
                "reference_payload_sha256": self.reference_payload_sha256,
                "reference_region_sha256": self.reference_region_sha256,
                "review_record_sha256": self.review_record_sha256,
            },
            "frame": {
                "height": self.profile.frame_height,
                "pixel_format": PixelFormat.BGRA8888.value,
                "width": self.profile.frame_width,
            },
            "profile": {
                "column_stride": layout.column_stride,
                "columns": INVENTORY_COLUMNS,
                "profile_id": self.profile.profile_id,
                "region": list(self.profile.region.as_tuple()),
                "row_stride": layout.row_stride,
                "rows": INVENTORY_ROWS,
                "slot_size": INVENTORY_SLOT_SIZE,
            },
            "review_status": "candidate-awaiting-release-approval",
        }


@dataclass(frozen=True, slots=True)
class InventoryReviewReplayReport:
    """Canonical detector-vs-review report for one non-activating candidate."""

    report_directory: Path
    payload: Mapping[str, object]

    @property
    def report_path(self) -> Path:
        return self.report_directory / _REPLAY_REPORT_NAME

    @property
    def passed(self) -> bool:
        value = self.payload.get("release_gate_passed")
        return value is True

    def to_json(self) -> str:
        return _canonical_json(dict(self.payload))


def prepare_inventory_review_package(
    session_directories: Sequence[Path],
    output_directory: Path,
    *,
    generator_head_sha: str,
) -> InventoryReviewPackage:
    """Create deterministic panel-only review artifacts from owned sessions.

    The conservative bottom-right review crop is discovered from active pixels
    without consulting any operator-selected case label.  Candidate geometry
    is deliberately deferred until explicit reviewer truth is supplied.
    """
    sessions = _load_source_sessions(session_directories)
    _require_git_sha(generator_head_sha, "generator_head_sha")
    if not isinstance(output_directory, Path):
        raise TypeError("output_directory must be pathlib.Path")
    output_directory.mkdir(parents=True, exist_ok=False)
    artifact_directory = output_directory / _ARTIFACT_DIRECTORY
    artifact_directory.mkdir(exist_ok=False)

    owned: list[tuple[InventoryValidationSessionReport, InventoryValidationSessionRecord, Frame]] = []
    for report in sessions:
        for record in report.captured_records:
            owned.append((report, record, _load_owned_frame(report, record)))
    if not owned:
        raise InventoryReviewGateError("source sessions contain no captured cases")
    first_frame = owned[0][2]
    geometry = (first_frame.width, first_frame.height, first_frame.pixel_format)
    if any(
        (frame.width, frame.height, frame.pixel_format) != geometry
        for _, _, frame in owned
    ):
        raise InventoryReviewGateError(
            "review package requires one common frame geometry and pixel format"
        )
    if first_frame.pixel_format is not PixelFormat.BGRA8888:
        raise InventoryReviewGateError("review package v1 requires BGRA8888 source frames")
    review_region = _label_blind_review_region(first_frame)

    package_cases: list[InventoryReviewPackageCase] = []
    for package_order, (report, record, frame) in enumerate(owned, start=1):
        assert record.capture_id is not None
        assert record.report_path is not None
        assert record.report_sha256 is not None
        assert record.payload_sha256 is not None
        assert record.frame_width is not None
        assert record.frame_height is not None
        assert record.pixel_format is not None
        assert record.window_class is not None
        crop = _crop_bgra(frame, review_region)
        safe_id = _safe_component(record.capture_id)
        raw_relative = f"{_ARTIFACT_DIRECTORY}/{package_order:03d}-{safe_id}.panel.bgra"
        bmp_relative = f"{_ARTIFACT_DIRECTORY}/{package_order:03d}-{safe_id}.panel.bmp"
        raw_path = output_directory / Path(raw_relative)
        bmp_path = output_directory / Path(bmp_relative)
        bmp = _encode_bgra_bmp(crop, review_region.width, review_region.height)
        _write_bytes_exclusive(raw_path, crop)
        _write_bytes_exclusive(bmp_path, bmp)
        package_cases.append(
            InventoryReviewPackageCase(
                order=package_order,
                session_id=report.session_id,
                capture_id=record.capture_id,
                operator_label=record.case.value,
                source_report_path=record.report_path,
                source_report_sha256=record.report_sha256,
                source_payload_sha256=record.payload_sha256,
                frame_width=record.frame_width,
                frame_height=record.frame_height,
                pixel_format=record.pixel_format,
                reported_dpi=record.reported_dpi,
                window_class=record.window_class,
                panel_raw_path=raw_relative,
                panel_raw_sha256=_sha256(crop),
                panel_bmp_path=bmp_relative,
                panel_bmp_sha256=_sha256(bmp),
            )
        )

    package = InventoryReviewPackage(
        package_directory=output_directory,
        generator_head_sha=generator_head_sha,
        source_sessions=tuple(
            InventoryReviewSourceSession(
                session_id=report.session_id,
                session_report_sha256=_sha256(report.report_path.read_bytes()),
                capture_build=report.provenance.capture_build,
                runelite_build=report.provenance.runelite_build,
                windows_scaling_percent=report.provenance.windows_scaling_percent,
                client_mode=report.provenance.client_mode,
                runelite_theme=report.provenance.runelite_theme,
                renderer=report.provenance.renderer,
                capture_configuration_id=report.provenance.capture_configuration_id,
            )
            for report in sessions
        ),
        review_region=review_region,
        cases=tuple(package_cases),
    )
    manifest = package.to_json().encode("utf-8")
    manifest_sha = _sha256(manifest)
    _write_bytes_exclusive(package.manifest_path, manifest)
    _write_text_exclusive(
        output_directory / _PACKAGE_SHA_NAME,
        f"{manifest_sha}  {_PACKAGE_MANIFEST_NAME}\n",
    )
    _write_text_exclusive(package.template_path, _review_template(package, manifest_sha))
    return package


def load_inventory_review_package(package_directory: Path) -> InventoryReviewPackage:
    """Load and verify a review package, including every sanitized artifact."""
    if not isinstance(package_directory, Path):
        raise TypeError("package_directory must be pathlib.Path")
    manifest_path = package_directory / _PACKAGE_MANIFEST_NAME
    raw_bytes = _read_bytes(manifest_path, "review package manifest")
    expected_sidecar = f"{_sha256(raw_bytes)}  {_PACKAGE_MANIFEST_NAME}\n"
    sidecar = _read_text(package_directory / _PACKAGE_SHA_NAME, "package SHA sidecar")
    if sidecar != expected_sidecar:
        raise InventoryReviewGateError("review package SHA-256 sidecar mismatch")
    raw = _json_object(raw_bytes, "review package manifest")
    if raw.get("package_kind") != "inventory-privacy-review-package":
        raise InventoryReviewGateError("unsupported review package kind")
    if raw.get("schema_version") != REVIEW_PACKAGE_SCHEMA_VERSION:
        raise InventoryReviewGateError("unsupported review package schema version")
    generator = _required_object(raw, "generator")
    privacy = _required_object(raw, "privacy")
    if (
        privacy.get("full_frames_included") is not False
        or privacy.get("free_form_notes_included") is not False
        or privacy.get("window_titles_included") is not False
    ):
        raise InventoryReviewGateError("review package privacy declaration is unsafe")
    review_region = _region_value(privacy.get("review_region"), "review_region")

    sessions_raw = _required_list(raw, "source_sessions")
    source_sessions = tuple(_source_session_from_json(item) for item in sessions_raw)
    if not source_sessions or len({item.session_id for item in source_sessions}) != len(
        source_sessions
    ):
        raise InventoryReviewGateError("source sessions must be non-empty and unique")
    session_ids = {item.session_id for item in source_sessions}
    cases_raw = _required_list(raw, "cases")
    cases = tuple(
        _package_case_from_json(item, package_directory, review_region)
        for item in cases_raw
    )
    if not cases:
        raise InventoryReviewGateError("review package cases must be non-empty")
    if tuple(case.order for case in cases) != tuple(range(1, len(cases) + 1)):
        raise InventoryReviewGateError("review package case order must be contiguous")
    identities = {(case.session_id, case.capture_id) for case in cases}
    if len(identities) != len(cases):
        raise InventoryReviewGateError("review package case identities must be unique")
    if any(case.session_id not in session_ids for case in cases):
        raise InventoryReviewGateError("review package case names an unknown source session")
    return InventoryReviewPackage(
        package_directory=package_directory,
        generator_head_sha=_required_git_sha(generator, "git_head_sha"),
        source_sessions=source_sessions,
        review_region=review_region,
        cases=cases,
    )


def load_inventory_review_record(
    review_record_path: Path,
    package: InventoryReviewPackage,
) -> InventoryReviewRecord:
    """Load explicit reviewer truth and bind every case to package bytes."""
    if not isinstance(review_record_path, Path):
        raise TypeError("review_record_path must be pathlib.Path")
    raw = _json_object(_read_bytes(review_record_path, "review record"), "review record")
    if raw.get("review_kind") != "inventory-evidence-review":
        raise InventoryReviewGateError("unsupported review record kind")
    if raw.get("schema_version") != REVIEW_RECORD_SCHEMA_VERSION:
        raise InventoryReviewGateError("unsupported review record schema version")
    manifest_sha = _sha256(package.manifest_path.read_bytes())
    if _required_sha256(raw, "package_manifest_sha256") != manifest_sha:
        raise InventoryReviewGateError("review record is bound to another package manifest")
    cases = tuple(_review_case_from_json(item) for item in _required_list(raw, "cases"))
    record = InventoryReviewRecord(
        package_manifest_sha256=manifest_sha,
        reviewer=_required_text(raw, "reviewer"),
        reviewed_at_utc=_required_text(raw, "reviewed_at_utc"),
        cases=cases,
    )
    expected = {
        (case.session_id, case.capture_id): case.panel_raw_sha256
        for case in package.cases
    }
    actual = {
        (case.session_id, case.capture_id): case.panel_raw_sha256
        for case in record.cases
    }
    if actual != expected:
        raise InventoryReviewGateError(
            "review record must cover every package case with exact artifact hashes"
        )
    return record


def run_inventory_review_replay_gate(
    session_directories: Sequence[Path],
    package_directory: Path,
    review_record_path: Path,
    output_directory: Path,
    *,
    expected_head_sha: str,
    fixture_output_directory: Path | None = None,
) -> InventoryReviewReplayReport:
    """Derive a candidate from reviewed evidence and run production replay.

    There are intentionally no policy, threshold, geometry, or activation
    overrides.  Candidate geometry comes from the unique full-vs-reference
    component lattice selected by explicit reviewer roles.
    """
    _require_git_sha(expected_head_sha, "expected_head_sha")
    package = load_inventory_review_package(package_directory)
    if package.generator_head_sha != expected_head_sha:
        raise InventoryReviewGateError(
            "review package generator head does not match the expected head"
        )
    review = load_inventory_review_record(review_record_path, package)
    sessions = _verified_session_map(session_directories, package)
    frames = _verified_package_frames(package, sessions)
    reviews = {
        (item.session_id, item.capture_id): item for item in review.cases
    }

    reference_review = _exact_review_role(
        review,
        lambda item: (
            item.decision is InventoryReviewDecision.APPROVED
            and item.validation_split is InventoryValidationSplit.REFERENCE
        ),
        "approved reference",
    )
    if (
        reference_review.visibility is not InventoryEvidenceVisibility.INVENTORY
        or reference_review.occupied_slots != 0
        or reference_review.selected_item_visible
        or reference_review.drag_visible
        or reference_review.quantity_text_visible
    ):
        raise InventoryReviewGateError(
            "reference review must be a clear, visible, zero-slot inventory"
        )
    geometry_review = _exact_review_role(
        review,
        lambda item: item.geometry_source,
        "approved 28-slot geometry source",
    )
    reference_key = (reference_review.session_id, reference_review.capture_id)
    geometry_key = (geometry_review.session_id, geometry_review.capture_id)
    _require_shared_profile_environment(package, reference_review, geometry_review)
    reference_frame = frames[reference_key]
    geometry_frame = frames[geometry_key]
    region, column_stride, row_stride = _derive_unique_inventory_lattice(
        reference_frame,
        geometry_frame,
    )
    if row_stride <= INVENTORY_SLOT_SIZE:
        raise InventoryReviewGateError(
            "derived candidate has no horizontal row-gutter obstruction guard"
        )
    if not _contains(package.review_region, region):
        raise InventoryReviewGateError(
            "derived inventory region falls outside the privacy review crop"
        )

    manifest_sha = review.package_manifest_sha256
    review_sha = _sha256(review_record_path.read_bytes())
    reference_region = _crop_bgra(reference_frame, region)
    reference_region_sha = _sha256(reference_region)
    candidate_identity = _sha256(
        _canonical_json(
            {
                "frame": {
                    "height": reference_frame.height,
                    "pixel_format": reference_frame.pixel_format.value,
                    "width": reference_frame.width,
                },
                "geometry": [*region.as_tuple(), column_stride, row_stride],
                "reference_region_sha256": reference_region_sha,
            }
        ).encode("utf-8")
    )
    profile_id = f"candidate-live-inventory-{candidate_identity[:16]}"
    layout = InventoryGridLayout(
        profile_id=profile_id,
        column_stride=column_stride,
        row_stride=row_stride,
    )
    profile = InventoryFrameProfile(
        profile_id=profile_id,
        frame_width=reference_frame.width,
        frame_height=reference_frame.height,
        region=region,
        layout=layout,
    )
    detector = inventory_detector_from_profile(profile, reference_frame)
    candidate = CandidateInventoryProfile(
        profile=profile,
        reference_session_id=reference_review.session_id,
        reference_capture_id=reference_review.capture_id,
        reference_payload_sha256=_sha256(reference_frame.payload),
        reference_region_sha256=reference_region_sha,
        package_manifest_sha256=manifest_sha,
        review_record_sha256=review_sha,
    )

    if not isinstance(output_directory, Path):
        raise TypeError("output_directory must be pathlib.Path")
    output_directory.mkdir(parents=True, exist_ok=False)
    replay_directory = output_directory / _REPLAY_DIRECTORY
    replay_directory.mkdir(exist_ok=False)
    results: list[dict[str, object]] = []
    fixture_cases: list[dict[str, object]] = []
    for package_case in package.cases:
        key = (package_case.session_id, package_case.capture_id)
        case_review = reviews[key]
        frame = frames[key]
        observation = detector.detect(frame)[0]
        detection = inventory_detection_from_observation(observation)
        sanitized = _sanitized_frame(frame, region)
        sanitized_observation = detector.detect(sanitized)[0]
        if sanitized_observation != observation:
            raise InventoryReviewGateError(
                "pixels outside the candidate inventory region changed detector output: "
                f"{package_case.session_id}/{package_case.capture_id}"
            )
        region_payload = _crop_bgra(frame, region)
        fixture_name = (
            f"{package_case.order:03d}-{_safe_component(package_case.capture_id)}.region.bgra"
        )
        relative_fixture = f"{_REPLAY_DIRECTORY}/{fixture_name}"
        _write_bytes_exclusive(output_directory / Path(relative_fixture), region_payload)
        requirement, agreement, gate_reason = _review_agreement(case_review, detection)
        result = {
            "candidate_region_artifact": {
                "path": relative_fixture,
                "sha256": _sha256(region_payload),
            },
            "capture_id": package_case.capture_id,
            "detector": _detection_dict(detection),
            "exact_owned_payload_sha256": package_case.source_payload_sha256,
            "operator_selection": {
                "label": package_case.operator_label,
                "truth_status": "operator-selected-unverified",
            },
            "review": case_review.as_dict(),
            "review_agreement": agreement,
            "review_gate_reason": gate_reason,
            "requirement": requirement,
            "sanitized_observation_equals_exact_owned": True,
            "session_id": package_case.session_id,
        }
        results.append(result)
        fixture_cases.append(
            {
                "case_id": f"{package_case.session_id}/{package_case.capture_id}",
                "current_safety_expectation": _detection_dict(detection),
                "frame_region": {
                    "path": fixture_name,
                    "sha256": _sha256(region_payload),
                },
                "review_truth": case_review.as_dict(),
                "source": {
                    "payload_sha256": package_case.source_payload_sha256,
                    "report_sha256": package_case.source_report_sha256,
                    "session_report_sha256": next(
                        item.session_report_sha256
                        for item in package.source_sessions
                        if item.session_id == package_case.session_id
                    ),
                },
            }
        )

    remaining_gaps = _remaining_release_gaps(
        package,
        review,
        results,
        candidate.reference_region_sha256,
    )
    failed_cases = [
        f"{item['session_id']}/{item['capture_id']}"
        for item in results
        if item["review_agreement"] is not True
    ]
    release_gate_passed = not failed_cases and not remaining_gaps
    payload: dict[str, object] = {
        "candidate": candidate.as_dict(detector),
        "detector": {
            "configuration_id": detector.configuration_id,
            "detector_id": detector.metadata.detector_id,
            "detector_version": detector.metadata.version,
        },
        "exact_head_sha": expected_head_sha,
        "failed_case_ids": failed_cases,
        "gate_kind": "inventory-review-replay",
        "release_gate_passed": release_gate_passed,
        "remaining_release_gaps": remaining_gaps,
        "results": results,
        "review_record_sha256": review_sha,
        "schema_version": REPLAY_REPORT_SCHEMA_VERSION,
        "warning": (
            "Candidate only. This report cannot activate a production profile, and "
            "operator-selected labels are not reviewer truth."
        ),
    }
    candidate_json = _canonical_json(candidate.as_dict(detector))
    report = InventoryReviewReplayReport(output_directory, payload)
    report_json = report.to_json()
    _write_text_exclusive(output_directory / _CANDIDATE_NAME, candidate_json)
    _write_text_exclusive(report.report_path, report_json)
    _write_text_exclusive(
        output_directory / _REPLAY_REPORT_SHA_NAME,
        f"{_sha256(report_json.encode('utf-8'))}  {_REPLAY_REPORT_NAME}\n",
    )
    if fixture_output_directory is not None:
        _publish_sanitized_fixture(
            fixture_output_directory,
            candidate,
            detector,
            fixture_cases,
            replay_directory,
            expected_head_sha,
        )
    return report


def _review_agreement(
    review: InventoryCaseReview,
    detection: InventoryDetection,
) -> tuple[str, bool, str | None]:
    if review.decision is InventoryReviewDecision.REJECTED:
        return "review-rejected", False, review.rejection_reason
    if review.visibility in (
        InventoryEvidenceVisibility.WRONG_TAB,
        InventoryEvidenceVisibility.OBSTRUCTED,
    ):
        passed = (
            detection.label == "unknown"
            and detection.occupied_slots is None
            and detection.confidence == 0.0
            and detection.reason is not None
        )
        return (
            "fail-closed-unknown",
            passed,
            None if passed else "negative evidence did not fail closed",
        )
    assert review.occupied_slots is not None
    adversarial = (
        review.selected_item_visible
        or review.drag_visible
        or review.quantity_text_visible
    )
    exact = (
        detection.occupied_slots == review.occupied_slots
        and detection.label == _label_for_count(review.occupied_slots)
        and detection.confidence > 0.0
        and detection.reason is None
    )
    fail_closed = (
        detection.label == "unknown"
        and detection.occupied_slots is None
        and detection.confidence == 0.0
        and detection.reason is not None
    )
    if adversarial:
        passed = exact or fail_closed
        return (
            "exact-count-or-fail-closed-unknown",
            passed,
            None if passed else "adversarial evidence produced an incorrect known count",
        )
    return (
        "exact-reviewer-count",
        exact,
        None if exact else f"detector did not publish reviewed count {review.occupied_slots}",
    )


@dataclass(frozen=True, slots=True)
class _PixelComponent:
    area: int
    x: int
    y: int
    width: int
    height: int
    center_x: int
    center_y: int


def _derive_unique_inventory_lattice(
    reference: Frame,
    full: Frame,
) -> tuple[Region, int, int]:
    if (reference.width, reference.height, reference.pixel_format) != (
        full.width,
        full.height,
        full.pixel_format,
    ):
        raise InventoryReviewGateError("geometry source and reference frames do not match")
    if reference.pixel_format is not PixelFormat.BGRA8888:
        raise InventoryReviewGateError("lattice derivation requires BGRA8888 frames")
    components = _changed_components(reference, full)
    by_center: dict[tuple[int, int], list[_PixelComponent]] = {}
    for component in components:
        by_center.setdefault((component.center_x, component.center_y), []).append(component)
    candidates: set[tuple[int, int, int, int]] = set()
    for top in components:
        for right in components:
            if right.center_y != top.center_y or right.center_x <= top.center_x:
                continue
            column_stride = right.center_x - top.center_x
            if column_stride < INVENTORY_SLOT_SIZE:
                continue
            for down in components:
                if down.center_x != top.center_x or down.center_y <= top.center_y:
                    continue
                row_stride = down.center_y - top.center_y
                if row_stride <= INVENTORY_SLOT_SIZE:
                    continue
                selected: list[_PixelComponent] = []
                valid = True
                for row in range(INVENTORY_ROWS):
                    for column in range(INVENTORY_COLUMNS):
                        center = (
                            top.center_x + column * column_stride,
                            top.center_y + row * row_stride,
                        )
                        matches = by_center.get(center, [])
                        if len(matches) != 1 or not _component_fits_centered_slot(
                            matches[0], center
                        ):
                            valid = False
                            break
                        selected.append(matches[0])
                    if not valid:
                        break
                if not valid or len(selected) != INVENTORY_CAPACITY:
                    continue
                origin_x = top.center_x - INVENTORY_SLOT_SIZE // 2
                origin_y = top.center_y - INVENTORY_SLOT_SIZE // 2
                if origin_x < 0 or origin_y < 0:
                    continue
                layout = InventoryGridLayout(
                    profile_id="candidate-lattice-validation",
                    column_stride=column_stride,
                    row_stride=row_stride,
                )
                region = layout.region_at(origin_x, origin_y)
                if not region.fits(reference.width, reference.height):
                    continue
                candidates.add((origin_x, origin_y, column_stride, row_stride))
    if len(candidates) != 1:
        raise InventoryReviewGateError(
            "reviewed reference/full evidence must yield exactly one 4x7 lattice; "
            f"found {len(candidates)}"
        )
    x, y, column_stride, row_stride = next(iter(candidates))
    layout = InventoryGridLayout(
        profile_id="candidate-lattice-result",
        column_stride=column_stride,
        row_stride=row_stride,
    )
    return layout.region_at(x, y), column_stride, row_stride


def _changed_components(reference: Frame, candidate: Frame) -> tuple[_PixelComponent, ...]:
    width = reference.width
    height = reference.height
    reference_payload = reference.payload
    candidate_payload = candidate.payload
    mask = bytearray(width * height)
    for pixel in range(width * height):
        offset = pixel * 4
        if max(
            abs(reference_payload[offset] - candidate_payload[offset]),
            abs(reference_payload[offset + 1] - candidate_payload[offset + 1]),
            abs(reference_payload[offset + 2] - candidate_payload[offset + 2]),
        ) >= _PIXEL_DIFFERENCE_THRESHOLD:
            mask[pixel] = 1
    components: list[_PixelComponent] = []
    for start in range(len(mask)):
        if not mask[start]:
            continue
        mask[start] = 0
        pending: deque[int] = deque((start,))
        start_y, start_x = divmod(start, width)
        min_x = max_x = start_x
        min_y = max_y = start_y
        area = 0
        while pending:
            current = pending.popleft()
            current_y, current_x = divmod(current, width)
            area += 1
            min_x = min(min_x, current_x)
            max_x = max(max_x, current_x)
            min_y = min(min_y, current_y)
            max_y = max(max_y, current_y)
            for neighbor_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                row_offset = neighbor_y * width
                for neighbor_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                    neighbor = row_offset + neighbor_x
                    if mask[neighbor]:
                        mask[neighbor] = 0
                        pending.append(neighbor)
        component_width = max_x - min_x + 1
        component_height = max_y - min_y + 1
        center_x_twice = min_x + max_x + 1
        center_y_twice = min_y + max_y + 1
        if (
            area < _COMPONENT_MIN_AREA
            or component_width > INVENTORY_SLOT_SIZE
            or component_height > INVENTORY_SLOT_SIZE
            or center_x_twice % 2
            or center_y_twice % 2
        ):
            continue
        components.append(
            _PixelComponent(
                area=area,
                x=min_x,
                y=min_y,
                width=component_width,
                height=component_height,
                center_x=center_x_twice // 2,
                center_y=center_y_twice // 2,
            )
        )
    return tuple(components)


def _component_fits_centered_slot(
    component: _PixelComponent,
    center: tuple[int, int],
) -> bool:
    left = center[0] - INVENTORY_SLOT_SIZE // 2
    top = center[1] - INVENTORY_SLOT_SIZE // 2
    return (
        component.x >= left
        and component.y >= top
        and component.x + component.width <= left + INVENTORY_SLOT_SIZE
        and component.y + component.height <= top + INVENTORY_SLOT_SIZE
    )


def _load_source_sessions(
    session_directories: Sequence[Path],
) -> tuple[InventoryValidationSessionReport, ...]:
    if isinstance(session_directories, (str, bytes, bytearray)):
        raise TypeError("session_directories must be a sequence of pathlib.Path")
    directories = tuple(session_directories)
    if not directories or any(not isinstance(item, Path) for item in directories):
        raise TypeError("session_directories must contain at least one pathlib.Path")
    reports = tuple(load_inventory_validation_session(path) for path in directories)
    if len({item.session_id for item in reports}) != len(reports):
        raise InventoryReviewGateError("source session ids must be unique")
    if any(not item.complete for item in reports):
        raise InventoryReviewGateError("source sessions must be complete")
    return reports


def _verified_session_map(
    session_directories: Sequence[Path],
    package: InventoryReviewPackage,
) -> dict[str, InventoryValidationSessionReport]:
    reports = _load_source_sessions(session_directories)
    actual = {
        report.session_id: (
            _sha256(report.report_path.read_bytes()),
            report.provenance.capture_build,
            report.provenance.runelite_build,
            report.provenance.windows_scaling_percent,
            report.provenance.client_mode,
            report.provenance.runelite_theme,
            report.provenance.renderer,
            report.provenance.capture_configuration_id,
        )
        for report in reports
    }
    expected = {
        item.session_id: (
            item.session_report_sha256,
            item.capture_build,
            item.runelite_build,
            item.windows_scaling_percent,
            item.client_mode,
            item.runelite_theme,
            item.renderer,
            item.capture_configuration_id,
        )
        for item in package.source_sessions
    }
    if actual != expected:
        raise InventoryReviewGateError("source sessions do not match the review package")
    return {report.session_id: report for report in reports}


def _verified_package_frames(
    package: InventoryReviewPackage,
    sessions: Mapping[str, InventoryValidationSessionReport],
) -> dict[tuple[str, str], Frame]:
    expected_records: list[
        tuple[
            InventoryReviewPackageCase,
            InventoryValidationSessionReport,
            InventoryValidationSessionRecord,
            Frame,
        ]
    ] = []
    expected_order = 0
    for source in package.source_sessions:
        report = sessions[source.session_id]
        for record in report.captured_records:
            expected_order += 1
            if expected_order > len(package.cases):
                raise InventoryReviewGateError(
                    "review package omits durable source captures"
                )
            package_case = package.cases[expected_order - 1]
            assert record.capture_id is not None
            assert record.report_path is not None
            assert record.report_sha256 is not None
            assert record.payload_sha256 is not None
            assert record.frame_width is not None
            assert record.frame_height is not None
            assert record.pixel_format is not None
            assert record.window_class is not None
            expected_metadata = (
                expected_order,
                report.session_id,
                record.capture_id,
                record.case.value,
                record.report_path,
                record.report_sha256,
                record.payload_sha256,
                record.frame_width,
                record.frame_height,
                record.pixel_format,
                record.reported_dpi,
                record.window_class,
            )
            package_metadata = (
                package_case.order,
                package_case.session_id,
                package_case.capture_id,
                package_case.operator_label,
                package_case.source_report_path,
                package_case.source_report_sha256,
                package_case.source_payload_sha256,
                package_case.frame_width,
                package_case.frame_height,
                package_case.pixel_format,
                package_case.reported_dpi,
                package_case.window_class,
            )
            if package_metadata != expected_metadata:
                raise InventoryReviewGateError(
                    "review package case sequence/metadata differs from durable sessions"
                )
            expected_records.append(
                (package_case, report, record, _load_owned_frame(report, record))
            )
    if expected_order != len(package.cases):
        raise InventoryReviewGateError("review package contains non-source captures")
    if not expected_records:
        raise InventoryReviewGateError("review package contains no durable captures")

    recomputed_review_region = _label_blind_review_region(expected_records[0][3])
    if recomputed_review_region != package.review_region:
        raise InventoryReviewGateError(
            "review crop is not the deterministic label-blind source-frame crop"
        )

    frames: dict[tuple[str, str], Frame] = {}
    for package_case, _, _, frame in expected_records:
        expected_raw = _crop_bgra(frame, package.review_region)
        expected_bmp = _encode_bgra_bmp(
            expected_raw,
            package.review_region.width,
            package.review_region.height,
        )
        actual_raw = _read_bytes(
            package.package_directory / Path(package_case.panel_raw_path),
            "panel BGRA artifact",
        )
        actual_bmp = _read_bytes(
            package.package_directory / Path(package_case.panel_bmp_path),
            "panel BMP artifact",
        )
        if (
            actual_raw != expected_raw
            or package_case.panel_raw_sha256 != _sha256(expected_raw)
        ):
            raise InventoryReviewGateError(
                "review panel BGRA does not match the durable owned frame"
            )
        if (
            actual_bmp != expected_bmp
            or package_case.panel_bmp_sha256 != _sha256(expected_bmp)
        ):
            raise InventoryReviewGateError(
                "review panel BMP does not match the durable owned frame"
            )
        frames[(package_case.session_id, package_case.capture_id)] = frame
    return frames


def _load_owned_frame(
    report: InventoryValidationSessionReport,
    record: InventoryValidationSessionRecord,
) -> Frame:
    if record.status.value != "captured":
        raise InventoryReviewGateError("review source record is not captured")
    assert record.report_path is not None
    assert record.report_sha256 is not None
    assert record.payload_sha256 is not None
    assert record.frame_width is not None
    assert record.frame_height is not None
    assert record.pixel_format is not None
    report_path = _owned_path(report.session_directory, record.report_path, "capture report")
    report_bytes = _read_bytes(report_path, "capture report")
    if _sha256(report_bytes) != record.report_sha256:
        raise InventoryReviewGateError("capture report SHA-256 mismatch")
    capture_report = _json_object(report_bytes, "capture report")
    artifacts = _required_object(capture_report, "artifacts")
    raw_artifact = _required_object(artifacts, "raw")
    raw_path = _owned_path(
        report_path.parent,
        _required_text(raw_artifact, "path"),
        "raw frame",
    )
    payload = _read_bytes(raw_path, "raw frame")
    if (
        _required_sha256(raw_artifact, "sha256") != record.payload_sha256
        or _sha256(payload) != record.payload_sha256
    ):
        raise InventoryReviewGateError("raw frame SHA-256 mismatch")
    try:
        pixel_format = PixelFormat(record.pixel_format)
    except ValueError as exc:
        raise InventoryReviewGateError("unsupported source pixel format") from exc
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=record.frame_width,
            height=record.frame_height,
            pixel_format=pixel_format,
        ),
        frame_id=record.order,
        captured_monotonic_s=float(record.order - 1),
    )


def _label_blind_review_region(frame: Frame) -> Region:
    if frame.pixel_format is not PixelFormat.BGRA8888:
        raise InventoryReviewGateError("review crop discovery requires BGRA8888")
    min_x = frame.width
    min_y = frame.height
    max_x = -1
    max_y = -1
    for y in range(frame.height):
        row = y * frame.width * 4
        for x in range(frame.width):
            offset = row + x * 4
            if any(frame.payload[offset + channel] != 0 for channel in range(4)):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        raise InventoryReviewGateError("source frame contains no active pixels")
    width = min(_REVIEW_PANEL_WIDTH, max_x + 1)
    height = min(_REVIEW_PANEL_HEIGHT, max_y + 1)
    region = Region(max_x + 1 - width, max_y + 1 - height, width, height)
    if not region.fits(frame.width, frame.height):  # pragma: no cover - invariant
        raise InventoryReviewGateError("derived review crop is outside the source frame")
    return region


def _crop_bgra(frame: Frame, region: Region) -> bytes:
    if frame.pixel_format is not PixelFormat.BGRA8888:
        raise InventoryReviewGateError("BGRA crop requested from another pixel format")
    if not region.fits(frame.width, frame.height):
        raise InventoryReviewGateError("crop region is outside the source frame")
    row_bytes = region.width * 4
    payload = bytearray(region.width * region.height * 4)
    for row in range(region.height):
        source_start = ((region.y + row) * frame.width + region.x) * 4
        target_start = row * row_bytes
        payload[target_start : target_start + row_bytes] = frame.payload[
            source_start : source_start + row_bytes
        ]
    return bytes(payload)


def _sanitized_frame(frame: Frame, region: Region) -> Frame:
    payload = bytearray(len(frame.payload))
    row_bytes = region.width * 4
    for row in range(region.height):
        source_start = ((region.y + row) * frame.width + region.x) * 4
        payload[source_start : source_start + row_bytes] = frame.payload[
            source_start : source_start + row_bytes
        ]
    return Frame(ref=frame.ref, payload=bytes(payload), pixel_format=frame.pixel_format)


def _encode_bgra_bmp(payload: bytes, width: int, height: int) -> bytes:
    expected = width * height * 4
    if len(payload) != expected:
        raise InventoryReviewGateError("BGRA BMP payload length mismatch")
    pixel_offset = 14 + 40
    file_size = pixel_offset + expected
    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
    info_header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        -height,
        1,
        32,
        0,
        expected,
        0,
        0,
        0,
        0,
    )
    return file_header + info_header + payload


def _review_template(package: InventoryReviewPackage, manifest_sha: str) -> str:
    cases = []
    for case in package.cases:
        cases.append(
            {
                "artwork_tags": None,
                "capture_id": case.capture_id,
                "decision": None,
                "drag_visible": None,
                "geometry_source": None,
                "occupied_slots": None,
                "operator_intent_confirmed": None,
                "panel_raw_sha256": case.panel_raw_sha256,
                "quantity_text_visible": None,
                "rejection_reason": None,
                "selected_item_visible": None,
                "session_id": case.session_id,
                "validation_split": None,
                "visibility": None,
            }
        )
    return _canonical_json(
        {
            "cases": cases,
            "package_manifest_sha256": manifest_sha,
            "review_kind": "inventory-evidence-review",
            "reviewed_at_utc": None,
            "reviewer": None,
            "schema_version": REVIEW_RECORD_SCHEMA_VERSION,
            "warning": (
                "Blank reviewer truth. No field is populated from an operator label."
            ),
        }
    )


def _source_session_from_json(value: object) -> InventoryReviewSourceSession:
    raw = _object_value(value, "source session")
    if raw.get("provenance_status") != "operator-reported-bound":
        raise InventoryReviewGateError(
            "source session provenance must remain operator-reported and bound"
        )
    return InventoryReviewSourceSession(
        session_id=_required_text(raw, "session_id"),
        session_report_sha256=_required_sha256(raw, "session_report_sha256"),
        capture_build=_optional_text(raw.get("capture_build"), "capture_build"),
        runelite_build=_optional_text(raw.get("runelite_build"), "runelite_build"),
        windows_scaling_percent=_optional_positive_int(
            raw.get("windows_scaling_percent"), "windows_scaling_percent"
        ),
        client_mode=_optional_text(raw.get("client_mode"), "client_mode"),
        runelite_theme=_optional_text(raw.get("runelite_theme"), "runelite_theme"),
        renderer=_optional_text(raw.get("renderer"), "renderer"),
        capture_configuration_id=_optional_text(
            raw.get("capture_configuration_id"), "capture_configuration_id"
        ),
    )


def _package_case_from_json(
    value: object,
    package_directory: Path,
    review_region: Region,
) -> InventoryReviewPackageCase:
    raw = _object_value(value, "review package case")
    source = _required_object(raw, "source")
    frame = _required_object(raw, "frame")
    operator = _required_object(raw, "operator_selection")
    artifacts = _required_object(raw, "artifacts")
    panel_raw = _required_object(artifacts, "panel_bgra")
    panel_bmp = _required_object(artifacts, "panel_bmp")
    if operator.get("truth_status") != "operator-selected-unverified":
        raise InventoryReviewGateError("operator selection cannot contain verified truth")
    raw_path, raw_sha = _verified_relative_artifact(
        package_directory, panel_raw, "panel BGRA"
    )
    bmp_path, bmp_sha = _verified_relative_artifact(
        package_directory, panel_bmp, "panel BMP"
    )
    if raw_path.stat().st_size != review_region.width * review_region.height * 4:
        raise InventoryReviewGateError("panel BGRA artifact length mismatch")
    return InventoryReviewPackageCase(
        order=_required_positive_int(raw, "order"),
        session_id=_required_text(raw, "session_id"),
        capture_id=_required_text(raw, "capture_id"),
        operator_label=_required_text(operator, "label"),
        source_report_path=_required_relative_path(source, "report_path"),
        source_report_sha256=_required_sha256(source, "report_sha256"),
        source_payload_sha256=_required_sha256(source, "payload_sha256"),
        frame_width=_required_positive_int(frame, "width"),
        frame_height=_required_positive_int(frame, "height"),
        pixel_format=_required_text(frame, "pixel_format"),
        reported_dpi=_optional_positive_int(frame.get("reported_dpi"), "reported_dpi"),
        window_class=_required_text(frame, "window_class"),
        panel_raw_path=raw_path.relative_to(package_directory.resolve()).as_posix(),
        panel_raw_sha256=raw_sha,
        panel_bmp_path=bmp_path.relative_to(package_directory.resolve()).as_posix(),
        panel_bmp_sha256=bmp_sha,
    )


def _review_case_from_json(value: object) -> InventoryCaseReview:
    raw = _object_value(value, "review case")
    try:
        decision = InventoryReviewDecision(_required_text(raw, "decision"))
        split = InventoryValidationSplit(_required_text(raw, "validation_split"))
        visibility = InventoryEvidenceVisibility(_required_text(raw, "visibility"))
    except ValueError as exc:
        raise InventoryReviewGateError("unsupported reviewer decision/split/visibility") from exc
    tags_raw = _required_list(raw, "artwork_tags")
    tags = tuple(_text_value(item, "artwork tag") for item in tags_raw)
    return InventoryCaseReview(
        session_id=_required_text(raw, "session_id"),
        capture_id=_required_text(raw, "capture_id"),
        panel_raw_sha256=_required_sha256(raw, "panel_raw_sha256"),
        decision=decision,
        validation_split=split,
        visibility=visibility,
        occupied_slots=_optional_slot_count(raw.get("occupied_slots")),
        operator_intent_confirmed=_required_bool(raw, "operator_intent_confirmed"),
        selected_item_visible=_required_bool(raw, "selected_item_visible"),
        drag_visible=_required_bool(raw, "drag_visible"),
        quantity_text_visible=_required_bool(raw, "quantity_text_visible"),
        geometry_source=_required_bool(raw, "geometry_source"),
        artwork_tags=tags,
        rejection_reason=_optional_text(raw.get("rejection_reason"), "rejection_reason"),
    )


def _exact_review_role(
    review: InventoryReviewRecord,
    predicate: Callable[[InventoryCaseReview], bool],
    label: str,
) -> InventoryCaseReview:
    matches = tuple(item for item in review.cases if predicate(item))
    if len(matches) != 1:
        raise InventoryReviewGateError(f"review record requires exactly one {label}")
    return matches[0]


def _require_shared_profile_environment(
    package: InventoryReviewPackage,
    reference: InventoryCaseReview,
    geometry: InventoryCaseReview,
) -> None:
    sources = {item.session_id: item for item in package.source_sessions}
    cases = {
        (item.session_id, item.capture_id): item for item in package.cases
    }
    reference_source = sources[reference.session_id]
    geometry_source = sources[geometry.session_id]
    reference_environment = (
        reference_source.capture_build,
        reference_source.runelite_build,
        reference_source.windows_scaling_percent,
        reference_source.client_mode,
        reference_source.runelite_theme,
        reference_source.renderer,
        reference_source.capture_configuration_id,
    )
    geometry_environment = (
        geometry_source.capture_build,
        geometry_source.runelite_build,
        geometry_source.windows_scaling_percent,
        geometry_source.client_mode,
        geometry_source.runelite_theme,
        geometry_source.renderer,
        geometry_source.capture_configuration_id,
    )
    reference_case = cases[(reference.session_id, reference.capture_id)]
    geometry_case = cases[(geometry.session_id, geometry.capture_id)]
    if reference_environment != geometry_environment:
        raise InventoryReviewGateError(
            "reference and geometry evidence use different capture environments"
        )
    if (
        reference_case.reported_dpi != geometry_case.reported_dpi
        or reference_case.window_class != geometry_case.window_class
    ):
        raise InventoryReviewGateError(
            "reference and geometry evidence use different DPI/window classes"
        )


def _detection_dict(detection: InventoryDetection) -> dict[str, object]:
    return {
        "confidence": detection.confidence,
        "configuration_id": detection.configuration_id,
        "label": detection.label,
        "localization_confidence": detection.localization_confidence,
        "occupied_slots": detection.occupied_slots,
        "profile_id": detection.profile_id,
        "reason": detection.reason,
        "region": None if detection.region is None else list(detection.region.as_tuple()),
        "slots": [
            {
                "confidence": item.confidence,
                "index": item.index,
                "score": item.score,
                "state": item.state.value,
            }
            for item in detection.slots
        ],
    }


def _remaining_release_gaps(
    package: InventoryReviewPackage,
    review: InventoryReviewRecord,
    results: Sequence[Mapping[str, object]],
    reference_region_sha256: str,
) -> list[str]:
    gaps: list[str] = []
    for source in package.source_sessions:
        if source.capture_build is None or not _is_git_sha(source.capture_build):
            gaps.append(
                f"source session {source.session_id} has no exact capture-build SHA"
            )
        if source.runelite_build is None:
            gaps.append(f"source session {source.session_id} has no RuneLite build")
        if source.windows_scaling_percent is None:
            gaps.append(
                f"source session {source.session_id} has no Windows scaling"
            )
        if source.client_mode is None:
            gaps.append(f"source session {source.session_id} has no client mode")
        if source.runelite_theme is None:
            gaps.append(f"source session {source.session_id} has no RuneLite theme")
        if source.renderer is None:
            gaps.append(f"source session {source.session_id} has no renderer")
        if source.capture_configuration_id is None:
            gaps.append(
                f"source session {source.session_id} has no capture configuration identity"
            )
        for label, value in (
            ("RuneLite build", source.runelite_build),
            ("client mode", source.client_mode),
            ("RuneLite theme", source.runelite_theme),
            ("renderer", source.renderer),
            ("capture configuration", source.capture_configuration_id),
        ):
            if value is not None and value.strip().casefold() in {
                "n/a",
                "none",
                "unknown",
                "unspecified",
            }:
                gaps.append(
                    f"source session {source.session_id} has placeholder {label}"
                )
        source_cases = tuple(
            item for item in package.cases if item.session_id == source.session_id
        )
        if any(item.reported_dpi is None for item in source_cases):
            gaps.append(f"source session {source.session_id} has no reported DPI")
        elif source.windows_scaling_percent is not None:
            expected_dpi = round(96 * source.windows_scaling_percent / 100)
            reported_dpis = {item.reported_dpi for item in source_cases}
            if reported_dpis != {expected_dpi}:
                gaps.append(
                    f"source session {source.session_id} scaling/DPI evidence disagrees"
                )
    if len({item.window_class for item in package.cases}) != 1:
        gaps.append("review evidence uses more than one window class")
    approved = tuple(
        item for item in review.cases if item.decision is InventoryReviewDecision.APPROVED
    )
    held_out_empty_hashes: set[str] = set()
    for item in results:
        review_value = item.get("review")
        artifact_value = item.get("candidate_region_artifact")
        if (
            isinstance(review_value, dict)
            and isinstance(artifact_value, dict)
            and review_value.get("decision") == InventoryReviewDecision.APPROVED.value
            and review_value.get("validation_split")
            == InventoryValidationSplit.HELD_OUT.value
            and review_value.get("visibility")
            == InventoryEvidenceVisibility.INVENTORY.value
            and review_value.get("occupied_slots") == 0
        ):
            held_out_empty_hashes.add(
                _text_value(artifact_value.get("sha256"), "candidate region SHA-256")
            )
    if not held_out_empty_hashes:
        gaps.append("no reviewed held-out empty inventory evidence")
    elif held_out_empty_hashes == {reference_region_sha256}:
        gaps.append(
            "held-out empty detector-owned pixels are byte-identical to the reference; "
            "independent capture is proven but pixel-domain variation is not"
        )
    artifact_hash_by_key: dict[tuple[str, str], str] = {}
    for item in results:
        review_value = item.get("review")
        artifact_value = item.get("candidate_region_artifact")
        if not isinstance(review_value, dict) or not isinstance(artifact_value, dict):
            continue
        key = (
            _text_value(review_value.get("session_id"), "review session id"),
            _text_value(review_value.get("capture_id"), "review capture id"),
        )
        artifact_hash_by_key[key] = _text_value(
            artifact_value.get("sha256"), "candidate region SHA-256"
        )
    obstruction_hashes = {
        artifact_hash_by_key[(item.session_id, item.capture_id)]
        for item in approved
        if item.visibility is InventoryEvidenceVisibility.OBSTRUCTED
        and item.validation_split
        in (InventoryValidationSplit.NEGATIVE, InventoryValidationSplit.ADVERSARIAL)
    }
    if len(obstruction_hashes) < 2:
        gaps.append("fewer than two distinct reviewed obstruction examples")
    if not any(
        item.visibility is InventoryEvidenceVisibility.INVENTORY
        and item.validation_split is InventoryValidationSplit.HELD_OUT
        and item.occupied_slots is not None
        and 0 < item.occupied_slots < INVENTORY_CAPACITY
        and not item.selected_item_visible
        and not item.drag_visible
        and not item.quantity_text_visible
        for item in approved
    ):
        gaps.append("no reviewed ordinary held-out partial inventory evidence")
    if not any(
        item.visibility is InventoryEvidenceVisibility.INVENTORY
        and item.validation_split is InventoryValidationSplit.HELD_OUT
        and item.occupied_slots == INVENTORY_CAPACITY
        and not item.geometry_source
        and not item.selected_item_visible
        and not item.drag_visible
        and not item.quantity_text_visible
        for item in approved
    ):
        gaps.append("no reviewed ordinary held-out full inventory evidence")
    if not any(
        item.visibility is InventoryEvidenceVisibility.WRONG_TAB
        and item.validation_split is InventoryValidationSplit.NEGATIVE
        for item in approved
    ):
        gaps.append("no reviewed wrong-tab negative evidence")
    if not any(
        item.visibility is InventoryEvidenceVisibility.INVENTORY
        and item.validation_split is InventoryValidationSplit.ADVERSARIAL
        and item.drag_visible
        for item in approved
    ):
        gaps.append("no reviewer-confirmed held/drag evidence")
    if not any(
        item.visibility is InventoryEvidenceVisibility.INVENTORY
        and item.validation_split is InventoryValidationSplit.ADVERSARIAL
        and item.quantity_text_visible
        for item in approved
    ):
        gaps.append("no reviewed quantity-text adversarial evidence")
    positive_inventory = tuple(
        item
        for item in approved
        if item.visibility is InventoryEvidenceVisibility.INVENTORY
        and item.occupied_slots not in (None, 0)
        and not item.selected_item_visible
        and not item.drag_visible
        and not item.quantity_text_visible
    )
    tags = {tag for item in positive_inventory for tag in item.artwork_tags}
    if "wide-sprite" not in tags:
        gaps.append("no reviewed wide-sprite inventory evidence")
    positive_art_evidence = {
        (
            artifact_hash_by_key[(item.session_id, item.capture_id)],
            frozenset(item.artwork_tags),
        )
        for item in positive_inventory
    }
    positive_hashes = {artifact_hash for artifact_hash, _ in positive_art_evidence}
    positive_art_sets = {artwork_tags for _, artwork_tags in positive_art_evidence}
    if len(positive_hashes) < 3 or len(positive_art_sets) < 3:
        gaps.append("insufficient byte-distinct varied-art partial/full evidence")
    return gaps


def _is_git_sha(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _publish_sanitized_fixture(
    output_directory: Path,
    candidate: CandidateInventoryProfile,
    detector: InventoryDetector,
    fixture_cases: Sequence[Mapping[str, object]],
    replay_directory: Path,
    generator_head_sha: str,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=False)
    frames_directory = output_directory / "frames"
    frames_directory.mkdir(exist_ok=False)
    published_cases: list[dict[str, object]] = []
    for case in fixture_cases:
        frame_region = case["frame_region"]
        if not isinstance(frame_region, Mapping):
            raise InventoryReviewGateError("fixture frame region is malformed")
        name = _text_value(frame_region.get("path"), "fixture frame path")
        source = replay_directory / name
        target = frames_directory / name
        payload = _read_bytes(source, "sanitized replay region")
        _write_bytes_exclusive(target, payload)
        copied = dict(case)
        copied["frame_region"] = {
            "path": f"frames/{name}",
            "sha256": _sha256(payload),
        }
        published_cases.append(copied)
    dataset_id = _sanitized_dataset_id(candidate.profile.profile_id, published_cases)
    manifest = {
        "activation_allowed": False,
        "candidate": candidate.as_dict(detector),
        "cases": published_cases,
        "dataset_id": dataset_id,
        "frame_reconstruction": {
            "fill_byte": 0,
            "height": candidate.profile.frame_height,
            "pixel_format": PixelFormat.BGRA8888.value,
            "region": list(candidate.profile.region.as_tuple()),
            "width": candidate.profile.frame_width,
        },
        "fixture_kind": "inventory-sanitized-region-replay",
        "generated": {"git_head_sha": generator_head_sha},
        "schema_version": SANITIZED_FIXTURE_SCHEMA_VERSION,
        "warning": "Safety regression fixture; reviewer truth remains separately release-gated.",
    }
    content = _canonical_json(manifest)
    _write_text_exclusive(output_directory / _FIXTURE_MANIFEST_NAME, content)
    _write_text_exclusive(
        output_directory / f"{_FIXTURE_MANIFEST_NAME}.sha256",
        f"{_sha256(content.encode('utf-8'))}  {_FIXTURE_MANIFEST_NAME}\n",
    )


def _sanitized_dataset_id(
    profile_id: str,
    cases: Sequence[Mapping[str, object]],
) -> str:
    evidence: list[dict[str, object]] = []
    for case in cases:
        frame_region = case.get("frame_region")
        source = case.get("source")
        review_truth = case.get("review_truth")
        if (
            not isinstance(frame_region, Mapping)
            or not isinstance(source, Mapping)
            or not isinstance(review_truth, Mapping)
        ):
            raise InventoryReviewGateError("sanitized fixture identity is malformed")
        evidence.append(
            {
                "case_id": _text_value(case.get("case_id"), "fixture case id"),
                "frame_region_sha256": _text_value(
                    frame_region.get("sha256"), "fixture frame SHA-256"
                ),
                "review_truth": dict(review_truth),
                "source_payload_sha256": _text_value(
                    source.get("payload_sha256"), "source payload SHA-256"
                ),
            }
        )
    identity = _sha256(
        _canonical_json(
            {"cases": evidence, "profile_id": profile_id}
        ).encode("utf-8")
    )
    return f"inventory-live-candidate-safety-{identity[:16]}"


def _verified_relative_artifact(
    root: Path,
    metadata: Mapping[str, object],
    label: str,
) -> tuple[Path, str]:
    relative = _required_relative_path(metadata, "path")
    path = _owned_path(root, relative, label)
    expected = _required_sha256(metadata, "sha256")
    if _sha256(_read_bytes(path, label)) != expected:
        raise InventoryReviewGateError(f"{label} SHA-256 mismatch")
    return path, expected


def _owned_path(root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or "\\" in relative or any(
        part in ("", ".", "..") for part in candidate.parts
    ):
        raise InventoryReviewGateError(f"{label} path must be portable and relative")
    root_resolved = root.resolve()
    path = (root / candidate).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise InventoryReviewGateError(f"{label} path escapes its owned root") from exc
    if not path.is_file():
        raise InventoryReviewGateError(f"{label} is missing")
    return path


def _contains(outer: Region, inner: Region) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def _label_for_count(count: int) -> str:
    if count == 0:
        return "empty"
    if count == INVENTORY_CAPACITY:
        return "full"
    return "partial"


def _safe_component(value: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-." else "-" for character in value)
    safe = safe.strip("-.")
    if not safe:
        raise InventoryReviewGateError("capture identity cannot form a safe artifact name")
    return safe


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


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        decoded: object = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryReviewGateError(f"{label} is not strict UTF-8 JSON") from exc
    return _object_value(decoded, label)


def _object_value(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InventoryReviewGateError(f"{label} must be an object with string keys")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _required_object(mapping: Mapping[str, object], key: str) -> dict[str, object]:
    return _object_value(mapping.get(key), key)


def _required_list(mapping: Mapping[str, object], key: str) -> list[object]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise InventoryReviewGateError(f"{key} must be an array")
    return list(value)


def _text_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InventoryReviewGateError(f"{label} must be a non-empty string")
    return value


def _required_text(mapping: Mapping[str, object], key: str) -> str:
    return _text_value(mapping.get(key), key)


def _required_sha256(mapping: Mapping[str, object], key: str) -> str:
    value = _required_text(mapping, key)
    _require_sha256(value, key)
    return value


def _required_git_sha(mapping: Mapping[str, object], key: str) -> str:
    value = _required_text(mapping, key)
    _require_git_sha(value, key)
    return value


def _require_text(value: object, label: str) -> None:
    _text_value(value, label)


def _require_sha256(value: object, label: str) -> None:
    text = _text_value(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a lowercase hexadecimal SHA-256")


def _require_git_sha(value: object, label: str) -> None:
    text = _text_value(value, label)
    if len(text) != 40 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a lowercase 40-character git SHA")


def _required_positive_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InventoryReviewGateError(f"{key} must be a positive integer")
    return value


def _required_bool(mapping: Mapping[str, object], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise InventoryReviewGateError(f"{key} must be a boolean")
    return value


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text_value(value, label)


def _optional_positive_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InventoryReviewGateError(f"{label} must be positive or null")
    return value


def _optional_slot_count(value: object) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= INVENTORY_CAPACITY
    ):
        raise InventoryReviewGateError("occupied_slots must be null or in [0, 28]")
    return value


def _required_relative_path(mapping: Mapping[str, object], key: str) -> str:
    value = _required_text(mapping, key)
    candidate = Path(value)
    if candidate.is_absolute() or "\\" in value or any(
        part in ("", ".", "..") for part in candidate.parts
    ):
        raise InventoryReviewGateError(f"{key} must be a portable relative path")
    return candidate.as_posix()


def _region_value(value: object, label: str) -> Region:
    if not isinstance(value, list) or len(value) != 4:
        raise InventoryReviewGateError(f"{label} must contain four integers")
    if any(not _strict_int(item) for item in value):
        raise InventoryReviewGateError(f"{label} must contain strict integers")
    return Region(value[0], value[1], value[2], value[3])


def _strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_utc_timestamp(value: str, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must be an explicit UTC timestamp")


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InventoryReviewGateError(f"cannot read {label}: {path}: {exc}") from exc


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InventoryReviewGateError(f"cannot read {label}: {path}: {exc}") from exc


def _write_bytes_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise InventoryReviewGateError(f"cannot publish artifact {path}: {exc}") from exc


def _write_text_exclusive(path: Path, data: str) -> None:
    _write_bytes_exclusive(path, data.encode("utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
