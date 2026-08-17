from __future__ import annotations

from pathlib import Path

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.contracts import Observation
from mining_automation.perception import (
    ResourceVisualState,
    build_varrock_east_iron_detector,
    evaluate_dataset,
    load_replay_dataset,
    load_varrock_east_iron_profile,
    materialize_gzip_replay_dataset,
    resource_states_from_observations,
    run_detector,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
)
MANIFEST = FIXTURE_ROOT / "manifest.json"

NORTHWEST = "varrock-east-iron-northwest"
SOUTHWEST = "varrock-east-iron-southwest"
CENTER = "varrock-east-iron-center"
NORTHEAST = "varrock-east-iron-northeast"
ALL_RESOURCES = (NORTHWEST, SOUTHWEST, CENTER, NORTHEAST)

EXPECTED_STATES = {
    "available-01": {
        NORTHWEST: ResourceVisualState.AVAILABLE,
        SOUTHWEST: ResourceVisualState.AVAILABLE,
        CENTER: ResourceVisualState.AVAILABLE,
        NORTHEAST: ResourceVisualState.AVAILABLE,
    },
    "lower-left-full-cycle-019": {
        NORTHWEST: ResourceVisualState.AVAILABLE,
        SOUTHWEST: ResourceVisualState.AVAILABLE,
        CENTER: ResourceVisualState.AVAILABLE,
        NORTHEAST: ResourceVisualState.AVAILABLE,
    },
    "lower-left-full-cycle-020": {
        NORTHWEST: ResourceVisualState.AVAILABLE,
        SOUTHWEST: ResourceVisualState.DEPLETED,
        CENTER: ResourceVisualState.AVAILABLE,
        NORTHEAST: ResourceVisualState.AVAILABLE,
    },
    "lower-left-full-cycle-028": {
        NORTHWEST: ResourceVisualState.AVAILABLE,
        SOUTHWEST: ResourceVisualState.DEPLETED,
        CENTER: ResourceVisualState.AVAILABLE,
        NORTHEAST: ResourceVisualState.AVAILABLE,
    },
    "lower-left-full-cycle-029": {
        NORTHWEST: ResourceVisualState.AVAILABLE,
        SOUTHWEST: ResourceVisualState.AVAILABLE,
        CENTER: ResourceVisualState.AVAILABLE,
        NORTHEAST: ResourceVisualState.AVAILABLE,
    },
}


def _load_real_dataset(tmp_path: Path):
    materialized = materialize_gzip_replay_dataset(
        MANIFEST,
        tmp_path / "materialized",
    )
    return load_replay_dataset(materialized)


def _states_by_resource(observations: tuple[Observation, ...]) -> dict[str, str]:
    states: dict[str, str] = {}
    for observation in observations:
        evidence = observation.evidence
        resource_id = evidence["resource_id"]
        state = evidence["state"]
        assert isinstance(resource_id, str)
        assert isinstance(state, str)
        states[resource_id] = state
    return states


