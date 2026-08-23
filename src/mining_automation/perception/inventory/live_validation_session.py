"""Guided, resumable real-client inventory evidence sessions.

A session coordinates the existing one-capture live-validation primitive across
an ordered set of operator-prepared RuneLite states.  It never manipulates the
client, never promotes operator labels to reviewed truth, and never treats a
successful capture as a detector pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from ...capture import MonotonicClock
from ...capture.windows import WindowsCaptureBackend
from .detector import InventoryDetector
from .live_validation import (
    InventoryValidationCase,
    InventoryValidationProvenance,
    run_inventory_live_validation,
)

__all__ = [
    "DEFAULT_INVENTORY_VALIDATION_CASES",
    "OPTIONAL_INVENTORY_VALIDATION_CASES",
    "InventoryValidationSessionError",
    "InventoryValidationSessionPaused",
    "InventoryValidationSessionRecord",
    "InventoryValidationSessionReport",
    "InventoryValidationSessionStatus",
    "load_inventory_validation_session",
    "run_inventory_validation_session",
]

SESSION_SCHEMA_VERSION: Final[int] = 1
PROFILE_REVIEW_DRAFT_SCHEMA_VERSION: Final[int] = 1
_SESSION_REPORT_NAME: Final[str] = "session-report.json"
_PROFILE_DRAFT_NAME: Final[str] = "inventory-profile-review.draft.json"
_CAPTURE_ROOT_NAME: Final[str] = "captures"
_MAX_DIRECTORY_ATTEMPTS: Final[int] = 10_000

DEFAULT_INVENTORY_VALIDATION_CASES: Final[tuple[InventoryValidationCase, ...]] = (
    InventoryValidationCase.EMPTY_REFERENCE,
    InventoryValidationCase.EMPTY_VALIDATION,
    InventoryValidationCase.PARTIAL,
    InventoryValidationCase.FULL,
    InventoryValidationCase.WRONG_TAB,
    InventoryValidationCase.OBSTRUCTED,
)
OPTIONAL_INVENTORY_VALIDATION_CASES: Final[tuple[InventoryValidationCase, ...]] = (
    InventoryValidationCase.HOVER_DRAG,
    InventoryValidationCase.QUANTITY_TEXT,
)

BackendFactory = Callable[[], WindowsCaptureBackend]
ReadyCallback = Callable[[InventoryValidationCase, int, int, Path], None]
UtcClock = Callable[[], datetime]


class InventoryValidationSessionError(RuntimeError):
    """A validation session could not preserve its evidence-safety contract."""


class InventoryValidationSessionPaused(InventoryValidationSessionError):
    """The operator paused a resumable session before all cases were captured."""

    def __init__(self, session_directory: Path) -> None:
        self.session_directory = session_directory
        super().__init__(f"inventory validation session paused: {session_directory}")


class InventoryValidationSessionStatus(StrEnum):
    """Durable state of one requested case inside a session."""

    PENDING = "pending"
    CAPTURING = "capturing"
    CAPTURED = "captured"


@dataclass(frozen=True, slots=True)
class _CaptureSummary:
    capture_id: str
    report_path: str
    report_sha256: str
    payload_sha256: str
    frame_width: int
    frame_height: int
    pixel_format: str
    reported_dpi: int | None
    window_class: str
    detector_mode: str
    detector_status: str
    detector_profile_id: str | None
    detector_configuration_id: str | None
    detector_occupied_slots: int | None
    detector_confidence: float | None
    detector_reason: str | None


@dataclass(frozen=True, slots=True)
class InventoryValidationSessionRecord:
    """Durable session state for one operator-selected, unverified case."""

    order: int
    case: InventoryValidationCase
    status: InventoryValidationSessionStatus = InventoryValidationSessionStatus.PENDING
    capture_id: str | None = None
    report_path: str | None = None
    report_sha256: str | None = None
    payload_sha256: str | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    pixel_format: str | None = None
    reported_dpi: int | None = None
    window_class: str | None = None
    detector_mode: str | None = None
    detector_status: str | None = None
    detector_profile_id: str | None = None
    detector_configuration_id: str | None = None
    detector_occupied_slots: int | None = None
    detector_confidence: float | None = None
    detector_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.order, int) or isinstance(self.order, bool) or self.order < 1:
            raise ValueError("session record order must be a positive integer")
        if not isinstance(self.case, InventoryValidationCase):
            raise TypeError("session record case must be InventoryValidationCase")
        if not isinstance(self.status, InventoryValidationSessionStatus):
            raise TypeError("session record status must be InventoryValidationSessionStatus")
        capture_fields = (
            self.capture_id,
            self.report_path,
            self.report_sha256,
            self.payload_sha256,
            self.frame_width,
            self.frame_height,
            self.pixel_format,
            self.window_class,
            self.detector_mode,
            self.detector_status,
        )
        if self.status is InventoryValidationSessionStatus.CAPTURED:
            if any(value is None for value in capture_fields):
                raise ValueError("a captured session record requires complete capture metadata")
        elif any(value is not None for value in capture_fields):
            raise ValueError("pending/capturing session records cannot publish capture metadata")

    @classmethod
    def from_summary(
        cls,
        prior: InventoryValidationSessionRecord,
        summary: _CaptureSummary,
    ) -> InventoryValidationSessionRecord:
        return cls(
            order=prior.order,
            case=prior.case,
            status=InventoryValidationSessionStatus.CAPTURED,
            capture_id=summary.capture_id,
            report_path=summary.report_path,
            report_sha256=summary.report_sha256,
            payload_sha256=summary.payload_sha256,
            frame_width=summary.frame_width,
            frame_height=summary.frame_height,
            pixel_format=summary.pixel_format,
            reported_dpi=summary.reported_dpi,
            window_class=summary.window_class,
            detector_mode=summary.detector_mode,
            detector_status=summary.detector_status,
            detector_profile_id=summary.detector_profile_id,
            detector_configuration_id=summary.detector_configuration_id,
            detector_occupied_slots=summary.detector_occupied_slots,
            detector_confidence=summary.detector_confidence,
            detector_reason=summary.detector_reason,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "capture": None
            if self.status is not InventoryValidationSessionStatus.CAPTURED
            else {
                "capture_id": self.capture_id,
                "detector": {
                    "confidence": self.detector_confidence,
                    "configuration_id": self.detector_configuration_id,
                    "mode": self.detector_mode,
                    "occupied_slots": self.detector_occupied_slots,
                    "profile_id": self.detector_profile_id,
                    "reason": self.detector_reason,
                    "status": self.detector_status,
                },
                "frame": {
                    "height": self.frame_height,
                    "payload_sha256": self.payload_sha256,
                    "pixel_format": self.pixel_format,
                    "reported_dpi": self.reported_dpi,
                    "width": self.frame_width,
                    "window_class": self.window_class,
                },
                "report_path": self.report_path,
                "report_sha256": self.report_sha256,
            },
            "operator_case": {
                "label": self.case.value,
                "truth_status": "operator-selected-unverified",
            },
            "order": self.order,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class InventoryValidationSessionReport:
    """Durable manifest and review summary for a guided capture session."""

    session_directory: Path
    session_id: str
    created_at_utc: str
    updated_at_utc: str
    provenance: InventoryValidationProvenance
    records: tuple[InventoryValidationSessionRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.session_directory, Path):
            raise TypeError("session_directory must be pathlib.Path")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if self.session_directory.name != self.session_id:
            raise ValueError("session_id must match the session directory name")
        if not isinstance(self.provenance, InventoryValidationProvenance):
            raise TypeError("provenance must be InventoryValidationProvenance")
        if not isinstance(self.records, tuple) or not self.records:
            raise ValueError("records must be a non-empty tuple")
        if any(not isinstance(item, InventoryValidationSessionRecord) for item in self.records):
            raise TypeError("records must contain InventoryValidationSessionRecord values")
        expected_orders = tuple(range(1, len(self.records) + 1))
        if tuple(item.order for item in self.records) != expected_orders:
            raise ValueError("session record order must be contiguous and start at 1")
        cases = tuple(item.case for item in self.records)
        if len(set(cases)) != len(cases):
            raise ValueError("session cases must be unique")

    @property
    def report_path(self) -> Path:
        return self.session_directory / _SESSION_REPORT_NAME

    @property
    def profile_review_draft_path(self) -> Path:
        return self.session_directory / _PROFILE_DRAFT_NAME

    @property
    def captured_records(self) -> tuple[InventoryValidationSessionRecord, ...]:
        return tuple(
            item
            for item in self.records
            if item.status is InventoryValidationSessionStatus.CAPTURED
        )

    @property
    def pending_records(self) -> tuple[InventoryValidationSessionRecord, ...]:
        return tuple(
            item
            for item in self.records
            if item.status is not InventoryValidationSessionStatus.CAPTURED
        )

    @property
    def complete(self) -> bool:
        return not self.pending_records

    @property
    def exit_code(self) -> int:
        return 1 if any(item.detector_status == "detector-error" for item in self.records) else 0

    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.pending_records:
            reasons.append(
                f"{len(self.pending_records)} requested capture case(s) remain incomplete"
            )
        captured = self.captured_records
        geometries = {
            (item.frame_width, item.frame_height)
            for item in captured
            if item.frame_width is not None and item.frame_height is not None
        }
        if len(geometries) > 1:
            reasons.append("captured cases use inconsistent frame geometry")
        pixel_formats = {item.pixel_format for item in captured if item.pixel_format is not None}
        if len(pixel_formats) > 1:
            reasons.append("captured cases use inconsistent pixel formats")
        window_classes = {item.window_class for item in captured if item.window_class is not None}
        if len(window_classes) > 1:
            reasons.append("captured cases use inconsistent window classes")
        reference = self._record_for_case(InventoryValidationCase.EMPTY_REFERENCE)
        validation = self._record_for_case(InventoryValidationCase.EMPTY_VALIDATION)
        if (
            reference is not None
            and validation is not None
            and reference.payload_sha256 is not None
            and reference.payload_sha256 == validation.payload_sha256
        ):
            reasons.append(
                "empty-reference and empty-validation captures are byte-identical; "
                "held-out evidence must be reviewed"
            )
        if any(item.detector_status == "detector-error" for item in captured):
            reasons.append("at least one configured detector evaluation failed")
        if captured and all(item.detector_mode == "capture-only" for item in captured):
            reasons.append("reviewed live detector/profile is not configured")
        reasons.append("all operator case labels and captured evidence remain unreviewed")
        return tuple(reasons)

    def as_dict(self) -> dict[str, object]:
        captured = self.captured_records
        return {
            "cases": [item.as_dict() for item in self.records],
            "created_at_utc": self.created_at_utc,
            "profile_review_draft": {
                "activation_allowed": False,
                "path": _PROFILE_DRAFT_NAME,
                "review_status": "unreviewed",
            },
            "provenance": self.provenance.as_dict(),
            "review_status": "unreviewed",
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": self.session_id,
            "session_kind": "inventory-live-validation-session",
            "summary": {
                "blocking_reasons": list(self.blocking_reasons()),
                "capture_only_cases": sum(
                    item.detector_mode == "capture-only" for item in captured
                ),
                "captured_cases": len(captured),
                "complete": self.complete,
                "detector_run_cases": sum(
                    item.detector_mode == "detector-run" for item in captured
                ),
                "pending_cases": len(self.pending_records),
                "requested_cases": len(self.records),
            },
            "updated_at_utc": self.updated_at_utc,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.as_dict(),
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ) + "\n"

    def render_text(self) -> str:
        state = "COMPLETE -- REVIEW REQUIRED" if self.complete else "PAUSED / INCOMPLETE"
        lines = [
            f"INVENTORY VALIDATION SESSION {state}",
            f"Session: {self.session_directory.resolve()}",
            f"Cases: {len(self.captured_records)}/{len(self.records)} captured",
            f"Session report: {self.report_path.resolve()}",
            f"Profile review draft: {self.profile_review_draft_path.resolve()}",
            "Operator labels are unverified; capture completion is not a detector pass.",
        ]
        if self.blocking_reasons():
            lines.append("Release-gate blockers / review items:")
            lines.extend(f"  - {reason}" for reason in self.blocking_reasons())
        return "\n".join(lines) + "\n"

    def _record_for_case(
        self, case: InventoryValidationCase
    ) -> InventoryValidationSessionRecord | None:
        return next((item for item in self.records if item.case is case), None)


def run_inventory_validation_session(
    *,
    backend_factory: BackendFactory,
    output_root: Path,
    provenance: InventoryValidationProvenance,
    cases: Sequence[InventoryValidationCase] = DEFAULT_INVENTORY_VALIDATION_CASES,
    detector: InventoryDetector | None = None,
    ready_callback: ReadyCallback | None = None,
    resume_directory: Path | None = None,
    capture_clock: MonotonicClock | None = None,
    utc_clock: UtcClock | None = None,
) -> InventoryValidationSessionReport:
    """Capture an ordered case plan with durable interruption/resume semantics."""
    if not callable(backend_factory):
        raise TypeError("backend_factory must be callable")
    if not isinstance(output_root, Path):
        raise TypeError("output_root must be pathlib.Path")
    if not isinstance(provenance, InventoryValidationProvenance):
        raise TypeError("provenance must be InventoryValidationProvenance")
    normalized_cases = _validate_cases(cases)
    callback = ready_callback or _no_op_ready

    if resume_directory is None:
        created_at = _utc_timestamp(utc_clock)
        session_directory = _allocate_session_directory(output_root, created_at)
        (session_directory / _CAPTURE_ROOT_NAME).mkdir(exist_ok=False)
        report = InventoryValidationSessionReport(
            session_directory=session_directory,
            session_id=session_directory.name,
            created_at_utc=created_at,
            updated_at_utc=created_at,
            provenance=provenance,
            records=tuple(
                InventoryValidationSessionRecord(order=index, case=case)
                for index, case in enumerate(normalized_cases, start=1)
            ),
        )
        _publish_session(report)
    else:
        report = load_inventory_validation_session(resume_directory)
        if tuple(item.case for item in report.records) != normalized_cases:
            raise InventoryValidationSessionError(
                "resume case plan differs from the durable session manifest"
            )
        if report.provenance != provenance:
            raise InventoryValidationSessionError(
                "resume provenance differs from the durable session manifest"
            )
        report = _reconcile_session(report, utc_clock)
        _publish_session(report)

    try:
        for record in report.records:
            if record.status is InventoryValidationSessionStatus.CAPTURED:
                continue
            callback(record.case, record.order, len(report.records), report.session_directory)
            report = _replace_record(
                report,
                replace(record, status=InventoryValidationSessionStatus.CAPTURING),
                utc_clock,
            )
            _publish_session(report)
            capture = run_inventory_live_validation(
                backend=backend_factory(),
                case=record.case,
                output_root=report.session_directory / _CAPTURE_ROOT_NAME,
                provenance=provenance,
                detector=detector,
                capture_clock=capture_clock,
                utc_clock=utc_clock,
            )
            summary = _load_capture_summary(
                capture.report_path,
                session_directory=report.session_directory,
                expected_case=record.case,
            )
            report = _replace_record(
                report,
                InventoryValidationSessionRecord.from_summary(record, summary),
                utc_clock,
            )
            _publish_session(report)
    except KeyboardInterrupt as exc:
        raise InventoryValidationSessionPaused(report.session_directory) from exc

    report = _reconcile_session(report, utc_clock)
    _publish_session(report)
    return report


def load_inventory_validation_session(
    session_directory: Path,
) -> InventoryValidationSessionReport:
    """Load and strictly validate one durable session manifest."""
    if not isinstance(session_directory, Path):
        raise TypeError("session_directory must be pathlib.Path")
    path = session_directory / _SESSION_REPORT_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryValidationSessionError(f"session report cannot be read: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise InventoryValidationSessionError("session report root must be an object")
    if raw.get("session_kind") != "inventory-live-validation-session":
        raise InventoryValidationSessionError("unsupported session report kind")
    if raw.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise InventoryValidationSessionError("unsupported session report schema version")
    session_id = _required_text(raw, "session_id")
    if session_id != session_directory.name:
        raise InventoryValidationSessionError("session report identity does not match its directory")
    provenance_raw = raw.get("provenance")
    if not isinstance(provenance_raw, dict):
        raise InventoryValidationSessionError("session provenance must be an object")
    notes = provenance_raw.get("notes")
    if not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
        raise InventoryValidationSessionError("session provenance notes must be strings")
    provenance = InventoryValidationProvenance(
        capture_build=_optional_text_value(provenance_raw.get("capture_build")),
        runelite_build=_optional_text_value(provenance_raw.get("runelite_build")),
        notes=tuple(notes),
    )
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise InventoryValidationSessionError("session cases must be a non-empty array")
    records = tuple(_record_from_json(item) for item in cases_raw)
    return InventoryValidationSessionReport(
        session_directory=session_directory,
        session_id=session_id,
        created_at_utc=_required_text(raw, "created_at_utc"),
        updated_at_utc=_required_text(raw, "updated_at_utc"),
        provenance=provenance,
        records=records,
    )


def _reconcile_session(
    report: InventoryValidationSessionReport,
    utc_clock: UtcClock | None,
) -> InventoryValidationSessionReport:
    capture_root = report.session_directory / _CAPTURE_ROOT_NAME
    if not capture_root.is_dir():
        raise InventoryValidationSessionError("session capture directory is missing")
    referenced = {
        item.capture_id
        for item in report.captured_records
        if item.capture_id is not None
    }
    summaries_by_case: dict[InventoryValidationCase, list[_CaptureSummary]] = {}
    partial_directories: list[Path] = []
    for child in sorted(capture_root.iterdir()):
        if not child.is_dir() or child.name in referenced:
            continue
        candidate_report = child / "report.json"
        if not candidate_report.is_file():
            partial_directories.append(child)
            continue
        summary, case = _load_unassigned_capture_summary(
            candidate_report,
            session_directory=report.session_directory,
        )
        summaries_by_case.setdefault(case, []).append(summary)
    if partial_directories:
        names = ", ".join(path.name for path in partial_directories)
        raise InventoryValidationSessionError(
            "partial/uncommitted capture evidence requires manual preservation review: " + names
        )

    updated = report
    for record in report.records:
        if record.status is InventoryValidationSessionStatus.CAPTURED:
            assert record.report_path is not None
            _load_capture_summary(
                report.session_directory / record.report_path,
                session_directory=report.session_directory,
                expected_case=record.case,
            )
            continue
        candidates = summaries_by_case.get(record.case, [])
        if len(candidates) > 1:
            raise InventoryValidationSessionError(
                f"multiple unassigned captures exist for {record.case.value!r}; "
                "refusing to choose or overwrite evidence"
            )
        if len(candidates) == 1:
            updated = _replace_record(
                updated,
                InventoryValidationSessionRecord.from_summary(record, candidates[0]),
                utc_clock,
            )
        elif record.status is InventoryValidationSessionStatus.CAPTURING:
            updated = _replace_record(
                updated,
                replace(record, status=InventoryValidationSessionStatus.PENDING),
                utc_clock,
            )
    return updated


def _load_unassigned_capture_summary(
    report_path: Path,
    *,
    session_directory: Path,
) -> tuple[_CaptureSummary, InventoryValidationCase]:
    raw = _read_capture_report(report_path)
    operator_case = raw.get("operator_case")
    if not isinstance(operator_case, dict):
        raise InventoryValidationSessionError("capture report operator_case must be an object")
    try:
        case = InventoryValidationCase(operator_case.get("label"))
    except ValueError as exc:
        raise InventoryValidationSessionError("capture report has unsupported case label") from exc
    return (
        _capture_summary_from_raw(
            raw,
            report_path=report_path,
            session_directory=session_directory,
            expected_case=case,
        ),
        case,
    )


def _load_capture_summary(
    report_path: Path,
    *,
    session_directory: Path,
    expected_case: InventoryValidationCase,
) -> _CaptureSummary:
    return _capture_summary_from_raw(
        _read_capture_report(report_path),
        report_path=report_path,
        session_directory=session_directory,
        expected_case=expected_case,
    )


def _read_capture_report(report_path: Path) -> dict[str, object]:
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryValidationSessionError(
            f"capture report cannot be read: {report_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise InventoryValidationSessionError("capture report root must be an object")
    return raw


def _capture_summary_from_raw(
    raw: dict[str, object],
    *,
    report_path: Path,
    session_directory: Path,
    expected_case: InventoryValidationCase,
) -> _CaptureSummary:
    if raw.get("report_kind") != "inventory-live-validation" or raw.get("schema_version") != 1:
        raise InventoryValidationSessionError("unsupported one-capture report format")
    if raw.get("review_status") != "unreviewed":
        raise InventoryValidationSessionError("session may only adopt unreviewed owned captures")
    operator_case = raw.get("operator_case")
    if not isinstance(operator_case, dict) or operator_case != {
        "label": expected_case.value,
        "truth_status": "operator-selected-unverified",
    }:
        raise InventoryValidationSessionError("capture case identity/truth status is invalid")
    capture_id = _required_text(raw, "capture_id")
    if capture_id != report_path.parent.name:
        raise InventoryValidationSessionError("capture id does not match its evidence directory")
    try:
        relative_report = report_path.relative_to(session_directory).as_posix()
    except ValueError as exc:
        raise InventoryValidationSessionError("capture report escapes the owned session") from exc
    artifacts = raw.get("artifacts")
    capture = raw.get("capture")
    detector = raw.get("detector")
    if not isinstance(artifacts, dict) or not isinstance(capture, dict):
        raise InventoryValidationSessionError("capture report artifacts/capture fields are invalid")
    if not isinstance(detector, dict):
        raise InventoryValidationSessionError("capture report detector field is invalid")
    raw_artifact = artifacts.get("raw")
    if not isinstance(raw_artifact, dict):
        raise InventoryValidationSessionError("capture raw artifact metadata is invalid")
    raw_name = _required_text(raw_artifact, "path")
    if Path(raw_name).is_absolute() or Path(raw_name).name != raw_name:
        raise InventoryValidationSessionError("capture raw artifact path must be local and relative")
    raw_path = report_path.parent / raw_name
    if not raw_path.is_file():
        raise InventoryValidationSessionError("capture raw artifact is missing")
    payload_sha256 = _required_text(raw_artifact, "sha256")
    if _sha256(raw_path.read_bytes()) != payload_sha256:
        raise InventoryValidationSessionError("capture raw artifact hash mismatch")
    window = capture.get("window")
    if not isinstance(window, dict):
        raise InventoryValidationSessionError("capture window metadata is invalid")
    reported_dpi = capture.get("reported_dpi")
    if reported_dpi is not None and (
        not isinstance(reported_dpi, int) or isinstance(reported_dpi, bool) or reported_dpi <= 0
    ):
        raise InventoryValidationSessionError("capture reported_dpi must be positive or null")
    confidence = detector.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, (int, float))
    ):
        raise InventoryValidationSessionError("detector confidence must be numeric or null")
    return _CaptureSummary(
        capture_id=capture_id,
        report_path=relative_report,
        report_sha256=_sha256(report_path.read_bytes()),
        payload_sha256=payload_sha256,
        frame_width=_required_int(capture, "width"),
        frame_height=_required_int(capture, "height"),
        pixel_format=_required_text(capture, "pixel_format"),
        reported_dpi=reported_dpi,
        window_class=_required_text(window, "class_name"),
        detector_mode=_required_text(detector, "mode"),
        detector_status=_required_text(detector, "status"),
        detector_profile_id=_optional_text_value(detector.get("profile_id")),
        detector_configuration_id=_optional_text_value(detector.get("configuration_id")),
        detector_occupied_slots=_optional_int(detector.get("occupied_slots")),
        detector_confidence=None if confidence is None else float(confidence),
        detector_reason=_optional_text_value(detector.get("reason")),
    )


def _record_from_json(value: object) -> InventoryValidationSessionRecord:
    if not isinstance(value, dict):
        raise InventoryValidationSessionError("session case record must be an object")
    operator_case = value.get("operator_case")
    if not isinstance(operator_case, dict):
        raise InventoryValidationSessionError("session operator_case must be an object")
    try:
        case = InventoryValidationCase(operator_case.get("label"))
        status = InventoryValidationSessionStatus(value.get("status"))
    except ValueError as exc:
        raise InventoryValidationSessionError("session case/status value is unsupported") from exc
    if operator_case.get("truth_status") != "operator-selected-unverified":
        raise InventoryValidationSessionError("session case truth status must remain unverified")
    order = _required_int(value, "order")
    capture = value.get("capture")
    if status is not InventoryValidationSessionStatus.CAPTURED:
        if capture is not None:
            raise InventoryValidationSessionError("uncaptured session record cannot contain capture")
        return InventoryValidationSessionRecord(order=order, case=case, status=status)
    if not isinstance(capture, dict):
        raise InventoryValidationSessionError("captured session record requires capture metadata")
    frame = capture.get("frame")
    detector = capture.get("detector")
    if not isinstance(frame, dict) or not isinstance(detector, dict):
        raise InventoryValidationSessionError("captured record frame/detector metadata is invalid")
    return InventoryValidationSessionRecord(
        order=order,
        case=case,
        status=status,
        capture_id=_required_text(capture, "capture_id"),
        report_path=_required_text(capture, "report_path"),
        report_sha256=_required_text(capture, "report_sha256"),
        payload_sha256=_required_text(frame, "payload_sha256"),
        frame_width=_required_int(frame, "width"),
        frame_height=_required_int(frame, "height"),
        pixel_format=_required_text(frame, "pixel_format"),
        reported_dpi=_optional_int(frame.get("reported_dpi")),
        window_class=_required_text(frame, "window_class"),
        detector_mode=_required_text(detector, "mode"),
        detector_status=_required_text(detector, "status"),
        detector_profile_id=_optional_text_value(detector.get("profile_id")),
        detector_configuration_id=_optional_text_value(detector.get("configuration_id")),
        detector_occupied_slots=_optional_int(detector.get("occupied_slots")),
        detector_confidence=_optional_float(detector.get("confidence")),
        detector_reason=_optional_text_value(detector.get("reason")),
    )


def _profile_review_draft(report: InventoryValidationSessionReport) -> str:
    captured = report.captured_records
    geometries = {
        (item.frame_width, item.frame_height, item.pixel_format)
        for item in captured
        if item.frame_width is not None
        and item.frame_height is not None
        and item.pixel_format is not None
    }
    common_geometry: dict[str, object] | None = None
    if len(geometries) == 1:
        width, height, pixel_format = next(iter(geometries))
        common_geometry = {
            "height": height,
            "pixel_format": pixel_format,
            "width": width,
        }
    evidence = {
        item.case.value: {
            "capture_id": item.capture_id,
            "payload_sha256": item.payload_sha256,
            "report_path": item.report_path,
            "truth_status": "operator-selected-unverified",
        }
        for item in captured
    }
    payload = {
        "activation_allowed": False,
        "approval": {
            "approved_at_utc": None,
            "approved_by": None,
            "status": "unreviewed",
        },
        "draft_kind": "inventory-live-profile-review",
        "draft_schema_version": PROFILE_REVIEW_DRAFT_SCHEMA_VERSION,
        "evidence": evidence,
        "frame": common_geometry,
        "inventory_profile": {
            "inventory_region": None,
            "layout": {
                "column_stride": None,
                "columns": 4,
                "row_stride": None,
                "rows": 7,
                "slot_size": 32,
            },
            "profile_id": None,
        },
        "required_review": [
            "privacy-review every BMP/raw capture before sharing or fixture promotion",
            "verify operator labels against visible inventory state",
            "review frame-local inventory origin and row/column stride",
            "keep empty-reference separate from held-out empty-validation evidence",
            "verify wrong-tab and obstructed captures fail closed with a reviewed detector",
            "approve profile/configuration identity before activation",
        ],
        "session_id": report.session_id,
        "warning": (
            "Draft only. No inventory coordinates, profile, or detector are activated by this file."
        ),
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _publish_session(report: InventoryValidationSessionReport) -> None:
    _atomic_write_text(report.report_path, report.to_json())
    _atomic_write_text(report.profile_review_draft_path, _profile_review_draft(report))


def _replace_record(
    report: InventoryValidationSessionReport,
    replacement: InventoryValidationSessionRecord,
    utc_clock: UtcClock | None,
) -> InventoryValidationSessionReport:
    records = tuple(
        replacement if item.order == replacement.order else item for item in report.records
    )
    return replace(report, records=records, updated_at_utc=_utc_timestamp(utc_clock))


def _allocate_session_directory(output_root: Path, created_at_utc: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = created_at_utc.replace("-", "").replace(":", "")
    prefix = f"{timestamp}-inventory-session"
    for attempt in range(1, _MAX_DIRECTORY_ATTEMPTS + 1):
        suffix = "" if attempt == 1 else f"-{attempt:03d}"
        candidate = output_root / f"{prefix}{suffix}"
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise InventoryValidationSessionError(
        f"could not allocate a unique inventory session under {output_root}"
    )


def _validate_cases(cases: Sequence[InventoryValidationCase]) -> tuple[InventoryValidationCase, ...]:
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes, bytearray)):
        raise TypeError("cases must be a sequence of InventoryValidationCase values")
    normalized = tuple(cases)
    if not normalized:
        raise ValueError("cases must contain at least one requested capture")
    if any(not isinstance(item, InventoryValidationCase) for item in normalized):
        raise TypeError("cases must contain InventoryValidationCase values")
    if len(set(normalized)) != len(normalized):
        raise ValueError("cases must not contain duplicates")
    return normalized


def _utc_timestamp(clock: UtcClock | None) -> str:
    value = datetime.now(UTC) if clock is None else clock()
    if not isinstance(value, datetime):
        raise TypeError("utc_clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("utc_clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required_text(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InventoryValidationSessionError(f"{key} must be a non-empty string")
    return value


def _required_int(mapping: dict[str, object], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InventoryValidationSessionError(f"{key} must be a non-negative integer")
    return value


def _optional_text_value(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InventoryValidationSessionError("optional text value must be non-empty or null")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InventoryValidationSessionError("optional integer must be non-negative or null")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InventoryValidationSessionError("optional float must be numeric or null")
    return float(value)


def _no_op_ready(
    case: InventoryValidationCase,
    order: int,
    total: int,
    session_directory: Path,
) -> None:
    del case, order, total, session_directory
