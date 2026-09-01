"""Tests for DepositResultEvidenceRecord: deposit-result verification packaging.

A batch containing *some* reviewed NON_EMPTY_BEFORE_DEPOSIT case and *some*
reviewed EMPTY_AFTER_DEPOSIT case is a weaker claim than "this exact deposit
attempt went from non-empty to empty" -- the two samples could come from
unrelated visits, or the receipt that supposedly caused the transition could
belong to a different attempt entirely. ``DepositResultEvidenceRecord`` makes
and validates the stronger, causally-bound claim required by all seven
conditions in the task spec:

1. the reviewed pre-deposit inventory was NON-EMPTY;
2. it belongs to one exact bank visit/cycle/checkpoint/profile;
3. one exact DepositAttemptReceipt occurred after that pre-deposit evidence;
4. the post-deposit inventory evidence belongs to that SAME visit/cycle;
5. the post-deposit observation is strictly after that exact receipt and
   within the causal freshness window;
6. the post-deposit inventory is verified EMPTY;
7. cross-visit, cross-session, wrong receipt, replayed receipt, stale
   evidence, swapped ordering, duplicate raw/package content, or a missing
   receipt cannot satisfy deposit-result coverage.

Everything here is synthetic architecture-test scaffolding. No pixels, real
or synthetic, are collected or referenced.
"""

from __future__ import annotations

import pytest

from mining_automation.banking.attempts import MAX_ATTEMPT_RECEIPT_AGE_S, DepositAttemptReceipt
from mining_automation.banking.contracts import BankCheckpointIdentity
from mining_automation.banking.evidence_intake import (
    BankEvidenceCase,
    DepositResultEvidenceRecord,
    FinalizedBankEvidencePackage,
    OperatorIntentLabel,
    ReviewedBankEvidenceCase,
    ReviewerVerdict,
)
from mining_automation.banking.testing import (
    SYNTHETIC_BANK_CHECKPOINT,
    SYNTHETIC_BANK_PROFILE,
    build_provenance,
)

_PRE_RAW_HASH = "1" * 64
_POST_RAW_HASH = "2" * 64
_MANIFEST_HASH = "b" * 64


def _operator_label(
    *, claimed_case: BankEvidenceCase, labeled_monotonic_s: float = 0.0
) -> OperatorIntentLabel:
    return OperatorIntentLabel(
        operator_id="operator-1",
        claimed_case=claimed_case,
        note="",
        labeled_monotonic_s=labeled_monotonic_s,
    )


def _package(
    *,
    package_id: str,
    raw_sha256: str,
    claimed_case: BankEvidenceCase,
    checkpoint: BankCheckpointIdentity = SYNTHETIC_BANK_CHECKPOINT,
    profile=SYNTHETIC_BANK_PROFILE,
    finalized_monotonic_s: float = 1.0,
) -> FinalizedBankEvidencePackage:
    return FinalizedBankEvidencePackage(
        package_id=package_id,
        checkpoint=checkpoint,
        profile=profile,
        raw_sha256=raw_sha256,
        manifest_sha256=_MANIFEST_HASH,
        operator_label=_operator_label(claimed_case=claimed_case),
        finalized_monotonic_s=finalized_monotonic_s,
    )


def _verdict(
    *,
    reviewed_case: BankEvidenceCase,
    bound_package_sha256: str,
    accepted: bool = True,
    reviewed_monotonic_s: float = 2.0,
) -> ReviewerVerdict:
    return ReviewerVerdict(
        reviewer_id="reviewer-1",
        accepted=accepted,
        reviewed_case=reviewed_case,
        bound_package_sha256=bound_package_sha256,
        reviewed_monotonic_s=reviewed_monotonic_s,
    )