def _replace_rgb_region(
    frame: Frame,
    region: tuple[int, int, int, int],
    rgb: tuple[int, int, int],
    *,
    frame_id: int,
) -> Frame:
    assert frame.pixel_format is PixelFormat.BGRA8888
    x, y, width, height = region
    red, green, blue = rgb
    payload = bytearray(frame.payload)
    row_stride = frame.width * 4
    for pixel_y in range(y, y + height):
        for pixel_x in range(x, x + width):
            offset = pixel_y * row_stride + pixel_x * 4
            payload[offset : offset + 4] = bytes((blue, green, red, 255))
    return Frame.from_raw(
        RawFrame(
            payload=bytes(payload),
            width=frame.width,
            height=frame.height,
            pixel_format=frame.pixel_format,
        ),
        frame_id=frame_id,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def test_packaged_profile_and_real_replay_pass_objective_evaluation(
    tmp_path: Path,
) -> None:
    profile = load_varrock_east_iron_profile()
    dataset = _load_real_dataset(tmp_path)
    detector = build_varrock_east_iron_detector()

    assert profile.profile_id == "varrock-east-iron-v1"
    assert profile.location_id == "varrock-east-mine"
    assert profile.ore_label == "iron"
    assert tuple(candidate.resource_id for candidate in profile.candidates) == ALL_RESOURCES

    report = evaluate_dataset(dataset, [detector])

    assert report.passed
    assert report.cases_run == 5
    assert report.cases_passed == 5
    assert report.cases_failed == 0
    assert report.failing_fixture_ids == ()


def test_real_available_depleted_and_respawn_sequence_matches_review(
    tmp_path: Path,
) -> None:
    dataset = _load_real_dataset(tmp_path)
    detector = build_varrock_east_iron_detector()

    assert [sample.case.case_id for sample in dataset.samples] == list(EXPECTED_STATES)

    for sample in dataset.samples:
        observations = run_detector(detector, sample.frame)
        actual = _states_by_resource(observations)
        expected = {
            resource_id: state.value
            for resource_id, state in EXPECTED_STATES[sample.case.case_id].items()
        }
        assert actual == expected

        shared_states = resource_states_from_observations(list(observations))
        for resource_id, expected_state in EXPECTED_STATES[sample.case.case_id].items():
            state = shared_states[resource_id]
            if expected_state is ResourceVisualState.AVAILABLE:
                assert state.available is True
                assert state.interaction_region is not None
            elif expected_state is ResourceVisualState.DEPLETED:
                assert state.available is False
                assert state.interaction_region is None
            else:
                raise AssertionError(expected_state)


def test_real_replay_report_is_deterministic(tmp_path: Path) -> None:
    dataset = _load_real_dataset(tmp_path)

    first = evaluate_dataset(dataset, [build_varrock_east_iron_detector()])
    second = evaluate_dataset(dataset, [build_varrock_east_iron_detector()])

    assert first.to_json() == second.to_json()
    assert first.render_text() == second.render_text()


def test_candidate_obstruction_becomes_uncertain_without_poisoning_scene(
    tmp_path: Path,
) -> None:
    dataset = _load_real_dataset(tmp_path)
    profile = load_varrock_east_iron_profile()
    source = dataset.samples[1].frame
    south_west = next(
        candidate for candidate in profile.candidates if candidate.resource_id == SOUTHWEST
    )
    obstructed = _replace_rgb_region(
        source,
        south_west.region,
        (255, 0, 255),
        frame_id=9001,
    )

    observations = run_detector(build_varrock_east_iron_detector(), obstructed)
    states = _states_by_resource(observations)

    assert states[SOUTHWEST] == ResourceVisualState.UNCERTAIN.value
    assert all(
        states[resource_id] == ResourceVisualState.AVAILABLE.value
        for resource_id in (NORTHWEST, CENTER, NORTHEAST)
    )
    shared = resource_states_from_observations(list(observations))
    assert shared[SOUTHWEST].available is None
    assert shared[SOUTHWEST].interaction_region is None


def test_wrong_scene_returns_uncertain_for_every_profiled_target(
    tmp_path: Path,
) -> None:
    dataset = _load_real_dataset(tmp_path)
    source = dataset.samples[0].frame
    black = Frame.from_raw(
        RawFrame(
            payload=bytes(source.width * source.height * 4),
            width=source.width,
            height=source.height,
            pixel_format=source.pixel_format,
        ),
        frame_id=9002,
        captured_monotonic_s=source.captured_monotonic_s + 1.0,
    )

    observations = run_detector(build_varrock_east_iron_detector(), black)

    assert _states_by_resource(observations) == {
        resource_id: ResourceVisualState.UNCERTAIN.value
        for resource_id in ALL_RESOURCES
    }
    assert all(
        observation.evidence["reason"] == "scene_not_recognized"
        for observation in observations
    )


def test_committed_real_frames_preserve_privacy_masks(tmp_path: Path) -> None:
    dataset = _load_real_dataset(tmp_path)

    for sample in dataset.samples:
        frame = sample.frame
        payload = memoryview(frame.payload).cast("B")
        row_stride = frame.width * 4

        # Title bar: colour channels are masked; alpha remains opaque.
        for pixel_x in range(0, frame.width, 37):
            offset = pixel_x * 4
            assert bytes(payload[offset : offset + 3]) == b"\x00\x00\x00"

        # Lower chat/status area.
        lower_row = 900 * row_stride
        for pixel_x in range(0, frame.width, 37):
            offset = lower_row + pixel_x * 4
            assert bytes(payload[offset : offset + 3]) == b"\x00\x00\x00"

        # Lower-right inventory/interface area.
        private_offset = 600 * row_stride + 700 * 4
        assert bytes(payload[private_offset : private_offset + 3]) == b"\x00\x00\x00"
