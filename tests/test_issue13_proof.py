"""Before/after proof for Issue #13 hardening.

Deliberately written against ONLY the API that exists at the pre-hardening
base commit (c1b8f27), so this identical file runs on both sides. Any failure
here is therefore a genuine behavioural difference in classification, not an
ImportError or a missing-attribute error from new fields.

Expected: FAILS at base, PASSES after hardening.
"""

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


def test_camera_drift_single_anchor_is_rejected(tmp_path: Path) -> None:
    """CAMERA DRIFT REGRESSION.

    One anchor's real patch is replaced with a colour at ~0.6 similarity --
    above the ~0.4 a single anchor can sink to while the 4-anchor weighted
    average still clears 0.85, and below the 0.90 per-anchor floor the
    hardening introduces. Every other anchor and every candidate keeps its
    real, unmutated pixels.

    Base behaviour: drift is averaged away, targets classify AVAILABLE.
    Hardened behaviour: all targets UNCERTAIN, scene rejected.
    """
    source = _real_frame(tmp_path)
    profile = load_varrock_east_iron_profile()
    anchor = next(a for a in profile.anchors if a.anchor_id == "south-ground")
    drifted = (
        anchor.signature.mean_rgb[0] + 0.6 * anchor.signature.max_distance,
        anchor.signature.mean_rgb[1],
        anchor.signature.mean_rgb[2],
    )
    frame = _mutate_region(source, anchor.region, drifted)

    observations = run_detector(build_varrock_east_iron_detector(), frame)

    assert _states(observations) == {
        resource_id: ResourceVisualState.UNCERTAIN.value for resource_id in ALL_RESOURCES
    }


def test_partial_occlusion_on_a_real_candidate_is_rejected(tmp_path: Path) -> None:
    """PARTIAL OCCLUSION REGRESSION.

    One quadrant of a genuinely-available candidate is replaced with an
    unrelated colour, standing in for something covering part of the rock.

    Base behaviour: the whole-region mean blends it away and still reports a
    confident state. Hardened behaviour: UNCERTAIN.
    """
    source = _real_frame(tmp_path)
    profile = load_varrock_east_iron_profile()
    candidate = next(c for c in profile.candidates if c.resource_id == NORTHWEST)
    x, y, width, height = candidate.region
    # The occluder is this candidate's own DEPLETED signature colour -- the
    # realistic hard case, not an implausibly bright one. A quadrant of the
    # rock reading as depleted while the rest reads available is exactly the
    # ambiguity a single blended mean cannot represent.
    frame = _mutate_region(
        source, (x, y, width // 2, height // 2), candidate.depleted_signature.mean_rgb
    )

    observations = run_detector(build_varrock_east_iron_detector(), frame)

    assert _states(observations)[NORTHWEST] == ResourceVisualState.UNCERTAIN.value
