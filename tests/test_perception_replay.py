from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from stat import S_IFREG
from types import MappingProxyType, SimpleNamespace

import pytest

import mining_automation.perception.replay as replay_module
from mining_automation.capture import CaptureBackend, Frame, PixelFormat, RawFrame
from mining_automation.perception.errors import (
    CorruptFixtureError,
    ManifestError,
    MissingFixtureError,
    UnsupportedManifestVersionError,
)
from mining_automation.perception.replay import (
    MANIFEST_SCHEMA_VERSION,
    ConfidenceRange,
    ExpectedObservation,
    FixtureCase,
    FixtureManifest,
    FrameFixture,
    ReplayDataset,
    ReplaySample,
    load_fixture_manifest,
    load_replay_dataset,
)


def _case(
    case_id: str = "normal-001",
    *,
    path: str | None = None,
    width: object = 2,
    height: object = 1,
    pixel_format: object = "rgb888",
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "frame": {
            "path": f"frames/{case_id}.raw" if path is None else path,
            "width": width,
            "height": height,
            "pixel_format": pixel_format,
        },
        "expected_observations": [
            {
                "kind": "resource",
                "label": "iron",
                "region": [0, 0, 1, 1],
                "confidence": {"min": 0.8, "max": 1.0},
            }
        ],
        "tags": ["normal", "synthetic"],
        "provenance": {"source": "unit-test", "issue": "6"},
        "notes": "tiny deterministic fixture",
    }


def _manifest(*cases: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": "synthetic-m2b",
        "cases": list(cases or (_case(),)),
    }


def _write_manifest(tmp_path: Path, data: object) -> Path:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    return manifest_path


def _write_payload(
    tmp_path: Path,
    case: dict[str, object],
    *,
    payload: bytes | None = None,
) -> Path:
    frame = case["frame"]
    assert isinstance(frame, dict)
    relative = frame["path"]
    assert isinstance(relative, str)
    target = tmp_path.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        width = frame["width"]
        height = frame["height"]
        pixel_format = frame["pixel_format"]
        assert isinstance(width, int) and not isinstance(width, bool)
        assert isinstance(height, int) and not isinstance(height, bool)
        assert isinstance(pixel_format, str)
        payload = bytes(
            [0x35]
            * width
            * height
            * PixelFormat(pixel_format).bytes_per_pixel
        )
    target.write_bytes(payload)
    return target


def _write_valid_dataset(tmp_path: Path, *cases: dict[str, object]) -> Path:
    selected = cases or (_case(),)
    for case in selected:
        _write_payload(tmp_path, case)
    return _write_manifest(tmp_path, _manifest(*selected))


def test_loads_one_frame_and_preserves_fixture_metadata(tmp_path: Path) -> None:
    manifest_path = _write_valid_dataset(tmp_path)

    dataset = load_replay_dataset(manifest_path)

    assert isinstance(dataset, ReplayDataset)
    assert dataset.manifest.dataset_id == "synthetic-m2b"
    assert dataset.manifest.schema_version == 1
    assert dataset.manifest_path == manifest_path.resolve()
    assert len(dataset) == 1
    assert dataset.cases == dataset.samples
    sample = dataset[0]
    assert sample.case.case_id == "normal-001"
    assert sample.case.tags == ("normal", "synthetic")
    assert dict(sample.case.provenance) == {"source": "unit-test", "issue": "6"}
    assert sample.case.notes == "tiny deterministic fixture"
    assert sample.frame.frame_id == 1
    assert sample.frame.captured_monotonic_s == 0.0
    assert (sample.frame.width, sample.frame.height) == (2, 1)
    assert sample.frame.pixel_format is PixelFormat.RGB888
    assert sample.frame.payload == b"\x35" * 6

    expectation = sample.case.expected_observations[0]
    assert expectation.kind == "resource"
    assert expectation.label == "iron"
    assert expectation.region == (0, 0, 1, 1)
    assert expectation.confidence == ConfidenceRange(0.8, 1.0)