def _pre_deposit_case(
    *,
    raw_sha256: str = _PRE_RAW_HASH,
    checkpoint: BankCheckpointIdentity = SYNTHETIC_BANK_CHECKPOINT,
    profile=SYNTHETIC_BANK_PROFILE,
    accepted: bool = True,
    finalized_monotonic_s: float = 1.0,
    reviewed_monotonic_s: float = 2.0,
) -> ReviewedBankEvidenceCase:
    package = _package(
        package_id="pkg-pre",
        raw_sha256=raw_sha256,
        claimed_case=BankEvidenceCase.NON_EMPTY_BEFORE_DEPOSIT,
        checkpoint=checkpoint,
        profile=profile,
        finalized_monotonic_s=finalized_monotonic_s,
    )
    verdict = _verdict(
        reviewed_case=BankEvidenceCase.NON_EMPTY_BEFORE_DEPOSIT,
        bound_package_sha256=package.package_sha256,
        accepted=accepted,
        reviewed_monotonic_s=reviewed_monotonic_s,
    )
    return ReviewedBankEvidenceCase(package=package, verdict=verdict)


def _post_deposit_case(
    *,
    raw_sha256: str = _POST_RAW_HASH,
    checkpoint: BankCheckpointIdentity = SYNTHETIC_BANK_CHECKPOINT,
    profile=SYNTHETIC_BANK_PROFILE,
    accepted: bool = True,
    finalized_monotonic_s: float = 100.0,
    reviewed_monotonic_s: float = 101.0,
) -> ReviewedBankEvidenceCase:
    package = _package(
        package_id="pkg-post",
        raw_sha256=raw_sha256,
        claimed_case=BankEvidenceCase.EMPTY_AFTER_DEPOSIT,
        checkpoint=checkpoint,
        profile=profile,
        finalized_monotonic_s=finalized_monotonic_s,
    )
    verdict = _verdict(
        reviewed_case=BankEvidenceCase.EMPTY_AFTER_DEPOSIT,
        bound_package_sha256=package.package_sha256,
        accepted=accepted,
        reviewed_monotonic_s=reviewed_monotonic_s,
    )
    return ReviewedBankEvidenceCase(package=package, verdict=verdict)


def _pre_deposit_provenance(
    *,
    raw_sha256: str = _PRE_RAW_HASH,
    cycle_id: str = "cycle-1",
    captured_monotonic_s: float = 10.0,
    frame_id: int = 1,
):
    return build_provenance(
        frame_id=frame_id,
        captured_monotonic_s=captured_monotonic_s,
        cycle_id=cycle_id,
        frame_sha256=raw_sha256,
    )


def _post_deposit_provenance(
    *,
    raw_sha256: str = _POST_RAW_HASH,
    cycle_id: str = "cycle-1",
    captured_monotonic_s: float = 10.6,
    frame_id: int = 2,
):
    return build_provenance(
        frame_id=frame_id,
        captured_monotonic_s=captured_monotonic_s,
        cycle_id=cycle_id,
        frame_sha256=raw_sha256,
    )


def _receipt(
    *,
    preceding_provenance,
    issued_monotonic_s: float = 10.5,
    attempt_id: str = "deposit-attempt-1",
) -> DepositAttemptReceipt:
    return DepositAttemptReceipt(
        attempt_id=attempt_id,
        issued_monotonic_s=issued_monotonic_s,
        preceding_provenance=preceding_provenance,
    )


