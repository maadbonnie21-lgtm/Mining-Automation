from __future__ import annotations

import gzip
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.scene_landmarks import MacroZone
from mining_automation.validation import camera_bridge_north_state as north_state
from mining_automation.validation.camera_bridge_north_state import (
    CAMERA_BRIDGE_EXACT_NORTH_QUALIFICATION_ID,
    CameraBridgeNorthStateError,
    qualify_exact_frozen_north_registration,
)
from mining_automation.validation.camera_bridge_planner import (
    FROZEN_ENDPOINT_SOURCE_SHA256,
)
from mining_automation.validation.robust_registration import (
    CorrespondenceEvidence,
    DistortionEvidence,
    EndpointEvidence,
    ModelEvidence,
    ModelFamily,
    RegistrationDisposition,
    RegistrationPolicy,
    RobustRegistrationEngine,
    RobustWorldRegistration,
)

_ZONES = (
    MacroZone.NORTH_WEST,
    MacroZone.NORTH_EAST,
    MacroZone.SOUTH_WEST,
)
_IDENTITY = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


def _registration() -> RobustWorldRegistration:
    endpoint = EndpointEvidence(
        payload_sha256=FROZEN_ENDPOINT_SOURCE_SHA256,
        payload_bytes=1005 * 1078 * 4,
        width=1005,
        height=1078,
        pixel_format="bgra8888",
    )
    policy = RegistrationPolicy()
    counts = tuple((zone, 20) for zone in _ZONES)
    cells = tuple((zone, 6) for zone in _ZONES)
    model = ModelEvidence(
        family=ModelFamily.TRANSLATION,
        forward_matrix=_IDENTITY,
        reverse_matrix=_IDENTITY,
        inliers=120,
        inlier_ratio=1.0,
        source_zone_inliers=counts,
        target_zone_inliers=counts,
        source_zone_cells=cells,
        target_zone_cells=cells,
        median_residual_px=0.0,
        p90_residual_px=0.0,
        cycle_median_px=0.0,
        cycle_p90_px=0.0,
        distortion=DistortionEvidence(
            finite=True,
            orientation_preserved=True,
            minimum_local_scale=1.0,
            maximum_local_scale=1.0,
            maximum_local_scale_ratio=1.0,
            maximum_local_condition=1.0,
            overlap_fraction=1.0,
            perspective_span=0.0,
            passed=True,
        ),
        adequate=True,
        rejection_reasons=(),
    )
    return RobustWorldRegistration(
        registration_id="issue31-robust-world-registration-r1",
        registration_version="1.0.0",
        source=endpoint,
        target=endpoint,
        profile_id="varrock-east-iron-v1",
        profile_fingerprint_sha256="1" * 64,
        exclusion_fingerprint_sha256="2" * 64,
        algorithm_fingerprint_sha256="3" * 64,
        policy_fingerprint_sha256="4" * 64,
        disposition=RegistrationDisposition.ACCEPTED,
        detail="identity",
        correspondence=CorrespondenceEvidence(
            source_features=500,
            target_features=500,
            total_forward_matches=500,
            total_reverse_matches=500,
            forward_ratio_matches=200,
            reverse_ratio_matches=200,
            mutual_matches=150,
            balanced_matches=120,
            per_zone_mutual_matches=counts,
        ),
        required_zones=_ZONES,
        excluded_regions=(),
        models=(model,),
        selected_family=ModelFamily.TRANSLATION,
        policy=policy,
    )


def test_exact_frozen_identity_registration_qualifies_zero_click() -> None:
    result = qualify_exact_frozen_north_registration(_registration())

    assert result.payload_sha256 == FROZEN_ENDPOINT_SOURCE_SHA256
    assert result.as_dict() == {
        "accepted": True,
        "exact_frozen_pixel_identity": True,
        "frozen_source_sha256": FROZEN_ENDPOINT_SOURCE_SHA256,
        "id": CAMERA_BRIDGE_EXACT_NORTH_QUALIFICATION_ID,
        "identity_matrix_tolerance": 1e-09,
        "method": "exact_pixels_and_identity_translation_registration",
        "minimum_overlap_fraction": 0.99,
        "observed_overlap_fraction": 1.0,
        "payload_sha256": FROZEN_ENDPOINT_SOURCE_SHA256,
        "production_scene_authority": False,
        "registration_input_authority": False,
        "required_zones": ["north_west", "north_east", "south_west"],
        "selected_model_family": "translation",
        "version": "1.0.0",
    }


def test_real_engine_qualifies_only_the_exact_same_reviewed_pixels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "perception"
        / "varrock-east-iron-v1"
        / "frames"
        / "available-01.raw.gz"
    )
    payload = gzip.decompress(fixture.read_bytes())
    digest = hashlib.sha256(payload).hexdigest()
    frame = Frame.from_raw(
        RawFrame(payload, 1005, 1078, PixelFormat.BGRA8888),
        frame_id=1,
        captured_monotonic_s=0.0,
    )
    monkeypatch.setattr(north_state, "FROZEN_ENDPOINT_SOURCE_SHA256", digest)

    registration = RobustRegistrationEngine().analyze(frame, frame)
    result = north_state.qualify_exact_frozen_north_registration(registration)

    assert registration.selected_family is ModelFamily.TRANSLATION
    assert result.payload_sha256 == digest
    assert result.overlap_fraction == 1.0


@pytest.mark.parametrize(
    "mutation",
    [
        "different_target",
        "similarity",
        "translated",
        "low_overlap",
        "missing_zone",
        "authority",
    ],
)
def test_zero_click_rejects_every_non_identity_or_authoritative_relation(
    mutation: str,
) -> None:
    registration = _registration()
    model = registration.selected_model
    assert model is not None and model.distortion is not None
    if mutation == "different_target":
        registration = replace(
            registration,
            target=replace(registration.target, payload_sha256="f" * 64),
        )
    elif mutation == "similarity":
        changed = replace(model, family=ModelFamily.SIMILARITY)
        registration = replace(
            registration,
            models=(changed,),
            selected_family=ModelFamily.SIMILARITY,
        )
    elif mutation == "translated":
        translated = (
            (1.0, 0.0, 1.0e-6),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        registration = replace(
            registration,
            models=(replace(model, forward_matrix=translated),),
        )
    elif mutation == "low_overlap":
        registration = replace(
            registration,
            models=(
                replace(
                    model,
                    distortion=replace(model.distortion, overlap_fraction=0.98),
                ),
            ),
        )
    elif mutation == "missing_zone":
        registration = replace(
            registration,
            models=(
                replace(
                    model,
                    source_zone_inliers=((MacroZone.NORTH_WEST, 20),),
                ),
            ),
        )
    else:
        object.__setattr__(registration, "can_accept", True)

    with pytest.raises(CameraBridgeNorthStateError):
        qualify_exact_frozen_north_registration(registration)
