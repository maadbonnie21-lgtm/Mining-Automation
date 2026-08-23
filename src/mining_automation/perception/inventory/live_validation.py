"""One-capture evidence and inventory-detector validation workflow.

The workflow is intentionally passive. It captures the selected RuneLite
client area once, persists owned evidence, and optionally evaluates a reviewed
inventory detector. An operator label remains unverified provenance and is
never converted into expected detector output or a pass result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

from ...capture import CaptureSource, Frame, MonotonicClock, PixelFormat
from ...capture.windows import WindowInfo, WindowsCaptureBackend
from ...capture.windows.bmp import write_bgra_bmp
from ..detector import run_detector, validate_detector
from .adapter import (
    inventory_detection_from_observation,
    inventory_state_from_observation,
)
from .classification import SlotOccupancy
from .detector import InventoryDetector
from .geometry import INVENTORY_CAPACITY

__all__ = [
    "InventoryLiveValidationError",
    "InventoryValidationCase",
    "InventoryValidationProvenance",
    "InventoryValidationReport",
    "run_inventory_live_validation",
]

REPORT_SCHEMA_VERSION: Final[int] = 1
DRAFT_SCHEMA_VERSION: Final[int] = 1
_RAW_NAME: Final[str] = "frame.bgra"
_BMP_NAME: Final[str] = "frame.bmp"
_DRAFT_NAME: Final[str] = "replay-case.draft.json"
_REPORT_NAME: Final[str] = "report.json"
_MAX_DIRECTORY_ATTEMPTS: Final[int] = 10_000


class InventoryLiveValidationError(RuntimeError):
    """The live validation workflow could not preserve its safety contract."""


class InventoryValidationCase(StrEnum):
    """Closed set of operator-selected, deliberately unverified case labels."""

    EMPTY_REFERENCE = "empty-reference"
    EMPTY_VALIDATION = "empty-validation"
    PARTIAL = "partial"
    FULL = "full"
    WRONG_TAB = "wrong-tab"
    OBSTRUCTED = "obstructed"
    HOVER_DRAG = "hover-drag"
    QUANTITY_TEXT = "quantity-text"


@dataclass(frozen=True, slots=True)
class InventoryValidationProvenance:
    """Operator-supplied build information retained with one capture."""

    capture_build: str | None = None
    runelite_build: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _optional_text("capture_build", self.capture_build)
        _optional_text("runelite_build", self.runelite_build)
        if not isinstance(self.notes, tuple):
            raise TypeError("notes must be an immutable tuple")
        for index, note in enumerate(self.notes):
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"notes[{index}] must be a non-empty string")

    def as_dict(self) -> dict[str, object]:
        return {
            "capture_build": self.capture_build,
            "notes": list(self.notes),
            "runelite_build": self.runelite_build,
        }


@dataclass(frozen=True, slots=True)
class InventoryDetectorReport:
    """Machine-readable outcome of the optional production detector run."""

    mode: str
    status: str
    detector_id: str | None = None
    detector_version: str | None = None
    configured_profile_id: str | None = None
    configured_configuration_id: str | None = None
    profile_id: str | None = None
    configuration_id: str | None = None
    label: str | None = None
    occupied_slots: int | None = None
    capacity: int | None = None
    confidence: float | None = None
    localization_confidence: float | None = None
    region: tuple[int, int, int, int] | None = None
    reason: str | None = None
    empty_slots: int | None = None
    detected_occupied_slots: int | None = None
    uncertain_slots: int | None = None
    error_type: str | None = None
    error_message: str | None = None

    @classmethod
    def capture_only(cls) -> InventoryDetectorReport:
        return cls(
            mode="capture-only",
            status="profile-not-configured",
            reason="no reviewed live inventory profile/reference detector was configured",
        )

    def as_dict(self) -> dict[str, object]:
        slot_counts: dict[str, int] | None = None
        if (
            self.empty_slots is not None
            and self.detected_occupied_slots is not None
            and self.uncertain_slots is not None
        ):
            slot_counts = {
                "empty": self.empty_slots,
                "occupied": self.detected_occupied_slots,
                "uncertain": self.uncertain_slots,
            }
        return {
            "capacity": self.capacity,
            "confidence": self.confidence,
            "configuration_id": self.configuration_id,
            "configured_configuration_id": self.configured_configuration_id,
            "configured_profile_id": self.configured_profile_id,
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "label": self.label,
            "localization_confidence": self.localization_confidence,
            "mode": self.mode,
            "occupied_slots": self.occupied_slots,
            "profile_id": self.profile_id,
            "reason": self.reason,
            "region": None if self.region is None else list(self.region),
            "slot_counts": slot_counts,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class InventoryValidationReport:
    """Complete report for one fresh owned frame and optional detector run."""

    run_directory: Path
    capture_id: str
    created_at_utc: str
    case: InventoryValidationCase
    provenance: InventoryValidationProvenance
    frame: Frame
    window_title: str
    window_class: str
    reported_dpi: int | None
    metadata_warnings: tuple[str, ...]
    payload_sha256: str
    bmp_sha256: str
    detector: InventoryDetectorReport

    @property
    def report_path(self) -> Path:
        return self.run_directory / _REPORT_NAME

    @property
    def exit_code(self) -> int:
        """Zero means evidence collection completed, never visual-case success."""
        return 1 if self.detector.status == "detector-error" else 0

    def as_dict(self) -> dict[str, object]:
        return {
            "artifacts": {
                "bmp": {"path": _BMP_NAME, "sha256": self.bmp_sha256},
                "draft": {"path": _DRAFT_NAME},
                "raw": {"path": _RAW_NAME, "sha256": self.payload_sha256},
            },
            "capture": {
                "captured_monotonic_s": self.frame.captured_monotonic_s,
                "frame_id": self.frame.frame_id,
                "height": self.frame.height,
                "metadata_warnings": list(self.metadata_warnings),
                "payload_bytes": self.frame.size_bytes,
                "payload_sha256": self.payload_sha256,
                "pixel_format": self.frame.pixel_format.value,
                "reported_dpi": self.reported_dpi,
                "width": self.frame.width,
                "window": {
                    "class_name": self.window_class,
                    "title": self.window_title,
                },
            },
            "capture_id": self.capture_id,
            "created_at_utc": self.created_at_utc,
            "detector": self.detector.as_dict(),
            "operator_case": {
                "label": self.case.value,
                "truth_status": "operator-selected-unverified",
            },
            "provenance": self.provenance.as_dict(),
            "report_kind": "inventory-live-validation",
            "review_status": "unreviewed",
            "schema_version": REPORT_SCHEMA_VERSION,
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
        detector_summary = f"{self.detector.mode} / {self.detector.status}"
        detail_lines: list[str] = []
        if self.detector.mode == "detector-run":
            detail_lines.extend(
                [
                    f"Inventory slots: {self.detector.occupied_slots!r} / "
                    f"{self.detector.capacity!r}",
                    f"Confidence: {self.detector.confidence!r}",
                    f"Profile: {self.detector.profile_id!r} "
                    f"(configured {self.detector.configured_profile_id!r})",
                    f"Configuration: {self.detector.configuration_id!r} "
                    f"(configured {self.detector.configured_configuration_id!r})",
                    f"Reason: {self.detector.reason!r}",
                ]
            )
            if self.detector.error_type is not None:
                detail_lines.append(
                    f"Detector error: {self.detector.error_type}: "
                    f"{self.detector.error_message}"
                )
        lines = [
            "CAPTURE COMPLETE -- NOT VALIDATED",
            f"Case: {self.case.value} (operator-selected, unverified)",
            f"Detector: {detector_summary}",
            *detail_lines,
            f"Evidence directory: {self.run_directory.resolve()}",
            f"JSON report: {self.report_path.resolve()}",
            "Capture completion is not a visual validation pass.",
            "CAPTURE COMPLETE -- NOT VALIDATED",
        ]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class _CapturedEvidence:
    frame: Frame
    window: WindowInfo
    reported_dpi: int | None
    warnings: tuple[str, ...]


def run_inventory_live_validation(
    *,
    backend: WindowsCaptureBackend,
    case: InventoryValidationCase,
    output_root: Path,
    provenance: InventoryValidationProvenance,
    detector: InventoryDetector | None = None,
    capture_clock: MonotonicClock | None = None,
    utc_clock: Callable[[], datetime] | None = None,
) -> InventoryValidationReport:
    """Capture exactly once, persist unique evidence, and optionally detect.

    A returned exit code of zero means the workflow completed and its evidence
    was written. It never means the operator-selected visual case was verified.
    """
    if not isinstance(backend, WindowsCaptureBackend):
        raise TypeError("backend must be WindowsCaptureBackend")
    if not isinstance(case, InventoryValidationCase):
        raise TypeError("case must be InventoryValidationCase")
    if not isinstance(output_root, Path):
        raise TypeError("output_root must be pathlib.Path")
    if not isinstance(provenance, InventoryValidationProvenance):
        raise TypeError("provenance must be InventoryValidationProvenance")
    if detector is not None and not isinstance(detector, InventoryDetector):
        raise TypeError("detector must be InventoryDetector or None")

    created_at = _utc_timestamp(utc_clock)
    run_directory = _allocate_run_directory(output_root, created_at, case)
    captured = _capture_once(backend, capture_clock)
    raw_path = run_directory / _RAW_NAME
    bmp_path = run_directory / _BMP_NAME
    if captured.frame.pixel_format is not PixelFormat.BGRA8888:
        raise InventoryLiveValidationError(
            "Windows inventory validation requires a BGRA8888 capture frame"
        )
    _write_bytes_exclusive(raw_path, captured.frame.payload)
    bmp_bytes = _encode_bmp_bytes(captured.frame, run_directory)
    _write_bytes_exclusive(bmp_path, bmp_bytes)

    detector_report = (
        InventoryDetectorReport.capture_only()
        if detector is None
        else _evaluate_detector(detector, captured.frame)
    )
    payload_sha256 = _sha256(captured.frame.payload)
    bmp_sha256 = _sha256(bmp_bytes)
    report = InventoryValidationReport(
        run_directory=run_directory,
        capture_id=run_directory.name,
        created_at_utc=created_at,
        case=case,
        provenance=provenance,
        frame=captured.frame,
        window_title=captured.window.title,
        window_class=captured.window.class_name,
        reported_dpi=captured.reported_dpi,
        metadata_warnings=captured.warnings,
        payload_sha256=payload_sha256,
        bmp_sha256=bmp_sha256,
        detector=detector_report,
    )
    _write_text_exclusive(run_directory / _DRAFT_NAME, _draft_json(report))
    _write_text_exclusive(report.report_path, report.to_json())
    return report


def _capture_once(
    backend: WindowsCaptureBackend,
    capture_clock: MonotonicClock | None,
) -> _CapturedEvidence:
    warnings: list[str] = []
    with CaptureSource(backend, clock=capture_clock) as source:
        frame = source.capture()
        window = backend.selected_window
        if window is None:
            raise InventoryLiveValidationError(
                "Windows backend did not retain selected-window metadata after capture"
            )
        try:
            reported_dpi = backend.current_dpi
        except Exception as exc:
            reported_dpi = None
            warnings.append(f"reported DPI unavailable: {type(exc).__name__}: {exc}")
        if reported_dpi is not None and (
            not isinstance(reported_dpi, int)
            or isinstance(reported_dpi, bool)
            or reported_dpi <= 0
        ):
            warnings.append(f"reported DPI was invalid and was omitted: {reported_dpi!r}")
            reported_dpi = None
    return _CapturedEvidence(
        frame=frame,
        window=window,
        reported_dpi=reported_dpi,
        warnings=tuple(warnings),
    )


def _evaluate_detector(
    detector: InventoryDetector,
    frame: Frame,
) -> InventoryDetectorReport:
    detector_id: str | None = None
    detector_version: str | None = None
    configured_profile_id: str | None = None
    configured_configuration_id: str | None = None
    try:
        metadata = validate_detector(detector)
        detector_id = metadata.detector_id
        detector_version = metadata.version
        configured_profile_id = _configured_profile_id(detector)
        configured_configuration_id = detector.configuration_id
        observations = run_detector(detector, frame, expected_metadata=metadata)
        if len(observations) != 1:
            raise InventoryLiveValidationError(
                "inventory detector must return exactly one inventory_state observation"
            )
        detection = inventory_detection_from_observation(observations[0])
        state = inventory_state_from_observation(observations[0])
        if (
            state.occupied_slots != detection.occupied_slots
            or state.capacity != INVENTORY_CAPACITY
            or state.confidence != detection.confidence
        ):
            raise InventoryLiveValidationError(
                "inventory diagnostic and InventoryState adapters disagreed"
            )
        if detection.configuration_id != configured_configuration_id:
            raise InventoryLiveValidationError(
                "inventory observation configuration identity changed during evaluation"
            )
        empty_slots = sum(slot.state is SlotOccupancy.EMPTY for slot in detection.slots)
        occupied_slots = sum(
            slot.state is SlotOccupancy.OCCUPIED for slot in detection.slots
        )
        uncertain_slots = sum(
            slot.state is SlotOccupancy.UNCERTAIN for slot in detection.slots
        )
        return InventoryDetectorReport(
            mode="detector-run",
            status="observation-recorded",
            detector_id=metadata.detector_id,
            detector_version=metadata.version,
            configured_profile_id=configured_profile_id,
            configured_configuration_id=configured_configuration_id,
            profile_id=detection.profile_id,
            configuration_id=detection.configuration_id,
            label=detection.label,
            occupied_slots=detection.occupied_slots,
            capacity=state.capacity,
            confidence=detection.confidence,
            localization_confidence=detection.localization_confidence,
            region=None if detection.region is None else detection.region.as_tuple(),
            reason=detection.reason,
            empty_slots=empty_slots,
            detected_occupied_slots=occupied_slots,
            uncertain_slots=uncertain_slots,
        )
    except Exception as exc:
        return InventoryDetectorReport(
            mode="detector-run",
            status="detector-error",
            detector_id=detector_id,
            detector_version=detector_version,
            configured_profile_id=configured_profile_id,
            configured_configuration_id=configured_configuration_id,
            reason="configured detector did not produce a trustworthy inventory observation",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def _configured_profile_id(detector: InventoryDetector) -> str | None:
    try:
        profile_id = getattr(detector.classifier, "profile_id", None)
    except Exception as exc:
        raise InventoryLiveValidationError(
            f"configured inventory profile identity could not be read: {exc}"
        ) from exc
    if profile_id is None:
        return None
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise InventoryLiveValidationError(
            "configured inventory profile identity must be a non-empty string or None"
        )
    return profile_id


def _utc_timestamp(clock: Callable[[], datetime] | None) -> str:
    value = datetime.now(UTC) if clock is None else clock()
    if not isinstance(value, datetime):
        raise TypeError("utc_clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("utc_clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _allocate_run_directory(
    output_root: Path,
    created_at_utc: str,
    case: InventoryValidationCase,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = created_at_utc.replace("-", "").replace(":", "")
    prefix = f"{timestamp}-{case.value}"
    for attempt in range(1, _MAX_DIRECTORY_ATTEMPTS + 1):
        suffix = "" if attempt == 1 else f"-{attempt:03d}"
        candidate = output_root / f"{prefix}{suffix}"
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise InventoryLiveValidationError(
        f"could not allocate a unique evidence directory under {output_root}"
    )


def _draft_json(report: InventoryValidationReport) -> str:
    payload: dict[str, object] = {
        "capture": {
            "height": report.frame.height,
            "payload_bytes": report.frame.size_bytes,
            "payload_sha256": report.payload_sha256,
            "pixel_format": report.frame.pixel_format.value,
            "raw_path": _RAW_NAME,
            "reported_dpi": report.reported_dpi,
            "width": report.frame.width,
            "window": {
                "class_name": report.window_class,
                "title": report.window_title,
            },
        },
        "created_at_utc": report.created_at_utc,
        "draft_kind": "inventory-replay-case-candidate",
        "draft_schema_version": DRAFT_SCHEMA_VERSION,
        "operator_case": {
            "label": report.case.value,
            "truth_status": "operator-selected-unverified",
        },
        "provenance": report.provenance.as_dict(),
        "review_status": "unreviewed",
        "warning": (
            "Draft only: privacy and ground truth review are required before replay promotion."
        ),
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _encode_bmp_bytes(frame: Frame, staging_parent: Path) -> bytes:
    """Use the shared encoder privately, then publish with exclusive create."""
    with TemporaryDirectory(prefix=".bmp-staging-", dir=staging_parent) as directory:
        staging_path = Path(directory) / _BMP_NAME
        write_bgra_bmp(
            staging_path,
            width=frame.width,
            height=frame.height,
            bgra_payload=frame.payload,
        )
        return staging_path.read_bytes()


def _write_bytes_exclusive(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)


def _write_text_exclusive(path: Path, data: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(data)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _optional_text(name: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be None or a non-empty string")