def test_multi_frame_replay_preserves_manifest_order_and_assigns_identity(tmp_path: Path) -> None:
    later = _case("z-last", width=1, height=1, pixel_format="gray8")
    earlier = _case("a-first", width=1, height=1, pixel_format="gray8")
    manifest_path = _write_valid_dataset(tmp_path, later, earlier)

    first_load = load_replay_dataset(manifest_path)
    second_load = load_replay_dataset(manifest_path)

    assert [sample.case.case_id for sample in first_load] == ["z-last", "a-first"]
    assert [sample.frame.frame_id for sample in first_load] == [1, 2]
    assert [sample.frame.captured_monotonic_s for sample in first_load] == [0.0, 1.0]
    assert [sample.frame for sample in first_load] == [sample.frame for sample in second_load]


@pytest.mark.parametrize("pixel_format", list(PixelFormat))
def test_replay_supports_every_merged_pixel_format(
    tmp_path: Path,
    pixel_format: PixelFormat,
) -> None:
    case = _case("all-formats", width=1, height=2, pixel_format=pixel_format.value)
    manifest_path = _write_valid_dataset(tmp_path, case)

    frame = load_replay_dataset(manifest_path)[0].frame

    assert frame.pixel_format is pixel_format
    assert frame.size_bytes == 2 * pixel_format.bytes_per_pixel


def test_empty_expectations_are_valid_for_negative_cases(tmp_path: Path) -> None:
    case = _case()
    case["expected_observations"] = []
    manifest_path = _write_valid_dataset(tmp_path, case)

    assert load_replay_dataset(manifest_path)[0].case.expected_observations == ()


def test_replay_dataset_is_not_a_live_capture_backend(tmp_path: Path) -> None:
    dataset = load_replay_dataset(_write_valid_dataset(tmp_path))

    assert not isinstance(dataset, CaptureBackend)


@pytest.mark.parametrize(
    ("confidence_range", "accepted", "rejected", "description"),
    [
        (ConfidenceRange(minimum=0.5), 0.5, 0.49, "[0.5, 1.0]"),
        (ConfidenceRange(maximum=0.5), 0.5, 0.51, "[0.0, 0.5]"),
        (ConfidenceRange(0.25, 0.75), 0.75, 0.751, "[0.25, 0.75]"),
    ],
)
def test_confidence_range_is_inclusive_and_describable(
    confidence_range: ConfidenceRange,
    accepted: float,
    rejected: float,
    description: str,
) -> None:
    assert confidence_range.contains(accepted)
    assert not confidence_range.contains(rejected)
    assert not confidence_range.contains(float("nan"))
    assert confidence_range.describe() == description


def test_manifest_can_be_parsed_without_loading_payloads(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _manifest(_case()))

    manifest = load_fixture_manifest(manifest_path)

    assert isinstance(manifest, FixtureManifest)
    assert isinstance(manifest.cases[0], FixtureCase)
    assert isinstance(manifest.cases[0].frame, FrameFixture)
    assert isinstance(manifest.cases[0].expected_observations[0], ExpectedObservation)


def test_missing_manifest_is_a_typed_manifest_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="does not exist"):
        load_replay_dataset(tmp_path / "missing.json")


@pytest.mark.parametrize("contents", ["{", "[]", "null", "NaN"])
def test_invalid_or_non_object_json_is_rejected(tmp_path: Path, contents: str) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ManifestError):
        load_fixture_manifest(manifest_path)


def test_invalid_utf8_manifest_is_a_typed_manifest_error(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"\xff")

    with pytest.raises(ManifestError, match="not valid UTF-8"):
        load_fixture_manifest(manifest_path)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        '{"schema_version":1,"dataset_id":"first","dataset_id":"second","cases":[]}',
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="duplicate JSON key"):
        load_fixture_manifest(manifest_path)


@pytest.mark.parametrize("version", [0, 2, -1])
def test_unknown_schema_versions_are_rejected(tmp_path: Path, version: int) -> None:
    data = _manifest(_case())
    data["schema_version"] = version
    manifest_path = _write_manifest(tmp_path, data)

    with pytest.raises(UnsupportedManifestVersionError, match="unsupported"):
        load_fixture_manifest(manifest_path)


@pytest.mark.parametrize("version", [True, 1.0, "1", None])
def test_schema_version_must_be_an_integer(tmp_path: Path, version: object) -> None:
    data = _manifest(_case())
    data["schema_version"] = version

    with pytest.raises(ManifestError, match="schema_version must be an integer"):
        load_fixture_manifest(_write_manifest(tmp_path, data))


