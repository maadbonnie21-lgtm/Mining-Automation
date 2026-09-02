from __future__ import annotations

from collections.abc import Callable

import pytest

from mining_automation.banking.contracts import (
    INVENTORY_CAPACITY,
    BankCheckpointIdentity,
    BankEvidenceProvenance,
    BankingBlocker,
    BankingVerificationResult,
    BankInterfaceState,
    BankObservation,
    BankProfileIdentity,
    DepositReadiness,
    PostDepositInventoryObservation,
    PreDepositInventoryObservation,
)
from mining_automation.banking.testing import (
    SYNTHETIC_BANK_CHECKPOINT,
    SYNTHETIC_BANK_PROFILE,
    build_bank_observation,
    build_post_deposit_inventory_observation,
    build_pre_deposit_inventory_observation,
    build_provenance,
)
from mining_automation.contracts import FrameRef


class _OverloadedInt(int):
    def __lt__(self, other: object) -> bool:
        return False


class _OverloadedFloat(float):
    def __lt__(self, other: object) -> bool:
        return False


class _OverloadedString(str):
    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = str.__hash__


def test_bank_interface_state_members() -> None:
    assert {member.value for member in BankInterfaceState} == {"unknown", "closed", "open"}


def test_bank_checkpoint_identity_accepts_valid_values() -> None:
    identity = BankCheckpointIdentity(checkpoint_id="varrock-east-bank", location_id="varrock")
    assert identity.checkpoint_id == "varrock-east-bank"
    assert identity.location_id == "varrock"


@pytest.mark.parametrize("checkpoint_id", ["", "   "])
def test_bank_checkpoint_identity_rejects_blank_checkpoint_id(checkpoint_id: str) -> None:
    with pytest.raises(ValueError, match="checkpoint_id must be a non-empty string"):
        BankCheckpointIdentity(checkpoint_id=checkpoint_id, location_id="varrock")


@pytest.mark.parametrize("location_id", ["", "   "])
def test_bank_checkpoint_identity_rejects_blank_location_id(location_id: str) -> None:
    with pytest.raises(ValueError, match="location_id must be a non-empty string"):
        BankCheckpointIdentity(checkpoint_id="varrock-east-bank", location_id=location_id)


def test_bank_profile_identity_accepts_valid_values() -> None:
    profile = BankProfileIdentity(
        profile_id="p", profile_version="1", schema_version=1, frame_width=64, frame_height=48
    )
    assert profile.frame_width == 64
    assert profile.frame_height == 48


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("schema_version", 0, "schema_version must be a positive integer"),
        ("schema_version", -1, "schema_version must be a positive integer"),
        ("frame_width", 0, "frame_width must be a positive integer"),
        ("frame_width", -1, "frame_width must be a positive integer"),
        ("frame_height", 0, "frame_height must be a positive integer"),
        ("frame_height", -1, "frame_height must be a positive integer"),
    ],
)
def test_bank_profile_identity_rejects_invalid_geometry(
    field_name: str, value: int, message: str
) -> None:
    kwargs: dict[str, object] = {
        "profile_id": "p",
        "profile_version": "1",
        "schema_version": 1,
        "frame_width": 64,
        "frame_height": 48,
    }
    kwargs[field_name] = value
    with pytest.raises(ValueError, match=message):
        BankProfileIdentity(**kwargs)  # type: ignore[arg-type]


def test_bank_evidence_provenance_accepts_valid_values() -> None:
    provenance = build_provenance()
    assert provenance.cycle_id == "synthetic-cycle-1"
    assert len(provenance.frame_sha256) == 64


def test_bank_evidence_provenance_rejects_non_exact_frame() -> None:
    with pytest.raises(ValueError, match="frame must be an exact FrameRef"):
        BankEvidenceProvenance(
            frame="not-a-frame-ref",  # type: ignore[arg-type]
            cycle_id="cycle",
            frame_sha256="a" * 64,
        )


@pytest.mark.parametrize(
    "frame",
    [
        FrameRef(_OverloadedInt(1), 0.0, 1, 1),
        FrameRef(1, _OverloadedFloat(0.0), 1, 1),
    ],
)
def test_bank_evidence_provenance_rejects_overloaded_frame_primitives(
    frame: FrameRef,
) -> None:
    with pytest.raises(ValueError, match="exact numeric primitives"):
        BankEvidenceProvenance(frame, "cycle", "a" * 64)


def test_bank_evidence_provenance_rejects_overloaded_cycle_id() -> None:
    with pytest.raises(ValueError, match="cycle_id must be a non-empty string"):
        BankEvidenceProvenance(
            FrameRef(1, 0.0, 1, 1),
            _OverloadedString("cycle"),
            "a" * 64,
        )


@pytest.mark.parametrize(
    "digest",
    ["", "a" * 63, "a" * 65, "g" * 64, ("A" * 64)],
)
def test_bank_evidence_provenance_rejects_invalid_digest(digest: str) -> None:
    with pytest.raises(
        ValueError, match="frame_sha256 must be a 64-character lowercase hex digest"
    ):
        BankEvidenceProvenance(
            frame=FrameRef(frame_id=1, captured_monotonic_s=0.0, width=1, height=1),
            cycle_id="cycle",
            frame_sha256=digest,
        )


