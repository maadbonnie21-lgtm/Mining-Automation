from __future__ import annotations

from dataclasses import replace

import pytest

from mining_automation.banking.contracts import BankInterfaceState
from mining_automation.banking.passive_campaign import (
    BANK_EVIDENCE_CASE_ORDER,
    BankEvidenceCampaignIdentity,
    BankEvidenceCampaignPlan,
    BankEvidenceCaseRole,
    BankEvidenceCaseSpec,
    BankEvidenceObservation,
    PassiveBankCampaignPhase,
    PassiveBankCampaignSequencer,
)
from mining_automation.capture import PixelFormat


def _sha(label: str) -> str:
    import hashlib

    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _plan() -> BankEvidenceCampaignPlan:
    identity = BankEvidenceCampaignIdentity(
        campaign_id="synthetic-bank-campaign-1",
        session_id="synthetic-bank-session-1",
        capture_source_id="synthetic-bank-source",
        capture_build_id="synthetic-bank-build",
        capture_build_sha256=_sha("build"),
        capture_configuration_sha256=_sha("config"),
        capture_environment_sha256=_sha("environment"),
        supported_location_id="synthetic-supported-bank",
        frame_width=1005,
        frame_height=1078,
        pixel_format=PixelFormat.BGRA8888,
    )
    return BankEvidenceCampaignPlan(
        identity=identity,
        cases=tuple(
            BankEvidenceCaseSpec(index, f"bank-case-{index:02d}", role)
            for index, role in enumerate(BANK_EVIDENCE_CASE_ORDER, start=1)
        ),
    )


def _observation(plan: BankEvidenceCampaignPlan, ordinal: int) -> BankEvidenceObservation:
    spec = plan.cases[ordinal - 1]
    bank_state = BankInterfaceState.UNKNOWN
    slots: int | None = None
    confidence = 0.0
    if spec.role is BankEvidenceCaseRole.OPEN:
        bank_state = BankInterfaceState.OPEN
        confidence = 0.95
    elif spec.role is BankEvidenceCaseRole.CLOSED:
        bank_state = BankInterfaceState.CLOSED
        confidence = 0.95
    elif spec.role is BankEvidenceCaseRole.NON_EMPTY_BEFORE_DEPOSIT:
        bank_state = BankInterfaceState.OPEN
        slots = 28
        confidence = 0.95
    elif spec.role is BankEvidenceCaseRole.EMPTY_AFTER_DEPOSIT:
        bank_state = BankInterfaceState.OPEN
        slots = 0
        confidence = 0.95
    elif spec.role is BankEvidenceCaseRole.NON_EMPTY_AFTER_DEPOSIT:
        bank_state = BankInterfaceState.OPEN
        slots = 17
        confidence = 0.95
    observed_location = (
        "synthetic-wrong-bank"
        if spec.role is BankEvidenceCaseRole.WRONG_LOCATION
        else plan.identity.supported_location_id
    )
    return BankEvidenceObservation(
        case_id=spec.case_id,
        role=spec.role,
        campaign_id=plan.identity.campaign_id,
        session_id=plan.identity.session_id,
        capture_source_id=plan.identity.capture_source_id,
        capture_build_sha256=plan.identity.capture_build_sha256,
        capture_configuration_sha256=plan.identity.capture_configuration_sha256,
        capture_environment_sha256=plan.identity.capture_environment_sha256,
        observed_location_id=observed_location,
        frame_id=ordinal,
        cycle_id=f"synthetic-bank-cycle-{ordinal}",
        captured_monotonic_s=float(ordinal),
        frame_width=plan.identity.frame_width,
        frame_height=plan.identity.frame_height,
        pixel_format=plan.identity.pixel_format,
        frame_sha256=_sha(f"frame-{ordinal}"),
        bank_state=bank_state,
        inventory_occupied_slots=slots,
        confidence=confidence,
    )


def test_fixed_campaign_finalizes_deterministically_without_authority() -> None:
    plan = _plan()
    first = PassiveBankCampaignSequencer(plan)
    second = PassiveBankCampaignSequencer(plan)
    for ordinal in range(1, len(plan.cases) + 1):
        observation = _observation(plan, ordinal)
        first.append(observation)
        second.append(observation)
    assert first.phase is PassiveBankCampaignPhase.COMPLETE
    package_1, receipt_1 = first.finalize()
    package_2, receipt_2 = second.finalize()
    assert first.phase is PassiveBankCampaignPhase.FINALIZED
    assert package_1 == package_2
    assert receipt_1 == receipt_2
    assert package_1.package_sha256 == receipt_1.package_sha256
    assert package_1.release_eligible is False
    assert package_1.input_authority is False
    assert receipt_1.release_eligible is False
    assert receipt_1.input_authority is False
    assert plan.retries_allowed is False
    assert plan.input_authority is False
    assert plan.activation_allowed is False


