"""Tests for the immutable future bank evidence intake / reviewer design (Part D4).

Everything here is a synthetic package/verdict shape. No pixels, real or
synthetic, are collected or referenced -- only identity, hashing, and
binding rules.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mining_automation.banking.contracts import BankingBlocker
from mining_automation.banking.evidence_intake import (
    REQUIRED_BANK_EVIDENCE_CASES,
    BankEvidenceCase,
    FinalizedBankEvidencePackage,
    OperatorIntentLabel,
    ReviewedBankEvidenceCase,
    ReviewerVerdict,
    validate_evidence_case_batch,
)
from mining_automation.banking.testing import SYNTHETIC_BANK_CHECKPOINT, SYNTHETIC_BANK_PROFILE

_RAW_HASH = "a" * 64
_MANIFEST_HASH = "b" * 64


def _operator_label(
    *, claimed_case: BankEvidenceCase = BankEvidenceCase.OPEN, labeled_monotonic_s: float = 0.0
) -> OperatorIntentLabel:
    return OperatorIntentLabel(
        operator_id="operator-1",
        claimed_case=claimed_case,
        note="looks open to me",
        labeled_monotonic_s=labeled_monotonic_s,
    )


def _package(
    *,
    package_id: str = "pkg-1",
    checkpoint=SYNTHETIC_BANK_CHECKPOINT,
    profile=SYNTHETIC_BANK_PROFILE,
    raw_sha256: str = _RAW_HASH,
    manifest_sha256: str = _MANIFEST_HASH,
    operator_label: OperatorIntentLabel | None = None,
    finalized_monotonic_s: float = 0.0,
) -> FinalizedBankEvidencePackage:
    return FinalizedBankEvidencePackage(
        package_id=package_id,
        checkpoint=checkpoint,
        profile=profile,
        raw_sha256=raw_sha256,
        manifest_sha256=manifest_sha256,
        operator_label=operator_label if operator_label is not None else _operator_label(),
        finalized_monotonic_s=finalized_monotonic_s,
    )


def _verdict(
    *,
    reviewer_id: str = "reviewer-1",
    accepted: bool = True,
    reviewed_case: BankEvidenceCase = BankEvidenceCase.OPEN,
    bound_package_sha256: str = _RAW_HASH,
    reviewed_monotonic_s: float = 1.0,
) -> ReviewerVerdict:
    return ReviewerVerdict(
        reviewer_id=reviewer_id,
        accepted=accepted,
        reviewed_case=reviewed_case,
        bound_package_sha256=bound_package_sha256,
        reviewed_monotonic_s=reviewed_monotonic_s,
    )


def _reviewed_case(
    *, package: FinalizedBankEvidencePackage | None = None, verdict: ReviewerVerdict | None = None
) -> ReviewedBankEvidenceCase:
    built_package = package if package is not None else _package()
    built_verdict = (
        verdict
        if verdict is not None
        else _verdict(bound_package_sha256=built_package.raw_sha256)
    )
    return ReviewedBankEvidenceCase(package=built_package, verdict=built_verdict)


# ---------------------------------------------------------------------------
# Typed contract construction
# ---------------------------------------------------------------------------


def test_operator_intent_label_accepts_valid_values() -> None:
    label = _operator_label()
    assert label.claimed_case is BankEvidenceCase.OPEN


@pytest.mark.parametrize("operator_id", ["", "   "])
def test_operator_intent_label_rejects_blank_operator_id(operator_id: str) -> None:
    with pytest.raises(ValueError, match="operator_id must be a non-empty string"):
        OperatorIntentLabel(
            operator_id=operator_id,
            claimed_case=BankEvidenceCase.OPEN,
            note="",
            labeled_monotonic_s=0.0,
        )


def test_operator_intent_label_rejects_non_exact_claimed_case() -> None:
    with pytest.raises(ValueError, match="claimed_case must be an exact BankEvidenceCase"):
        OperatorIntentLabel(
            operator_id="op",
            claimed_case="open",  # type: ignore[arg-type]
            note="",
            labeled_monotonic_s=0.0,
        )


def test_operator_intent_label_rejects_non_string_note() -> None:
    with pytest.raises(ValueError, match="note must be a string"):
        OperatorIntentLabel(
            operator_id="op",
            claimed_case=BankEvidenceCase.OPEN,
            note=123,  # type: ignore[arg-type]
            labeled_monotonic_s=0.0,
        )


@pytest.mark.parametrize("labeled_monotonic_s", [float("nan"), float("inf"), "not-a-number"])
def test_operator_intent_label_rejects_invalid_labeled_time(labeled_monotonic_s: object) -> None:
    with pytest.raises(ValueError, match="labeled_monotonic_s must be a finite number"):
        OperatorIntentLabel(
            operator_id="op",
            claimed_case=BankEvidenceCase.OPEN,
            note="",
            labeled_monotonic_s=labeled_monotonic_s,  # type: ignore[arg-type]
        )


def test_finalized_package_accepts_valid_values() -> None:
    package = _package()
    assert package.raw_sha256 == _RAW_HASH


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("raw_sha256", "not-hex", "raw_sha256 must be a 64-character lowercase hex digest"),
        (
            "manifest_sha256",
            "A" * 64,
            "manifest_sha256 must be a 64-character lowercase hex digest",
        ),
        ("package_id", "", "package_id must be a non-empty string"),
    ],
)
def test_finalized_package_rejects_invalid_fields(
    field_name: str, value: str, message: str
) -> None:
    kwargs: dict[str, object] = {
        "package_id": "pkg-1",
        "checkpoint": SYNTHETIC_BANK_CHECKPOINT,
        "profile": SYNTHETIC_BANK_PROFILE,
        "raw_sha256": _RAW_HASH,
        "manifest_sha256": _MANIFEST_HASH,
        "operator_label": _operator_label(),
        "finalized_monotonic_s": 0.0,
    }
    kwargs[field_name] = value
    with pytest.raises(ValueError, match=message):
        FinalizedBankEvidencePackage(**kwargs)  # type: ignore[arg-type]


def test_finalized_package_rejects_non_exact_operator_label() -> None:
    with pytest.raises(ValueError, match="operator_label must be an exact OperatorIntentLabel"):
        _package(operator_label="not-a-label")  # type: ignore[arg-type]


def test_finalized_package_rejects_non_exact_checkpoint() -> None:
    with pytest.raises(ValueError, match="checkpoint must be an exact BankCheckpointIdentity"):
        _package(checkpoint="not-a-checkpoint")  # type: ignore[arg-type]


def test_finalized_package_rejects_non_exact_profile() -> None:
    with pytest.raises(ValueError, match="profile must be an exact BankProfileIdentity"):
        _package(profile="not-a-profile")  # type: ignore[arg-type]


@pytest.mark.parametrize("finalized_monotonic_s", [float("nan"), float("inf"), "not-a-number"])
def test_finalized_package_rejects_invalid_finalized_time(finalized_monotonic_s: object) -> None:
    with pytest.raises(ValueError, match="finalized_monotonic_s must be a finite number"):
        _package(finalized_monotonic_s=finalized_monotonic_s)  # type: ignore[arg-type]


def test_reviewer_verdict_accepts_valid_values() -> None:
    verdict = _verdict()
    assert verdict.accepted is True


def test_reviewer_verdict_reviewed_case_may_differ_from_operator_claim() -> None:
    """The whole point of separating operator intent from reviewer truth."""
    package = _package(operator_label=_operator_label(claimed_case=BankEvidenceCase.OPEN))
    verdict = _verdict(reviewed_case=BankEvidenceCase.CLOSED, bound_package_sha256=package.raw_sha256)
    case = ReviewedBankEvidenceCase(package=package, verdict=verdict)
    assert case.package.operator_label.claimed_case is BankEvidenceCase.OPEN
    assert case.verdict.reviewed_case is BankEvidenceCase.CLOSED


def test_reviewer_verdict_rejects_non_exact_reviewed_case() -> None:
    with pytest.raises(ValueError, match="reviewed_case must be an exact BankEvidenceCase"):
        _verdict(reviewed_case="open")  # type: ignore[arg-type]


def test_reviewer_verdict_rejects_invalid_bound_hash() -> None:
    with pytest.raises(
        ValueError, match="bound_package_sha256 must be a 64-character lowercase hex digest"
    ):
        _verdict(bound_package_sha256="not-hex")


def test_reviewer_verdict_rejects_non_boolean_accepted() -> None:
    with pytest.raises(ValueError, match="accepted must be a boolean"):
        _verdict(accepted="yes")  # type: ignore[arg-type]


@pytest.mark.parametrize("reviewed_monotonic_s", [float("nan"), float("inf"), "not-a-number"])
def test_reviewer_verdict_rejects_invalid_reviewed_time(reviewed_monotonic_s: object) -> None:
    with pytest.raises(ValueError, match="reviewed_monotonic_s must be a finite number"):
        _verdict(reviewed_monotonic_s=reviewed_monotonic_s)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cryptographic package<->verdict binding (Part D4 core invariant)
# ---------------------------------------------------------------------------


def test_reviewed_case_accepts_matching_binding() -> None:
    case = _reviewed_case()
    assert case.verdict.bound_package_sha256 == case.package.raw_sha256


def test_reviewed_case_rejects_mismatched_binding() -> None:
    package = _package(raw_sha256=_RAW_HASH)
    verdict = _verdict(bound_package_sha256="c" * 64)
    with pytest.raises(ValueError, match="verdict.bound_package_sha256 does not match"):
        ReviewedBankEvidenceCase(package=package, verdict=verdict)


def test_reviewed_case_rejects_non_exact_package_type() -> None:
    with pytest.raises(ValueError, match="package must be an exact FinalizedBankEvidencePackage"):
        ReviewedBankEvidenceCase(package="not-a-package", verdict=_verdict())  # type: ignore[arg-type]


def test_reviewed_case_rejects_non_exact_verdict_type() -> None:
    with pytest.raises(ValueError, match="verdict must be an exact ReviewerVerdict"):
        ReviewedBankEvidenceCase(package=_package(), verdict="not-a-verdict")  # type: ignore[arg-type]


def test_reviewed_case_is_frozen_no_mutation_path() -> None:
    """No update/replace path exists -- a correction requires a brand-new case."""
    case = _reviewed_case()
    with pytest.raises(AttributeError):
        case.verdict = _verdict()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# validate_evidence_case_batch (adversarial batch checks)
# ---------------------------------------------------------------------------


def _full_required_batch() -> tuple[ReviewedBankEvidenceCase, ...]:
    cases = []
    for index, case_label in enumerate(BankEvidenceCase):
        package = _package(package_id=f"pkg-{index}", finalized_monotonic_s=float(index))
        verdict = _verdict(
            reviewed_case=case_label,
            bound_package_sha256=package.raw_sha256,
            reviewed_monotonic_s=float(index) + 1.0,
        )
        cases.append(ReviewedBankEvidenceCase(package=package, verdict=verdict))
    return tuple(cases)


def test_validate_evidence_case_batch_accepts_full_coverage() -> None:
    blockers = validate_evidence_case_batch(
        _full_required_batch(),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert blockers == ()


def test_validate_evidence_case_batch_rejects_missing_required_case() -> None:
    partial = _full_required_batch()[:-1]
    blockers = validate_evidence_case_batch(
        partial,
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert BankingBlocker.MISSING_REQUIRED_EVIDENCE_CASE in blockers


def test_validate_evidence_case_batch_rejects_duplicate_package_id() -> None:
    case = _reviewed_case()
    blockers = validate_evidence_case_batch(
        (case, case),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert BankingBlocker.DUPLICATE_EVIDENCE_PACKAGE in blockers


def test_validate_evidence_case_batch_rejects_foreign_checkpoint() -> None:
    from mining_automation.banking.contracts import BankCheckpointIdentity

    foreign = _reviewed_case(
        package=_package(checkpoint=BankCheckpointIdentity("other", "other"))
    )
    blockers = validate_evidence_case_batch(
        (foreign,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH in blockers


def test_validate_evidence_case_batch_rejects_foreign_profile() -> None:
    foreign_profile = replace(SYNTHETIC_BANK_PROFILE, profile_version="9.9.9")
    foreign = _reviewed_case(package=_package(profile=foreign_profile))
    blockers = validate_evidence_case_batch(
        (foreign,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert BankingBlocker.BANK_PROFILE_MISMATCH in blockers


def test_validate_evidence_case_batch_rejects_included_rejected_verdict() -> None:
    rejected = _reviewed_case(verdict=_verdict(accepted=False))
    blockers = validate_evidence_case_batch(
        (rejected,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert BankingBlocker.REJECTED_EVIDENCE_PACKAGE_INCLUDED in blockers


def test_validate_evidence_case_batch_rejects_reviewer_verdict_preceding_finalization() -> None:
    package = _package(finalized_monotonic_s=10.0)
    verdict = _verdict(bound_package_sha256=package.raw_sha256, reviewed_monotonic_s=5.0)
    case = ReviewedBankEvidenceCase(package=package, verdict=verdict)
    blockers = validate_evidence_case_batch(
        (case,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert BankingBlocker.REVIEWER_VERDICT_PRECEDES_FINALIZATION in blockers


def test_validate_evidence_case_batch_rejects_stale_package() -> None:
    case = _reviewed_case(package=_package(finalized_monotonic_s=0.0))
    blockers = validate_evidence_case_batch(
        (case,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=1_000_000.0,
        max_age_s=1.0,
    )
    assert BankingBlocker.EVIDENCE_PACKAGE_STALE in blockers


def test_validate_evidence_case_batch_rejects_wrong_cases_type() -> None:
    with pytest.raises(TypeError, match="cases must be a tuple of exact ReviewedBankEvidenceCase"):
        validate_evidence_case_batch(
            ["not-a-case"],  # type: ignore[arg-type]
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=0.0,
        )


def test_validate_evidence_case_batch_rejects_wrong_expected_checkpoint_type() -> None:
    with pytest.raises(TypeError, match="expected_checkpoint must be an exact"):
        validate_evidence_case_batch(
            (),
            expected_checkpoint="not-an-identity",  # type: ignore[arg-type]
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=0.0,
        )


def test_validate_evidence_case_batch_rejects_wrong_expected_profile_type() -> None:
    with pytest.raises(TypeError, match="expected_profile must be an exact"):
        validate_evidence_case_batch(
            (),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile="not-a-profile",  # type: ignore[arg-type]
            evaluated_monotonic_s=0.0,
        )


def test_required_bank_evidence_cases_covers_every_enum_member() -> None:
    assert REQUIRED_BANK_EVIDENCE_CASES == frozenset(BankEvidenceCase)
