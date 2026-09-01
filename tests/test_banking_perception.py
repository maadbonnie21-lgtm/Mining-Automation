from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from types import SimpleNamespace

import pytest

from mining_automation.banking.contracts import (
    BankCheckpointIdentity,
    BankingBlocker,
    BankInterfaceState,
    PostDepositInventoryObservation,
    PreDepositInventoryObservation,
)
from mining_automation.banking.errors import (
    BankDetectorContractError,
    BankDetectorExecutionError,
)
from mining_automation.banking.perception import (
    BANK_PUBLICATION_CONFIDENCE_FLOOR,
    MAX_BANKING_EVIDENCE_AGE_S,
    BankDetectorMetadata,
    BankPerceptionResult,
    InventoryPerceptionResult,
    evaluate_bank_observation,
    evaluate_inventory_observation,
    run_bank_detector,
    validate_bank_detector,
)
from mining_automation.banking.testing import (
    SYNTHETIC_BANK_CHECKPOINT,
    SYNTHETIC_BANK_DETECTOR_METADATA,
    SYNTHETIC_BANK_PROFILE,
    build_ambiguous_bank_observation,
    build_bank_observation,
    build_obstructed_bank_observation,
    build_post_deposit_inventory_observation,
    build_pre_deposit_inventory_observation,
    build_provenance,
)
from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.contracts import FrameRef, InventoryState


