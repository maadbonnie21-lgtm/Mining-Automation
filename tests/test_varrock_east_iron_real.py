from __future__ import annotations

from pathlib import Path

import pytest

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


def _translate(frame: Frame, offset_x: int, offset_y: int) -> Frame:
    """Translate a reviewed frame for deterministic camera-jitter regression."""

    stride = frame.width * frame.pixel_format.bytes_per_pixel
    source = frame.payload
    translated = bytearray(len(source))
    for y in range(frame.height):
        source_y = (y - offset_y) % frame.height
        for x in range(frame.width):
            source_x = (x - offset_x) % frame.width
            destination_offset = y * stride + x * 4
            source_offset = source_y * stride + source_x * 4
            translated[destination_offset : destination_offset + 4] = source[
                source_offset : source_offset + 4
            ]
    return Frame.from_raw(
        RawFrame(
            payload=bytes(translated),
            width=frame.width,
            height=frame.height,
            pixel_format=frame.pixel_format,
        ),
        frame_id=frame.frame_id + 1,
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


@pytest.mark.parametrize(
    ("offset_x", "offset_y"),
    ((2, 0), (-2, 0), (0, 2), (0, -2)),
)
def test_two_pixel_cardinal_jitter_preserves_every_reviewed_resource_state(
    tmp_path: Path,
    offset_x: int,
    offset_y: int,
) -> None:
    """The accepted jitter envelope preserves exact production classifications."""

    dataset = _load_real_dataset(tmp_path)
    detector = build_varrock_east_iron_detector()

    for sample in dataset.samples:
        observations = run_detector(
            detector,
            _translate(sample.frame, offset_x, offset_y),
        )
        expected = {
            resource_id: state.value
            for resource_id, state in EXPECTED_STATES[sample.case.case_id].items()
        }
        assert _states_by_resource(observations) == expected

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
    # Every anchor is wildly wrong against an all-black frame (0.0 confidence
    # against a 0.90 floor), so the per-anchor fail-closed check -- Issue #13
    # hardening -- now reports this decisively rather than falling through to
    # the generic weighted-average rejection. A single-anchor drift subtle
    # enough to survive the average but not the floor is covered separately
    # by test_single_anchor_drift_is_caught_by_the_per_anchor_floor below;
    # this case is the most extreme form (every anchor fails at once) and
    # continues to prove the scene is rejected outright, not partially
    # trusted.
    # Schema v3 (Issue #18): an all-black frame matches none of the six
    # structural landmarks, so distributed evidence rejects the scene with a
    # specific quorum reason. Previously this tripped the v2 per-anchor floor;
    # the outcome is unchanged (every target uncertain) and the reason is now
    # more diagnostic.
    assert all(
        observation.evidence["reason"].startswith("insufficient_landmark_quorum")
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


# ---------------------------------------------------------------------------
# Issue #13 hardening: real-frame regression coverage
#
# Each test below starts from an actual reviewed capture and mutates only a
# small, precisely-targeted pixel region, leaving every other real pixel
# untouched. This grounds the hardening tests in the real production profile
# and real background pixels, rather than a synthetic frame built from
# scratch, while keeping each mutation exact and deterministic.
# ---------------------------------------------------------------------------


def _mutate_region(
    frame: Frame, region: tuple[int, int, int, int], rgb: tuple[float, float, float]
) -> Frame:
    """Return a copy of ``frame`` with every pixel in ``region`` set to a
    solid colour, in BGRA byte order. Every other pixel is byte-for-byte
    identical to the source."""
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


def test_legacy_anchor_patch_change_is_non_gating_under_schema_v3(tmp_path: Path) -> None:
    """Issue #18, lead decision B/D: legacy mean-RGB anchors no longer veto.

    Under schema v2 this exact mutation tripped the Issue #13 per-anchor floor
    and rejected the whole scene. That veto is the confirmed reacquisition
    bug: the four legacy anchors measure 0.78-3.27 structural variance against
    a 8.0 discriminative floor, so they never carried information capable of
    telling one camera view from another. Under v3 the six structural
    landmarks still agree, so the scene remains validated.
    """
    dataset = _load_real_dataset(tmp_path)
    source = next(
        sample.frame
        for sample in dataset.samples
        if sample.case.case_id == "available-01"
    )
    profile = load_varrock_east_iron_profile()
    south_ground = next(a for a in profile.anchors if a.anchor_id == "south-ground")

    drifted_rgb = (
        south_ground.signature.mean_rgb[0] + 0.6 * south_ground.signature.max_distance,
        south_ground.signature.mean_rgb[1],
        south_ground.signature.mean_rgb[2],
    )
    frame = _mutate_region(source, south_ground.region, drifted_rgb)

    observations = run_detector(build_varrock_east_iron_detector(), frame)

    assert _states_by_resource(observations) == {
        resource_id: ResourceVisualState.AVAILABLE.value for resource_id in ALL_RESOURCES
    }
    # The legacy anchor measurement is still recorded as evidence, just not
    # used to gate the decision.
    assert "anchor_confidences" in observations[0].evidence
    assert observations[0].evidence["anchor_confidences"]["south-ground"] < 0.65
    # Every landmark is untouched by this mutation and still matches exactly.
    distances = observations[0].evidence["landmark_distances"]
    assert len(distances) == 6
    assert max(distances.values()) == 0.0


def test_partial_occlusion_on_a_real_candidate_is_suspected(tmp_path: Path) -> None:
    """One quadrant of a real, genuinely-available candidate region is
    replaced with an unrelated colour -- standing in for a player, another
    player, or an overlay covering part of the rock. The production profile's
    2x2 occlusion grid must refuse to trust the blended result."""
    dataset = _load_real_dataset(tmp_path)
    source = next(
        sample.frame
        for sample in dataset.samples
        if sample.case.case_id == "available-01"
    )
    profile = load_varrock_east_iron_profile()
    northwest = next(
        c for c in profile.candidates if c.resource_id == "varrock-east-iron-northwest"
    )
    x, y, width, height = northwest.region
    top_left_quadrant = (x, y, width // 2, height // 2)
    # The occluder is this candidate's own DEPLETED signature colour, not an
    # implausibly bright one. This is the realistic hard case and the one that
    # actually proves the mechanism: a quadrant reading as depleted while the
    # rest reads available is precisely the ambiguity a single blended mean
    # cannot represent. Verified against the pre-hardening base commit
    # (c1b8f27), which reports this frame as a confident "available" at 0.838
    # -- a genuine missed occlusion. A more extreme colour (e.g. bright
    # magenta) is *already* caught at base by the ordinary ambiguity check, so
    # it would not have demonstrated any gap.
    occluder_rgb = northwest.depleted_signature.mean_rgb
    frame = _mutate_region(source, top_left_quadrant, occluder_rgb)

    observations = run_detector(build_varrock_east_iron_detector(), frame)
    states = _states_by_resource(observations)

    # The occluded candidate must not silently keep reporting available --
    # and must not flip to a confidently wrong depleted, either.
    assert states[NORTHWEST] == ResourceVisualState.UNCERTAIN.value
    northwest_observation = next(
        o for o in observations if o.evidence["resource_id"] == NORTHWEST
    )
    assert northwest_observation.evidence["reason"] == "partial_occlusion_suspected"
    assert northwest_observation.evidence["occlusion_cell_states"].count("available") == 3
    # The three untouched candidates, including the scene anchors, are
    # entirely real, unmutated pixels and must classify normally -- the
    # occlusion is local to the one mutated candidate, not global.
    assert states[SOUTHWEST] == ResourceVisualState.AVAILABLE.value
    assert states[CENTER] == ResourceVisualState.AVAILABLE.value
    assert states[NORTHEAST] == ResourceVisualState.AVAILABLE.value


def test_wrong_ore_colour_in_a_real_candidate_window_is_uncertain(tmp_path: Path) -> None:
    """A colour representative of a different ore (not iron's available or
    depleted signature) sitting in a real candidate window must not be
    confidently matched to either state."""
    dataset = _load_real_dataset(tmp_path)
    source = next(
        sample.frame
        for sample in dataset.samples
        if sample.case.case_id == "available-01"
    )
    profile = load_varrock_east_iron_profile()
    center = next(c for c in profile.candidates if c.resource_id == CENTER)
    # A saturated, distinctly different colour from both iron signatures at
    # this location (available ~(57,30,20), depleted ~(59,53,53)).
    wrong_ore_rgb = (40.0, 170.0, 210.0)
    frame = _mutate_region(source, center.region, wrong_ore_rgb)

    observations = run_detector(build_varrock_east_iron_detector(), frame)
    states = _states_by_resource(observations)

    assert states[CENTER] == ResourceVisualState.UNCERTAIN.value
    center_observation = next(o for o in observations if o.evidence["resource_id"] == CENTER)
    assert center_observation.evidence["reason"] in (
        "partial_occlusion_suspected",
        "candidate_colour_ambiguous",
    )
    # Untouched real candidates must be unaffected.
    assert states[NORTHWEST] == ResourceVisualState.AVAILABLE.value
    assert states[SOUTHWEST] == ResourceVisualState.AVAILABLE.value
    assert states[NORTHEAST] == ResourceVisualState.AVAILABLE.value
