"""Capture, annotate, and promote real resource frames into replay fixtures.

This module is development infrastructure.  A normal application user never
records coordinates or edits detector data.  It writes immutable frame bytes,
a review preview, and a separate annotation draft so raw capture is never
silently treated as ground truth.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final

from ..capture import Frame, PixelFormat
from ..capture.windows.bmp import write_bgra_bmp
from .resource import ResourceVisualState, observation_kind_for_state

__all__ = [
    "RESOURCE_FIXTURE_SCHEMA_VERSION",
    "ResourceFixtureAnnotation",
    "ResourceFixtureDraft",
    "ResourceFixturePaths",
    "ResourceFixtureReviewStatus",
    "add_resource_annotation",
    "build_replay_manifest",
    "load_resource_fixture_draft",
    "mark_resource_fixture_reviewed",
    "save_resource_fixture_draft",
    "write_resource_fixture_draft",
]

RESOURCE_FIXTURE_SCHEMA_VERSION: Final[int] = 1
_CASE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    import math

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _require_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _validate_case_id(value: str) -> None:
    if not _CASE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "case_id must start with an alphanumeric character and contain only "
            "letters, numbers, dot, underscore, or hyphen"
        )


def _validate_region(region: tuple[int, int, int, int]) -> None:
    if (
        not isinstance(region, tuple)
        or len(region) != 4
        or any(not _is_integer(component) for component in region)
    ):
        raise ValueError("region must be a tuple of four integers")
    x, y, width, height = region
    if x < 0 or y < 0:
        raise ValueError("region origin must be non-negative and frame-local")
    if width <= 0 or height <= 0:
        raise ValueError("region width and height must be positive")


def _validate_relative_path(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{field_name} must be a portable relative POSIX path")


class ResourceFixtureReviewStatus(StrEnum):
    """Whether human ground truth has been completed for a captured frame."""

    DRAFT = "draft"
    REVIEWED = "reviewed"


@dataclass(frozen=True, slots=True)
class ResourceFixtureAnnotation:
    """Reviewed frame-local ground truth for one resource target."""

    resource_id: str
    ore_label: str
    state: ResourceVisualState
    region: tuple[int, int, int, int]
    confidence_min: float = 0.0
    confidence_max: float = 1.0
    notes: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_string(self.resource_id, "resource_id")
        _require_nonempty_string(self.ore_label, "ore_label")
        if not isinstance(self.state, ResourceVisualState):
            raise ValueError("state must be ResourceVisualState")
        _validate_region(self.region)
        for name, value in (
            ("confidence_min", self.confidence_min),
            ("confidence_max", self.confidence_max),
        ):
            if not _is_finite_number(value) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be finite and between 0 and 1")
        if self.confidence_min > self.confidence_max:
            raise ValueError("confidence_min cannot exceed confidence_max")
        if not isinstance(self.notes, str):
            raise ValueError("annotation notes must be a string")


@dataclass(frozen=True, slots=True)
class ResourceFixtureDraft:
    """One captured frame plus separate, explicitly reviewed annotations."""

    schema_version: int
    dataset_id: str
    case_id: str
    location_id: str
    frame_path: str
    preview_path: str
    width: int
    height: int
    pixel_format: PixelFormat
    captured_monotonic_s: float
    review_status: ResourceFixtureReviewStatus = ResourceFixtureReviewStatus.DRAFT
    annotations: tuple[ResourceFixtureAnnotation, ...] = ()
    tags: tuple[str, ...] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != RESOURCE_FIXTURE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported resource fixture schema version {self.schema_version}"
            )
        _require_nonempty_string(self.dataset_id, "dataset_id")
        _require_nonempty_string(self.case_id, "case_id")
        _validate_case_id(self.case_id)
        _require_nonempty_string(self.location_id, "location_id")
        _validate_relative_path(self.frame_path, "frame_path")
        _validate_relative_path(self.preview_path, "preview_path")
        if not _is_integer(self.width) or self.width <= 0:
            raise ValueError("fixture width must be a positive integer")
        if not _is_integer(self.height) or self.height <= 0:
            raise ValueError("fixture height must be a positive integer")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("pixel_format must be PixelFormat")
        if not _is_finite_number(self.captured_monotonic_s) or self.captured_monotonic_s < 0:
            raise ValueError("captured_monotonic_s must be finite and non-negative")
        if not isinstance(self.review_status, ResourceFixtureReviewStatus):
            raise ValueError("review_status must be ResourceFixtureReviewStatus")
        if (
            not isinstance(self.annotations, tuple)
            or any(
                not isinstance(annotation, ResourceFixtureAnnotation)
                for annotation in self.annotations
            )
        ):
            raise ValueError("annotations must be a tuple of ResourceFixtureAnnotation values")
        resource_ids = [annotation.resource_id for annotation in self.annotations]
        if len(set(resource_ids)) != len(resource_ids):
            raise ValueError("annotation resource_ids must be unique within one fixture")
        for annotation in self.annotations:
            x, y, width, height = annotation.region
            if x + width > self.width or y + height > self.height:
                raise ValueError("annotation region must fit inside the captured frame")
        if (
            not isinstance(self.tags, tuple)
            or any(not isinstance(tag, str) or not tag.strip() for tag in self.tags)
            or len(set(self.tags)) != len(self.tags)
        ):
            raise ValueError("tags must be unique non-empty strings")
        if not isinstance(self.provenance, Mapping):
            raise ValueError("provenance must be a string mapping")
        clean_provenance = dict(self.provenance)
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in clean_provenance.items()
        ):
            raise ValueError("provenance must map non-empty strings to non-empty strings")
        object.__setattr__(self, "provenance", MappingProxyType(clean_provenance))
        if not isinstance(self.notes, str):
            raise ValueError("fixture notes must be a string")
        if self.review_status is ResourceFixtureReviewStatus.REVIEWED and not self.annotations:
            raise ValueError("a reviewed resource fixture must contain annotations")


@dataclass(frozen=True, slots=True)
class ResourceFixturePaths:
    """Files created by :func:`write_resource_fixture_draft`."""

    frame: Path
    preview: Path
    draft: Path


def write_resource_fixture_draft(
    frame: Frame,
    root: Path,
    *,
    dataset_id: str,
    case_id: str,
    location_id: str,
    tags: tuple[str, ...] = (),
    provenance: Mapping[str, str] | None = None,
    notes: str = "",
) -> ResourceFixturePaths:
    """Persist a capture as raw bytes, BMP preview, and unreviewed JSON draft.

    All three target paths must be absent.  On any failure, files created by
    this call are removed so a partial draft cannot be mistaken for a complete
    capture.
    """

    if not isinstance(frame, Frame):
        raise TypeError("frame must be Frame")
    root = Path(root)
    draft = ResourceFixtureDraft(
        schema_version=RESOURCE_FIXTURE_SCHEMA_VERSION,
        dataset_id=dataset_id,
        case_id=case_id,
        location_id=location_id,
        frame_path=f"frames/{case_id}.raw",
        preview_path=f"previews/{case_id}.bmp",
        width=frame.width,
        height=frame.height,
        pixel_format=frame.pixel_format,
        captured_monotonic_s=frame.captured_monotonic_s,
        tags=tags,
        provenance={} if provenance is None else provenance,
        notes=notes,
    )
    paths = ResourceFixturePaths(
        frame=root / draft.frame_path,
        preview=root / draft.preview_path,
        draft=root / "drafts" / f"{case_id}.json",
    )
    targets = (paths.frame, paths.preview, paths.draft)
    existing = [path for path in targets if path.exists()]
    if existing:
        joined = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"resource fixture target already exists: {joined}")

    created: list[Path] = []
    try:
        _exclusive_write_bytes(paths.frame, frame.payload)
        created.append(paths.frame)
        preview_payload = _frame_to_bgra(frame)
        paths.preview.parent.mkdir(parents=True, exist_ok=True)
        # The BMP writer itself does not offer exclusive creation, so write to
        # a private temporary file and install it only after encoding succeeds.
        with tempfile.NamedTemporaryFile(
            prefix=f".{case_id}.", suffix=".bmp", dir=paths.preview.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            write_bgra_bmp(
                temporary_path,
                width=frame.width,
                height=frame.height,
                bgra_payload=preview_payload,
            )
            _install_temp_exclusive(temporary_path, paths.preview)
        finally:
            temporary_path.unlink(missing_ok=True)
        created.append(paths.preview)
        _exclusive_write_bytes(paths.draft, _draft_json_bytes(draft))
        created.append(paths.draft)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return paths


def load_resource_fixture_draft(path: Path) -> ResourceFixtureDraft:
    """Load one strict resource fixture draft."""

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"resource fixture draft cannot be read: {path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"resource fixture draft is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("resource fixture draft root must be an object")
    expected_keys = {
        "schema_version",
        "dataset_id",
        "case_id",
        "location_id",
        "frame",
        "review_status",
        "annotations",
        "tags",
        "provenance",
        "notes",
    }
    if set(raw) != expected_keys:
        missing = expected_keys - set(raw)
        unknown = set(raw) - expected_keys
        raise ValueError(
            "resource fixture draft fields mismatch; "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    frame_raw = raw["frame"]
    if not isinstance(frame_raw, dict) or set(frame_raw) != {
        "path",
        "preview_path",
        "width",
        "height",
        "pixel_format",
        "captured_monotonic_s",
    }:
        raise ValueError("resource fixture frame object has invalid fields")
    annotations_raw = raw["annotations"]
    if not isinstance(annotations_raw, list):
        raise ValueError("resource fixture annotations must be an array")
    annotations = tuple(_annotation_from_json(item) for item in annotations_raw)
    tags_raw = raw["tags"]
    if not isinstance(tags_raw, list):
        raise ValueError("resource fixture tags must be an array")
    provenance_raw = raw["provenance"]
    if not isinstance(provenance_raw, dict):
        raise ValueError("resource fixture provenance must be an object")
    try:
        return ResourceFixtureDraft(
            schema_version=raw["schema_version"],
            dataset_id=raw["dataset_id"],
            case_id=raw["case_id"],
            location_id=raw["location_id"],
            frame_path=frame_raw["path"],
            preview_path=frame_raw["preview_path"],
            width=frame_raw["width"],
            height=frame_raw["height"],
            pixel_format=PixelFormat(frame_raw["pixel_format"]),
            captured_monotonic_s=frame_raw["captured_monotonic_s"],
            review_status=ResourceFixtureReviewStatus(raw["review_status"]),
            annotations=annotations,
            tags=tuple(tags_raw),
            provenance=provenance_raw,
            notes=raw["notes"],
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid resource fixture draft: {exc}") from exc


def save_resource_fixture_draft(draft: ResourceFixtureDraft, path: Path) -> None:
    """Atomically replace a draft after an explicit annotation/review edit."""

    if not isinstance(draft, ResourceFixtureDraft):
        raise TypeError("draft must be ResourceFixtureDraft")
    _atomic_replace_bytes(Path(path), _draft_json_bytes(draft))


def add_resource_annotation(
    draft: ResourceFixtureDraft,
    annotation: ResourceFixtureAnnotation,
    *,
    replace_existing: bool = False,
) -> ResourceFixtureDraft:
    """Return a draft with one annotation added or deliberately replaced."""

    if not isinstance(draft, ResourceFixtureDraft):
        raise TypeError("draft must be ResourceFixtureDraft")
    if not isinstance(annotation, ResourceFixtureAnnotation):
        raise TypeError("annotation must be ResourceFixtureAnnotation")
    existing = {item.resource_id: item for item in draft.annotations}
    if annotation.resource_id in existing and not replace_existing:
        raise ValueError(f"annotation already exists for {annotation.resource_id!r}")
    existing[annotation.resource_id] = annotation
    # Preserve original ordering for unaffected entries; append a new id.
    ordered_ids = [item.resource_id for item in draft.annotations]
    if annotation.resource_id not in ordered_ids:
        ordered_ids.append(annotation.resource_id)
    annotations = tuple(existing[resource_id] for resource_id in ordered_ids)
    return replace(
        draft,
        review_status=ResourceFixtureReviewStatus.DRAFT,
        annotations=annotations,
    )


def mark_resource_fixture_reviewed(draft: ResourceFixtureDraft) -> ResourceFixtureDraft:
    """Mark a fully annotated draft as reviewed."""

    if not draft.annotations:
        raise ValueError("cannot review a resource fixture without annotations")
    return replace(draft, review_status=ResourceFixtureReviewStatus.REVIEWED)


def build_replay_manifest(
    drafts: tuple[ResourceFixtureDraft, ...] | list[ResourceFixtureDraft],
    output_path: Path,
) -> dict[str, object]:
    """Promote reviewed drafts into merged replay-schema-v1 JSON.

    Resource state is encoded in the stable observation kind
    (``resource.available`` / ``resource.depleted`` / ``resource.uncertain``),
    while ``label`` remains the ore type.  This makes state regression-testable
    without changing the generic replay schema.
    """

    if not drafts:
        raise ValueError("at least one reviewed resource fixture is required")
    dataset_ids = {draft.dataset_id for draft in drafts}
    if len(dataset_ids) != 1:
        raise ValueError("all resource fixtures must share one dataset_id")
    case_ids = [draft.case_id for draft in drafts]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("resource fixture case_ids must be unique")
    cases: list[dict[str, object]] = []
    for draft in drafts:
        if draft.review_status is not ResourceFixtureReviewStatus.REVIEWED:
            raise ValueError(f"resource fixture {draft.case_id!r} has not been reviewed")
        expectations = [
            {
                "kind": observation_kind_for_state(annotation.state),
                "label": annotation.ore_label,
                "region": list(annotation.region),
                "confidence": {
                    "min": annotation.confidence_min,
                    "max": annotation.confidence_max,
                },
            }
            for annotation in draft.annotations
        ]
        state_tags = tuple(
            f"state:{state}"
            for state in sorted({annotation.state.value for annotation in draft.annotations})
        )
        provenance = dict(draft.provenance)
        provenance.update(
            {
                "capture_monotonic_s": str(draft.captured_monotonic_s),
                "location_id": draft.location_id,
                "resource_fixture_schema": str(draft.schema_version),
            }
        )
        cases.append(
            {
                "case_id": draft.case_id,
                "frame": {
                    "path": draft.frame_path,
                    "width": draft.width,
                    "height": draft.height,
                    "pixel_format": draft.pixel_format.value,
                },
                "expected_observations": expectations,
                "tags": list(
                    dict.fromkeys(
                        (*draft.tags, f"location:{draft.location_id}", *state_tags)
                    )
                ),
                "provenance": dict(sorted(provenance.items())),
                "notes": draft.notes,
            }
        )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "dataset_id": next(iter(dataset_ids)),
        "cases": cases,
    }
    _atomic_replace_bytes(
        Path(output_path),
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return manifest


def _annotation_from_json(value: object) -> ResourceFixtureAnnotation:
    if not isinstance(value, dict) or set(value) != {
        "resource_id",
        "ore_label",
        "state",
        "region",
        "confidence",
        "notes",
    }:
        raise ValueError("resource annotation object has invalid fields")
    region = value["region"]
    confidence = value["confidence"]
    if not isinstance(region, list) or len(region) != 4:
        raise ValueError("resource annotation region must contain four values")
    if not isinstance(confidence, dict) or set(confidence) != {"min", "max"}:
        raise ValueError("resource annotation confidence must define min and max")
    return ResourceFixtureAnnotation(
        resource_id=value["resource_id"],
        ore_label=value["ore_label"],
        state=ResourceVisualState(value["state"]),
        region=(region[0], region[1], region[2], region[3]),
        confidence_min=confidence["min"],
        confidence_max=confidence["max"],
        notes=value["notes"],
    )


def _draft_json_bytes(draft: ResourceFixtureDraft) -> bytes:
    payload = {
        "schema_version": draft.schema_version,
        "dataset_id": draft.dataset_id,
        "case_id": draft.case_id,
        "location_id": draft.location_id,
        "frame": {
            "path": draft.frame_path,
            "preview_path": draft.preview_path,
            "width": draft.width,
            "height": draft.height,
            "pixel_format": draft.pixel_format.value,
            "captured_monotonic_s": draft.captured_monotonic_s,
        },
        "review_status": draft.review_status.value,
        "annotations": [
            {
                "resource_id": annotation.resource_id,
                "ore_label": annotation.ore_label,
                "state": annotation.state.value,
                "region": list(annotation.region),
                "confidence": {
                    "min": annotation.confidence_min,
                    "max": annotation.confidence_max,
                },
                "notes": annotation.notes,
            }
            for annotation in draft.annotations
        ],
        "tags": list(draft.tags),
        "provenance": dict(sorted(draft.provenance.items())),
        "notes": draft.notes,
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _exclusive_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _install_temp_exclusive(temporary_path: Path, target: Path) -> None:
    # Copy through an exclusive destination rather than os.replace so an
    # unexpected pre-existing fixture can never be overwritten.
    _exclusive_write_bytes(target, temporary_path.read_bytes())


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _frame_to_bgra(frame: Frame) -> bytes:
    if frame.pixel_format is PixelFormat.BGRA8888:
        return frame.payload
    source = memoryview(frame.payload).cast("B")
    output = bytearray(frame.width * frame.height * 4)
    source_bpp = frame.pixel_format.bytes_per_pixel
    output_offset = 0
    for source_offset in range(0, len(source), source_bpp):
        if frame.pixel_format is PixelFormat.RGBA8888:
            red, green, blue, alpha = (
                source[source_offset],
                source[source_offset + 1],
                source[source_offset + 2],
                source[source_offset + 3],
            )
        elif frame.pixel_format is PixelFormat.RGB888:
            red, green, blue = (
                source[source_offset],
                source[source_offset + 1],
                source[source_offset + 2],
            )
            alpha = 255
        elif frame.pixel_format is PixelFormat.BGR888:
            blue, green, red = (
                source[source_offset],
                source[source_offset + 1],
                source[source_offset + 2],
            )
            alpha = 255
        elif frame.pixel_format is PixelFormat.GRAY8:
            red = green = blue = source[source_offset]
            alpha = 255
        else:  # pragma: no cover - PixelFormat enum is exhaustive
            raise ValueError(f"unsupported preview pixel format: {frame.pixel_format}")
        output[output_offset : output_offset + 4] = bytes((blue, green, red, alpha))
        output_offset += 4
    return bytes(output)
