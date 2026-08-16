from __future__ import annotations

from collections.abc import Sequence

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.contracts import Observation
from mining_automation.perception.detector import (
    Detector,
    DetectorMetadata,
    run_detector,
    validate_detector,
)
from mining_automation.perception.errors import (
    CorruptFixtureError,
    DetectorContractError,
    DetectorError,
    DetectorExecutionError,
    ManifestError,
    MissingFixtureError,
    PerceptionError,
    ReplayError,
    UnsupportedManifestVersionError,
)


def _frame(*, frame_id: int = 1) -> Frame:
    return Frame.from_raw(
        RawFrame(payload=b"\x00", width=1, height=1, pixel_format=PixelFormat.GRAY8),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id - 1),
    )


def _observation(
    frame: Frame,
    *,
    kind: object = "resource",
    version: str = "1.2.3",
) -> Observation:
    return Observation(
        kind=kind,  # type: ignore[arg-type]
        frame=frame.ref,
        confidence=0.9,
        detector_version=version,
    )


class StaticDetector:
    def __init__(
        self,
        observations: Sequence[Observation] = (),
        *,
        metadata: DetectorMetadata | None = None,
    ) -> None:
        self._metadata = metadata or DetectorMetadata("test.resource", "1.2.3")
        self.observations = observations
        self.frames: list[Frame] = []

    @property
    def metadata(self) -> DetectorMetadata:
        return self._metadata

    def detect(self, frame: Frame) -> Sequence[Observation]:
        self.frames.append(frame)
        return self.observations


class NoDetectMethod:
    metadata = DetectorMetadata("test.missing", "1")


class WrongMetadataDetector:
    metadata = "test.resource@1"

    def detect(self, frame: Frame) -> Sequence[Observation]:
        return ()


class ExplodingMetadataDetector:
    @property
    def metadata(self) -> DetectorMetadata:
        raise RuntimeError("metadata store unavailable")

    def detect(self, frame: Frame) -> Sequence[Observation]:
        return ()


class ExplodingDetector(StaticDetector):
    def detect(self, frame: Frame) -> Sequence[Observation]:
        raise RuntimeError("model failed")


class InterruptingDetector(StaticDetector):
    def detect(self, frame: Frame) -> Sequence[Observation]:
        raise KeyboardInterrupt


class NonSequenceDetector(StaticDetector):
    def detect(self, frame: Frame) -> Sequence[Observation]:
        return (item for item in ())  # type: ignore[return-value]


class NonObservationDetector(StaticDetector):
    def detect(self, frame: Frame) -> Sequence[Observation]:
        return ["not an observation"]  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("detector_id", "", "detector_id must be a non-empty string"),
        ("detector_id", " \t", "detector_id must be a non-empty string"),
        ("detector_id", None, "detector_id must be a non-empty string"),
        ("version", "", "detector version must be a non-empty string"),
        ("version", " \n", "detector version must be a non-empty string"),
        ("version", 1, "detector version must be a non-empty string"),
    ],
)
def test_detector_metadata_rejects_invalid_values(
    field: str,
    value: object,
    message: str,
) -> None:
    values: dict[str, object] = {"detector_id": "test.resource", "version": "1.2.3"}
    values[field] = value

    with pytest.raises(ValueError, match=message):
        DetectorMetadata(**values)  # type: ignore[arg-type]


def test_detector_metadata_preserves_opaque_identity_and_version() -> None:
    metadata = DetectorMetadata(" resource.detector/v2 ", " build-2026.08 ")

    assert metadata.detector_id == " resource.detector/v2 "
    assert metadata.version == " build-2026.08 "


def test_detector_protocol_is_runtime_checkable() -> None:
    detector = StaticDetector()

    assert isinstance(detector, Detector)
    assert not isinstance(NoDetectMethod(), Detector)


def test_validate_detector_returns_typed_metadata_without_running_detector() -> None:
    detector = StaticDetector()

    assert validate_detector(detector) is detector.metadata
    assert detector.frames == []


def test_validate_detector_rejects_missing_protocol_members() -> None:
    with pytest.raises(DetectorContractError, match="must satisfy Detector protocol"):
        validate_detector(NoDetectMethod())


def test_validate_detector_rejects_wrong_metadata_type() -> None:
    with pytest.raises(
        DetectorContractError,
        match="metadata must be DetectorMetadata, got str",
    ):
        validate_detector(WrongMetadataDetector())


def test_validate_detector_normalizes_metadata_property_failure() -> None:
    with pytest.raises(DetectorContractError, match="metadata could not be read") as caught:
        validate_detector(ExplodingMetadataDetector())

    assert isinstance(caught.value.__cause__, RuntimeError)


