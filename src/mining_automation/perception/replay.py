"""Versioned, display-free replay fixtures for perception development.

Version 1 stores the exact raw bytes from :class:`~mining_automation.capture.Frame`.
The manifest supplies the geometry and pixel layout needed to reconstruct a
validated consumer-facing frame.  Replay deliberately is not a capture backend:
it has no platform lifecycle and cannot be mistaken for a live surface.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from os import fstat
from pathlib import Path, PurePosixPath, PureWindowsPath
from stat import S_ISREG
from types import MappingProxyType
from typing import Final, cast, overload

from ..capture import Frame, PixelFormat, RawFrame
from ..capture.errors import InvalidFrameError, InvalidTimestampError
from .errors import (
    CorruptFixtureError,
    ManifestError,
    MissingFixtureError,
    UnsupportedManifestVersionError,
)

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "ConfidenceRange",
    "ExpectedObservation",
    "FixtureCase",
    "FixtureManifest",
    "FrameFixture",
    "ReplayDataset",
    "ReplaySample",
    "load_fixture_manifest",
    "load_replay_dataset",
]

MANIFEST_SCHEMA_VERSION: Final[int] = 1
_MAX_REPLAY_PAYLOAD_BYTES: Final[int] = 512 * 1024 * 1024
_WINDOWS_INVALID_PATH_CHARACTERS: Final[frozenset[str]] = frozenset('<>:"|?*')


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and isfinite(value)


def _require_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _validate_relative_frame_path(value: str) -> None:
    """Require one portable, dataset-relative POSIX path.

    Checking both POSIX and Windows path grammars prevents a manifest authored
    on one platform from escaping its dataset directory when replayed on the
    other.  Backslashes are rejected so the same manifest names the same file
    everywhere.
    """

    if "\x00" in value or "\\" in value:
        raise ValueError("frame path must be a portable POSIX relative path")
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    raw_parts = value.split("/")
    has_nonportable_windows_component = any(
        any(ord(character) < 32 or character in _WINDOWS_INVALID_PATH_CHARACTERS for character in part)
        or part.endswith((" ", "."))
        or PureWindowsPath(part).is_reserved()
        for part in raw_parts
    )
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or any(part in {"", ".", ".."} for part in raw_parts)
        or posix_path == PurePosixPath(".")
        or has_nonportable_windows_component
    ):
        raise ValueError(
            "frame path must stay inside the manifest directory and be portable across platforms"
        )


def _validate_region(region: tuple[int, int, int, int]) -> None:
    if len(region) != 4 or any(not _is_integer(component) for component in region):
        raise ValueError("region must contain four integers")
    x, y, width, height = region
    if x < 0 or y < 0:
        raise ValueError("region origin must be non-negative and frame-local")
    if width <= 0 or height <= 0:
        raise ValueError("region width and height must be positive")


@dataclass(frozen=True, slots=True)
class ConfidenceRange:
    """Inclusive confidence bounds for one expected observation."""

    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        if self.minimum is None and self.maximum is None:
            raise ValueError("confidence range must define min, max, or both")
        for name, value in (("min", self.minimum), ("max", self.maximum)):
            if value is not None and (not _is_number(value) or not 0.0 <= value <= 1.0):
                raise ValueError(f"confidence {name} must be finite and between 0 and 1")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("confidence min cannot exceed max")

    def contains(self, confidence: float) -> bool:
        """Return whether ``confidence`` lies within the inclusive bounds."""

        if not _is_number(confidence):
            return False
        if self.minimum is not None and confidence < self.minimum:
            return False
        return self.maximum is None or confidence <= self.maximum

    def describe(self) -> str:
        """Return a concise stable description for evaluation reports."""

        lower = "0.0" if self.minimum is None else str(self.minimum)
        upper = "1.0" if self.maximum is None else str(self.maximum)
        return f"[{lower}, {upper}]"


@dataclass(frozen=True, slots=True)
class ExpectedObservation:
    """Generic expectation understood without a detector-private schema."""

    kind: str
    label: str | None = None
    region: tuple[int, int, int, int] | None = None
    confidence: ConfidenceRange | None = None

    def __post_init__(self) -> None:
        _require_nonempty_string(self.kind, "expectation kind")
        if self.label is not None:
            _require_nonempty_string(self.label, "expectation label")
        if self.region is not None:
            if not isinstance(self.region, tuple):
                raise ValueError("region must be a tuple of four integers")
            _validate_region(self.region)
        if self.confidence is not None and not isinstance(self.confidence, ConfidenceRange):
            raise ValueError("confidence must be a ConfidenceRange or None")


@dataclass(frozen=True, slots=True)
class FrameFixture:
    """Raw frame payload metadata from one manifest case."""

    path: str
    width: int
    height: int
    pixel_format: PixelFormat

    def __post_init__(self) -> None:
        _require_nonempty_string(self.path, "frame path")
        _validate_relative_frame_path(self.path)
        if not _is_integer(self.width) or self.width <= 0:
            raise ValueError("frame width must be a positive integer")
        if not _is_integer(self.height) or self.height <= 0:
            raise ValueError("frame height must be a positive integer")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ValueError("frame pixel_format must be a supported PixelFormat")
        if self.expected_payload_bytes > _MAX_REPLAY_PAYLOAD_BYTES:
            raise ValueError("declared frame geometry exceeds the replay payload guard")

    @property
    def expected_payload_bytes(self) -> int:
        return self.width * self.height * self.pixel_format.bytes_per_pixel


@dataclass(frozen=True, slots=True)
class FixtureCase:
    """One ordered replay case and its expected observations."""

    case_id: str
    frame: FrameFixture
    expected_observations: tuple[ExpectedObservation, ...]
    tags: tuple[str, ...] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_string(self.case_id, "case id")
        if not isinstance(self.frame, FrameFixture):
            raise ValueError("case frame must be a FrameFixture")
        if not isinstance(self.expected_observations, tuple) or any(
            not isinstance(expectation, ExpectedObservation)
            for expectation in self.expected_observations
        ):
            raise ValueError("expected_observations must be a tuple of expectations")
        if (
            not isinstance(self.tags, tuple)
            or any(not isinstance(tag, str) or not tag.strip() for tag in self.tags)
            or len(set(self.tags)) != len(self.tags)
        ):
            raise ValueError("tags must be unique non-empty strings")
        if not isinstance(self.provenance, Mapping):
            raise ValueError("provenance must map non-empty strings to non-empty strings")
        provenance = dict(self.provenance)
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in provenance.items()
        ):
            raise ValueError("provenance must map non-empty strings to non-empty strings")
        if not isinstance(self.notes, str):
            raise ValueError("notes must be a string")

        object.__setattr__(self, "provenance", MappingProxyType(provenance))

        for expectation in self.expected_observations:
            if expectation.region is None:
                continue
            x, y, width, height = expectation.region
            if x + width > self.frame.width or y + height > self.frame.height:
                raise ValueError("expected region must fit inside the fixture frame")


@dataclass(frozen=True, slots=True)
class FixtureManifest:
    """Parsed schema-v1 manifest with cases in canonical replay order."""

    schema_version: int
    dataset_id: str
    cases: tuple[FixtureCase, ...]

    def __post_init__(self) -> None:
        if not _is_integer(self.schema_version):
            raise ValueError("schema_version must be an integer")
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported manifest schema version {self.schema_version}")
        _require_nonempty_string(self.dataset_id, "dataset_id")
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("manifest cases must be a non-empty tuple")
        if any(not isinstance(case, FixtureCase) for case in self.cases):
            raise ValueError("manifest cases must contain FixtureCase values")
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("manifest case ids must be unique")


@dataclass(frozen=True, slots=True)
class ReplaySample:
    """A fixture case paired with its validated consumer-facing frame."""

    case: FixtureCase
    frame: Frame

    def __post_init__(self) -> None:
        if not isinstance(self.case, FixtureCase):
            raise ValueError("replay sample case must be a FixtureCase")
        if not isinstance(self.frame, Frame):
            raise ValueError("replay sample frame must be a Frame")
        if (
            self.frame.width != self.case.frame.width
            or self.frame.height != self.case.frame.height
            or self.frame.pixel_format is not self.case.frame.pixel_format
        ):
            raise ValueError("replay sample frame metadata must match its fixture case")


@dataclass(frozen=True, slots=True)
class ReplayDataset(Sequence[ReplaySample]):
    """Eagerly validated replay samples in manifest order."""

    manifest_path: Path
    manifest: FixtureManifest
    samples: tuple[ReplaySample, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_path, Path):
            raise ValueError("replay manifest_path must be a Path")
        if not isinstance(self.manifest, FixtureManifest):
            raise ValueError("replay manifest must be a FixtureManifest")
        if not isinstance(self.samples, tuple) or any(
            not isinstance(sample, ReplaySample) for sample in self.samples
        ):
            raise ValueError("replay samples must be a tuple of ReplaySample values")
        if len(self.samples) != len(self.manifest.cases):
            raise ValueError("replay samples must correspond one-to-one with manifest cases")
        for index, (sample, case) in enumerate(
            zip(self.samples, self.manifest.cases, strict=True)
        ):
            if sample.case != case:
                raise ValueError("replay sample order must match manifest case order")
            if (
                sample.frame.frame_id != index + 1
                or sample.frame.captured_monotonic_s != float(index)
            ):
                raise ValueError("replay sample identities must follow manifest order")

    @property
    def cases(self) -> tuple[ReplaySample, ...]:
        """Alias for callers that describe evaluation inputs as cases."""

        return self.samples

    def __len__(self) -> int:
        return len(self.samples)

    def __iter__(self) -> Iterator[ReplaySample]:
        return iter(self.samples)

    @overload
    def __getitem__(self, index: int) -> ReplaySample: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ReplaySample, ...]: ...

    def __getitem__(self, index: int | slice) -> ReplaySample | tuple[ReplaySample, ...]:
        return self.samples[index]


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number {value!r}")


def _as_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ManifestError(f"{context} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ManifestError(f"{context} keys must be strings")
        result[key] = item
    return result


def _as_list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ManifestError(f"{context} must be an array")
    return list(value)


def _check_keys(
    value: Mapping[str, object],
    *,
    required: set[str],
    optional: set[str] | None = None,
    context: str,
) -> None:
    optional_fields = optional if optional is not None else set()
    missing = required - value.keys()
    unknown = value.keys() - required - optional_fields
    if missing:
        raise ManifestError(f"{context} missing required fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ManifestError(f"{context} has unknown fields: {', '.join(sorted(unknown))}")


def _as_nonempty_string(value: object, context: str) -> str:
    try:
        return _require_nonempty_string(value, context)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc


def _as_positive_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ManifestError(f"{context} must be a positive integer")
    return value


def _as_confidence(value: object, context: str) -> float:
    if not _is_number(value):
        raise ManifestError(f"{context} must be finite and between 0 and 1")
    assert isinstance(value, (int, float))
    if value < 0 or value > 1:
        raise ManifestError(f"{context} must be finite and between 0 and 1")
    return float(value)


def _parse_confidence(value: object, context: str) -> ConfidenceRange:
    raw = _as_object(value, context)
    _check_keys(raw, required=set(), optional={"min", "max"}, context=context)
    if not raw:
        raise ManifestError(f"{context} must define min, max, or both")
    minimum = _as_confidence(raw["min"], f"{context}.min") if "min" in raw else None
    maximum = _as_confidence(raw["max"], f"{context}.max") if "max" in raw else None
    try:
        return ConfidenceRange(minimum=minimum, maximum=maximum)
    except ValueError as exc:
        raise ManifestError(f"{context}: {exc}") from exc


def _parse_region(value: object, context: str) -> tuple[int, int, int, int]:
    raw = _as_list(value, context)
    if len(raw) != 4 or any(not _is_integer(component) for component in raw):
        raise ManifestError(f"{context} must contain four integers")
    region = cast(tuple[int, int, int, int], tuple(raw))
    try:
        _validate_region(region)
    except ValueError as exc:
        raise ManifestError(f"{context}: {exc}") from exc
    return region


def _parse_expectation(value: object, context: str) -> ExpectedObservation:
    raw = _as_object(value, context)
    _check_keys(
        raw,
        required={"kind"},
        optional={"label", "region", "confidence"},
        context=context,
    )
    kind = _as_nonempty_string(raw["kind"], f"{context}.kind")
    label_value = raw.get("label")
    label = (
        None
        if label_value is None
        else _as_nonempty_string(label_value, f"{context}.label")
    )
    region_value = raw.get("region")
    region = None if region_value is None else _parse_region(region_value, f"{context}.region")
    confidence_value = raw.get("confidence")
    confidence = (
        None
        if confidence_value is None
        else _parse_confidence(confidence_value, f"{context}.confidence")
    )
    return ExpectedObservation(kind=kind, label=label, region=region, confidence=confidence)


def _parse_frame(value: object, context: str) -> FrameFixture:
    raw = _as_object(value, context)
    _check_keys(
        raw,
        required={"path", "width", "height", "pixel_format"},
        context=context,
    )
    path = _as_nonempty_string(raw["path"], f"{context}.path")
    width = _as_positive_int(raw["width"], f"{context}.width")
    height = _as_positive_int(raw["height"], f"{context}.height")
    pixel_format_value = _as_nonempty_string(
        raw["pixel_format"], f"{context}.pixel_format"
    )
    try:
        pixel_format = PixelFormat(pixel_format_value)
    except ValueError as exc:
        supported = ", ".join(pixel_format.value for pixel_format in PixelFormat)
        raise ManifestError(
            f"{context}.pixel_format must be one of: {supported}"
        ) from exc
    try:
        return FrameFixture(
            path=path,
            width=width,
            height=height,
            pixel_format=pixel_format,
        )
    except ValueError as exc:
        raise ManifestError(f"{context}: {exc}") from exc


def _parse_tags(value: object, context: str) -> tuple[str, ...]:
    raw = _as_list(value, context)
    tags = tuple(_as_nonempty_string(tag, f"{context}[{index}]") for index, tag in enumerate(raw))
    if len(set(tags)) != len(tags):
        raise ManifestError(f"{context} must not contain duplicates")
    return tags


def _parse_provenance(value: object, context: str) -> dict[str, str]:
    raw = _as_object(value, context)
    provenance: dict[str, str] = {}
    for key, item in raw.items():
        clean_key = _as_nonempty_string(key, f"{context} key")
        provenance[clean_key] = _as_nonempty_string(item, f"{context}.{key}")
    return provenance


def _parse_case(value: object, index: int) -> FixtureCase:
    context = f"cases[{index}]"
    raw = _as_object(value, context)
    _check_keys(
        raw,
        required={"case_id", "frame", "expected_observations"},
        optional={"tags", "provenance", "notes"},
        context=context,
    )
    case_id = _as_nonempty_string(raw["case_id"], f"{context}.case_id")
    frame = _parse_frame(raw["frame"], f"{context}.frame")
    expected_raw = _as_list(raw["expected_observations"], f"{context}.expected_observations")
    expected = tuple(
        _parse_expectation(item, f"{context}.expected_observations[{expected_index}]")
        for expected_index, item in enumerate(expected_raw)
    )
    tags = _parse_tags(raw.get("tags", []), f"{context}.tags")
    provenance = _parse_provenance(
        raw.get("provenance", {}), f"{context}.provenance"
    )
    notes_value = raw.get("notes", "")
    if not isinstance(notes_value, str):
        raise ManifestError(f"{context}.notes must be a string")
    try:
        return FixtureCase(
            case_id=case_id,
            frame=frame,
            expected_observations=expected,
            tags=tags,
            provenance=provenance,
            notes=notes_value,
        )
    except ValueError as exc:
        raise ManifestError(f"{context}: {exc}") from exc


def _parse_manifest(value: object) -> FixtureManifest:
    raw = _as_object(value, "manifest")
    _check_keys(
        raw,
        required={"schema_version", "dataset_id", "cases"},
        context="manifest",
    )
    version = raw["schema_version"]
    if not _is_integer(version):
        raise ManifestError("manifest.schema_version must be an integer")
    if version != MANIFEST_SCHEMA_VERSION:
        raise UnsupportedManifestVersionError(
            f"unsupported manifest schema version {version}; expected {MANIFEST_SCHEMA_VERSION}"
        )
    dataset_id = _as_nonempty_string(raw["dataset_id"], "manifest.dataset_id")
    cases_raw = _as_list(raw["cases"], "manifest.cases")
    if not cases_raw:
        raise ManifestError("manifest.cases must contain at least one case")
    cases = tuple(_parse_case(case, index) for index, case in enumerate(cases_raw))
    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ManifestError("manifest case ids must be unique")
    return FixtureManifest(
        schema_version=version,
        dataset_id=dataset_id,
        cases=cases,
    )


def _parse_fixture_manifest_bytes(
    manifest_bytes: bytes,
    *,
    manifest_path: Path,
) -> FixtureManifest:
    """Parse the exact bytes read for one manifest snapshot."""

    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"manifest is not valid UTF-8: {manifest_path}: {exc}") from exc
    try:
        raw: object = json.loads(
            manifest_text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ManifestError(f"manifest is not valid strict JSON: {exc}") from exc
    return _parse_manifest(raw)


def _load_fixture_manifest_snapshot(
    path: str | Path,
) -> tuple[FixtureManifest, bytes]:
    """Read, parse, and retain exactly one immutable manifest byte snapshot."""

    manifest_path = Path(path)
    try:
        manifest_bytes = manifest_path.read_bytes()
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest does not exist: {manifest_path}") from exc
    except OSError as exc:
        raise ManifestError(f"manifest cannot be read: {manifest_path}: {exc}") from exc
    return (
        _parse_fixture_manifest_bytes(
            manifest_bytes,
            manifest_path=manifest_path,
        ),
        manifest_bytes,
    )


def load_fixture_manifest(path: str | Path) -> FixtureManifest:
    """Parse and validate one schema-v1 manifest without loading payloads."""

    manifest, _manifest_bytes = _load_fixture_manifest_snapshot(path)
    return manifest


def _resolve_payload_path(manifest_path: Path, fixture: FrameFixture) -> Path:
    try:
        root = manifest_path.parent.resolve()
        candidate = (root / Path(*PurePosixPath(fixture.path).parts)).resolve()
    except (OSError, RuntimeError) as exc:
        raise CorruptFixtureError(
            f"fixture payload path cannot be resolved: {fixture.path}: {exc}"
        ) from exc
    if not candidate.is_relative_to(root):
        raise ManifestError(f"frame path escapes the manifest directory: {fixture.path}")
    return candidate


def _load_sample(manifest_path: Path, case: FixtureCase, index: int) -> ReplaySample:
    payload_path = _resolve_payload_path(manifest_path, case.frame)
    expected_size = case.frame.expected_payload_bytes
    try:
        with payload_path.open("rb") as payload_file:
            payload_stat = fstat(payload_file.fileno())
            if not S_ISREG(payload_stat.st_mode):
                raise CorruptFixtureError(
                    f"fixture {case.case_id!r} payload is not a regular file: {case.frame.path}"
                )
            if payload_stat.st_size != expected_size:
                raise CorruptFixtureError(
                    f"fixture {case.case_id!r} payload size {payload_stat.st_size} bytes does not "
                    f"match declared {case.frame.width}x{case.frame.height} "
                    f"{case.frame.pixel_format.value} ({expected_size} bytes)"
                )
            payload = payload_file.read(expected_size + 1)
    except FileNotFoundError as exc:
        raise MissingFixtureError(
            f"fixture {case.case_id!r} payload does not exist: {case.frame.path}"
        ) from exc
    except CorruptFixtureError:
        raise
    except OSError as exc:
        raise CorruptFixtureError(
            f"fixture {case.case_id!r} payload is not a regular file or cannot be read: "
            f"{case.frame.path}: {exc}"
        ) from exc
    if len(payload) != expected_size:
        raise CorruptFixtureError(
            f"fixture {case.case_id!r} payload changed while being read: "
            f"{len(payload)} != {expected_size}"
        )

    raw = RawFrame(
        payload=payload,
        width=case.frame.width,
        height=case.frame.height,
        pixel_format=case.frame.pixel_format,
    )
    try:
        frame = Frame.from_raw(
            raw,
            frame_id=index + 1,
            captured_monotonic_s=float(index),
        )
    except (InvalidFrameError, InvalidTimestampError) as exc:
        raise CorruptFixtureError(
            f"fixture {case.case_id!r} could not construct a valid frame: {exc}"
        ) from exc
    return ReplaySample(case=case, frame=frame)


def load_replay_dataset(path: str | Path) -> ReplayDataset:
    """Load and eagerly validate all fixture frames in manifest order."""

    try:
        manifest_path = Path(path).resolve()
    except (OSError, RuntimeError) as exc:
        raise ManifestError(f"manifest path cannot be resolved: {path}: {exc}") from exc
    manifest = load_fixture_manifest(manifest_path)
    samples = tuple(
        _load_sample(manifest_path, case, index)
        for index, case in enumerate(manifest.cases)
    )
    return ReplayDataset(
        manifest_path=manifest_path,
        manifest=manifest,
        samples=samples,
    )