def test_bank_observation_accepts_valid_values() -> None:
    observation = build_bank_observation()
    assert observation.interface_state is BankInterfaceState.OPEN


@pytest.mark.parametrize(
    ("detector_id", "detector_version"),
    [
        (_OverloadedString("detector"), "1"),
        ("detector", _OverloadedString("1")),
    ],
)
def test_bank_observation_rejects_overloaded_detector_strings(
    detector_id: str, detector_version: str
) -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        build_bank_observation(
            detector_id=detector_id,
            detector_version=detector_version,
        )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: build_bank_observation(interface_state="open"),
            "interface_state must be an exact",
        ),
    ],
)
def test_bank_observation_rejects_non_exact_interface_state(
    factory: Callable[[], BankObservation], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


@pytest.mark.parametrize("confidence", [-0.0001, 1.0001, float("nan"), float("inf"), True])
def test_bank_observation_rejects_invalid_confidence(confidence: object) -> None:
    with pytest.raises(ValueError, match="confidence must be finite"):
        build_bank_observation(confidence=confidence)  # type: ignore[arg-type]


def test_bank_observation_accepts_integer_confidence() -> None:
    observation = build_bank_observation(confidence=1)
    assert observation.confidence == 1


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("identity", "not-an-identity", "identity must be an exact BankCheckpointIdentity"),
        ("profile", "not-a-profile", "profile must be an exact BankProfileIdentity"),
        ("provenance", "not-a-provenance", "provenance must be an exact BankEvidenceProvenance"),
    ],
)
def test_bank_observation_rejects_wrong_field_types(
    field_name: str, value: object, message: str
) -> None:
    kwargs: dict[str, object] = {
        "identity": SYNTHETIC_BANK_CHECKPOINT,
        "profile": SYNTHETIC_BANK_PROFILE,
        "provenance": build_provenance(),
        "interface_state": BankInterfaceState.OPEN,
        "confidence": 0.9,
        "detector_id": "d",
        "detector_version": "1",
    }
    kwargs[field_name] = value
    with pytest.raises(ValueError, match=message):
        BankObservation(**kwargs)  # type: ignore[arg-type]


def test_pre_and_post_deposit_inventory_observation_are_distinct_types() -> None:
    pre = build_pre_deposit_inventory_observation(occupied_slots=28)
    post = build_post_deposit_inventory_observation(occupied_slots=0)
    assert type(pre) is not type(post)
    assert isinstance(pre, PreDepositInventoryObservation)
    assert isinstance(post, PostDepositInventoryObservation)


def test_inventory_observation_rejects_non_exact_state() -> None:
    with pytest.raises(ValueError, match="state must be an exact InventoryState"):
        PreDepositInventoryObservation(
            state="not-a-state",  # type: ignore[arg-type]
            provenance=build_provenance(),
            detector_id="d",
            detector_version="1",
        )


def test_inventory_observation_rejects_non_exact_provenance() -> None:
    with pytest.raises(ValueError, match="provenance must be an exact BankEvidenceProvenance"):
        build_post_deposit_inventory_observation(
            occupied_slots=0,
            provenance="not-a-provenance",  # type: ignore[arg-type]
        )


def test_inventory_observation_uses_shared_capacity_default() -> None:
    observation = build_pre_deposit_inventory_observation(occupied_slots=10)
    assert observation.state.capacity == INVENTORY_CAPACITY


def test_deposit_readiness_members() -> None:
    assert {member.value for member in DepositReadiness} == {"not_ready", "ready"}


def test_banking_verification_result_verified_requires_no_blockers() -> None:
    result = BankingVerificationResult(verified=True)
    assert result.blockers == ()


def test_banking_verification_result_verified_rejects_blockers() -> None:
    with pytest.raises(ValueError, match="a verified result cannot carry blockers"):
        BankingVerificationResult(verified=True, blockers=(BankingBlocker.BANK_STATE_UNKNOWN,))


def test_banking_verification_result_denied_requires_a_blocker() -> None:
    with pytest.raises(ValueError, match="a denied result must carry at least one blocker"):
        BankingVerificationResult(verified=False)


def test_banking_verification_result_rejects_duplicate_blockers() -> None:
    with pytest.raises(ValueError, match="blockers must be unique"):
        BankingVerificationResult(
            verified=False,
            blockers=(BankingBlocker.BANK_STATE_UNKNOWN, BankingBlocker.BANK_STATE_UNKNOWN),
        )


def test_banking_verification_result_rejects_non_boolean_verified() -> None:
    with pytest.raises(ValueError, match="verified must be a boolean"):
        BankingVerificationResult(verified="yes")  # type: ignore[arg-type]


def test_banking_verification_result_rejects_wrong_blocker_element_type() -> None:
    with pytest.raises(ValueError, match="blockers must be a tuple of exact BankingBlocker"):
        BankingVerificationResult(verified=False, blockers=("not-a-blocker",))  # type: ignore[arg-type]


def test_synthetic_fixtures_are_internally_consistent() -> None:
    observation = build_bank_observation()
    assert observation.identity == SYNTHETIC_BANK_CHECKPOINT
    assert observation.profile == SYNTHETIC_BANK_PROFILE
    assert observation.provenance.frame.width == SYNTHETIC_BANK_PROFILE.frame_width
    assert observation.provenance.frame.height == SYNTHETIC_BANK_PROFILE.frame_height
