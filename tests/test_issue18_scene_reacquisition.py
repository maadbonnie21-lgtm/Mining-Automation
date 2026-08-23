"""Issue #18 deterministic proof suite: structural scene validation.

Covers the eight required cases. Negatives are precise, reproducible
perturbations of the real reviewed capture -- every pixel outside the mutated
region is byte-for-byte the real frame -- so each test exercises the real
profile, real geometry, and real frozen descriptors rather than a synthetic
scene built from scratch.

Not covered here, and deliberately not claimed anywhere: the 36 real
camera-drift frames. Those live only on the owner's machine and are validated
through ``tools/validate_varrock_east_drift.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    ResourceVisualState,
    build_varrock_east_iron_detector,
    load_replay_dataset,
    load_varrock_east_iron_profile,
    materialize_gzip_replay_dataset,
    run_detector,
)
from mining_automation.perception.resource import (
    ColorSignature,
    ResourceDetectorProfile,
    RockCandidateProfile,
    SceneAnchorProfile,
)
from mining_automation.perception.scene_landmarks import (
    MINIMUM_STRUCTURAL_VARIANCE,
    MacroZone,
    SceneLandmarkProfile,
    SceneVerdictReason,
    describe_region,
    descriptor_distance,
    evaluate_scene,
    macro_zone_for_region,
    structural_variance,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "perception" / "varrock-east-iron-v1"
)
MANIFEST = FIXTURE_ROOT / "manifest.json"

NORTHWEST = "varrock-east-iron-northwest"
SOUTHWEST = "varrock-east-iron-southwest"
CENTER = "varrock-east-iron-center"
NORTHEAST = "varrock-east-iron-northeast"
ALL_RESOURCES = (NORTHWEST, SOUTHWEST, CENTER, NORTHEAST)

# The reviewed fixtures are privacy-sanitized: title bar, chat/status area and
# the lower-right inventory panel are masked to solid black. Landmarks must
# never be calibrated inside these, because those coordinates hold live UI on a
# real machine -- a landmark there would pass every test here and fail live.
MASKED_ROWS_TOP = (0, 33)
MASKED_ROWS_BOTTOM = (850, 1077)


def _dataset(tmp_path: Path):
    return load_replay_dataset(materialize_gzip_replay_dataset(MANIFEST, tmp_path))


def _frame(tmp_path: Path, case_id: str = "available-01") -> Frame:
    return next(
        sample.frame for sample in _dataset(tmp_path).samples if sample.case.case_id == case_id
    )


def _states(observations) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {o.evidence["resource_id"]: o.evidence["state"] for o in observations}


def _fill(frame: Frame, region: tuple[int, int, int, int], rgb: tuple[float, float, float]) -> Frame:
    """Fill one region with a solid colour; every other pixel stays real."""
    x, y, width, height = region
    blue, green, red = int(round(rgb[2])), int(round(rgb[1])), int(round(rgb[0]))
    payload = bytearray(frame.payload)
    stride = frame.width * 4
    for row in range(y, y + height):
        base = row * stride
        for column in range(x, x + width):
            offset = base + column * 4
            payload[offset : offset + 4] = bytes((blue, green, red, 255))
    return Frame.from_raw(
        RawFrame(bytes(payload), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def _translate(frame: Frame, dx: int, dy: int) -> Frame:
    """Translate the whole frame: a deterministic stand-in for camera drift."""
    stride = frame.width * 4
    source = frame.payload
    out = bytearray(len(source))
    for y in range(frame.height):
        src_y = (y - dy) % frame.height
        for x in range(frame.width):
            src_x = (x - dx) % frame.width
            dst = y * stride + x * 4
            src = src_y * stride + src_x * 4
            out[dst : dst + 4] = source[src : src + 4]
    return Frame.from_raw(
        RawFrame(bytes(out), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


def _rescale_brightness(frame: Frame, factor: float, offset: int = 0) -> Frame:
    """Uniform brightness change: a restored view under different lighting."""
    source = frame.payload
    out = bytearray(len(source))
    for i in range(0, len(source), 4):
        for channel in range(3):
            out[i + channel] = max(0, min(255, int(source[i + channel] * factor + offset)))
        out[i + 3] = source[i + 3]
    return Frame.from_raw(
        RawFrame(bytes(out), frame.width, frame.height, frame.pixel_format),
        frame_id=frame.frame_id + 1,
        captured_monotonic_s=frame.captured_monotonic_s + 1.0,
    )


# ---------------------------------------------------------------------------
# 1. reviewed available/depleted/respawn fixtures remain definitive
# ---------------------------------------------------------------------------


def test_all_reviewed_fixtures_remain_definitive_under_schema_v3(tmp_path: Path) -> None:
    detector = build_varrock_east_iron_detector()
    for sample in _dataset(tmp_path).samples:
        observations = run_detector(detector, sample.frame)
        states = _states(observations)
        assert set(states) == set(ALL_RESOURCES)
        assert ResourceVisualState.UNCERTAIN.value not in states.values(), (
            f"{sample.case.case_id} regressed to uncertain under v3"
        )
        assert observations[0].evidence["reason"] in {
            "available_signature_matched",
            "depleted_signature_matched",
        }


def test_schema_v3_profile_is_loaded_with_six_landmarks() -> None:
    profile = load_varrock_east_iron_profile()
    assert len(profile.scene_landmarks) == 6
    assert profile.minimum_landmark_quorum == 5
    assert profile.minimum_landmark_zones == 3
    for landmark in profile.scene_landmarks:
        assert landmark.grid == 4
        assert landmark.maximum_distance == pytest.approx(0.12)


# ---------------------------------------------------------------------------
# 2. Issue #13 drift/occlusion guarantees remain fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shift", [4, 8, 16, 32])
def test_camera_translation_fails_closed(tmp_path: Path, shift: int) -> None:
    frame = _translate(_frame(tmp_path), shift, shift // 2)
    observations = run_detector(build_varrock_east_iron_detector(), frame)

    assert _states(observations) == {
        resource_id: ResourceVisualState.UNCERTAIN.value for resource_id in ALL_RESOURCES
    }
    assert all(o.evidence["reason"].startswith("insufficient_landmark") for o in observations)
    # No uncertain target may expose a clickable region (Issue #13 guarantee).
    for observation in observations:
        assert observation.evidence.get("region") is None or observation.evidence["state"] == (
            ResourceVisualState.UNCERTAIN.value
        )


def test_uncertain_targets_never_expose_an_interaction_region(tmp_path: Path) -> None:
    from mining_automation.perception.resource import resource_state_from_observation

    frame = _translate(_frame(tmp_path), 16, 8)
    for observation in run_detector(build_varrock_east_iron_detector(), frame):
        state = resource_state_from_observation(observation)
        assert state.available is None
        assert state.interaction_region is None


def test_occlusion_grid_guarantee_survives(tmp_path: Path) -> None:
    """Issue #13's 2x2 occlusion voting still gates individual candidates."""
    profile = load_varrock_east_iron_profile()
    candidate = next(c for c in profile.candidates if c.resource_id == NORTHWEST)
    x, y, width, height = candidate.region
    frame = _fill(
        _frame(tmp_path),
        (x, y, width // 2, height // 2),
        candidate.depleted_signature.mean_rgb,
    )
    observations = run_detector(build_varrock_east_iron_detector(), frame)
    states = _states(observations)
    assert states[NORTHWEST] == ResourceVisualState.UNCERTAIN.value
    assert states[SOUTHWEST] == ResourceVisualState.AVAILABLE.value


# ---------------------------------------------------------------------------
# 3. one weak local region does not reject a scene others still prove
# ---------------------------------------------------------------------------


def test_single_obstructed_landmark_degrades_safely(tmp_path: Path) -> None:
    """5-of-6 quorum: one covered landmark must not veto a proven view.

    This is the reacquisition half of Issue #18. Under the v2 per-anchor floor
    any single failing region rejected the entire scene, which is why a
    restored camera could never recover.
    """
    profile = load_varrock_east_iron_profile()
    landmark = profile.scene_landmarks[0]
    frame = _fill(_frame(tmp_path), landmark.region, (90.0, 90.0, 90.0))

    observations = run_detector(build_varrock_east_iron_detector(), frame)

    assert _states(observations) == {
        resource_id: ResourceVisualState.AVAILABLE.value for resource_id in ALL_RESOURCES
    }
    distances = observations[0].evidence["landmark_distances"]
    assert distances[landmark.landmark_id] > landmark.maximum_distance
    matched = sum(
        1
        for item in profile.scene_landmarks
        if distances[item.landmark_id] <= item.maximum_distance
    )
    assert matched == 5
    # Landmarks are calibrated 2-per-zone across the three usable macro zones,
    # so losing any single one still leaves 3 zones represented and the spatial
    # spread requirement satisfied. A 1-per-zone layout would fail here.
    assert observations[0].evidence["reason"] in {
        "available_signature_matched",
        "depleted_signature_matched",
    }


def test_two_obstructed_landmarks_fail_closed(tmp_path: Path) -> None:
    """Quorum is load-bearing: losing a second landmark rejects the scene."""
    profile = load_varrock_east_iron_profile()
    frame = _frame(tmp_path)
    for landmark in profile.scene_landmarks[:2]:
        frame = _fill(frame, landmark.region, (90.0, 90.0, 90.0))

    observations = run_detector(build_varrock_east_iron_detector(), frame)

    assert _states(observations) == {
        resource_id: ResourceVisualState.UNCERTAIN.value for resource_id in ALL_RESOURCES
    }
    assert observations[0].evidence["reason"].startswith("insufficient_landmark_quorum")


def test_restored_view_reacquires_after_drift(tmp_path: Path) -> None:
    """Drift rejects; restoring the supported view becomes definitive again.

    This is the reacquisition half of Issue #18. Under the v2 per-anchor floor
    any single failing terrain patch vetoed the scene permanently.
    """
    source = _frame(tmp_path)
    detector = build_varrock_east_iron_detector()

    drifted = _translate(source, 16, 8)
    assert _states(run_detector(detector, drifted)) == {
        resource_id: ResourceVisualState.UNCERTAIN.value for resource_id in ALL_RESOURCES
    }

    restored = _states(run_detector(detector, source))
    assert ResourceVisualState.UNCERTAIN.value not in restored.values()


def test_scene_validation_is_brightness_invariant(tmp_path: Path) -> None:
    """A restored view under different lighting still validates the scene.

    Asserted at the scene layer deliberately. Candidate available/depleted
    classification uses mean-RGB signatures and is *not* brightness-invariant
    by design (Issue #13); under a global brightness shift the rocks correctly
    become uncertain. What Issue #18 requires is that the *scene* remains
    recognisable so the detector can reacquire once conditions return, and
    that is what this measures.
    """
    profile = load_varrock_east_iron_profile()
    source = _frame(tmp_path)

    for factor, offset in ((0.85, 0), (1.15, 0), (1.0, 20), (1.0, -20)):
        shifted = _rescale_brightness(source, factor, offset)
        verdict = evaluate_scene(
            shifted,
            profile.scene_landmarks,
            required_quorum=profile.minimum_landmark_quorum,
            required_zones=profile.minimum_landmark_zones,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
        )
        assert verdict.validated, (
            f"scene failed to validate at factor={factor} offset={offset}: "
            f"{verdict.detail}"
        )
        assert verdict.matched_count == 6


# ---------------------------------------------------------------------------
# 4. one strong repeated-terrain match cannot validate the scene
# ---------------------------------------------------------------------------


def test_single_matching_landmark_cannot_validate_the_scene(tmp_path: Path) -> None:
    """Quorum defeats a lone strong match, however perfect it is."""
    profile = load_varrock_east_iron_profile()
    frame = _frame(tmp_path)
    # Destroy five of six landmarks; the survivor matches perfectly.
    for landmark in profile.scene_landmarks[1:]:
        frame = _fill(frame, landmark.region, (12.0, 200.0, 240.0))

    verdict = evaluate_scene(
        frame,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
    )
    assert verdict.matched_count == 1
    assert not verdict.validated
    assert verdict.reason is SceneVerdictReason.INSUFFICIENT_LANDMARK_QUORUM


def test_clustered_matches_cannot_validate_without_spatial_spread() -> None:
    """Zone spread defeats a quorum concentrated in one part of the frame.

    Repeated terrain is locally convincing, so a cluster of matches in one
    corner must not carry the scene even when it satisfies the count.
    """
    descriptor = tuple(float(value) for value in range(16))
    scale = max(abs(v) for v in descriptor)
    normalised = tuple(v / scale for v in descriptor)
    clustered = tuple(
        SceneLandmarkProfile(
            landmark_id=f"cluster-{index}",
            region=(10 + index * 60, 10, 48, 48),  # all north-west
            reference_descriptor=normalised,
            maximum_distance=0.12,
        )
        for index in range(6)
    )
    for landmark in clustered:
        assert landmark.zone(1005, 1078) is MacroZone.NORTH_WEST

    # Force every landmark to "match" by comparing a descriptor to itself.
    matches_all = tuple(
        SceneLandmarkProfile(
            landmark_id=item.landmark_id,
            region=item.region,
            reference_descriptor=item.reference_descriptor,
            maximum_distance=2.0,  # accept anything
        )
        for item in clustered
    )
    blank = Frame.from_raw(
        RawFrame(bytes(1005 * 1078 * 4), 1005, 1078, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    verdict = evaluate_scene(
        blank, matches_all, required_quorum=5, required_zones=3,
        frame_width=1005, frame_height=1078,
    )
    assert verdict.matched_count == 6
    assert not verdict.validated
    assert verdict.reason is SceneVerdictReason.INSUFFICIENT_SPATIAL_SPREAD


def test_calibrated_landmarks_span_the_required_zones() -> None:
    profile = load_varrock_east_iron_profile()
    zones = {
        macro_zone_for_region(item.region, profile.frame_width, profile.frame_height)
        for item in profile.scene_landmarks
    }
    assert len(zones) >= profile.minimum_landmark_zones


# ---------------------------------------------------------------------------
# 5. candidate-region visual changes do not corrupt scene validation
# ---------------------------------------------------------------------------


def test_rock_state_changes_leave_landmarks_untouched(tmp_path: Path) -> None:
    """Measured across every reviewed depletion/respawn frame."""
    profile = load_varrock_east_iron_profile()
    dataset = _dataset(tmp_path)
    reference = next(s.frame for s in dataset.samples if s.case.case_id == "available-01")

    for landmark in profile.scene_landmarks:
        base = describe_region(reference, landmark.region, grid=landmark.grid)
        for sample in dataset.samples:
            observed = describe_region(sample.frame, landmark.region, grid=landmark.grid)
            assert descriptor_distance(observed, base) < 0.001, (
                f"{landmark.landmark_id} moved on {sample.case.case_id}"
            )


def test_filling_every_candidate_region_does_not_reject_the_scene(tmp_path: Path) -> None:
    """Even total candidate corruption must leave scene validation intact."""
    profile = load_varrock_east_iron_profile()
    frame = _frame(tmp_path)
    for candidate in profile.candidates:
        frame = _fill(frame, candidate.region, (255.0, 0.0, 255.0))

    verdict = evaluate_scene(
        frame,
        profile.scene_landmarks,
        required_quorum=profile.minimum_landmark_quorum,
        required_zones=profile.minimum_landmark_zones,
        frame_width=profile.frame_width,
        frame_height=profile.frame_height,
    )
    assert verdict.validated
    assert verdict.matched_count == 6

    # The scene stays valid; the candidates themselves become uncertain.
    states = _states(run_detector(build_varrock_east_iron_detector(), frame))
    assert set(states.values()) == {ResourceVisualState.UNCERTAIN.value}


def test_landmarks_may_not_overlap_candidate_regions() -> None:
    profile = load_varrock_east_iron_profile()
    for landmark in profile.scene_landmarks:
        lx, ly, lw, lh = landmark.region
        for candidate in profile.candidates:
            cx, cy, cw, ch = candidate.region
            overlap = lx < cx + cw and cx < lx + lw and ly < cy + ch and cy < ly + lh
            assert not overlap, f"{landmark.landmark_id} overlaps {candidate.resource_id}"


# ---------------------------------------------------------------------------
# 6. partial obstruction never creates a false definitive target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coverage", [0.25, 0.5, 0.75, 1.0])
def test_partial_candidate_obstruction_never_becomes_a_false_target(
    tmp_path: Path, coverage: float
) -> None:
    profile = load_varrock_east_iron_profile()
    candidate = next(c for c in profile.candidates if c.resource_id == CENTER)
    x, y, width, height = candidate.region
    covered = max(1, int(height * coverage))
    frame = _fill(_frame(tmp_path), (x, y, width, covered), (150.0, 60.0, 200.0))

    observations = run_detector(build_varrock_east_iron_detector(), frame)
    target = next(o for o in observations if o.evidence["resource_id"] == CENTER)

    assert target.evidence["state"] != ResourceVisualState.DEPLETED.value or coverage == 1.0
    if target.evidence["state"] == ResourceVisualState.AVAILABLE.value:
        pytest.fail("partial obstruction produced a definitive available target")


def test_obstruction_over_landmark_and_candidate_together_fails_closed(
    tmp_path: Path,
) -> None:
    """A large overlay covering several landmarks fails the scene closed."""
    profile = load_varrock_east_iron_profile()
    frame = _frame(tmp_path)
    for landmark in profile.scene_landmarks[:3]:
        frame = _fill(frame, landmark.region, (200.0, 200.0, 200.0))

    observations = run_detector(build_varrock_east_iron_detector(), frame)
    assert _states(observations) == {
        resource_id: ResourceVisualState.UNCERTAIN.value for resource_id in ALL_RESOURCES
    }


# ---------------------------------------------------------------------------
# 7. insufficient/malformed scene evidence fails closed with a specific reason
# ---------------------------------------------------------------------------


def test_no_landmarks_configured_reports_its_own_reason() -> None:
    blank = Frame.from_raw(
        RawFrame(bytes(64 * 64 * 4), 64, 64, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    verdict = evaluate_scene(
        blank, (), required_quorum=5, required_zones=3, frame_width=64, frame_height=64
    )
    assert not verdict.validated
    assert verdict.reason is SceneVerdictReason.NO_LANDMARKS_CONFIGURED


def test_landmark_region_outside_the_frame_fails_closed() -> None:
    blank = Frame.from_raw(
        RawFrame(bytes(64 * 64 * 4), 64, 64, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    off_frame = (
        SceneLandmarkProfile(
            landmark_id="off-frame",
            region=(40, 40, 48, 48),  # extends past a 64x64 frame
            reference_descriptor=tuple(0.0 for _ in range(16)),
            maximum_distance=0.12,
        ),
    )
    verdict = evaluate_scene(
        blank, off_frame, required_quorum=1, required_zones=1,
        frame_width=64, frame_height=64,
    )
    assert not verdict.validated
    assert verdict.reason is SceneVerdictReason.MALFORMED_SCENE_EVIDENCE


def test_malformed_evidence_does_not_shrink_the_quorum_denominator() -> None:
    """A broken landmark must fail the scene, never be silently skipped.

    Skipping it would make validation *easier* by reducing the number of
    landmarks that must agree, which is exactly backwards.
    """
    blank = Frame.from_raw(
        RawFrame(bytes(200 * 200 * 4), 200, 200, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    landmarks = (
        SceneLandmarkProfile(
            landmark_id="valid",
            region=(0, 0, 48, 48),
            reference_descriptor=tuple(0.0 for _ in range(16)),
            maximum_distance=0.12,
        ),
        SceneLandmarkProfile(
            landmark_id="broken",
            region=(180, 180, 48, 48),  # off-frame
            reference_descriptor=tuple(0.0 for _ in range(16)),
            maximum_distance=0.12,
        ),
    )
    verdict = evaluate_scene(
        blank, landmarks, required_quorum=1, required_zones=1,
        frame_width=200, frame_height=200,
    )
    assert not verdict.validated
    assert verdict.reason is SceneVerdictReason.MALFORMED_SCENE_EVIDENCE


def test_descriptor_grid_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="descriptor has"):
        SceneLandmarkProfile(
            landmark_id="wrong-length",
            region=(0, 0, 48, 48),
            reference_descriptor=(0.0, 1.0),
            maximum_distance=0.12,
        )


def test_region_not_divisible_by_grid_is_rejected() -> None:
    with pytest.raises(ValueError, match="divide evenly"):
        SceneLandmarkProfile(
            landmark_id="indivisible",
            region=(0, 0, 50, 48),
            reference_descriptor=tuple(0.0 for _ in range(16)),
            maximum_distance=0.12,
        )


def test_descriptor_distance_rejects_incomparable_lengths() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        descriptor_distance((0.0, 1.0), (0.0, 1.0, 2.0))


def test_quorum_larger_than_landmark_count_is_rejected() -> None:
    profile = load_varrock_east_iron_profile()
    with pytest.raises(ValueError, match="minimum_landmark_quorum"):
        ResourceDetectorProfile(
            profile_id=profile.profile_id,
            location_id=profile.location_id,
            ore_label=profile.ore_label,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            pixel_format=profile.pixel_format,
            anchors=profile.anchors,
            candidates=profile.candidates,
            scene_landmarks=profile.scene_landmarks,
            minimum_landmark_quorum=99,
            minimum_landmark_zones=3,
        )


def test_zone_requirement_that_can_never_be_satisfied_is_rejected() -> None:
    """Fail at construction, not silently at runtime forever."""
    descriptor = tuple(float(index % 3) - 1.0 for index in range(16))
    clustered = tuple(
        SceneLandmarkProfile(
            landmark_id=f"nw-{index}",
            region=(10 + index * 60, 10, 48, 48),
            reference_descriptor=descriptor,
            maximum_distance=0.12,
        )
        for index in range(5)
    )
    with pytest.raises(ValueError, match="could never be satisfied"):
        ResourceDetectorProfile(
            profile_id="test",
            location_id="test",
            ore_label="iron",
            frame_width=1005,
            frame_height=1078,
            pixel_format=PixelFormat.BGRA8888,
            anchors=(
                SceneAnchorProfile("a", (900, 900, 8, 8), ColorSignature((1.0, 1.0, 1.0), 50.0)),
            ),
            candidates=(
                RockCandidateProfile(
                    "r", (500, 500, 20, 20),
                    ColorSignature((120.0, 40.0, 30.0), 60.0),
                    ColorSignature((90.0, 90.0, 90.0), 60.0),
                ),
            ),
            scene_landmarks=clustered,
            minimum_landmark_quorum=4,
            minimum_landmark_zones=3,
        )


def test_landmark_overlapping_a_candidate_is_rejected() -> None:
    profile = load_varrock_east_iron_profile()
    candidate = profile.candidates[0]
    overlapping = SceneLandmarkProfile(
        landmark_id="overlaps-rock",
        region=(candidate.region[0], candidate.region[1], 48, 48),
        reference_descriptor=tuple(float(i % 3) - 1.0 for i in range(16)),
        maximum_distance=0.12,
    )
    with pytest.raises(ValueError, match="must not overlap a candidate"):
        ResourceDetectorProfile(
            profile_id=profile.profile_id,
            location_id=profile.location_id,
            ore_label=profile.ore_label,
            frame_width=profile.frame_width,
            frame_height=profile.frame_height,
            pixel_format=profile.pixel_format,
            anchors=profile.anchors,
            candidates=profile.candidates,
            scene_landmarks=(*profile.scene_landmarks, overlapping),
            minimum_landmark_quorum=5,
            minimum_landmark_zones=3,
        )


# ---------------------------------------------------------------------------
# 8. configuration identity / version and determinism
# ---------------------------------------------------------------------------


def test_schema_version_is_three() -> None:
    from mining_automation.perception.resource import RESOURCE_PROFILE_SCHEMA_VERSION

    assert RESOURCE_PROFILE_SCHEMA_VERSION == 3


def test_profile_round_trips_through_v3_json(tmp_path: Path) -> None:
    from mining_automation.perception.resource import (
        load_resource_detector_profile,
        save_resource_detector_profile,
    )

    profile = load_varrock_east_iron_profile()
    path = tmp_path / "profile.json"
    save_resource_detector_profile(profile, path)
    assert load_resource_detector_profile(path) == profile


def test_same_frame_and_profile_are_deterministic(tmp_path: Path) -> None:
    frame = _frame(tmp_path)
    detector = build_varrock_east_iron_detector()
    first = run_detector(detector, frame)
    second = run_detector(build_varrock_east_iron_detector(), frame)

    assert [o.evidence for o in first] == [o.evidence for o in second]
    assert [o.confidence for o in first] == [o.confidence for o in second]


def test_descriptor_is_deterministic(tmp_path: Path) -> None:
    profile = load_varrock_east_iron_profile()
    frame = _frame(tmp_path)
    for landmark in profile.scene_landmarks:
        values = {
            describe_region(frame, landmark.region, grid=landmark.grid) for _ in range(5)
        }
        assert len(values) == 1


# ---------------------------------------------------------------------------
# calibration guards: no generic terrain, no masked regions
# ---------------------------------------------------------------------------


def test_calibrated_landmarks_clear_the_structural_variance_floor(tmp_path: Path) -> None:
    profile = load_varrock_east_iron_profile()
    frame = _frame(tmp_path)
    for landmark in profile.scene_landmarks:
        variance = structural_variance(frame, landmark.region, grid=landmark.grid)
        assert variance >= MINIMUM_STRUCTURAL_VARIANCE, (
            f"{landmark.landmark_id} is not discriminative ({variance:.2f})"
        )


def test_legacy_terrain_anchors_would_fail_the_structural_floor(tmp_path: Path) -> None:
    """Why the v2 anchors were never viable, measured rather than asserted.

    Each legacy anchor is generic grass or dirt. Their structural variance is
    far below the discriminative floor, so they could not distinguish one
    camera view from another -- which is why giving any single one of them veto
    power blocked reacquisition.
    """
    profile = load_varrock_east_iron_profile()
    frame = _frame(tmp_path)
    for anchor in profile.anchors:
        variance = structural_variance(frame, anchor.region, grid=4)
        assert variance < MINIMUM_STRUCTURAL_VARIANCE


def test_no_landmark_sits_inside_a_sanitized_mask_region() -> None:
    """Guards against calibrating on privacy-mask edges.

    Mask edges are perfectly stable and extremely high-contrast, so a naive
    "most structural region" search ranks them first. They hold live UI on a
    real machine, so a landmark there would pass every test in this file and
    fail on the first live frame.
    """
    profile = load_varrock_east_iron_profile()
    for landmark in profile.scene_landmarks:
        _, y, _, height = landmark.region
        assert y > MASKED_ROWS_TOP[1], f"{landmark.landmark_id} is in the title-bar mask"
        assert y + height < MASKED_ROWS_BOTTOM[0], (
            f"{landmark.landmark_id} is in the chat/status mask"
        )
