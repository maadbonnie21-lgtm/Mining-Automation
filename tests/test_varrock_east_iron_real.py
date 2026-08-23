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
    # Every anchor is wildly wrong against an all-black frame (0.0 confidence
    # against a 0.90 floor), so the per-anchor fail-closed check -- Issue #13
    # hardening -- now reports this decisively rather than falling through to
    # the generic weighted-average rejection. A single-anchor drift subtle
    # enough to survive the average but not the floor is covered separately
    # by test_single_anchor_drift_is_caught_by_the_per_anchor_floor below;
    # this case is the most extreme form (every anchor fails at once) and
    # continues to prove the scene is rejected outright, not partially
    # trusted.
    assert all(
        observation.evidence["reason"].startswith("anchor_confidence_below_floor")
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


def test_single_anchor_drift_is_caught_by_the_per_anchor_floor(tmp_path: Path) -> None:
    """A camera-drift stand-in: one real anchor's patch is replaced with a
    colour distinct enough to matter but not so extreme it fails outright.
    similarity ~= 0.6 -- comfortably above the ~0.4 a single anchor could
    survive at under the old weighted-average-only check (see the
    ResourceDetectorProfile docstring for that arithmetic), but below the
    production profile's 0.90 per-anchor floor. Every other anchor and every
    candidate region keep their real, unmutated pixels."""
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
        resource_id: ResourceVisualState.UNCERTAIN.value for resource_id in ALL_RESOURCES
    }
    assert all(
        observation.evidence["reason"].startswith("anchor_confidence_below_floor")
        and "south-ground" in observation.evidence["reason"]
        for observation in observations
    )
    # The other three anchors are untouched real pixels and should still read
    # as a near-perfect match, confirming the rejection is specific to the
    # one drifted anchor rather than a side effect of the mutation.
    anchor_confidences = observations[0].evidence["anchor_confidences"]
    for anchor_id in ("grass-west", "grass-center", "east-slope"):
        assert anchor_confidences[anchor_id] > 0.99
    assert anchor_confidences["south-ground"] < 0.65


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
