from __future__ import annotations

from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.resource import (
    ColorSignature,
    ProfiledResourceDetector,
    ResourceDetectorProfile,
    RockCandidateProfile,
    SceneAnchorProfile,
    load_resource_detector_profile,
    resource_state_from_observation,
    resource_states_from_observations,
    save_resource_detector_profile,
)


def make_bgra_frame(
    pixels: list[tuple[int, int, int]], width: int, height: int, *, frame_id: int = 1
) -> Frame:
    payload = bytearray()
    for red, green, blue in pixels:
        payload.extend((blue, green, red, 255))
    return Frame.from_raw(
        RawFrame(bytes(payload), width, height, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def profile() -> ResourceDetectorProfile:
    return ResourceDetectorProfile(
        profile_id="test-site",
        location_id="test-mine",
        ore_label="iron",
        frame_width=4,
        frame_height=2,
        pixel_format=PixelFormat.BGRA8888,
        anchors=(
            SceneAnchorProfile(
                "ground",
                (0, 0, 2, 2),
                ColorSignature((20.0, 80.0, 20.0), 80.0),
            ),
        ),
        candidates=(
            RockCandidateProfile(
                "iron-1",
                (2, 0, 2, 2),
                ColorSignature((120.0, 40.0, 30.0), 100.0),
                ColorSignature((90.0, 90.0, 90.0), 100.0),
                minimum_similarity=0.5,
                minimum_margin=0.1,
            ),
        ),
        minimum_scene_confidence=0.6,
        sample_step=1,
    )


def test_detects_available_and_adapts_to_resource_state() -> None:
    frame = make_bgra_frame(
        [(20, 80, 20), (20, 80, 20), (120, 40, 30), (120, 40, 30)] * 2,
        4,
        2,
    )
    detector = ProfiledResourceDetector(profile(), version="1")

    observations = detector.detect(frame)

    assert [observation.kind for observation in observations] == ["resource.available"]
    assert observations[0].evidence["state"] == "available"
    state = resource_state_from_observation(observations[0])
    assert state.available is True
    assert state.interaction_region == (2, 0, 2, 2)


def test_detects_depleted_and_never_exposes_a_click_region() -> None:
    frame = make_bgra_frame(
        [(20, 80, 20), (20, 80, 20), (90, 90, 90), (90, 90, 90)] * 2,
        4,
        2,
    )
    detector = ProfiledResourceDetector(profile(), version="1")

    observation = detector.detect(frame)[0]
    state = resource_state_from_observation(observation)

    assert observation.kind == "resource.depleted"
    assert state.available is False
    assert state.interaction_region is None


def test_scene_mismatch_returns_explicit_uncertainty() -> None:
    frame = make_bgra_frame([(200, 0, 200)] * 8, 4, 2)
    observation = ProfiledResourceDetector(profile(), version="1").detect(frame)[0]

    assert observation.kind == "resource.uncertain"
    assert observation.evidence["reason"] == "scene_not_recognized"
    assert resource_state_from_observation(observation).available is None


def test_geometry_mismatch_returns_uncertainty_without_invalid_region() -> None:
    frame = make_bgra_frame([(20, 80, 20)] * 2, 2, 1)
    observation = ProfiledResourceDetector(profile(), version="1").detect(frame)[0]

    assert observation.kind == "resource.uncertain"
    assert "region" not in observation.evidence


def test_ambiguous_candidate_is_uncertain() -> None:
    frame = make_bgra_frame(
        [(20, 80, 20), (20, 80, 20), (100, 65, 60), (100, 65, 60)] * 2,
        4,
        2,
    )
    observation = ProfiledResourceDetector(profile(), version="1").detect(frame)[0]
    assert observation.kind == "resource.uncertain"


def test_resource_adapter_rejects_inconsistent_state() -> None:
    frame = make_bgra_frame([(20, 80, 20), (20, 80, 20), (120, 40, 30), (120, 40, 30)] * 2, 4, 2)
    observation = ProfiledResourceDetector(profile(), version="1").detect(frame)[0]
    bad = type(observation)(
        kind=observation.kind,
        frame=observation.frame,
        confidence=observation.confidence,
        evidence={**observation.evidence, "state": "depleted"},
        detector_version=observation.detector_version,
    )
    with pytest.raises(ValueError, match="disagree"):
        resource_state_from_observation(bad)


def test_duplicate_resource_states_are_rejected() -> None:
    frame = make_bgra_frame([(20, 80, 20), (20, 80, 20), (120, 40, 30), (120, 40, 30)] * 2, 4, 2)
    observation = ProfiledResourceDetector(profile(), version="1").detect(frame)[0]
    with pytest.raises(ValueError, match="duplicate"):
        resource_states_from_observations([observation, observation])



def test_resource_profile_json_round_trip(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    save_resource_detector_profile(profile(), profile_path)

    loaded = load_resource_detector_profile(profile_path)

    assert loaded == profile()
    assert profile_path.read_text(encoding="utf-8").endswith("\n")


# ---------------------------------------------------------------------------
# Issue #13 hardening: overlap validation
# ---------------------------------------------------------------------------


def test_overlapping_candidate_regions_are_rejected() -> None:
    with pytest.raises(ValueError, match="candidate regions must not overlap"):
        ResourceDetectorProfile(
            profile_id="test-site",
            location_id="test-mine",
            ore_label="iron",
            frame_width=10,
            frame_height=10,
            pixel_format=PixelFormat.BGRA8888,
            anchors=(
                SceneAnchorProfile("ground", (0, 0, 2, 2), ColorSignature((20.0, 80.0, 20.0), 80.0)),
            ),
            candidates=(
                RockCandidateProfile(
                    "iron-1", (2, 2, 4, 4),
                    ColorSignature((120.0, 40.0, 30.0), 100.0),
                    ColorSignature((90.0, 90.0, 90.0), 100.0),
                ),
                RockCandidateProfile(
                    "iron-2", (4, 4, 4, 4),  # overlaps iron-1 at (4,4)-(6,6)
                    ColorSignature((120.0, 40.0, 30.0), 100.0),
                    ColorSignature((90.0, 90.0, 90.0), 100.0),
                ),
            ),
        )


def test_candidate_overlapping_an_anchor_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not overlap a scene anchor"):
        ResourceDetectorProfile(
            profile_id="test-site",
            location_id="test-mine",
            ore_label="iron",
            frame_width=10,
            frame_height=10,
            pixel_format=PixelFormat.BGRA8888,
            anchors=(
                SceneAnchorProfile("ground", (0, 0, 4, 4), ColorSignature((20.0, 80.0, 20.0), 80.0)),
            ),
            candidates=(
                RockCandidateProfile(
                    "iron-1", (2, 2, 4, 4),  # overlaps the anchor at (2,2)-(4,4)
                    ColorSignature((120.0, 40.0, 30.0), 100.0),
                    ColorSignature((90.0, 90.0, 90.0), 100.0),
                ),
            ),
        )


def test_edge_touching_regions_are_not_overlap() -> None:
    """Two regions calibrated flush against each other share only a boundary
    line -- zero-area intersection -- and are a legitimate tight calibration,
    not a mistake."""
    built = ResourceDetectorProfile(
        profile_id="test-site",
        location_id="test-mine",
        ore_label="iron",
        frame_width=10,
        frame_height=10,
        pixel_format=PixelFormat.BGRA8888,
        anchors=(
            SceneAnchorProfile("ground", (0, 0, 2, 2), ColorSignature((20.0, 80.0, 20.0), 80.0)),
        ),
        candidates=(
            RockCandidateProfile(
                "iron-1", (2, 0, 2, 2),
                ColorSignature((120.0, 40.0, 30.0), 100.0),
                ColorSignature((90.0, 90.0, 90.0), 100.0),
            ),
            RockCandidateProfile(
                "iron-2", (4, 0, 2, 2),  # starts exactly where iron-1 ends
                ColorSignature((120.0, 40.0, 30.0), 100.0),
                ColorSignature((90.0, 90.0, 90.0), 100.0),
            ),
        ),
    )
    assert len(built.candidates) == 2


# ---------------------------------------------------------------------------
# Issue #13 hardening: per-anchor fail-closed floor (camera drift)
# ---------------------------------------------------------------------------


def _profile_with_anchor_floor(minimum_anchor_confidence: float) -> ResourceDetectorProfile:
    return ResourceDetectorProfile(
        profile_id="test-site",
        location_id="test-mine",
        ore_label="iron",
        frame_width=6,
        frame_height=2,
        pixel_format=PixelFormat.BGRA8888,
        anchors=(
            SceneAnchorProfile("ground-a", (0, 0, 2, 2), ColorSignature((20.0, 80.0, 20.0), 50.0)),
            SceneAnchorProfile("ground-b", (2, 0, 2, 2), ColorSignature((20.0, 80.0, 20.0), 50.0)),
        ),
        candidates=(
            RockCandidateProfile(
                "iron-1", (4, 0, 2, 2),
                ColorSignature((120.0, 40.0, 30.0), 100.0),
                ColorSignature((90.0, 90.0, 90.0), 100.0),
            ),
        ),
        minimum_scene_confidence=0.6,
        minimum_anchor_confidence=minimum_anchor_confidence,
        sample_step=1,
    )


def test_default_anchor_floor_is_zero_and_changes_nothing() -> None:
    """Backward compatibility: an unset floor must not add any new rejection
    beyond what the existing weighted-average check already does."""
    profile = _profile_with_anchor_floor(0.0)
    assert profile.minimum_anchor_confidence == 0.0
    # ground-a exactly matches; ground-b is moderately drifted. Weighted
    # average = (1.0 + 0.5) / 2 = 0.75, above the 0.6 scene threshold, so this
    # must still classify normally with no anchor-floor rejection.
    row = [(20, 80, 20), (20, 80, 20), (45, 80, 20), (45, 80, 20), (120, 40, 30), (120, 40, 30)]
    frame = make_bgra_frame(row * 2, 6, 2)
    observations = ProfiledResourceDetector(profile, version="1").detect(frame)
    assert observations[0].kind == "resource.available"


def test_single_drifted_anchor_survives_the_weighted_average_but_not_the_floor() -> None:
    """The exact gap this hardening closes: one anchor moderately drifted,
    the other unaffected. The average (0.75) clears a 0.6 scene threshold, so
    only the new per-anchor floor catches it."""
    profile = _profile_with_anchor_floor(0.9)
    # ground-a exact match (1.0); ground-b shifted by 25 in the red channel
    # against max_distance=50 -> similarity = 1 - 25/50 = 0.5.
    row = [(20, 80, 20), (20, 80, 20), (45, 80, 20), (45, 80, 20), (120, 40, 30), (120, 40, 30)]
    frame = make_bgra_frame(row * 2, 6, 2)
    observations = ProfiledResourceDetector(profile, version="1").detect(frame)
    assert observations[0].kind == "resource.uncertain"
    assert observations[0].evidence["reason"].startswith("anchor_confidence_below_floor")
    assert "ground-b" in observations[0].evidence["reason"]
    # The candidate region's own colour is a clean, unambiguous match -- the
    # only thing wrong is the anchor -- so this proves the floor, not the
    # candidate colour check, is what triggered the rejection.


# ---------------------------------------------------------------------------
# Issue #13 hardening: partial occlusion defense via sub-region voting
# ---------------------------------------------------------------------------


def _occlusion_profile(minimum_occlusion_agreement: float = 1.0) -> ResourceDetectorProfile:
    return ResourceDetectorProfile(
        profile_id="test-site",
        location_id="test-mine",
        ore_label="iron",
        frame_width=6,
        frame_height=4,
        pixel_format=PixelFormat.BGRA8888,
        anchors=(
            SceneAnchorProfile("ground", (0, 0, 2, 2), ColorSignature((20.0, 80.0, 20.0), 50.0)),
        ),
        candidates=(
            RockCandidateProfile(
                "iron-1",
                (2, 0, 4, 4),
                ColorSignature((120.0, 40.0, 30.0), 40.0),
                ColorSignature((90.0, 90.0, 90.0), 40.0),
                minimum_similarity=0.5,
                minimum_margin=0.1,
                occlusion_grid_columns=2,
                occlusion_grid_rows=2,
                minimum_occlusion_agreement=minimum_occlusion_agreement,
            ),
        ),
        minimum_scene_confidence=0.5,
        sample_step=1,
    )


def _occlusion_frame(quadrant_colors: dict[str, tuple[int, int, int]]) -> Frame:
    """6x4 frame: a 2x2 ground anchor, plus a 4x4 candidate region split into
    tl/tr/bl/br quadrants, each independently colourable."""
    pixels: list[tuple[int, int, int]] = []
    for y in range(4):
        for x in range(6):
            if x < 2 and y < 2:
                pixels.append((20, 80, 20))  # anchor
            elif x < 2:
                pixels.append((20, 80, 20))  # fill below the anchor, unused by any region
            else:
                cx, cy = x - 2, y
                quadrant = ("tl" if cx < 2 else "tr") if cy < 2 else ("bl" if cx < 2 else "br")
                pixels.append(quadrant_colors[quadrant])
    return make_bgra_frame(pixels, 6, 4)


def test_occlusion_grid_unanimous_quadrants_classify_normally() -> None:
    frame = _occlusion_frame({q: (120, 40, 30) for q in ("tl", "tr", "bl", "br")})
    observations = ProfiledResourceDetector(_occlusion_profile(), version="1").detect(frame)
    assert observations[0].kind == "resource.available"
    assert observations[0].evidence["occlusion_agreement_fraction"] == 1.0


def test_occlusion_grid_minority_occluder_is_suspected_not_averaged_away() -> None:
    """The exact failure mode a whole-region mean cannot defend against: one
    quadrant occluded by a colour that could plausibly bias a single blended
    mean, while three quadrants show the true state."""
    frame = _occlusion_frame(
        {"tl": (90, 90, 90), "tr": (120, 40, 30), "bl": (120, 40, 30), "br": (120, 40, 30)}
    )
    observations = ProfiledResourceDetector(_occlusion_profile(), version="1").detect(frame)
    assert observations[0].kind == "resource.uncertain"
    assert observations[0].evidence["reason"] == "partial_occlusion_suspected"
    assert observations[0].evidence["occlusion_cell_states"].count("available") == 3
    assert observations[0].evidence["occlusion_agreement_fraction"] == pytest.approx(0.75)


def test_occlusion_grid_relaxed_agreement_tolerates_a_minority_disagreement() -> None:
    """The same minority-occluded frame as above, but with agreement relaxed
    to 0.75 -- confirms the threshold is genuinely load-bearing, not just
    ignored."""
    frame = _occlusion_frame(
        {"tl": (90, 90, 90), "tr": (120, 40, 30), "bl": (120, 40, 30), "br": (120, 40, 30)}
    )
    detector = ProfiledResourceDetector(_occlusion_profile(minimum_occlusion_agreement=0.75), version="1")
    observations = detector.detect(frame)
    assert observations[0].kind == "resource.available"


def test_whole_region_mean_would_have_been_fooled_by_the_same_minority_occluder() -> None:
    """Direct proof the grid adds real protection: classifying the identical
    frame with a 1x1 (whole-region) profile produces a confident answer from
    a mean blended across the occluded quadrant, rather than flagging it."""
    frame = _occlusion_frame(
        {"tl": (150, 60, 50), "tr": (120, 40, 30), "bl": (120, 40, 30), "br": (120, 40, 30)}
    )
    whole_region_profile = ResourceDetectorProfile(
        profile_id="test-site",
        location_id="test-mine",
        ore_label="iron",
        frame_width=6,
        frame_height=4,
        pixel_format=PixelFormat.BGRA8888,
        anchors=(
            SceneAnchorProfile("ground", (0, 0, 2, 2), ColorSignature((20.0, 80.0, 20.0), 50.0)),
        ),
        candidates=(
            RockCandidateProfile(
                "iron-1", (2, 0, 4, 4),
                ColorSignature((120.0, 40.0, 30.0), 40.0),
                ColorSignature((90.0, 90.0, 90.0), 40.0),
                minimum_similarity=0.5,
                minimum_margin=0.1,
                # occlusion_grid_columns/rows default to 1x1: today's
                # whole-region-mean behaviour, unchanged.
            ),
        ),
        minimum_scene_confidence=0.5,
        sample_step=1,
    )
    observations = ProfiledResourceDetector(whole_region_profile, version="1").detect(frame)
    # One quadrant occluded by an unrelated colour (150,60,50) -- not even a
    # colour resembling either signature -- still blends into a mean
    # (127.5, 45, 35) that is a *confident* 0.74 match to "available": the
    # whole-region classifier has no way to see that a quarter of its
    # evidence disagrees. Exactly the risk the grid mechanism exists to catch.
    assert observations[0].kind == "resource.available"
    assert observations[0].confidence > 0.7
    assert "occlusion_cell_states" not in observations[0].evidence

    # The identical frame, through the grid-enabled profile, correctly
    # refuses to trust it.
    grid_observations = ProfiledResourceDetector(_occlusion_profile(), version="1").detect(frame)
    assert grid_observations[0].kind == "resource.uncertain"
    assert grid_observations[0].evidence["reason"] == "partial_occlusion_suspected"


def test_occlusion_grid_majority_occluder_is_correctly_uncertain() -> None:
    frame = _occlusion_frame(
        {"tl": (90, 90, 90), "tr": (90, 90, 90), "bl": (90, 90, 90), "br": (120, 40, 30)}
    )
    observations = ProfiledResourceDetector(_occlusion_profile(), version="1").detect(frame)
    assert observations[0].kind == "resource.uncertain"
    assert observations[0].evidence["reason"] == "partial_occlusion_suspected"


def test_occlusion_grid_dimensions_must_divide_the_region_evenly() -> None:
    with pytest.raises(ValueError, match="must divide evenly"):
        RockCandidateProfile(
            "iron-1", (0, 0, 5, 4),
            ColorSignature((120.0, 40.0, 30.0), 100.0),
            ColorSignature((90.0, 90.0, 90.0), 100.0),
            occlusion_grid_columns=2,  # 5 does not divide evenly by 2
        )


def test_occlusion_grid_columns_and_rows_must_be_positive() -> None:
    with pytest.raises(ValueError, match="occlusion_grid_columns"):
        RockCandidateProfile(
            "iron-1", (0, 0, 4, 4),
            ColorSignature((120.0, 40.0, 30.0), 100.0),
            ColorSignature((90.0, 90.0, 90.0), 100.0),
            occlusion_grid_columns=0,
        )


def test_minimum_occlusion_agreement_must_be_positive() -> None:
    with pytest.raises(ValueError, match="minimum_occlusion_agreement"):
        RockCandidateProfile(
            "iron-1", (0, 0, 4, 4),
            ColorSignature((120.0, 40.0, 30.0), 100.0),
            ColorSignature((90.0, 90.0, 90.0), 100.0),
            minimum_occlusion_agreement=0.0,
        )


def test_occlusion_cell_regions_reduce_to_the_whole_region_by_default() -> None:
    candidate = RockCandidateProfile(
        "iron-1", (2, 0, 4, 4),
        ColorSignature((120.0, 40.0, 30.0), 100.0),
        ColorSignature((90.0, 90.0, 90.0), 100.0),
    )
    assert candidate.occlusion_cell_count == 1
    assert candidate.occlusion_cell_regions() == ((2, 0, 4, 4),)


# ---------------------------------------------------------------------------
# Issue #13 hardening: wrong-visual regression
# ---------------------------------------------------------------------------


def test_unrelated_ore_colour_in_a_candidate_window_is_uncertain_not_confident() -> None:
    """A different ore's colour (here: a synthetic copper-like orange, clearly
    distinct from both the iron-available and iron-depleted signatures used
    in `profile()`) sitting in a candidate window must not be confidently
    matched to either signature."""
    copper_like = (200, 110, 40)  # far from both (120,40,30) and (90,90,90)
    frame = make_bgra_frame(
        [(20, 80, 20), (20, 80, 20), copper_like, copper_like] * 2, 4, 2,
    )
    observations = ProfiledResourceDetector(profile(), version="1").detect(frame)
    assert observations[0].kind == "resource.uncertain"
    assert observations[0].evidence["reason"] == "candidate_colour_ambiguous"


def test_occlusion_grid_rows_must_be_positive() -> None:
    with pytest.raises(ValueError, match="occlusion_grid_rows"):
        RockCandidateProfile(
            "iron-1", (0, 0, 4, 4),
            ColorSignature((120.0, 40.0, 30.0), 100.0),
            ColorSignature((90.0, 90.0, 90.0), 100.0),
            occlusion_grid_rows=0,
        )


def test_occlusion_grid_row_dimension_must_divide_the_region_evenly() -> None:
    with pytest.raises(ValueError, match="height .* must divide evenly"):
        RockCandidateProfile(
            "iron-1", (0, 0, 4, 5),
            ColorSignature((120.0, 40.0, 30.0), 100.0),
            ColorSignature((90.0, 90.0, 90.0), 100.0),
            occlusion_grid_rows=2,  # 5 does not divide evenly by 2
        )
