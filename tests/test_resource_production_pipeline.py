from __future__ import annotations

import gzip
import inspect
from collections.abc import Callable, Sequence
from dataclasses import FrozenInstanceError
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
    ProductionResourceEvaluationResult,
    capture_detect_trust_varrock_east_iron,
    capture_evaluate_trust_varrock_east_iron,
    evaluate_varrock_east_iron_frame,
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


def test_rich_source_owned_cycle_retains_exact_frame_observations_and_trust(
    reviewed_available_raw: RawFrame,
) -> None:
    backend = FakeCaptureBackend([reviewed_available_raw])
    with CaptureSource(backend, clock=ManualClock(17.5)) as source:
        result = capture_evaluate_trust_varrock_east_iron(source)

    assert isinstance(result, ProductionResourceEvaluationResult)
    assert result.frame.ref == FrameRef(
        frame_id=1,
        captured_monotonic_s=17.5,
        width=1005,
        height=1078,
    )
    assert result.frame.payload == reviewed_available_raw.payload
    assert tuple(observation.frame for observation in result.observations) == (
        result.frame.ref,
    ) * 4
    assert result.trust.accepted is True
    assert result.trust.frame == result.frame.ref
    assert backend.grab_calls == 1

    with pytest.raises(FrozenInstanceError):
        cast(Any, result).trust = result.trust


def test_fixed_frame_evaluator_uses_owned_frame_without_another_capture(
    reviewed_available_raw: RawFrame,
) -> None:
    backend = FakeCaptureBackend([reviewed_available_raw])
    with CaptureSource(backend, clock=ManualClock(4.5)) as source:
        frame = source.capture()
        result = evaluate_varrock_east_iron_frame(frame)

    assert result.frame is frame
    assert all(observation.frame == frame.ref for observation in result.observations)
    assert result.trust.frame == frame.ref
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


@pytest.mark.parametrize(
    "operation",
    [capture_evaluate_trust_varrock_east_iron, evaluate_varrock_east_iron_frame],
)
def test_rich_operations_have_one_positional_input_and_no_policy_injection(
    operation: Callable[..., object],
    reviewed_available_raw: RawFrame,
) -> None:
    signature = inspect.signature(operation)
    expected_name = (
        "source"
        if operation is capture_evaluate_trust_varrock_east_iron
        else "frame"
    )

    assert tuple(signature.parameters) == (expected_name,)
    assert (
        signature.parameters[expected_name].kind
        is inspect.Parameter.POSITIONAL_ONLY
    )

    if operation is capture_evaluate_trust_varrock_east_iron:
        value: object = CaptureSource(FakeCaptureBackend([reviewed_available_raw]))
    else:
        with CaptureSource(
            FakeCaptureBackend([reviewed_available_raw]),
            clock=ManualClock(),
        ) as source:
            value = source.capture()
    with pytest.raises(TypeError):
        cast(Any, operation)(
            value,
            detector=object(),
            current_frame=FrameRef(1, 0.0, 1005, 1078),
            policy=object(),
        )


def test_fixed_frame_evaluator_rejects_non_frame_input() -> None:
    with pytest.raises(TypeError, match="frame must be Frame"):
        cast(Any, evaluate_varrock_east_iron_frame)(object())


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


def test_unsupported_scene_stops_after_one_capture_without_camera_recovery(
    reviewed_available_raw: RawFrame,
) -> None:
    unsupported = RawFrame(
        payload=bytes(len(reviewed_available_raw.payload)),
        width=reviewed_available_raw.width,
        height=reviewed_available_raw.height,
        pixel_format=reviewed_available_raw.pixel_format,
    )
    backend = FakeCaptureBackend([unsupported, reviewed_available_raw])

    with CaptureSource(backend, clock=ManualClock(23.0)) as source:
        result = capture_detect_trust_varrock_east_iron(source)

    # ``accepted`` means the four-observation identity/shape contract was
    # complete. It is not scene or action authority. The unsupported scene is
    # retained as explicit UNKNOWN state and cannot expose a target.
    assert result.accepted is True
    assert tuple(resource.available for resource in result.resources) == (
        None,
        None,
        None,
        None,
    )
    assert all(resource.interaction_region is None for resource in result.resources)
    assert result.actionable_targets == ()
    # A reviewed supported frame remains queued: the production boundary does
    # not retry, hunt for a camera pose, or silently recover from uncertainty.
    assert backend.grab_calls == 1


def test_rich_unsupported_scene_retains_evidence_without_retry(
    reviewed_available_raw: RawFrame,
) -> None:
    unsupported = RawFrame(
        payload=bytes(len(reviewed_available_raw.payload)),
        width=reviewed_available_raw.width,
        height=reviewed_available_raw.height,
        pixel_format=reviewed_available_raw.pixel_format,
    )
    backend = FakeCaptureBackend([unsupported, reviewed_available_raw])

    with CaptureSource(backend, clock=ManualClock(23.0)) as source:
        result = capture_evaluate_trust_varrock_east_iron(source)

    assert result.frame.payload == unsupported.payload
    assert tuple(observation.kind for observation in result.observations) == (
        "resource.uncertain",
    ) * 4
    assert result.trust.accepted is True
    assert tuple(resource.available for resource in result.trust.resources) == (
        None,
        None,
        None,
        None,
    )
    assert result.trust.actionable_targets == ()
    assert backend.grab_calls == 1


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
