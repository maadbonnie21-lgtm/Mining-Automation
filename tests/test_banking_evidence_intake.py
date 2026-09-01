"""Tests for the immutable future bank evidence intake / reviewer design (Part D4).

Everything here is a synthetic package/verdict shape. No pixels, real or
synthetic, are collected or referenced -- only identity, hashing, and
binding rules.
"""

from __future__ import annotations

from dataclasses import fields, replace
from hashlib import sha256

import pytest

from mining_automation.banking import evidence_intake as evidence_intake_module
from mining_automation.banking.contracts import (
    BankCheckpointIdentity,
    BankingBlocker,
    BankProfileIdentity,
)
from mining_automation.banking.evidence_intake import (
    REQUIRED_BANK_EVIDENCE_CASES,
    BankEvidenceCase,
    FinalizedBankEvidencePackage,
    OperatorIntentLabel,
    ReviewedBankEvidenceCase,
    ReviewerVerdict,
    validate_release_evidence_case_batch,
)
from mining_automation.banking.testing import SYNTHETIC_BANK_CHECKPOINT, SYNTHETIC_BANK_PROFILE

_RAW_HASH = "a" * 64
_MANIFEST_HASH = "b" * 64

# The configurable engine is private so no caller can confuse a weakened
# policy result with the release-facing validator. Tests exercise it directly
# only to prove the fixed wrapper cannot inherit caller-controlled policy.
validate_evidence_case_batch = evidence_intake_module._validate_evidence_case_batch


