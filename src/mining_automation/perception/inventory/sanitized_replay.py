"""Replay privacy-sanitized real inventory regions as full detector frames."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ...capture import Frame, PixelFormat, RawFrame
from .adapter import inventory_detection_from_observation
from .configuration import inventory_detector_from_profile
from .detector import InventoryDetection
from .geometry import (
    INVENTORY_COLUMNS,
    INVENTORY_ROWS,
    INVENTORY_SLOT_SIZE,
    InventoryGridLayout,
    Region,
)
from .localization import InventoryFrameProfile

__all__ = [
    "InventorySanitizedReplayCaseResult",
    "InventorySanitizedReplayError",
    "InventorySanitizedReplayReport",
    "replay_inventory_sanitized_fixture",
]


class InventorySanitizedReplayError(RuntimeError):
    """A sanitized fixture or its provenance contract was invalid."""


_CANDIDATE_KIND = "inventory-live-profile-candidate"
_CANDIDATE_SCHEMA_VERSION = 1
_CANDIDATE_REVIEW_STATUS = "candidate-awaiting-release-approval"


@dataclass(frozen=True, slots=True)
class InventorySanitizedReplayCaseResult:
    """Expected-versus-actual production result for one sanitized real case."""

    case_id: str
    expected: Mapping[str, object]
    actual: Mapping[str, object]

    @property
    def passed(self) -> bool:
        return dict(self.actual) == dict(self.expected)


@dataclass(frozen=True, slots=True)
class InventorySanitizedReplayReport:
    """Deterministic replay result for a privacy-sanitized real dataset."""

    dataset_id: str
    detector_id: str
    detector_version: str
    profile_id: str
    configuration_id: str
    cases: tuple[InventorySanitizedReplayCaseResult, ...]
    fixture_schema_version: int
    generator_head_sha: str | None
    fixture_manifest_sha256: str

    @property
    def passed(self) -> bool:
        return bool(self.cases) and all(item.passed for item in self.cases)

    @property
    def failed_case_ids(self) -> tuple[str, ...]:
        return tuple(item.case_id for item in self.cases if not item.passed)


def replay_inventory_sanitized_fixture(
    fixture_directory: Path,
) -> InventorySanitizedReplayReport:
    """Verify and replay a region-only real-client inventory fixture.

    The fixture is a safety regression, not an activation artifact.  The
    reviewed inventory region is placed into an otherwise zero full-size frame,
    then the ordinary profile factory and its unchanged production defaults are
    used for every case.
    """
    if not isinstance(fixture_directory, Path):
        raise TypeError("fixture_directory must be pathlib.Path")
    manifest_path = fixture_directory / "manifest.json"
    manifest_bytes = _read_bytes(manifest_path, "fixture manifest")
    expected_sidecar = f"{_sha256(manifest_bytes)}  manifest.json\n"
    sidecar = _read_text(
        fixture_directory / "manifest.json.sha256", "fixture SHA-256 sidecar"
    )
    if sidecar != expected_sidecar:
        raise InventorySanitizedReplayError("fixture manifest SHA-256 mismatch")
    manifest = _json_object(manifest_bytes, "fixture manifest")
    if manifest.get("fixture_kind") != "inventory-sanitized-region-replay":
        raise InventorySanitizedReplayError("unsupported sanitized fixture kind")
    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in (1, 2)
    ):
        raise InventorySanitizedReplayError("unsupported sanitized fixture schema")
    generator_head_sha: str | None = None
    if schema_version == 2:
        generator_head_sha = _required_git_sha(
            _required_object(manifest, "generated"), "git_head_sha"
        )
    if manifest.get("activation_allowed") is not False:
        raise InventorySanitizedReplayError("sanitized fixture cannot allow activation")

    candidate = _required_object(manifest, "candidate")
    if candidate.get("activation_allowed") is not False:
        raise InventorySanitizedReplayError("candidate fixture cannot allow activation")
    if candidate.get("candidate_kind") != _CANDIDATE_KIND:
        raise InventorySanitizedReplayError("unsupported inventory candidate kind")
    if (
        _required_positive_int(candidate, "candidate_schema_version")
        != _CANDIDATE_SCHEMA_VERSION
    ):
        raise InventorySanitizedReplayError("unsupported inventory candidate schema")
    if candidate.get("review_status") != _CANDIDATE_REVIEW_STATUS:
        raise InventorySanitizedReplayError(
            "sanitized candidate is not awaiting release approval"
        )
    candidate_evidence = _candidate_evidence(candidate)
    frame_metadata = _required_object(candidate, "frame")
    reconstruction = _required_object(manifest, "frame_reconstruction")
    width = _required_positive_int(reconstruction, "width")
    height = _required_positive_int(reconstruction, "height")
    if reconstruction.get("fill_byte") != 0:
        raise InventorySanitizedReplayError("sanitized reconstruction fill must be zero")
    if reconstruction.get("pixel_format") != PixelFormat.BGRA8888.value:
        raise InventorySanitizedReplayError("sanitized fixture must use BGRA8888")
    if (
        _required_positive_int(frame_metadata, "width") != width
        or _required_positive_int(frame_metadata, "height") != height
        or frame_metadata.get("pixel_format") != PixelFormat.BGRA8888.value
    ):
        raise InventorySanitizedReplayError(
            "candidate and reconstruction frame geometry differ"
        )
    region = _region_value(reconstruction.get("region"), "reconstruction region")
    if not region.fits(width, height):
        raise InventorySanitizedReplayError("reconstruction region is out of frame")

    profile_metadata = _required_object(candidate, "profile")
    profile_id = _required_text(profile_metadata, "profile_id")
    profile_region = _region_value(profile_metadata.get("region"), "profile region")
    if profile_region != region:
        raise InventorySanitizedReplayError(
            "candidate profile and reconstruction regions differ"
        )
    if (
        profile_metadata.get("columns") != INVENTORY_COLUMNS
        or profile_metadata.get("rows") != INVENTORY_ROWS
        or profile_metadata.get("slot_size") != INVENTORY_SLOT_SIZE
    ):
        raise InventorySanitizedReplayError(
            "fixture does not use the authoritative inventory grid"
        )
    layout = InventoryGridLayout(
        profile_id=profile_id,
        column_stride=_required_positive_int(profile_metadata, "column_stride"),
        row_stride=_required_positive_int(profile_metadata, "row_stride"),
    )
    if layout.region_at(region.x, region.y) != region:
        raise InventorySanitizedReplayError(
            "fixture region does not match its declared grid layout"
        )
    profile = InventoryFrameProfile(
        profile_id=profile_id,
        frame_width=width,
        frame_height=height,
        region=region,
        layout=layout,
    )

    cases_raw = _required_list(manifest, "cases")
    if not cases_raw:
        raise InventorySanitizedReplayError("sanitized fixture has no cases")
    if schema_version == 2:
        expected_dataset_id = _sanitized_dataset_id(profile_id, cases_raw)
        if _required_text(manifest, "dataset_id") != expected_dataset_id:
            raise InventorySanitizedReplayError(
                "sanitized fixture dataset identity does not match its evidence"
            )
    parsed_cases = tuple(
        _fixture_case(value, fixture_directory, region, width, height)
        for value in cases_raw
    )
    case_ids = tuple(item[0] for item in parsed_cases)
    if len(set(case_ids)) != len(case_ids):
        raise InventorySanitizedReplayError("sanitized fixture case ids are not unique")
    imported_candidate = (
        schema_version == 2
        and _candidate_derivation_mode(candidate)
        == "imported-reviewed-sanitized-fixture"
    )
    if imported_candidate:
        references = tuple(
            item
            for item in parsed_cases
            if _is_imported_candidate_reference(item, candidate_evidence)
        )
    else:
        references = tuple(
            item
            for item in parsed_cases
            if item[3].get("validation_split") == "reference"
            and item[3].get("decision") == "approved"
            and item[3].get("visibility") == "inventory-visible"
            and item[3].get("occupied_slots") == 0
        )
    if len(references) != 1:
        if imported_candidate:
            raise InventorySanitizedReplayError(
                "imported sanitized fixture requires its candidate-evidence "
                "reference to be one approved clear empty reference/held-out case"
            )
        raise InventorySanitizedReplayError(
            "sanitized fixture requires exactly one approved empty reference"
        )
    reference = _reconstructed_frame(
        references[0][1], region, width, height, frame_id=1
    )
    _validate_reference_provenance(references[0], candidate_evidence)
    reference_region_sha256 = _sha256(references[0][1])
    if candidate_evidence["reference_region_sha256"] != reference_region_sha256:
        raise InventorySanitizedReplayError(
            "candidate reference-region SHA-256 differs from the replay bytes"
        )
    expected_profile_id = _candidate_profile_id(
        width=width,
        height=height,
        region=region,
        column_stride=layout.column_stride,
        row_stride=layout.row_stride,
        reference_region_sha256=reference_region_sha256,
    )
    if profile_id != expected_profile_id:
        raise InventorySanitizedReplayError(
            "candidate profile identity is not derived from its replay geometry "
            "and reference bytes"
        )
    detector = inventory_detector_from_profile(profile, reference)

    detector_metadata = _required_object(candidate, "detector")
    if (
        detector_metadata.get("detector_id") != detector.metadata.detector_id
        or detector_metadata.get("detector_version") != detector.metadata.version
        or detector_metadata.get("configuration_id") != detector.configuration_id
        or detector_metadata.get("minimum_slot_confidence")
        != detector.minimum_slot_confidence
    ):
        raise InventorySanitizedReplayError(
            "fixture detector identity differs from unchanged production defaults"
        )

    results: list[InventorySanitizedReplayCaseResult] = []
    for frame_id, (case_id, payload, expectation, _, _) in enumerate(
        parsed_cases, start=1
    ):
        frame = _reconstructed_frame(payload, region, width, height, frame_id=frame_id)
        detection = inventory_detection_from_observation(detector.detect(frame)[0])
        results.append(
            InventorySanitizedReplayCaseResult(
                case_id=case_id,
                expected=expectation,
                actual=_detection_dict(detection),
            )
        )
    return InventorySanitizedReplayReport(
        dataset_id=_required_text(manifest, "dataset_id"),
        detector_id=detector.metadata.detector_id,
        detector_version=detector.metadata.version,
        profile_id=profile_id,
        configuration_id=detector.configuration_id,
        cases=tuple(results),
        fixture_schema_version=schema_version,
        generator_head_sha=generator_head_sha,
        fixture_manifest_sha256=_sha256(manifest_bytes),
    )


def _fixture_case(
    value: object,
    fixture_directory: Path,
    region: Region,
    width: int,
    height: int,
) -> tuple[
    str,
    bytes,
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    raw = _object_value(value, "sanitized fixture case")
    case_id = _required_text(raw, "case_id")
    artifact = _required_object(raw, "frame_region")
    relative = _required_relative_path(artifact, "path")
    path = _owned_path(fixture_directory, relative, "sanitized frame region")
    payload = _read_bytes(path, "sanitized frame region")
    if _required_sha256(artifact, "sha256") != _sha256(payload):
        raise InventorySanitizedReplayError(
            f"sanitized frame region SHA-256 mismatch: {case_id}"
        )
    if len(payload) != region.width * region.height * 4:
        raise InventorySanitizedReplayError(
            f"sanitized frame region length mismatch: {case_id}"
        )
    if width <= 0 or height <= 0:  # pragma: no cover - parsed above
        raise InventorySanitizedReplayError("invalid reconstruction geometry")
    expectation = _required_object(raw, "current_safety_expectation")
    review_truth = _required_object(raw, "review_truth")
    source = _required_object(raw, "source")
    review_session_id = _required_text(review_truth, "session_id")
    review_capture_id = _required_text(review_truth, "capture_id")
    if case_id != f"{review_session_id}/{review_capture_id}":
        raise InventorySanitizedReplayError(
            f"review truth identity differs from sanitized case id: {case_id}"
        )
    _required_sha256(review_truth, "panel_raw_sha256")
    _required_sha256(source, "payload_sha256")
    _required_sha256(source, "report_sha256")
    _required_sha256(source, "session_report_sha256")
    return case_id, payload, expectation, review_truth, source


def _candidate_evidence(candidate: Mapping[str, object]) -> dict[str, str]:
    evidence = _required_object(candidate, "evidence")
    return {
        "package_manifest_sha256": _required_sha256(
            evidence, "package_manifest_sha256"
        ),
        "reference_capture_id": _required_text(evidence, "reference_capture_id"),
        "reference_payload_sha256": _required_sha256(
            evidence, "reference_payload_sha256"
        ),
        "reference_region_sha256": _required_sha256(
            evidence, "reference_region_sha256"
        ),
        "reference_session_id": _required_text(evidence, "reference_session_id"),
        "review_record_sha256": _required_sha256(evidence, "review_record_sha256"),
    }


def _candidate_derivation_mode(candidate: Mapping[str, object]) -> str | None:
    value = candidate.get("derivation")
    if value is None:
        return None
    derivation = _object_value(value, "candidate derivation")
    mode = _required_text(derivation, "mode")
    if mode != "imported-reviewed-sanitized-fixture":
        return mode
    source = _required_object(derivation, "source_fixture")
    _required_text(source, "dataset_id")
    _required_sha256(source, "manifest_sha256")
    source_schema = _required_positive_int(source, "schema_version")
    if source_schema not in (1, 2):
        raise InventorySanitizedReplayError(
            "imported candidate source fixture has an unsupported schema"
        )
    source_generator = source.get("generator_head_sha")
    if source_schema == 2:
        _required_git_sha(source, "generator_head_sha")
    elif source_generator is not None:
        raise InventorySanitizedReplayError(
            "schema-v1 imported candidate source cannot claim a generator head"
        )
    return mode


def _is_imported_candidate_reference(
    case: tuple[
        str,
        bytes,
        dict[str, object],
        dict[str, object],
        dict[str, object],
    ],
    candidate_evidence: Mapping[str, str],
) -> bool:
    case_id, _, _, review_truth, _ = case
    session_id = review_truth.get("session_id")
    capture_id = review_truth.get("capture_id")
    return (
        session_id == candidate_evidence["reference_session_id"]
        and capture_id == candidate_evidence["reference_capture_id"]
        and case_id == f"{session_id}/{capture_id}"
        and review_truth.get("decision") == "approved"
        and review_truth.get("validation_split") in ("reference", "held-out")
        and review_truth.get("visibility") == "inventory-visible"
        and review_truth.get("occupied_slots") == 0
        and review_truth.get("operator_intent_confirmed") is True
        and review_truth.get("hover_visible") is False
        and review_truth.get("selected_item_visible") is False
        and review_truth.get("drag_visible") is False
        and review_truth.get("quantity_text_visible") is False
        and review_truth.get("geometry_source") is False
    )


def _validate_reference_provenance(
    reference: tuple[
        str,
        bytes,
        dict[str, object],
        dict[str, object],
        dict[str, object],
    ],
    candidate_evidence: Mapping[str, str],
) -> None:
    case_id, _, _, review_truth, source = reference
    session_id = _required_text(review_truth, "session_id")
    capture_id = _required_text(review_truth, "capture_id")
    if (
        session_id != candidate_evidence["reference_session_id"]
        or capture_id != candidate_evidence["reference_capture_id"]
        or case_id != f"{session_id}/{capture_id}"
    ):
        raise InventorySanitizedReplayError(
            "candidate reference identity differs from its reviewed reference case"
        )
    if (
        _required_sha256(source, "payload_sha256")
        != candidate_evidence["reference_payload_sha256"]
    ):
        raise InventorySanitizedReplayError(
            "candidate reference payload identity differs from its source provenance"
        )


def _candidate_profile_id(
    *,
    width: int,
    height: int,
    region: Region,
    column_stride: int,
    row_stride: int,
    reference_region_sha256: str,
) -> str:
    identity = _sha256(
        _canonical_json(
            {
                "frame": {
                    "height": height,
                    "pixel_format": PixelFormat.BGRA8888.value,
                    "width": width,
                },
                "geometry": [*region.as_tuple(), column_stride, row_stride],
                "reference_region_sha256": reference_region_sha256,
            }
        ).encode("utf-8")
    )
    return f"candidate-live-inventory-{identity[:16]}"


def _sanitized_dataset_id(profile_id: str, cases: list[object]) -> str:
    evidence: list[dict[str, object]] = []
    for value in cases:
        case = _object_value(value, "sanitized fixture case")
        frame_region = _required_object(case, "frame_region")
        source = _required_object(case, "source")
        review_truth = _required_object(case, "review_truth")
        evidence.append(
            {
                "case_id": _required_text(case, "case_id"),
                "frame_region_sha256": _required_sha256(frame_region, "sha256"),
                "review_truth": review_truth,
                "source_payload_sha256": _required_sha256(
                    source, "payload_sha256"
                ),
            }
        )
    identity = _sha256(
        _canonical_json(
            {"cases": evidence, "profile_id": profile_id}
        ).encode("utf-8")
    )
    return f"inventory-live-candidate-safety-{identity[:16]}"


def _reconstructed_frame(
    region_payload: bytes,
    region: Region,
    width: int,
    height: int,
    *,
    frame_id: int,
) -> Frame:
    payload = bytearray(width * height * 4)
    row_bytes = region.width * 4
    for row in range(region.height):
        source_start = row * row_bytes
        target_start = ((region.y + row) * width + region.x) * 4
        payload[target_start : target_start + row_bytes] = region_payload[
            source_start : source_start + row_bytes
        ]
    return Frame.from_raw(
        RawFrame(
            payload=bytes(payload),
            width=width,
            height=height,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
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


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value: object = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventorySanitizedReplayError(f"{label} is not strict UTF-8 JSON") from exc
    return _object_value(value, label)


def _object_value(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InventorySanitizedReplayError(
            f"{label} must be an object with string keys"
        )
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _required_object(mapping: Mapping[str, object], key: str) -> dict[str, object]:
    return _object_value(mapping.get(key), key)


def _required_list(mapping: Mapping[str, object], key: str) -> list[object]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise InventorySanitizedReplayError(f"{key} must be an array")
    return list(value)


def _required_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InventorySanitizedReplayError(f"{key} must be a non-empty string")
    return value


def _required_positive_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InventorySanitizedReplayError(f"{key} must be a positive integer")
    return value


def _required_sha256(mapping: Mapping[str, object], key: str) -> str:
    value = _required_text(mapping, key)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise InventorySanitizedReplayError(
            f"{key} must be a lowercase hexadecimal SHA-256"
        )
    return value


def _required_git_sha(mapping: Mapping[str, object], key: str) -> str:
    value = _required_text(mapping, key)
    if len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise InventorySanitizedReplayError(
            f"{key} must be a lowercase hexadecimal Git SHA"
        )
    return value


def _required_relative_path(mapping: Mapping[str, object], key: str) -> str:
    value = _required_text(mapping, key)
    candidate = Path(value)
    if candidate.is_absolute() or "\\" in value or any(
        part in ("", ".", "..") for part in candidate.parts
    ):
        raise InventorySanitizedReplayError(f"{key} must be portable and relative")
    return candidate.as_posix()


def _owned_path(root: Path, relative: str, label: str) -> Path:
    root_resolved = root.resolve()
    path = (root / Path(relative)).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise InventorySanitizedReplayError(f"{label} escapes its fixture root") from exc
    if not path.is_file():
        raise InventorySanitizedReplayError(f"{label} is missing")
    return path


def _region_value(value: object, label: str) -> Region:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(item, int) or isinstance(item, bool) for item in value)
    ):
        raise InventorySanitizedReplayError(f"{label} must contain four integers")
    return Region(value[0], value[1], value[2], value[3])


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


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InventorySanitizedReplayError(f"cannot read {label}: {path}: {exc}") from exc


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InventorySanitizedReplayError(f"cannot read {label}: {path}: {exc}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
