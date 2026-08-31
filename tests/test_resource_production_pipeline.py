from __future__ import annotations

import gzip
import inspect
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from mining_automation.capture import (
    CaptureClosedError,
    CaptureSource,
    PixelFormat,
    RawFrame,
)
from mining_automation.capture.testing import FakeCaptureBackend, ManualClock
from mining_automation.contracts import FrameRef, Observation
from mining_automation.perception import (
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    DetectorContractError,
    DetectorMetadata,
    capture_detect_trust_varrock_east_iron,
)
from mining_automation.perception import production_resource_pipeline as pipeline

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
    / "frames"
    / "available-01.raw.gz"
)


@pytest.fixture(scope="module")
def reviewed_available_raw() -> RawFrame:
    with gzip.open(_FIXTURE, "rb") as source:
        payload = source.read()
    return RawFrame(
        payload=payload,
        width=1005,
        height=1078,
        pixel_format=PixelFormat.BGRA8888,
    )


def test_source_owned_cycle_accepts_reviewed_current_frame(
    reviewed_available_raw: RawFrame,
) -> None:
    backend = FakeCaptureBackend([reviewed_available_raw])
    with CaptureSource(backend, clock=ManualClock(17.5)) as source:
        result = capture_detect_trust_varrock_east_iron(source)

    assert result.accepted is True
    assert result.reason == "trusted_complete_production_ensemble"
    assert result.frame == FrameRef(
        frame_id=1,
        captured_monotonic_s=17.5,
        width=1005,
        height=1078,
    )
    assert len(result.resources) == 4
    assert len(result.actionable_targets) == 4
    assert backend.grab_calls == 1


def test_cycle_binds_trust_to_exact_frame_captured_inside_operation(
    monkeypatch: pytest.MonkeyPatch,
    reviewed_available_raw: RawFrame,
) -> None:
    observed: dict[str, object] = {}
    real_trust = pipeline.trust_varrock_east_iron_observations

    def recording_trust(
        observations: Sequence[Observation],
        *,
        current_frame: FrameRef,
    ):
        observed["current_frame"] = current_frame
        observed["observation_frames"] = tuple(item.frame for item in observations)
        return real_trust(observations, current_frame=current_frame)

    monkeypatch.setattr(
        pipeline,
        "trust_varrock_east_iron_observations",
        recording_trust,
    )
    with CaptureSource(
        FakeCaptureBackend([reviewed_available_raw]),
        clock=ManualClock(9.25),
    ) as source:
        result = pipeline.capture_detect_trust_varrock_east_iron(source)

    assert result.accepted is True
    assert observed["observation_frames"] == (observed["current_frame"],) * 4


def test_public_cycle_has_no_observation_detector_or_frame_token_injection() -> None:
    signature = inspect.signature(capture_detect_trust_varrock_east_iron)

    assert tuple(signature.parameters) == ("source",)
    assert signature.parameters["source"].kind is inspect.Parameter.POSITIONAL_ONLY

    source = CaptureSource(FakeCaptureBackend())
    with pytest.raises(TypeError):
        cast(Any, capture_detect_trust_varrock_east_iron)(
            source,
            observations=(),
            current_frame=FrameRef(1, 0.0, 4, 2),
        )


def test_duck_typed_capture_cannot_replace_owned_capture_source() -> None:
    class ArbitraryCapture:
        called = False

        def capture(self) -> None:
            self.called = True

    arbitrary = ArbitraryCapture()

    with pytest.raises(TypeError, match="source must be CaptureSource"):
        cast(Any, capture_detect_trust_varrock_east_iron)(arbitrary)

    assert arbitrary.called is False


def test_closed_capture_fails_without_a_trust_result(
    reviewed_available_raw: RawFrame,
) -> None:
    source = CaptureSource(FakeCaptureBackend([reviewed_available_raw]))

    with pytest.raises(CaptureClosedError):
        capture_detect_trust_varrock_east_iron(source)


def test_wrong_geometry_fails_closed_to_zero_resource_authority() -> None:
    raw = RawFrame(
        payload=b"\x00" * (4 * 2 * 4),
        width=4,
        height=2,
        pixel_format=PixelFormat.BGRA8888,
    )
    with CaptureSource(FakeCaptureBackend([raw]), clock=ManualClock()) as source:
        result = capture_detect_trust_varrock_east_iron(source)

    assert result.accepted is False
    assert result.reason == "frame_geometry_mismatch"
    assert result.frame is None
    assert result.resources == ()
    assert result.actionable_targets == ()


def test_internal_detector_metadata_drift_cannot_reach_trust(
    monkeypatch: pytest.MonkeyPatch,
    reviewed_available_raw: RawFrame,
) -> None:
    class ForeignDetector:
        metadata = DetectorMetadata(
            detector_id=VARROCK_EAST_IRON_DETECTOR_ID,
            version=f"{VARROCK_EAST_IRON_DETECTOR_VERSION}-foreign",
        )

        def detect(self, frame: object) -> Sequence[Observation]:
            return ()

    trust_called = False

    def forbidden_trust(*args: object, **kwargs: object) -> None:
        nonlocal trust_called
        trust_called = True
        raise AssertionError("metadata drift must stop before trust")

    monkeypatch.setattr(pipeline, "build_varrock_east_iron_detector", ForeignDetector)
    monkeypatch.setattr(
        pipeline,
        "trust_varrock_east_iron_observations",
        forbidden_trust,
    )
    with CaptureSource(
        FakeCaptureBackend([reviewed_available_raw]),
        clock=ManualClock(),
    ) as source:
        with pytest.raises(DetectorContractError, match="metadata changed"):
            pipeline.capture_detect_trust_varrock_east_iron(source)

    assert trust_called is False