def _valid_record(**overrides: object) -> DepositResultEvidenceRecord:
    defaults: dict[str, object] = {
        "pre_deposit": _pre_deposit_case(),
        "attempt_receipt": _receipt(preceding_provenance=_pre_deposit_provenance()),
        "post_deposit": _post_deposit_case(),
        "post_deposit_provenance": _post_deposit_provenance(),
    }
    defaults.update(overrides)
    return DepositResultEvidenceRecord(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_deposit_result_evidence_record_accepts_valid_causally_bound_result() -> None:
    record = _valid_record()
    assert record.pre_deposit.verdict.reviewed_case is BankEvidenceCase.NON_EMPTY_BEFORE_DEPOSIT
    assert record.post_deposit.verdict.reviewed_case is BankEvidenceCase.EMPTY_AFTER_DEPOSIT


# ---------------------------------------------------------------------------
# THE genuinely remaining defect: wrong / replayed / cross-session receipt.
# This is the failing-regression case: a naive record type that only checks
# case labels + checkpoint/profile + package-level finalized-timestamp
# ordering (no binding to any DepositAttemptReceipt at all) would ACCEPT
# this. A properly-bound record must reject it.
# ---------------------------------------------------------------------------


def test_deposit_result_evidence_record_rejects_receipt_issued_against_different_evidence() -> None:
    """The 'wrong receipt' attack: a real, valid-looking receipt, but issued
    against some other pre-deposit evidence, not the one in this record."""
    unrelated_pre_deposit_provenance = build_provenance(
        frame_id=999, captured_monotonic_s=500.0, cycle_id="cycle-1", frame_sha256="9" * 64
    )
    wrong_receipt = _receipt(
        preceding_provenance=unrelated_pre_deposit_provenance, issued_monotonic_s=500.5
    )
    with pytest.raises(
        ValueError, match="attempt_receipt.preceding_provenance does not match"
    ):
        _valid_record(
            attempt_receipt=wrong_receipt,
            post_deposit_provenance=_post_deposit_provenance(captured_monotonic_s=501.0),
        )


def test_deposit_result_evidence_record_rejects_replayed_receipt_from_earlier_visit() -> None:
    """A genuinely-valid receipt from an entirely different (earlier) visit,
    replayed here to make an unrelated pre/post pair look causally linked."""
    earlier_visit_provenance = build_provenance(
        frame_id=1, captured_monotonic_s=1.0, cycle_id="cycle-0", frame_sha256="7" * 64
    )
    replayed_receipt = _receipt(
        preceding_provenance=earlier_visit_provenance, issued_monotonic_s=1.5, attempt_id="old-attempt"
    )
    with pytest.raises(ValueError, match="attempt_receipt.preceding_provenance does not match"):
        _valid_record(attempt_receipt=replayed_receipt)


def test_deposit_result_evidence_record_rejects_cross_session_post_deposit() -> None:
    """Post-deposit evidence from a different cycle/session than the receipt."""
    with pytest.raises(ValueError, match="same visit/cycle"):
        _valid_record(post_deposit_provenance=_post_deposit_provenance(cycle_id="cycle-2"))


def test_deposit_result_evidence_record_rejects_post_deposit_not_matching_its_own_package() -> None:
    """post_deposit_provenance must be the exact frame the post_deposit package represents."""
    with pytest.raises(ValueError, match="post_deposit_provenance does not match"):
        _valid_record(post_deposit_provenance=_post_deposit_provenance(raw_sha256="e" * 64))


def test_deposit_result_evidence_record_rejects_swapped_ordering() -> None:
    """Receipt issued before the pre-deposit evidence it supposedly followed."""
    pre_provenance = _pre_deposit_provenance(captured_monotonic_s=10.0)
    backwards_receipt = _receipt(preceding_provenance=pre_provenance, issued_monotonic_s=5.0)
    with pytest.raises(ValueError, match="issued at or after"):
        _valid_record(attempt_receipt=backwards_receipt)


def test_deposit_result_evidence_record_rejects_post_deposit_not_strictly_after_receipt() -> None:
    receipt = _receipt(preceding_provenance=_pre_deposit_provenance(), issued_monotonic_s=10.5)
    with pytest.raises(ValueError, match="strictly after the attempt receipt"):
        _valid_record(
            attempt_receipt=receipt,
            post_deposit_provenance=_post_deposit_provenance(captured_monotonic_s=10.5),
        )


def test_deposit_result_evidence_record_rejects_post_deposit_outside_freshness_window() -> None:
    receipt = _receipt(preceding_provenance=_pre_deposit_provenance(), issued_monotonic_s=10.5)
    stale_post_provenance = _post_deposit_provenance(
        captured_monotonic_s=10.5 + MAX_ATTEMPT_RECEIPT_AGE_S + 1.0
    )
    with pytest.raises(ValueError, match="causal freshness window"):
        _valid_record(attempt_receipt=receipt, post_deposit_provenance=stale_post_provenance)


def test_deposit_result_evidence_record_rejects_stale_receipt_relative_to_pre_deposit() -> None:
    pre_provenance = _pre_deposit_provenance(captured_monotonic_s=10.0)
    stale_receipt = _receipt(
        preceding_provenance=pre_provenance,
        issued_monotonic_s=10.0 + MAX_ATTEMPT_RECEIPT_AGE_S + 1.0,
    )
    with pytest.raises(ValueError, match="issued too long after"):
        _valid_record(
            attempt_receipt=stale_receipt,
            post_deposit_provenance=_post_deposit_provenance(
                captured_monotonic_s=stale_receipt.issued_monotonic_s + 0.1
            ),
        )


def test_deposit_result_evidence_record_rejects_missing_receipt_type() -> None:
    with pytest.raises(ValueError, match="attempt_receipt must be an exact DepositAttemptReceipt"):
        _valid_record(attempt_receipt="not-a-receipt")


def test_deposit_result_evidence_record_rejects_non_exact_pre_deposit_type() -> None:
    with pytest.raises(ValueError, match="pre_deposit must be an exact ReviewedBankEvidenceCase"):
        _valid_record(pre_deposit="not-a-case")


def test_deposit_result_evidence_record_rejects_non_exact_post_deposit_type() -> None:
    with pytest.raises(ValueError, match="post_deposit must be an exact ReviewedBankEvidenceCase"):
        _valid_record(post_deposit="not-a-case")


def test_deposit_result_evidence_record_rejects_non_exact_post_deposit_provenance_type() -> None:
    with pytest.raises(
        ValueError, match="post_deposit_provenance must be an exact BankEvidenceProvenance"
    ):
        _valid_record(post_deposit_provenance="not-a-provenance")


def test_deposit_result_evidence_record_rejects_foreign_profile() -> None:
    from dataclasses import replace

    foreign_profile = replace(SYNTHETIC_BANK_PROFILE, profile_version="9.9.9")
    foreign = _post_deposit_case(profile=foreign_profile)
    with pytest.raises(ValueError, match="same profile"):
        _valid_record(post_deposit=foreign)


def test_deposit_result_evidence_record_rejects_duplicate_raw_content() -> None:
    with pytest.raises(ValueError, match="must not be backed by the same underlying evidence"):
        _valid_record(post_deposit=_post_deposit_case(raw_sha256=_PRE_RAW_HASH))


def test_deposit_result_evidence_record_rejects_wrong_pre_deposit_label() -> None:
    wrong = _post_deposit_case(raw_sha256=_PRE_RAW_HASH)
    with pytest.raises(ValueError, match="pre_deposit case must be reviewed as"):
        _valid_record(pre_deposit=wrong)


def test_deposit_result_evidence_record_rejects_wrong_post_deposit_label() -> None:
    wrong = _pre_deposit_case(raw_sha256=_POST_RAW_HASH)
    with pytest.raises(ValueError, match="post_deposit case must be reviewed as"):
        _valid_record(post_deposit=wrong)


def test_deposit_result_evidence_record_rejects_rejected_pre_deposit_verdict() -> None:
    rejected = _pre_deposit_case(accepted=False)
    with pytest.raises(ValueError, match="pre_deposit verdict must be accepted"):
        _valid_record(
            pre_deposit=rejected,
            attempt_receipt=_receipt(preceding_provenance=_pre_deposit_provenance()),
        )


def test_deposit_result_evidence_record_rejects_rejected_post_deposit_verdict() -> None:
    rejected = _post_deposit_case(accepted=False)
    with pytest.raises(ValueError, match="post_deposit verdict must be accepted"):
        _valid_record(post_deposit=rejected)


def test_deposit_result_evidence_record_rejects_foreign_checkpoint() -> None:
    foreign = _post_deposit_case(checkpoint=BankCheckpointIdentity("other", "other"))
    with pytest.raises(ValueError, match="same checkpoint"):
        _valid_record(post_deposit=foreign)