def test_run_detector_returns_an_immutable_ordered_tuple() -> None:
    frame = _frame()
    expected = [_observation(frame, kind="resource"), _observation(frame, kind="inventory")]
    detector = StaticDetector(expected)

    actual = run_detector(detector, frame)

    assert actual == tuple(expected)
    assert detector.frames == [frame]


def test_run_detector_accepts_an_empty_observation_sequence() -> None:
    assert run_detector(StaticDetector(), _frame()) == ()


def test_run_detector_rejects_a_non_frame_input_before_detection() -> None:
    detector = StaticDetector()

    with pytest.raises(DetectorContractError, match="input must be Frame, got object"):
        run_detector(detector, object())  # type: ignore[arg-type]

    assert detector.frames == []


def test_run_detector_wraps_detector_exceptions_and_preserves_cause() -> None:
    frame = _frame(frame_id=7)

    with pytest.raises(DetectorExecutionError) as caught:
        run_detector(ExplodingDetector(), frame)

    assert "'test.resource' version '1.2.3' on frame 7" in str(caught.value)
    assert "RuntimeError: model failed" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_run_detector_does_not_swallow_process_control_exceptions() -> None:
    with pytest.raises(KeyboardInterrupt):
        run_detector(InterruptingDetector(), _frame())


def test_run_detector_requires_a_reusable_deterministic_sequence() -> None:
    with pytest.raises(
        DetectorContractError,
        match=r"must return a Sequence\[Observation\], got generator",
    ):
        run_detector(NonSequenceDetector(), _frame())


def test_run_detector_rejects_non_observation_output_with_index() -> None:
    with pytest.raises(
        DetectorContractError,
        match=r"output\[0\] must be Observation, got str",
    ):
        run_detector(NonObservationDetector(), _frame())


@pytest.mark.parametrize("kind", ["", " ", "\t\n", 42])
def test_run_detector_rejects_blank_or_non_string_observation_kinds(kind: object) -> None:
    frame = _frame()
    detector = StaticDetector([_observation(frame, kind=kind)])

    with pytest.raises(
        DetectorContractError,
        match=r"output\[0\] kind must be a non-empty string",
    ):
        run_detector(detector, frame)


def test_run_detector_rejects_an_observation_for_a_different_frame() -> None:
    frame = _frame(frame_id=1)
    other_frame = _frame(frame_id=2)
    detector = StaticDetector([_observation(other_frame)])

    with pytest.raises(
        DetectorContractError,
        match="references frame 2, expected input frame 1",
    ):
        run_detector(detector, frame)


def test_run_detector_rejects_an_observation_with_a_non_frame_ref() -> None:
    frame = _frame()
    observation = Observation(
        kind="resource",
        frame="not-a-frame-ref",  # type: ignore[arg-type]
        confidence=0.9,
        detector_version="1.2.3",
    )

    with pytest.raises(
        DetectorContractError,
        match=r"output\[0\] frame must be FrameRef, got str",
    ):
        run_detector(StaticDetector([observation]), frame)


def test_run_detector_rejects_an_observation_with_non_mapping_evidence() -> None:
    frame = _frame()
    observation = Observation(
        kind="resource",
        frame=frame.ref,
        confidence=0.9,
        evidence=None,  # type: ignore[arg-type]
        detector_version="1.2.3",
    )

    with pytest.raises(
        DetectorContractError,
        match=r"output\[0\] evidence must be a Mapping, got NoneType",
    ):
        run_detector(StaticDetector([observation]), frame)


@pytest.mark.parametrize("version", ["unknown", "1.2.2", "", " 1.2.3 "])
def test_run_detector_requires_exact_observation_version_provenance(version: str) -> None:
    frame = _frame()
    detector = StaticDetector([_observation(frame, version=version)])

    with pytest.raises(
        DetectorContractError,
        match="does not match metadata version '1.2.3'",
    ):
        run_detector(detector, frame)


@pytest.mark.parametrize(
    ("error", "direct_parent"),
    [
        (DetectorContractError("bad output"), DetectorError),
        (DetectorExecutionError("failed"), DetectorError),
        (ManifestError("bad manifest"), ReplayError),
        (UnsupportedManifestVersionError("schema 9"), ManifestError),
        (MissingFixtureError("missing"), ReplayError),
        (CorruptFixtureError("corrupt"), ReplayError),
    ],
)
def test_error_taxonomy_keeps_failures_under_perception(
    error: PerceptionError,
    direct_parent: type[PerceptionError],
) -> None:
    assert isinstance(error, direct_parent)
    assert isinstance(error, PerceptionError)
