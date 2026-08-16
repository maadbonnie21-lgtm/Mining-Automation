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