@pytest.mark.parametrize("dataset_id", ["", " ", None, 6])
def test_dataset_id_must_be_a_nonempty_string(tmp_path: Path, dataset_id: object) -> None:
    data = _manifest(_case())
    data["dataset_id"] = dataset_id

    with pytest.raises(ManifestError, match="dataset_id"):
        load_fixture_manifest(_write_manifest(tmp_path, data))


def test_manifest_requires_at_least_one_case(tmp_path: Path) -> None:
    data = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": "synthetic-m2b",
        "cases": [],
    }
    with pytest.raises(ManifestError, match="at least one case"):
        load_fixture_manifest(_write_manifest(tmp_path, data))


def test_manifest_rejects_missing_and_unknown_fields(tmp_path: Path) -> None:
    missing = _manifest(_case())
    del missing["dataset_id"]
    with pytest.raises(ManifestError, match="missing required fields: dataset_id"):
        load_fixture_manifest(_write_manifest(tmp_path, missing))

    unknown = _manifest(_case())
    unknown["private_detector_schema"] = {}
    with pytest.raises(ManifestError, match="unknown fields"):
        load_fixture_manifest(_write_manifest(tmp_path, unknown))


def test_case_ids_must_be_unique(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="case ids must be unique"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(_case(), _case())))


@pytest.mark.parametrize("case_id", ["", " ", None, 6])
def test_case_id_must_be_a_nonempty_string(tmp_path: Path, case_id: object) -> None:
    case = _case()
    case["case_id"] = case_id

    with pytest.raises(ManifestError, match=r"cases\[0\]\.case_id"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize("field", ["frame", "expected_observations"])
def test_case_required_fields_cannot_be_omitted(tmp_path: Path, field: str) -> None:
    case = _case()
    del case[field]

    with pytest.raises(ManifestError, match="missing required fields"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


def test_case_rejects_unknown_fields(tmp_path: Path) -> None:
    case = _case()
    case["detector_threshold"] = 0.5

    with pytest.raises(ManifestError, match="unknown fields"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize("tags", ["normal", [""], ["normal", "normal"], [1]])
def test_tags_must_be_unique_nonempty_strings(tmp_path: Path, tags: object) -> None:
    case = _case()
    case["tags"] = tags

    with pytest.raises(ManifestError, match="tags"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize(
    "provenance",
    ["synthetic", {"": "unit-test"}, {"source": ""}, {"source": 1}],
)
def test_provenance_is_a_string_mapping(tmp_path: Path, provenance: object) -> None:
    case = _case()
    case["provenance"] = provenance

    with pytest.raises(ManifestError, match="provenance"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize("notes", [None, 6, []])
def test_notes_must_be_a_string(tmp_path: Path, notes: object) -> None:
    case = _case()
    case["notes"] = notes

    with pytest.raises(ManifestError, match="notes must be a string"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize("dimension", [0, -1, True, 1.5, "1"])
@pytest.mark.parametrize("field", ["width", "height"])
def test_frame_dimensions_must_be_positive_integers(
    tmp_path: Path,
    field: str,
    dimension: object,
) -> None:
    case = _case()
    frame = case["frame"]
    assert isinstance(frame, dict)
    frame[field] = dimension

    with pytest.raises(ManifestError, match=f"{field} must be a positive integer"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize("pixel_format", ["RGB888", "png", "", None, 4])
def test_pixel_format_must_be_a_supported_wire_value(
    tmp_path: Path,
    pixel_format: object,
) -> None:
    case = _case(pixel_format=pixel_format)

    with pytest.raises(ManifestError, match="pixel_format"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute.raw",
        "../outside.raw",
        "frames/../outside.raw",
        "frames\\windows.raw",
        "C:/outside.raw",
        "C:\\outside.raw",
        "//server/share.raw",
        "frames//empty.raw",
        "frames/./dot.raw",
        "frames/con.raw",
        "frames/AUX/payload.raw",
        "frames/payload.raw:stream",
        "frames/question?.raw",
        "frames/trailing-dot.raw.",
        "frames/trailing-space.raw ",
        "frames/control-\x01.raw",
    ],
)
def test_frame_paths_must_be_portable_and_dataset_relative(tmp_path: Path, path: str) -> None:
    case = _case(path=path)

    with pytest.raises(ManifestError, match="path"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


def test_payload_symlink_cannot_escape_manifest_directory(tmp_path: Path) -> None:
    dataset_root = tmp_path / "dataset"
    payload_link = dataset_root / "frames" / "linked.raw"
    payload_link.parent.mkdir(parents=True)
    outside_payload = tmp_path / "outside.raw"
    outside_payload.write_bytes(b"\x00")
    try:
        payload_link.symlink_to(outside_payload)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")
    case = _case(path="frames/linked.raw", width=1, height=1, pixel_format="gray8")
    manifest_path = _write_manifest(dataset_root, _manifest(case))

    with pytest.raises(ManifestError, match="escapes the manifest directory"):
        load_replay_dataset(manifest_path)


def test_fixture_provenance_is_defensively_copied_and_read_only() -> None:
    source = {"source": "unit-test"}
    case = FixtureCase(
        case_id="immutable-provenance",
        frame=FrameFixture("frames/a.raw", 1, 1, PixelFormat.GRAY8),
        expected_observations=(),
        provenance=source,
    )

    source["source"] = "mutated"

    assert isinstance(case.provenance, MappingProxyType)
    assert dict(case.provenance) == {"source": "unit-test"}


def test_payload_read_is_single_handle_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingPayloadFile:
        def __init__(self) -> None:
            self.requested_sizes: list[int] = []

        def __enter__(self) -> RecordingPayloadFile:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def fileno(self) -> int:
            return 123

        def read(self, size: int) -> bytes:
            self.requested_sizes.append(size)
            return b"\x00\x01"

    payload_file = RecordingPayloadFile()
    opened_paths: list[tuple[Path, str]] = []

    def open_payload(path: Path, mode: str) -> RecordingPayloadFile:
        opened_paths.append((path, mode))
        return payload_file

    inspected_descriptors: list[int] = []

    def inspect_payload(descriptor: int) -> SimpleNamespace:
        inspected_descriptors.append(descriptor)
        return SimpleNamespace(st_mode=S_IFREG, st_size=1)

    monkeypatch.setattr(Path, "open", open_payload)
    monkeypatch.setattr(replay_module, "fstat", inspect_payload)
    case = FixtureCase(
        case_id="bounded-read",
        frame=FrameFixture("frames/a.raw", 1, 1, PixelFormat.GRAY8),
        expected_observations=(),
    )

    with pytest.raises(CorruptFixtureError, match="changed while being read"):
        replay_module._load_sample(tmp_path / "manifest.json", case, 0)

    assert opened_paths == [(tmp_path / "frames" / "a.raw", "rb")]
    assert inspected_descriptors == [123]
    assert payload_file.requested_sizes == [2]


def test_implausibly_large_frame_metadata_is_rejected_before_read(tmp_path: Path) -> None:
    case = _case(width=100_000, height=100_000, pixel_format="bgra8888")

    with pytest.raises(ManifestError, match="payload guard"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize("kind", ["", " ", None, 6])
def test_expectation_kind_must_be_a_nonempty_string(tmp_path: Path, kind: object) -> None:
    case = _case()
    expectations = case["expected_observations"]
    assert isinstance(expectations, list) and isinstance(expectations[0], dict)
    expectations[0]["kind"] = kind

    with pytest.raises(ManifestError, match="kind"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize("label", ["", " ", 6, []])
def test_expectation_label_must_be_null_or_nonempty_string(
    tmp_path: Path,
    label: object,
) -> None:
    case = _case()
    expectations = case["expected_observations"]
    assert isinstance(expectations, list) and isinstance(expectations[0], dict)
    expectations[0]["label"] = label

    with pytest.raises(ManifestError, match="label"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


def test_optional_expectation_constraints_accept_null(tmp_path: Path) -> None:
    case = _case()
    case["expected_observations"] = [
        {"kind": "bank", "label": None, "region": None, "confidence": None}
    ]
    manifest = load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))

    assert manifest.cases[0].expected_observations == (ExpectedObservation("bank"),)


@pytest.mark.parametrize(
    "region",
    [
        [],
        [0, 0, 1],
        [0, 0, 1, 1, 1],
        [0.0, 0, 1, 1],
        [True, 0, 1, 1],
        [-1, 0, 1, 1],
        [0, -1, 1, 1],
        [0, 0, 0, 1],
        [0, 0, 1, 0],
        [1, 0, 2, 1],
        [0, 0, 1, 2],
    ],
)
def test_regions_are_well_formed_frame_local_and_in_bounds(
    tmp_path: Path,
    region: object,
) -> None:
    case = _case(width=2, height=1)
    expectations = case["expected_observations"]
    assert isinstance(expectations, list) and isinstance(expectations[0], dict)
    expectations[0]["region"] = region

    with pytest.raises(ManifestError, match="region"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


@pytest.mark.parametrize(
    "confidence",
    [
        {},
        {"minimum": 0.5},
        {"min": -0.1},
        {"max": 1.1},
        {"min": True},
        {"min": "0.5"},
        {"min": 0.8, "max": 0.2},
    ],
)
def test_confidence_ranges_are_strict_and_bounded(
    tmp_path: Path,
    confidence: object,
) -> None:
    case = _case()
    expectations = case["expected_observations"]
    assert isinstance(expectations, list) and isinstance(expectations[0], dict)
    expectations[0]["confidence"] = confidence

    with pytest.raises(ManifestError, match="confidence"):
        load_fixture_manifest(_write_manifest(tmp_path, _manifest(case)))


def test_missing_payload_has_a_typed_error(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, _manifest(_case()))

    with pytest.raises(MissingFixtureError, match="normal-001"):
        load_replay_dataset(manifest_path)


def test_payload_directory_is_corrupt_not_missing(tmp_path: Path) -> None:
    case = _case(path="frames/payload.raw")
    payload_dir = tmp_path / "frames" / "payload.raw"
    payload_dir.mkdir(parents=True)
    manifest_path = _write_manifest(tmp_path, _manifest(case))

    with pytest.raises(CorruptFixtureError, match="not a regular file"):
        load_replay_dataset(manifest_path)


@pytest.mark.parametrize("payload", [b"\x00" * 5, b"\x00" * 7])
def test_payload_size_must_exactly_match_declared_shape(
    tmp_path: Path,
    payload: bytes,
) -> None:
    case = _case()
    _write_payload(tmp_path, case, payload=payload)
    manifest_path = _write_manifest(tmp_path, _manifest(case))

    with pytest.raises(CorruptFixtureError, match="payload size"):
        load_replay_dataset(manifest_path)


def test_expected_payload_bytes_uses_pixel_width() -> None:
    fixture = FrameFixture("frames/a.raw", 3, 2, PixelFormat.BGRA8888)

    assert fixture.expected_payload_bytes == 24


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ConfidenceRange(),
        lambda: ConfidenceRange(-0.1, 0.5),
        lambda: ConfidenceRange(0.8, 0.2),
        lambda: ExpectedObservation("", region=None),
        lambda: ExpectedObservation("rock", region=(-1, 0, 1, 1)),
        lambda: FrameFixture("../escape.raw", 1, 1, PixelFormat.GRAY8),
    ],
)
def test_public_fixture_value_objects_reject_invalid_direct_construction(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        factory()


def test_replay_sample_requires_frame_metadata_to_match_fixture() -> None:
    case = FixtureCase(
        "case-1",
        FrameFixture("frames/case.raw", 1, 1, PixelFormat.GRAY8),
        (),
    )
    frame = Frame.from_raw(
        RawFrame(b"\x00\x00", 2, 1, PixelFormat.GRAY8),
        frame_id=1,
        captured_monotonic_s=0.0,
    )

    with pytest.raises(ValueError, match="metadata must match"):
        ReplaySample(case, frame)


def test_replay_dataset_requires_one_sample_per_manifest_case() -> None:
    case = FixtureCase(
        "case-1",
        FrameFixture("frames/case.raw", 1, 1, PixelFormat.GRAY8),
        (),
    )
    manifest = FixtureManifest(1, "direct-test", (case,))

    with pytest.raises(ValueError, match="one-to-one"):
        ReplayDataset(Path("manifest.json"), manifest, ())


def test_replay_dataset_requires_deterministic_sample_identity() -> None:
    case = FixtureCase(
        "case-1",
        FrameFixture("frames/case.raw", 1, 1, PixelFormat.GRAY8),
        (),
    )
    frame = Frame.from_raw(
        RawFrame(b"\x00", 1, 1, PixelFormat.GRAY8),
        frame_id=2,
        captured_monotonic_s=0.0,
    )
    manifest = FixtureManifest(1, "direct-test", (case,))

    with pytest.raises(ValueError, match="identities"):
        ReplayDataset(Path("manifest.json"), manifest, (ReplaySample(case, frame),))