def _digest_for(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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
    checkpoint: BankCheckpointIdentity = SYNTHETIC_BANK_CHECKPOINT,
    profile: BankProfileIdentity = SYNTHETIC_BANK_PROFILE,
    raw_sha256: str = _RAW_HASH,
    manifest_sha256: str = _MANIFEST_HASH,
    operator_label: OperatorIntentLabel | None = None,
    finalized_monotonic_s: float = 1.0,
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
    reviewed_monotonic_s: float = 2.0,
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
        else _verdict(bound_package_sha256=built_package.package_sha256)
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
    with pytest.raises(ValueError, match="labeled_monotonic_s must be an exact finite"):
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
    with pytest.raises(ValueError, match="finalized_monotonic_s must be an exact finite"):
        _package(finalized_monotonic_s=finalized_monotonic_s)  # type: ignore[arg-type]


def test_reviewer_verdict_accepts_valid_values() -> None:
    verdict = _verdict()
    assert verdict.accepted is True


def test_reviewer_verdict_reviewed_case_may_differ_from_operator_claim() -> None:
    """The whole point of separating operator intent from reviewer truth."""
    package = _package(operator_label=_operator_label(claimed_case=BankEvidenceCase.OPEN))
    verdict = _verdict(
        reviewed_case=BankEvidenceCase.CLOSED,
        bound_package_sha256=package.package_sha256,
    )
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
    with pytest.raises(ValueError, match="reviewed_monotonic_s must be an exact finite"):
        _verdict(reviewed_monotonic_s=reviewed_monotonic_s)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cryptographic package<->verdict binding (Part D4 core invariant)
# ---------------------------------------------------------------------------


def test_reviewed_case_accepts_matching_binding() -> None:
    case = _reviewed_case()
    assert case.verdict.bound_package_sha256 == case.package.package_sha256


def test_finalized_package_digest_is_deterministic_and_domain_separated() -> None:
    package = _package(
        operator_label=_operator_label(labeled_monotonic_s=1.0),
        finalized_monotonic_s=2.0,
    )
    equivalent = _package(
        operator_label=_operator_label(labeled_monotonic_s=1.0),
        finalized_monotonic_s=2.0,
    )

    assert package.package_sha256 == equivalent.package_sha256
    assert (
        package.package_sha256 == "b7fd51ae43a9736fc4dbf6a1b870620c1b6b09a9da65f56126538c695f14d6e4"
    )
    assert len(package.package_sha256) == 64
    assert package.package_sha256 not in {package.raw_sha256, package.manifest_sha256}


def test_reviewer_binding_rejects_raw_digest_without_package_metadata() -> None:
    package = _package()
    verdict = _verdict(bound_package_sha256=package.raw_sha256)

    with pytest.raises(ValueError, match=r"does not match package\.package_sha256"):
        ReviewedBankEvidenceCase(package=package, verdict=verdict)


def test_package_digest_binds_every_authoritative_metadata_field() -> None:
    package = _package(
        operator_label=_operator_label(labeled_monotonic_s=1.0),
        finalized_monotonic_s=10.0,
    )
    verdict = _verdict(
        bound_package_sha256=package.package_sha256,
        reviewed_monotonic_s=20.0,
    )
    metadata_mutations = (
        replace(package, package_id="pkg-2"),
        replace(
            package,
            checkpoint=replace(package.checkpoint, checkpoint_id="checkpoint-2"),
        ),
        replace(
            package,
            checkpoint=replace(package.checkpoint, location_id="location-2"),
        ),
        replace(package, profile=replace(package.profile, profile_id="profile-2")),
        replace(
            package,
            profile=replace(package.profile, profile_version="2.0.0"),
        ),
        replace(
            package,
            profile=replace(package.profile, schema_version=package.profile.schema_version + 1),
        ),
        replace(
            package,
            profile=replace(package.profile, frame_width=package.profile.frame_width + 1),
        ),
        replace(
            package,
            profile=replace(package.profile, frame_height=package.profile.frame_height + 1),
        ),
        replace(package, manifest_sha256="c" * 64),
        replace(
            package,
            operator_label=replace(package.operator_label, operator_id="operator-2"),
        ),
        replace(
            package,
            operator_label=replace(
                package.operator_label,
                claimed_case=BankEvidenceCase.CLOSED,
            ),
        ),
        replace(
            package,
            operator_label=replace(package.operator_label, note="a corrected label note"),
        ),
        replace(
            package,
            operator_label=replace(package.operator_label, labeled_monotonic_s=2.0),
        ),
        replace(package, finalized_monotonic_s=11.0),
    )

    for mutated in metadata_mutations:
        assert mutated.raw_sha256 == package.raw_sha256
        assert mutated.package_sha256 != package.package_sha256
        with pytest.raises(ValueError, match=r"does not match package\.package_sha256"):
            ReviewedBankEvidenceCase(package=mutated, verdict=verdict)

    raw_mutation = replace(package, raw_sha256="d" * 64)
    assert raw_mutation.package_sha256 != package.package_sha256
    with pytest.raises(ValueError, match=r"does not match package\.package_sha256"):
        ReviewedBankEvidenceCase(package=raw_mutation, verdict=verdict)


def test_canonical_digest_known_answer_and_field_schema_are_frozen() -> None:
    package = _package(
        operator_label=_operator_label(labeled_monotonic_s=1.0),
        finalized_monotonic_s=2.0,
    )
    assert (
        package.package_sha256 == "b7fd51ae43a9736fc4dbf6a1b870620c1b6b09a9da65f56126538c695f14d6e4"
    )
    assert {field.name for field in fields(FinalizedBankEvidencePackage)} == {
        "package_id",
        "checkpoint",
        "profile",
        "raw_sha256",
        "manifest_sha256",
        "operator_label",
        "finalized_monotonic_s",
    }
    assert {field.name for field in fields(OperatorIntentLabel)} == {
        "operator_id",
        "claimed_case",
        "note",
        "labeled_monotonic_s",
    }
    assert {field.name for field in fields(BankCheckpointIdentity)} == {
        "checkpoint_id",
        "location_id",
    }
    assert {field.name for field in fields(BankProfileIdentity)} == {
        "profile_id",
        "profile_version",
        "schema_version",
        "frame_width",
        "frame_height",
    }


def test_reviewed_case_rejects_mismatched_binding() -> None:
    package = _package(raw_sha256=_RAW_HASH)
    verdict = _verdict(bound_package_sha256="c" * 64)
    with pytest.raises(
        ValueError,
        match=r"verdict\.bound_package_sha256 does not match package\.package_sha256",
    ):
        ReviewedBankEvidenceCase(package=package, verdict=verdict)


@pytest.mark.parametrize(
    ("operator_id", "reviewer_id"),
    [
        ("same-actor", "same-actor"),
        (" Same-Actor ", "same-actor"),
        ("same-actor", "SAME-ACTOR"),
    ],
)
def test_reviewed_case_requires_independent_actor_identities(
    operator_id: str,
    reviewer_id: str,
) -> None:
    package = _package(
        operator_label=replace(_operator_label(), operator_id=operator_id),
    )
    verdict = _verdict(
        reviewer_id=reviewer_id,
        bound_package_sha256=package.package_sha256,
    )

    with pytest.raises(ValueError, match="must identify different actors"):
        ReviewedBankEvidenceCase(package=package, verdict=verdict)


def test_reviewed_case_normalizes_unicode_actor_identities() -> None:
    package = _package(
        operator_label=replace(_operator_label(), operator_id="\u00e9vidence-reviewer"),
    )
    verdict = _verdict(
        reviewer_id="e\u0301vidence-reviewer",
        bound_package_sha256=package.package_sha256,
    )
    with pytest.raises(ValueError, match="must identify different actors"):
        ReviewedBankEvidenceCase(package=package, verdict=verdict)


def test_finalized_package_rejects_finalization_before_operator_label() -> None:
    with pytest.raises(ValueError, match="must follow operator labeling"):
        _package(
            operator_label=_operator_label(labeled_monotonic_s=10.0),
            finalized_monotonic_s=9.0,
        )


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


def test_batch_revalidates_post_construction_package_forgery() -> None:
    case = _reviewed_case()
    object.__setattr__(case.package, "manifest_sha256", "c" * 64)

    with pytest.raises(ValueError, match=r"does not match package\.package_sha256"):
        validate_evidence_case_batch(
            (case,),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=10.0,
        )


def test_batch_revalidates_post_construction_nested_identity_forgery() -> None:
    isolated_checkpoint = replace(SYNTHETIC_BANK_CHECKPOINT, checkpoint_id="isolated")
    case = _reviewed_case(package=_package(checkpoint=isolated_checkpoint))
    object.__setattr__(case.package.checkpoint, "checkpoint_id", "   ")

    with pytest.raises(ValueError, match="checkpoint_id must be a non-empty string"):
        validate_release_evidence_case_batch(
            (case,),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=10.0,
        )


# ---------------------------------------------------------------------------
# validate_evidence_case_batch (adversarial batch checks)
# ---------------------------------------------------------------------------


def _full_required_batch() -> tuple[ReviewedBankEvidenceCase, ...]:
    cases = []
    for index, case_label in enumerate(BankEvidenceCase):
        package = _package(
            package_id=f"pkg-{index}",
            raw_sha256=_digest_for(f"raw-{index}"),
            manifest_sha256=_digest_for(f"manifest-{index}"),
            operator_label=_operator_label(
                claimed_case=case_label,
                labeled_monotonic_s=float(index),
            ),
            finalized_monotonic_s=float(index) + 1.0,
        )
        verdict = _verdict(
            reviewed_case=case_label,
            bound_package_sha256=package.package_sha256,
            reviewed_monotonic_s=float(index) + 2.0,
        )
        cases.append(ReviewedBankEvidenceCase(package=package, verdict=verdict))
    return tuple(cases)


def test_finalized_package_rejects_equal_label_and_finalization_time() -> None:
    with pytest.raises(ValueError, match="must follow operator labeling"):
        _package(
            operator_label=_operator_label(labeled_monotonic_s=1.0),
            finalized_monotonic_s=1.0,
        )


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


def test_validate_evidence_case_batch_rejects_raw_reuse_across_truth_cases() -> None:
    first_package = _package(
        package_id="pkg-open",
        manifest_sha256=_digest_for("manifest-open"),
        finalized_monotonic_s=1.0,
    )
    second_package = _package(
        package_id="pkg-closed",
        manifest_sha256=_digest_for("manifest-closed"),
        operator_label=_operator_label(claimed_case=BankEvidenceCase.CLOSED),
        finalized_monotonic_s=2.0,
    )
    assert first_package.raw_sha256 == second_package.raw_sha256
    assert first_package.package_sha256 != second_package.package_sha256
    cases = (
        _reviewed_case(
            package=first_package,
            verdict=_verdict(
                reviewed_case=BankEvidenceCase.OPEN,
                bound_package_sha256=first_package.package_sha256,
                reviewed_monotonic_s=3.0,
            ),
        ),
        _reviewed_case(
            package=second_package,
            verdict=_verdict(
                reviewed_case=BankEvidenceCase.CLOSED,
                bound_package_sha256=second_package.package_sha256,
                reviewed_monotonic_s=3.0,
            ),
        ),
    )

    blockers = validate_evidence_case_batch(
        cases,
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=4.0,
        required_cases=frozenset({BankEvidenceCase.OPEN, BankEvidenceCase.CLOSED}),
    )

    assert BankingBlocker.DUPLICATE_EVIDENCE_PACKAGE in blockers


def test_release_validator_rejects_one_raw_reused_under_every_truth_case() -> None:
    repeated_raw = _digest_for("one-raw-used-for-every-case")
    cases = []
    for index, truth_case in enumerate(BankEvidenceCase):
        package = _package(
            package_id=f"reused-raw-{index}",
            raw_sha256=repeated_raw,
            manifest_sha256=_digest_for(f"reused-raw-manifest-{index}"),
            operator_label=_operator_label(
                claimed_case=truth_case,
                labeled_monotonic_s=float(index),
            ),
            finalized_monotonic_s=float(index) + 1.0,
        )
        cases.append(
            _reviewed_case(
                package=package,
                verdict=_verdict(
                    reviewed_case=truth_case,
                    bound_package_sha256=package.package_sha256,
                    reviewed_monotonic_s=float(index) + 2.0,
                ),
            )
        )

    blockers = validate_release_evidence_case_batch(
        tuple(cases),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert BankingBlocker.DUPLICATE_EVIDENCE_PACKAGE in blockers


def test_validate_evidence_case_batch_rejects_package_digest_reuse_across_truth_cases() -> None:
    package = _package(finalized_monotonic_s=1.0)
    cases = (
        _reviewed_case(
            package=package,
            verdict=_verdict(
                reviewed_case=BankEvidenceCase.OPEN,
                bound_package_sha256=package.package_sha256,
                reviewed_monotonic_s=2.0,
            ),
        ),
        _reviewed_case(
            package=package,
            verdict=_verdict(
                reviewed_case=BankEvidenceCase.CLOSED,
                bound_package_sha256=package.package_sha256,
                reviewed_monotonic_s=2.0,
            ),
        ),
    )

    blockers = validate_evidence_case_batch(
        cases,
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=3.0,
        required_cases=frozenset({BankEvidenceCase.OPEN, BankEvidenceCase.CLOSED}),
    )

    assert BankingBlocker.DUPLICATE_EVIDENCE_PACKAGE in blockers


def test_validate_evidence_case_batch_rejects_foreign_checkpoint() -> None:
    foreign = _reviewed_case(package=_package(checkpoint=BankCheckpointIdentity("other", "other")))
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
    package = _package()
    rejected = _reviewed_case(
        package=package,
        verdict=_verdict(accepted=False, bound_package_sha256=package.package_sha256),
    )
    blockers = validate_evidence_case_batch(
        (rejected,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=100.0,
    )
    assert BankingBlocker.REJECTED_EVIDENCE_PACKAGE_INCLUDED in blockers


def test_reviewed_case_rejects_reviewer_verdict_preceding_finalization() -> None:
    package = _package(finalized_monotonic_s=10.0)
    verdict = _verdict(
        bound_package_sha256=package.package_sha256,
        reviewed_monotonic_s=5.0,
    )
    with pytest.raises(ValueError, match="must follow package finalization"):
        ReviewedBankEvidenceCase(package=package, verdict=verdict)


def test_reviewed_case_rejects_equal_finalization_and_review_time() -> None:
    package = _package(finalized_monotonic_s=10.0)
    verdict = _verdict(
        bound_package_sha256=package.package_sha256,
        reviewed_monotonic_s=10.0,
    )
    with pytest.raises(ValueError, match="must follow package finalization"):
        ReviewedBankEvidenceCase(package=package, verdict=verdict)


def test_validate_evidence_case_batch_rejects_stale_package() -> None:
    case = _reviewed_case(package=_package(finalized_monotonic_s=1.0))
    blockers = validate_evidence_case_batch(
        (case,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=1_000_000.0,
        max_age_s=1.0,
    )
    assert BankingBlocker.EVIDENCE_PACKAGE_STALE in blockers


@pytest.mark.parametrize("evaluated_monotonic_s", [None, float("nan"), float("inf"), "bad"])
def test_validate_evidence_case_batch_rejects_invalid_evaluation_time(
    evaluated_monotonic_s: object,
) -> None:
    blockers = validate_evidence_case_batch(
        _full_required_batch(),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=evaluated_monotonic_s,
    )

    assert BankingBlocker.EVALUATION_TIME_INVALID in blockers


@pytest.mark.parametrize(
    ("labeled", "finalized", "reviewed", "evaluated"),
    [
        (8.0, 9.0, 10.0, 7.0),
        (1.0, 9.0, 10.0, 8.0),
        (1.0, 2.0, 10.0, 9.0),
    ],
    ids=("label-and-successors", "finalization-and-review", "review-only"),
)
def test_validate_evidence_case_batch_rejects_each_future_chronology_stage(
    labeled: float,
    finalized: float,
    reviewed: float,
    evaluated: float,
) -> None:
    package = _package(
        operator_label=_operator_label(labeled_monotonic_s=labeled),
        finalized_monotonic_s=finalized,
    )
    case = _reviewed_case(
        package=package,
        verdict=_verdict(
            bound_package_sha256=package.package_sha256,
            reviewed_monotonic_s=reviewed,
        ),
    )

    blockers = validate_evidence_case_batch(
        (case,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=evaluated,
        required_cases=frozenset({BankEvidenceCase.OPEN}),
    )

    assert BankingBlocker.EVIDENCE_FROM_FUTURE in blockers


@pytest.mark.parametrize("max_age_s", [-1.0, float("nan"), float("inf"), True, "bad"])
def test_validate_evidence_case_batch_rejects_invalid_freshness_policy(
    max_age_s: object,
) -> None:
    with pytest.raises(ValueError, match="max_age_s must be a finite non-negative number"):
        validate_evidence_case_batch(
            (),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=0.0,
            max_age_s=max_age_s,  # type: ignore[arg-type]
        )


def test_validate_evidence_case_batch_rejects_invalid_required_cases_policy() -> None:
    with pytest.raises(TypeError, match="required_cases must be a frozenset"):
        validate_evidence_case_batch(
            (),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=0.0,
            required_cases={BankEvidenceCase.OPEN},  # type: ignore[arg-type]
        )


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


# ---------------------------------------------------------------------------
# Fixed source-owned release validation
# ---------------------------------------------------------------------------


def test_validate_release_evidence_case_batch_accepts_full_fresh_coverage() -> None:
    assert (
        validate_release_evidence_case_batch(
            _full_required_batch(),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=100.0,
        )
        == ()
    )


def test_release_validator_does_not_accept_policy_overrides() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'required_cases'"):
        validate_release_evidence_case_batch(
            _full_required_batch(),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=100.0,
            required_cases=frozenset({BankEvidenceCase.OPEN}),  # type: ignore[call-arg]
        )

    with pytest.raises(TypeError, match="unexpected keyword argument 'max_age_s'"):
        validate_release_evidence_case_batch(
            _full_required_batch(),
            expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
            expected_profile=SYNTHETIC_BANK_PROFILE,
            evaluated_monotonic_s=100.0,
            max_age_s=1_000_000_000.0,  # type: ignore[call-arg]
        )


def test_release_validator_ignores_mutated_public_policy_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evidence_intake_module,
        "REQUIRED_BANK_EVIDENCE_CASES",
        frozenset({BankEvidenceCase.ARRIVAL}),
    )
    monkeypatch.setattr(evidence_intake_module, "MAX_EVIDENCE_PACKAGE_AGE_S", 1_000_000_000.0)

    blockers = validate_release_evidence_case_batch(
        _full_required_batch()[:-1],
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=1_000_000.0,
    )

    assert BankingBlocker.MISSING_REQUIRED_EVIDENCE_CASE in blockers
    assert BankingBlocker.EVIDENCE_PACKAGE_STALE in blockers


def test_private_policy_engine_is_absent_from_supported_exports() -> None:
    one_case = _full_required_batch()[0]
    diagnostic = validate_evidence_case_batch(
        (one_case,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=1_000_000.0,
        required_cases=frozenset({one_case.verdict.reviewed_case}),
        max_age_s=2_000_000.0,
    )
    release = validate_release_evidence_case_batch(
        (one_case,),
        expected_checkpoint=SYNTHETIC_BANK_CHECKPOINT,
        expected_profile=SYNTHETIC_BANK_PROFILE,
        evaluated_monotonic_s=1_000_000.0,
    )

    assert diagnostic == ()
    assert BankingBlocker.MISSING_REQUIRED_EVIDENCE_CASE in release
    assert BankingBlocker.EVIDENCE_PACKAGE_STALE in release
    assert "validate_evidence_case_batch" not in evidence_intake_module.__all__


def test_canonical_digest_normalizes_signed_zero() -> None:
    positive = _package(
        operator_label=_operator_label(labeled_monotonic_s=0.0),
        finalized_monotonic_s=1.0,
    )
    negative = _package(
        operator_label=_operator_label(labeled_monotonic_s=-0.0),
        finalized_monotonic_s=1.0,
    )
    assert positive.package_sha256 == negative.package_sha256
    assert evidence_intake_module._canonical_float_hex(0.0) == (
        evidence_intake_module._canonical_float_hex(-0.0)
    )


@pytest.mark.parametrize(
    ("constructor", "value"),
    [
        (_operator_label, 2**53),
        (_package, 2**53),
        (_verdict, 2**53),
    ],
)
def test_evidence_timestamps_reject_non_float_precision_aliases(
    constructor: object, value: int
) -> None:
    if constructor is _operator_label:
        with pytest.raises(ValueError, match="exact finite"):
            _operator_label(labeled_monotonic_s=value)  # type: ignore[arg-type]
    elif constructor is _package:
        with pytest.raises(ValueError, match="exact finite"):
            _package(finalized_monotonic_s=value)  # type: ignore[arg-type]
    else:
        with pytest.raises(ValueError, match="exact finite"):
            _verdict(reviewed_monotonic_s=value)  # type: ignore[arg-type]
