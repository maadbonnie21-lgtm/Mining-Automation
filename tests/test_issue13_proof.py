"""Regression proof for Issue #13 safety guarantees after Issue #18 migration."""

from __future__ import annotations

from pathlib import Path

from mining_automation.capture import Frame, RawFrame
from mining_automation.perception import (
    ResourceVisualState,
    build_varrock_east_iron_detector,
    load_replay_dataset,
    load_varrock_east_iron_profile,
    materialize_gzip_replay_dataset,
    run_detector,
)
from mining_automation.perception.resource import resource_state_from_observation

FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "perception" / "varrock-east-iron-v1"
)
MANIFEST = FIXTURE_ROOT / "manifest.json"

NORTHWEST = "varrock-east-iron-northwest"
CENTER = "varrock-east-iron-center"
ALL_RESOURCES = (
    NORTHWEST,
    "varrock-east-iron-southwest",
    CENTER,
    "varrock-east-iron-northeast",
)


def _load(tmp_path: Path):
    return load_replay_dataset(materialize_gzip_replay_dataset(MANIFEST, tmp_path))


def _states(observations):
    return {o.evidence["resource_id"]: o.evidence["state"] for o in observations}


def _real_frame(tmp_path: Path, case_id: str = "available-01") -> Frame:
    dataset = _load(tmp_path)
    return next(s.frame for s in dataset.samples if s.case.case_id == case_id)


def _mutate_region(frame, region, rgb):
    x, y, width, height = region
    blue, green, red = int(round(rgb[2])), int(round(rgb[1])), int(round(rgb[0]))
    payload = bytearray(frame.payload)
    row_stride = frame.width * 4
    for row in range(y, y + height):
        row_start = row * row_stride
        for col in range(x, x + width):
            offset = row_start + col * 4
            payload[offset : offset + 4] = bytes((blue, green, red, 255))
    return Frame.from_raw(
        RawFrame(bytes(payload), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def _translate(frame: Frame, dx: int, dy: int) -> Frame:
    """Translate the captured pixels as a deterministic camera-drift stand-in."""

    bytes_per_pixel = frame.pixel_format.bytes_per_pixel
    row_stride = frame.width * bytes_per_pixel
    source = frame.payload
    output = bytearray(len(source))
    for y in range(frame.height):
        source_y = (y - dy) % frame.height
        for x in range(frame.width):
            source_x = (x - dx) % frame.width
            destination = y * row_stride + x * bytes_per_pixel
            source_offset = source_y * row_stride + source_x * bytes_per_pixel
            output[destination : destination + bytes_per_pixel] = source[
                source_offset : source_offset + bytes_per_pixel
            ]
    return Frame.from_raw(
        RawFrame(bytes(output), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def test_camera_drift_remains_fail_closed_after_schema_v3(tmp_path: Path) -> None:
    """Issue #13's fail-closed drift guarantee survives the v3 mechanism swap."""

    source = _real_frame(tmp_path)
    frame = _translate(source, 16, 8)
    observations = run_detector(build_varrock_east_iron_detector(), frame)

    assert _states(observations) == {
        resource_id: ResourceVisualState.UNCERTAIN.value for resource_id in ALL_RESOURCES
    }
    assert all(
        observation.evidence["reason"].startswith("insufficient_landmark_quorum")
        for observation in observations
    )
    for observation in observations:
        state = resource_state_from_observation(observation)
        assert state.available is None
        assert state.interaction_region is None


def test_partial_occlusion_on_a_real_candidate_is_rejected(tmp_path: Path) -> None:
    """Issue #13's candidate-grid occlusion defense remains active under v3."""

    source = _real_frame(tmp_path)
    profile = load_varrock_east_iron_profile()
    candidate = next(c for c in profile.candidates if c.resource_id == NORTHWEST)
    x, y, width, height = candidate.region
    frame = _mutate_region(
        source,
        (x, y, width // 2, height // 2),
        candidate.depleted_signature.mean_rgb,
    )

    observations = run_detector(build_varrock_east_iron_detector(), frame)

    assert _states(observations)[NORTHWEST] == ResourceVisualState.UNCERTAIN.value