class _OverloadedString(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = str.__hash__


class _OverloadedInt(int):
    def __lt__(self, other: object) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = int.__hash__


class _OverloadedFloat(float):
    def __lt__(self, other: object) -> bool:
        return False

    def __le__(self, other: object) -> bool:
        return False


# ---------------------------------------------------------------------------
# BankDetector protocol / guarded runner
# ---------------------------------------------------------------------------


def _frame(*, frame_id: int = 1, width: int = 64, height: int = 48) -> Frame:
    raw = RawFrame(
        payload=bytes(width * height * PixelFormat.BGRA8888.bytes_per_pixel),
        width=width,
        height=height,
        pixel_format=PixelFormat.BGRA8888,
    )
    return Frame.from_raw(raw, frame_id=frame_id, captured_monotonic_s=float(frame_id))


def _provenance_for_frame(frame: Frame, *, frame_id: int | None = None):
    return build_provenance(
        frame_id=frame.frame_id if frame_id is None else frame_id,
        captured_monotonic_s=frame.captured_monotonic_s,
        width=frame.width,
        height=frame.height,
        frame_sha256=sha256(frame.payload).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class _ConformingDetector:
    metadata: BankDetectorMetadata = BankDetectorMetadata("synthetic", "1.0.0")

    def observe(self, frame: Frame) -> object:
        return build_bank_observation(
            provenance=_provenance_for_frame(frame),
            detector_id=self.metadata.detector_id,
            detector_version=self.metadata.version,
        )


@dataclass(frozen=True, slots=True)
class _RaisingDetector:
    metadata: BankDetectorMetadata = BankDetectorMetadata("raising", "1.0.0")

    def observe(self, frame: Frame) -> object:
        raise RuntimeError("boom")


@dataclass(frozen=True, slots=True)
class _WrongReturnTypeDetector:
    metadata: BankDetectorMetadata = BankDetectorMetadata("wrong-type", "1.0.0")

    def observe(self, frame: Frame) -> object:
        return "not-an-observation"


@dataclass(frozen=True, slots=True)
class _WrongFrameDetector:
    metadata: BankDetectorMetadata = BankDetectorMetadata("wrong-frame", "1.0.0")

    def observe(self, frame: Frame) -> object:
        return build_bank_observation(
            provenance=_provenance_for_frame(frame, frame_id=frame.frame_id + 5)
        )


@dataclass(frozen=True, slots=True)
class _WrongVersionDetector:
    metadata: BankDetectorMetadata = BankDetectorMetadata("wrong-version", "1.0.0")

    def observe(self, frame: Frame) -> object:
        return build_bank_observation(
            provenance=_provenance_for_frame(frame),
            detector_id=self.metadata.detector_id,
            detector_version="does-not-match",
        )


@dataclass(frozen=True, slots=True)
class _WrongIdDetector:
    metadata: BankDetectorMetadata = BankDetectorMetadata("declared", "1.0.0")

    def observe(self, frame: Frame) -> object:
        return build_bank_observation(
            provenance=_provenance_for_frame(frame),
            detector_id="rogue",
            detector_version=self.metadata.version,
        )


class _InPlaceMetadataMutationDetector:
    def __init__(self) -> None:
        self.metadata = BankDetectorMetadata("trusted", "1.0.0")

    def observe(self, frame: Frame) -> object:
        object.__setattr__(self.metadata, "detector_id", "rogue")
        object.__setattr__(self.metadata, "version", "2.0.0")
        return build_bank_observation(
            provenance=_provenance_for_frame(frame),
            detector_id="rogue",
            detector_version="2.0.0",
        )


class _InputFrameMutationDetector:
    metadata = BankDetectorMetadata("frame-mutator", "1.0.0")

    def observe(self, frame: Frame) -> object:
        object.__setattr__(
            frame,
            "ref",
            FrameRef(
                frame_id=frame.frame_id + 1,
                captured_monotonic_s=frame.captured_monotonic_s + 1.0,
                width=frame.width,
                height=frame.height,
            ),
        )


class _ForgedStringObservationDetector:
    metadata = BankDetectorMetadata("trusted", "1.0.0")

    def observe(self, frame: Frame) -> object:
        observation = build_bank_observation(
            provenance=_provenance_for_frame(frame),
            detector_id="rogue",
            detector_version="rogue-version",
        )
        object.__setattr__(observation, "detector_id", _OverloadedString("rogue"))
        object.__setattr__(observation, "detector_version", _OverloadedString("rogue-version"))
        return observation


@dataclass(frozen=True, slots=True)
class _ForgedNestedObservationDetector:
    mutation: str
    metadata: BankDetectorMetadata = BankDetectorMetadata("nested-forger", "1.0.0")

    def observe(self, frame: Frame) -> object:
        observation = build_bank_observation(
            provenance=_provenance_for_frame(frame),
            detector_id=self.metadata.detector_id,
            detector_version=self.metadata.version,
        )
        if self.mutation == "cycle":
            object.__setattr__(observation.provenance, "cycle_id", "")
        else:
            object.__setattr__(observation.provenance.frame, "frame_id", True)
        return observation


@dataclass(frozen=True, slots=True)
class _WrongDigestDetector:
    metadata: BankDetectorMetadata = BankDetectorMetadata("wrong-digest", "1.0.0")

    def observe(self, frame: Frame) -> object:
        provenance = _provenance_for_frame(frame)
        object.__setattr__(provenance, "frame_sha256", "f" * 64)
        return build_bank_observation(
            provenance=provenance,
            detector_id=self.metadata.detector_id,
            detector_version=self.metadata.version,
        )


class _PayloadMutationDetector:
    metadata = BankDetectorMetadata("payload-mutator", "1.0.0")

    def observe(self, frame: Frame) -> object:
        original_provenance = _provenance_for_frame(frame)
        object.__setattr__(frame, "payload", b"\x01" * len(frame.payload))
        return build_bank_observation(
            provenance=original_provenance,
            detector_id=self.metadata.detector_id,
            detector_version=self.metadata.version,
        )


def test_run_bank_detector_returns_observation_for_conforming_detector() -> None:
    frame = _frame()
    observation = run_bank_detector(_ConformingDetector(), frame)
    assert observation.interface_state is BankInterfaceState.OPEN
    assert observation.provenance.frame_sha256 == sha256(frame.payload).hexdigest()


def test_run_bank_detector_wraps_raised_exception() -> None:
    with pytest.raises(BankDetectorExecutionError) as exc_info:
        run_bank_detector(_RaisingDetector(), _frame())
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_run_bank_detector_rejects_wrong_return_type() -> None:
    with pytest.raises(BankDetectorContractError, match="must return BankObservation"):
        run_bank_detector(_WrongReturnTypeDetector(), _frame())


def test_run_bank_detector_rejects_mismatched_frame() -> None:
    with pytest.raises(BankDetectorContractError, match="references frame"):
        run_bank_detector(_WrongFrameDetector(), _frame())


def test_run_bank_detector_rejects_mismatched_payload_digest() -> None:
    with pytest.raises(BankDetectorContractError, match="frame_sha256"):
        run_bank_detector(_WrongDigestDetector(), _frame())


def test_run_bank_detector_rejects_input_payload_mutation() -> None:
    with pytest.raises(BankDetectorContractError, match="mutated its input frame payload"):
        run_bank_detector(_PayloadMutationDetector(), _frame())


def test_run_bank_detector_rejects_mismatched_detector_version() -> None:
    with pytest.raises(BankDetectorContractError, match="detector_version"):
        run_bank_detector(_WrongVersionDetector(), _frame())


def test_run_bank_detector_rejects_mismatched_detector_id() -> None:
    with pytest.raises(BankDetectorContractError, match="detector_id"):
        run_bank_detector(_WrongIdDetector(), _frame())


def test_run_bank_detector_rejects_metadata_drift_within_a_run() -> None:
    detector = _ConformingDetector()
    other_metadata = BankDetectorMetadata("synthetic", "2.0.0")
    with pytest.raises(BankDetectorContractError, match="metadata changed"):
        run_bank_detector(detector, _frame(), expected_metadata=other_metadata)


def test_run_bank_detector_rejects_in_place_metadata_mutation_during_observe() -> None:
    detector = _InPlaceMetadataMutationDetector()
    with pytest.raises(BankDetectorContractError, match="metadata changed while observe"):
        run_bank_detector(
            detector,
            _frame(),
            expected_metadata=BankDetectorMetadata("trusted", "1.0.0"),
        )


def test_run_bank_detector_rejects_input_frame_identity_mutation() -> None:
    with pytest.raises(BankDetectorContractError, match="mutated its input frame identity"):
        run_bank_detector(_InputFrameMutationDetector(), _frame())


def test_run_bank_detector_rejects_overloaded_observation_identity_strings() -> None:
    with pytest.raises(BankDetectorContractError, match="invalid BankObservation"):
        run_bank_detector(_ForgedStringObservationDetector(), _frame())


@pytest.mark.parametrize("mutation", ["cycle", "frame-id"])
def test_run_bank_detector_recursively_revalidates_nested_observation(
    mutation: str,
) -> None:
    with pytest.raises(BankDetectorContractError, match="invalid BankObservation"):
        run_bank_detector(_ForgedNestedObservationDetector(mutation), _frame())


@pytest.mark.parametrize(
    "ref",
    [
        FrameRef(_OverloadedInt(1), 1.0, 64, 48),
        FrameRef(1, _OverloadedFloat(1.0), 64, 48),
    ],
)
def test_run_bank_detector_rejects_overloaded_frame_identity_primitives(ref: FrameRef) -> None:
    frame = _frame()
    object.__setattr__(frame, "ref", ref)
    with pytest.raises(BankDetectorContractError, match="invalid frame identity"):
        run_bank_detector(_ConformingDetector(), frame)


def test_validate_bank_detector_rejects_non_conforming_object() -> None:
    with pytest.raises(BankDetectorContractError, match="must satisfy BankDetector protocol"):
        validate_bank_detector(object())


def test_run_bank_detector_rejects_non_frame_input() -> None:
    with pytest.raises(BankDetectorContractError, match="must be Frame"):
        run_bank_detector(_ConformingDetector(), "not-a-frame")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# evaluate_bank_observation
# ---------------------------------------------------------------------------


def test_evaluate_bank_observation_passes_through_open() -> None:
    result = evaluate_bank_observation(
        build_bank_observation(interface_state=BankInterfaceState.OPEN),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.interface_state is BankInterfaceState.OPEN


def test_evaluate_bank_observation_passes_through_closed() -> None:
    result = evaluate_bank_observation(
        build_bank_observation(interface_state=BankInterfaceState.CLOSED),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.interface_state is BankInterfaceState.CLOSED


@pytest.mark.parametrize(
    ("detector_id", "detector_version", "expected_blocker"),
    (
        (
            "rogue-detector",
            SYNTHETIC_BANK_DETECTOR_METADATA.version,
            BankingBlocker.BANK_DETECTOR_ID_MISMATCH,
        ),
        (
            SYNTHETIC_BANK_DETECTOR_METADATA.detector_id,
            "wrong-version",
            BankingBlocker.BANK_DETECTOR_VERSION_MISMATCH,
        ),
    ),
)
def test_evaluate_bank_observation_rejects_wrong_detector_identity(
    detector_id: str,
    detector_version: str,
    expected_blocker: BankingBlocker,
) -> None:
    result = evaluate_bank_observation(
        build_bank_observation(
            detector_id=detector_id,
            detector_version=detector_version,
        ),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )

    assert result.interface_state is BankInterfaceState.UNKNOWN
    assert result.blockers == (expected_blocker,)


def test_evaluate_bank_observation_rejects_forged_overloaded_detector_strings() -> None:
    observation = build_bank_observation()
    object.__setattr__(observation, "detector_id", _OverloadedString("rogue"))
    object.__setattr__(observation, "detector_version", _OverloadedString("rogue"))
    result = evaluate_bank_observation(
        observation,
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,)


def test_evaluate_bank_observation_ambiguous_reading_is_accepted_unknown() -> None:
    result = evaluate_bank_observation(
        build_bank_observation(interface_state=BankInterfaceState.UNKNOWN),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.interface_state is BankInterfaceState.UNKNOWN
    assert result.blockers == ()


def test_evaluate_bank_observation_missing_observation_carries_no_authority() -> None:
    result = evaluate_bank_observation(
        None,
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert not result.accepted
    assert result.interface_state is BankInterfaceState.UNKNOWN
    assert result.blockers == (BankingBlocker.BANK_OBSERVATION_MISSING,)


def test_evaluate_bank_observation_rejects_wrong_type() -> None:
    result = evaluate_bank_observation(
        "not-an-observation",  # type: ignore[arg-type]
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,)


def test_evaluate_bank_observation_rejects_wrong_checkpoint_identity() -> None:
    result = evaluate_bank_observation(
        build_bank_observation(identity=BankCheckpointIdentity("other", "other")),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH in result.blockers
    assert result.interface_state is BankInterfaceState.UNKNOWN


def test_evaluate_bank_observation_rejects_wrong_profile_version() -> None:
    other_profile = replace(SYNTHETIC_BANK_PROFILE, profile_version="9.9.9")
    result = evaluate_bank_observation(
        build_bank_observation(profile=other_profile),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.BANK_PROFILE_MISMATCH,)


def test_evaluate_bank_observation_wrong_geometry_resolves_unknown() -> None:
    unsupported_profile = replace(SYNTHETIC_BANK_PROFILE, frame_width=999, frame_height=999)
    result = evaluate_bank_observation(
        build_bank_observation(profile=unsupported_profile),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=unsupported_profile,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.BANK_GEOMETRY_UNSUPPORTED,)
    assert result.interface_state is BankInterfaceState.UNKNOWN


def test_evaluate_bank_observation_rejects_stale_evidence() -> None:
    result = evaluate_bank_observation(
        build_bank_observation(provenance=build_provenance(captured_monotonic_s=0.0)),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=MAX_BANKING_EVIDENCE_AGE_S + 1.0,
    )
    assert result.blockers == (BankingBlocker.BANK_EVIDENCE_STALE,)


def test_evaluate_bank_observation_rejects_evidence_from_the_future() -> None:
    result = evaluate_bank_observation(
        build_bank_observation(provenance=build_provenance(captured_monotonic_s=10.0)),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.EVIDENCE_FROM_FUTURE,)


@pytest.mark.parametrize("evaluated_monotonic_s", [float("nan"), True, "not-a-number"])
def test_evaluate_bank_observation_rejects_invalid_evaluation_time(
    evaluated_monotonic_s: object,
) -> None:
    result = evaluate_bank_observation(
        build_bank_observation(),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=evaluated_monotonic_s,
    )
    assert result.blockers == (BankingBlocker.EVALUATION_TIME_INVALID,)


def test_evaluate_bank_observation_rejects_ordering_regression() -> None:
    earlier = build_provenance(frame_id=5, captured_monotonic_s=5.0)
    later_but_not_advancing = build_provenance(frame_id=5, captured_monotonic_s=5.0)
    result = evaluate_bank_observation(
        build_bank_observation(provenance=later_but_not_advancing),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=5.0,
        previous_provenance=earlier,
    )
    assert result.blockers == (BankingBlocker.EVIDENCE_ORDERING_REGRESSION,)


def test_evaluate_bank_observation_rejects_cross_cycle_advancing_frame() -> None:
    previous = build_provenance(frame_id=1, captured_monotonic_s=1.0, cycle_id="trusted-cycle")
    current = build_provenance(frame_id=2, captured_monotonic_s=2.0, cycle_id="foreign-cycle")
    result = evaluate_bank_observation(
        build_bank_observation(provenance=current),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=2.0,
        previous_provenance=previous,
    )
    assert result.blockers == (BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH,)


def test_evaluate_bank_observation_current_provenance_rejects_mixed_frame_evidence() -> None:
    """An independently-sourced current_provenance catches smuggled evidence.

    This models a future real caller that derives its own provenance from the
    capture layer and checks the detector's claimed evidence against it --
    the "mixed-frame provenance" guard required of the perception seam.
    """
    claimed_current = build_provenance(frame_id=1, cycle_id="cycle-a")
    actually_from = build_provenance(frame_id=1, cycle_id="cycle-b")
    result = evaluate_bank_observation(
        build_bank_observation(provenance=actually_from),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
        current_provenance=claimed_current,
    )
    assert result.blockers == (BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH,)


def test_evaluate_bank_observation_obstructed_view_resolves_unknown_no_blocker() -> None:
    """Obstruction: a confidently-unknown reading is genuine uncertainty, not a defect."""
    result = evaluate_bank_observation(
        build_obstructed_bank_observation(),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.interface_state is BankInterfaceState.UNKNOWN
    assert result.blockers == ()


def test_evaluate_bank_observation_ambiguous_ui_resolves_unknown_no_blocker() -> None:
    """Ambiguous UI (e.g. mid-transition): same safe handling as obstruction."""
    result = evaluate_bank_observation(
        build_ambiguous_bank_observation(),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.interface_state is BankInterfaceState.UNKNOWN
    assert result.blockers == ()


def test_evaluate_bank_observation_rejects_false_open_below_confidence_floor() -> None:
    """A confidently-labeled OPEN with low detector confidence is a 'false OPEN'.

    It must be rejected outright (forced to UNKNOWN with a blocker), never
    accepted as if it were a genuine, high-confidence OPEN reading.
    """
    result = evaluate_bank_observation(
        build_bank_observation(interface_state=BankInterfaceState.OPEN, confidence=0.3),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert not result.accepted
    assert result.interface_state is BankInterfaceState.UNKNOWN
    assert result.blockers == (BankingBlocker.BANK_CONFIDENCE_BELOW_FLOOR,)


def test_evaluate_bank_observation_rejects_false_closed_below_confidence_floor() -> None:
    result = evaluate_bank_observation(
        build_bank_observation(interface_state=BankInterfaceState.CLOSED, confidence=0.1),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.BANK_CONFIDENCE_BELOW_FLOOR,)


def test_evaluate_bank_observation_accepts_confidence_exactly_at_floor() -> None:
    result = evaluate_bank_observation(
        build_bank_observation(
            interface_state=BankInterfaceState.OPEN, confidence=BANK_PUBLICATION_CONFIDENCE_FLOOR
        ),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.interface_state is BankInterfaceState.OPEN


def test_evaluate_bank_observation_current_provenance_accepts_matching_evidence() -> None:
    provenance = build_provenance()
    result = evaluate_bank_observation(
        build_bank_observation(provenance=provenance),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
        evaluated_monotonic_s=0.0,
        current_provenance=provenance,
    )
    assert result.accepted


# ---------------------------------------------------------------------------
# evaluate_inventory_observation
# ---------------------------------------------------------------------------


def test_evaluate_inventory_observation_accepts_known_non_empty() -> None:
    result = evaluate_inventory_observation(
        build_pre_deposit_inventory_observation(occupied_slots=28),
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.state.occupied_slots == 28


def test_evaluate_inventory_observation_accepts_known_empty() -> None:
    result = evaluate_inventory_observation(
        build_post_deposit_inventory_observation(occupied_slots=0),
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.state.occupied_slots == 0


def test_evaluate_inventory_observation_missing_is_rejected() -> None:
    result = evaluate_inventory_observation(None, evaluated_monotonic_s=0.0)
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_MISSING,)
    assert result.state.occupied_slots is None


def test_evaluate_inventory_observation_rejects_wrong_shape() -> None:
    result = evaluate_inventory_observation("not-an-observation", evaluated_monotonic_s=0.0)
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,)


def test_evaluate_inventory_observation_rejects_structural_fake() -> None:
    fake = SimpleNamespace(
        state=InventoryState(occupied_slots=28, capacity=28, confidence=0.99),
        provenance=build_provenance(),
        detector_id="structural-fake",
        detector_version="1",
    )
    result = evaluate_inventory_observation(fake, evaluated_monotonic_s=0.0)
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,)


@pytest.mark.parametrize(
    "observation",
    [
        build_pre_deposit_inventory_observation(occupied_slots=28),
        build_post_deposit_inventory_observation(occupied_slots=0),
    ],
)
def test_evaluate_inventory_observation_revalidates_mutated_nested_state(
    observation: PreDepositInventoryObservation | PostDepositInventoryObservation,
) -> None:
    state = observation.state
    object.__setattr__(state, "confidence", float("nan"))
    result = evaluate_inventory_observation(observation, evaluated_monotonic_s=0.0)
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,)


def test_evaluate_inventory_observation_unknown_count_is_rejected() -> None:
    result = evaluate_inventory_observation(
        build_pre_deposit_inventory_observation(occupied_slots=None),
        evaluated_monotonic_s=0.0,
    )
    assert BankingBlocker.INVENTORY_UNKNOWN in result.blockers
    assert result.state.occupied_slots is None


def test_evaluate_inventory_observation_rejects_stale_evidence() -> None:
    result = evaluate_inventory_observation(
        build_pre_deposit_inventory_observation(
            occupied_slots=28, provenance=build_provenance(captured_monotonic_s=0.0)
        ),
        evaluated_monotonic_s=MAX_BANKING_EVIDENCE_AGE_S + 1.0,
    )
    assert result.blockers == (BankingBlocker.INVENTORY_EVIDENCE_STALE,)


def test_evaluate_inventory_observation_rejects_low_confidence() -> None:
    result = evaluate_inventory_observation(
        build_pre_deposit_inventory_observation(occupied_slots=28, confidence=0.1),
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.INVENTORY_CONFIDENCE_BELOW_FLOOR,)


def test_evaluate_inventory_observation_rejects_layout_mismatch() -> None:
    result = evaluate_inventory_observation(
        build_pre_deposit_inventory_observation(occupied_slots=10, capacity=30),
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.INVENTORY_LAYOUT_MISMATCH,)


def test_evaluate_inventory_observation_current_provenance_rejects_mixed_frame() -> None:
    claimed_current = build_provenance(frame_id=1, cycle_id="cycle-a")
    actually_from = build_provenance(frame_id=1, cycle_id="cycle-b")
    result = evaluate_inventory_observation(
        build_pre_deposit_inventory_observation(occupied_slots=28, provenance=actually_from),
        evaluated_monotonic_s=0.0,
        current_provenance=claimed_current,
    )
    assert result.blockers == (BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH,)


def test_evaluate_inventory_observation_rejects_cross_cycle_advancing_frame() -> None:
    previous = build_provenance(frame_id=1, captured_monotonic_s=1.0, cycle_id="trusted-cycle")
    current = build_provenance(frame_id=2, captured_monotonic_s=2.0, cycle_id="foreign-cycle")
    result = evaluate_inventory_observation(
        build_pre_deposit_inventory_observation(occupied_slots=28, provenance=current),
        evaluated_monotonic_s=2.0,
        previous_provenance=previous,
    )
    assert result.blockers == (BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH,)


def test_evaluate_inventory_observation_rejects_wrong_current_provenance_type() -> None:
    with pytest.raises(TypeError, match="current_provenance must be an exact"):
        evaluate_inventory_observation(
            build_pre_deposit_inventory_observation(occupied_slots=28),
            evaluated_monotonic_s=0.0,
            current_provenance="not-a-provenance",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Constructor / defensive-branch validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("detector_id", ["", "   "])
def test_bank_detector_metadata_rejects_blank_detector_id(detector_id: str) -> None:
    with pytest.raises(ValueError, match="detector_id must be a non-empty string"):
        BankDetectorMetadata(detector_id=detector_id, version="1.0.0")


@pytest.mark.parametrize("version", ["", "   "])
def test_bank_detector_metadata_rejects_blank_version(version: str) -> None:
    with pytest.raises(ValueError, match="detector version must be a non-empty string"):
        BankDetectorMetadata(detector_id="d", version=version)


@pytest.mark.parametrize(
    ("detector_id", "version"),
    [
        (_OverloadedString("trusted"), "1"),
        ("trusted", _OverloadedString("1")),
    ],
)
def test_bank_detector_metadata_rejects_string_subclasses(detector_id: str, version: str) -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        BankDetectorMetadata(detector_id=detector_id, version=version)


class _RaisingMetadataDetector:
    @property
    def metadata(self) -> BankDetectorMetadata:
        raise RuntimeError("metadata unavailable")

    def observe(self, frame: Frame) -> object:
        raise AssertionError("must not be called")


def test_validate_bank_detector_wraps_metadata_property_exception() -> None:
    with pytest.raises(BankDetectorContractError, match="metadata could not be read"):
        validate_bank_detector(_RaisingMetadataDetector())


class _WrongMetadataTypeDetector:
    @property
    def metadata(self) -> object:
        return "not-metadata"

    def observe(self, frame: Frame) -> object:
        raise AssertionError("must not be called")


def test_validate_bank_detector_rejects_wrong_metadata_type() -> None:
    with pytest.raises(BankDetectorContractError, match="must be BankDetectorMetadata"):
        validate_bank_detector(_WrongMetadataTypeDetector())


def test_bank_perception_result_rejects_non_exact_interface_state() -> None:
    with pytest.raises(ValueError, match="interface_state must be an exact"):
        BankPerceptionResult(interface_state="open")  # type: ignore[arg-type]


def test_bank_perception_result_rejects_blockers_with_non_unknown_state() -> None:
    with pytest.raises(ValueError, match="a rejected bank reading must resolve to UNKNOWN"):
        BankPerceptionResult(
            interface_state=BankInterfaceState.OPEN,
            blockers=(BankingBlocker.BANK_STATE_UNKNOWN,),
        )


def test_bank_perception_result_rejects_duplicate_blockers() -> None:
    with pytest.raises(ValueError, match="blockers must be unique"):
        BankPerceptionResult(
            interface_state=BankInterfaceState.UNKNOWN,
            blockers=(BankingBlocker.BANK_STATE_UNKNOWN, BankingBlocker.BANK_STATE_UNKNOWN),
        )


def test_bank_perception_result_rejects_wrong_blocker_element_type() -> None:
    with pytest.raises(ValueError, match="blockers must be a tuple of exact BankingBlocker"):
        BankPerceptionResult(
            interface_state=BankInterfaceState.UNKNOWN,
            blockers=("not-a-blocker",),  # type: ignore[arg-type]
        )


def test_inventory_perception_result_rejects_non_exact_state() -> None:
    with pytest.raises(ValueError, match="state must be an exact InventoryState"):
        InventoryPerceptionResult(state="not-a-state")  # type: ignore[arg-type]


def test_inventory_perception_result_rejects_blockers_with_known_count() -> None:
    with pytest.raises(
        ValueError, match="a rejected inventory reading must resolve to an unknown count"
    ):
        InventoryPerceptionResult(
            state=InventoryState(occupied_slots=5, capacity=28, confidence=0.9),
            blockers=(BankingBlocker.INVENTORY_UNKNOWN,),
        )


def test_inventory_perception_result_rejects_wrong_blocker_element_type() -> None:
    with pytest.raises(ValueError, match="blockers must be a tuple of exact BankingBlocker"):
        InventoryPerceptionResult(
            state=InventoryState(occupied_slots=None, capacity=28, confidence=0.0),
            blockers=("not-a-blocker",),  # type: ignore[arg-type]
        )


def test_inventory_perception_result_rejects_duplicate_blockers() -> None:
    with pytest.raises(ValueError, match="blockers must be unique"):
        InventoryPerceptionResult(
            state=InventoryState(occupied_slots=None, capacity=28, confidence=0.0),
            blockers=(BankingBlocker.INVENTORY_UNKNOWN, BankingBlocker.INVENTORY_UNKNOWN),
        )


def test_evaluate_bank_observation_rejects_wrong_expected_checkpoint_type() -> None:
    with pytest.raises(TypeError, match="expected_checkpoint must be an exact"):
        evaluate_bank_observation(
            build_bank_observation(),
            expected_checkpoint="not-an-identity",  # type: ignore[arg-type]
            expected_profile=SYNTHETIC_BANK_PROFILE,
            expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
            evaluated_monotonic_s=0.0,
        )


def test_evaluate_bank_observation_rejects_wrong_expected_profile_type() -> None:
    with pytest.raises(TypeError, match="expected_profile must be an exact"):
        evaluate_bank_observation(
            build_bank_observation(),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile="not-a-profile",  # type: ignore[arg-type]
            expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
            evaluated_monotonic_s=0.0,
        )


def test_evaluate_bank_observation_rejects_wrong_expected_detector_type() -> None:
    with pytest.raises(TypeError, match="expected_detector must be an exact"):
        evaluate_bank_observation(
            build_bank_observation(),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            expected_detector="not-a-detector",  # type: ignore[arg-type]
            evaluated_monotonic_s=0.0,
        )


def test_evaluate_bank_observation_rejects_wrong_current_provenance_type() -> None:
    with pytest.raises(TypeError, match="current_provenance must be an exact"):
        evaluate_bank_observation(
            build_bank_observation(),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            expected_detector=SYNTHETIC_BANK_DETECTOR_METADATA,
            evaluated_monotonic_s=0.0,
            current_provenance="not-a-provenance",  # type: ignore[arg-type]
        )