@pytest.mark.parametrize(
    ("role", "field", "value", "message"),
    (
        (BankEvidenceCaseRole.OPEN, "bank_state", BankInterfaceState.CLOSED, "OPEN"),
        (BankEvidenceCaseRole.CLOSED, "bank_state", BankInterfaceState.OPEN, "CLOSED"),
        (BankEvidenceCaseRole.UNKNOWN, "bank_state", BankInterfaceState.OPEN, "UNKNOWN"),
        (BankEvidenceCaseRole.OBSTRUCTION, "bank_state", BankInterfaceState.OPEN, "UNKNOWN"),
        (BankEvidenceCaseRole.AMBIGUITY, "bank_state", BankInterfaceState.CLOSED, "UNKNOWN"),
        (
            BankEvidenceCaseRole.NON_EMPTY_BEFORE_DEPOSIT,
            "inventory_occupied_slots",
            0,
            "non-empty",
        ),
        (
            BankEvidenceCaseRole.EMPTY_AFTER_DEPOSIT,
            "inventory_occupied_slots",
            1,
            "empty-after",
        ),
        (
            BankEvidenceCaseRole.NON_EMPTY_AFTER_DEPOSIT,
            "inventory_occupied_slots",
            0,
            "non-empty-after",
        ),
    ),
)
def test_each_bank_and_inventory_case_is_fail_closed(
    role: BankEvidenceCaseRole,
    field: str,
    value: object,
    message: str,
) -> None:
    plan = _plan()
    ordinal = BANK_EVIDENCE_CASE_ORDER.index(role) + 1
    valid = _observation(plan, ordinal)
    with pytest.raises(ValueError, match=message):
        replace(valid, **{field: value})


def test_wrong_location_case_must_be_foreign_and_all_others_must_match() -> None:
    plan = _plan()
    wrong_ordinal = BANK_EVIDENCE_CASE_ORDER.index(BankEvidenceCaseRole.WRONG_LOCATION) + 1
    wrong = _observation(plan, wrong_ordinal)
    sequencer = PassiveBankCampaignSequencer(plan)
    for ordinal in range(1, wrong_ordinal):
        sequencer.append(_observation(plan, ordinal))
    with pytest.raises(ValueError, match="did not use a foreign"):
        sequencer.append(
            replace(wrong, observed_location_id=plan.identity.supported_location_id)
        )
    assert sequencer.phase is PassiveBankCampaignPhase.STOPPED

    ordinary = PassiveBankCampaignSequencer(plan)
    with pytest.raises(ValueError, match="location differs"):
        ordinary.append(
            replace(_observation(plan, 1), observed_location_id="synthetic-foreign")
        )
    assert ordinary.phase is PassiveBankCampaignPhase.STOPPED


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("session_id", "foreign-session", "session identity differs"),
        ("capture_source_id", "foreign-source", "capture source identity differs"),
        ("capture_build_sha256", _sha("foreign-build"), "capture build identity differs"),
        (
            "capture_configuration_sha256",
            _sha("foreign-config"),
            "capture configuration identity differs",
        ),
        (
            "capture_environment_sha256",
            _sha("foreign-env"),
            "capture environment identity differs",
        ),
        ("frame_width", 1004, "frame contract differs"),
    ),
)
def test_mixed_or_foreign_campaign_evidence_stops_absorbingly(
    field: str,
    value: object,
    message: str,
) -> None:
    plan = _plan()
    sequencer = PassiveBankCampaignSequencer(plan)
    with pytest.raises(ValueError, match=message):
        sequencer.append(replace(_observation(plan, 1), **{field: value}))
    assert sequencer.phase is PassiveBankCampaignPhase.STOPPED
    with pytest.raises(RuntimeError, match="not collecting"):
        sequencer.append(_observation(plan, 1))
    with pytest.raises(RuntimeError, match="complete"):
        sequencer.finalize()


def test_out_of_order_duplicate_stale_and_same_cycle_evidence_stop() -> None:
    plan = _plan()
    out_of_order = PassiveBankCampaignSequencer(plan)
    with pytest.raises(ValueError, match="exact next"):
        out_of_order.append(_observation(plan, 2))

    stale_frame = PassiveBankCampaignSequencer(plan)
    stale_frame.append(_observation(plan, 1))
    with pytest.raises(ValueError, match="frame_id must be strictly newer"):
        stale_frame.append(replace(_observation(plan, 2), frame_id=1))

    stale_time = PassiveBankCampaignSequencer(plan)
    stale_time.append(_observation(plan, 1))
    with pytest.raises(ValueError, match="capture time must be strictly newer"):
        stale_time.append(replace(_observation(plan, 2), captured_monotonic_s=1.0))

    same_cycle = PassiveBankCampaignSequencer(plan)
    same_cycle.append(_observation(plan, 1))
    with pytest.raises(ValueError, match="cycle_id must be fresh"):
        same_cycle.append(replace(_observation(plan, 2), cycle_id="synthetic-bank-cycle-1"))


def test_finalized_package_detects_post_construction_tamper() -> None:
    plan = _plan()
    sequencer = PassiveBankCampaignSequencer(plan)
    for ordinal in range(1, len(plan.cases) + 1):
        sequencer.append(_observation(plan, ordinal))
    package, _ = sequencer.finalize()
    object.__setattr__(package, "package_sha256", _sha("tampered"))
    with pytest.raises(ValueError, match="digest differs"):
        type(package).__post_init__(package)
