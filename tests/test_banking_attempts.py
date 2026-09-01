"""Tests for future attempt-receipt causality contracts (data-only, no input).

These exercise :mod:`mining_automation.banking.attempts` in isolation, purely
at the type/pure-function level. Integration into the workflow's
``OpenBankAttempted``/``DepositAttempted`` transitions is covered separately
in ``test_banking_workflow.py``.
"""

from __future__ import annotations

import pytest

from mining_automation.banking.attempts import (
    MAX_ATTEMPT_RECEIPT_AGE_S,
    AttemptCausalityResult,
    DepositAttemptReceipt,
    OpenBankAttemptReceipt,
    evaluate_attempt_receipt_causality,
)
from mining_automation.banking.contracts import BankingBlocker
from mining_automation.banking.testing import (
    build_deposit_attempt_receipt,
    build_open_bank_attempt_receipt,
    build_provenance,
)


def test_open_bank_attempt_receipt_accepts_valid_values() -> None:
    receipt = build_open_bank_attempt_receipt()
    assert receipt.attempt_id == "synthetic-open-attempt-1"


@pytest.mark.parametrize("attempt_id", ["", "   "])
def test_open_bank_attempt_receipt_rejects_blank_attempt_id(attempt_id: str) -> None:
    with pytest.raises(ValueError, match="attempt_id must be a non-empty string"):
        OpenBankAttemptReceipt(
            attempt_id=attempt_id, issued_monotonic_s=0.0, preceding_provenance=build_provenance()
        )


@pytest.mark.parametrize("issued_monotonic_s", [float("nan"), float("inf"), "not-a-number", True])
def test_open_bank_attempt_receipt_rejects_invalid_issued_time(issued_monotonic_s: object) -> None:
    with pytest.raises(ValueError, match="issued_monotonic_s must be a finite number"):
        OpenBankAttemptReceipt(
            attempt_id="a",
            issued_monotonic_s=issued_monotonic_s,  # type: ignore[arg-type]
            preceding_provenance=build_provenance(),
        )


def test_open_bank_attempt_receipt_rejects_non_exact_provenance() -> None:
    with pytest.raises(ValueError, match="preceding_provenance must be an exact"):
        OpenBankAttemptReceipt(
            attempt_id="a", issued_monotonic_s=0.0, preceding_provenance="not-a-provenance"  # type: ignore[arg-type]
        )


def test_deposit_attempt_receipt_accepts_valid_values() -> None:
    receipt = build_deposit_attempt_receipt()
    assert receipt.attempt_id == "synthetic-deposit-attempt-1"


def test_deposit_attempt_receipt_rejects_non_exact_provenance() -> None:
    with pytest.raises(ValueError, match="preceding_provenance must be an exact"):
        DepositAttemptReceipt(
            attempt_id="a", issued_monotonic_s=0.0, preceding_provenance="not-a-provenance"  # type: ignore[arg-type]
        )


def test_attempt_causality_result_rejects_non_boolean_accepted() -> None:
    with pytest.raises(ValueError, match="accepted must be a boolean"):
        AttemptCausalityResult(accepted="yes")  # type: ignore[arg-type]


def test_attempt_causality_result_rejects_wrong_blocker_element_type() -> None:
    with pytest.raises(ValueError, match="blockers must be a tuple of exact BankingBlocker"):
        AttemptCausalityResult(accepted=False, blockers=("not-a-blocker",))  # type: ignore[arg-type]


def test_attempt_causality_result_accepted_requires_no_blockers() -> None:
    result = AttemptCausalityResult(accepted=True)
    assert result.blockers == ()


def test_attempt_causality_result_accepted_rejects_blockers() -> None:
    with pytest.raises(ValueError, match="an accepted causality result cannot carry blockers"):
        AttemptCausalityResult(accepted=True, blockers=(BankingBlocker.ATTEMPT_RECEIPT_STALE,))


def test_attempt_causality_result_rejected_requires_a_blocker() -> None:
    with pytest.raises(ValueError, match="a rejected causality result must carry at least one blocker"):
        AttemptCausalityResult(accepted=False)


def test_attempt_causality_result_rejects_duplicate_blockers() -> None:
    with pytest.raises(ValueError, match="blockers must be unique"):
        AttemptCausalityResult(
            accepted=False,
            blockers=(BankingBlocker.ATTEMPT_RECEIPT_STALE, BankingBlocker.ATTEMPT_RECEIPT_STALE),
        )


def test_evaluate_attempt_receipt_causality_accepts_fresh_matching_receipt() -> None:
    provenance = build_provenance(frame_id=1, captured_monotonic_s=0.0)
    receipt = build_open_bank_attempt_receipt(
        attempt_id="attempt-1", issued_monotonic_s=0.0, preceding_provenance=provenance
    )
    result = evaluate_attempt_receipt_causality(
        receipt,
        expected_preceding_provenance=provenance,
        used_attempt_ids=frozenset(),
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted
    assert result.blockers == ()


def test_evaluate_attempt_receipt_causality_rejects_duplicate() -> None:
    provenance = build_provenance()
    receipt = build_open_bank_attempt_receipt(
        attempt_id="attempt-1", preceding_provenance=provenance
    )
    result = evaluate_attempt_receipt_causality(
        receipt,
        expected_preceding_provenance=provenance,
        used_attempt_ids=frozenset({"attempt-1"}),
        evaluated_monotonic_s=0.0,
    )
    assert not result.accepted
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_DUPLICATE,)


def test_evaluate_attempt_receipt_causality_rejects_wrong_provenance() -> None:
    receipt = build_open_bank_attempt_receipt(preceding_provenance=build_provenance(frame_id=1))
    result = evaluate_attempt_receipt_causality(
        receipt,
        expected_preceding_provenance=build_provenance(frame_id=2),
        used_attempt_ids=frozenset(),
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_WRONG_PROVENANCE,)


def test_evaluate_attempt_receipt_causality_rejects_stale_receipt() -> None:
    provenance = build_provenance()
    receipt = build_open_bank_attempt_receipt(issued_monotonic_s=0.0, preceding_provenance=provenance)
    result = evaluate_attempt_receipt_causality(
        receipt,
        expected_preceding_provenance=provenance,
        used_attempt_ids=frozenset(),
        evaluated_monotonic_s=MAX_ATTEMPT_RECEIPT_AGE_S + 1.0,
    )
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_STALE,)


def test_evaluate_attempt_receipt_causality_rejects_receipt_from_the_future() -> None:
    provenance = build_provenance()
    receipt = build_open_bank_attempt_receipt(issued_monotonic_s=10.0, preceding_provenance=provenance)
    result = evaluate_attempt_receipt_causality(
        receipt,
        expected_preceding_provenance=provenance,
        used_attempt_ids=frozenset(),
        evaluated_monotonic_s=0.0,
    )
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_FROM_FUTURE,)


@pytest.mark.parametrize("evaluated_monotonic_s", [float("nan"), True, "not-a-number"])
def test_evaluate_attempt_receipt_causality_rejects_invalid_evaluation_time(
    evaluated_monotonic_s: object,
) -> None:
    provenance = build_provenance()
    receipt = build_open_bank_attempt_receipt(preceding_provenance=provenance)
    result = evaluate_attempt_receipt_causality(
        receipt,
        expected_preceding_provenance=provenance,
        used_attempt_ids=frozenset(),
        evaluated_monotonic_s=evaluated_monotonic_s,
    )
    assert result.blockers == (BankingBlocker.ATTEMPT_RECEIPT_EVALUATION_TIME_INVALID,)


def test_evaluate_attempt_receipt_causality_can_report_multiple_blockers() -> None:
    provenance = build_provenance(frame_id=1)
    receipt = build_open_bank_attempt_receipt(
        attempt_id="dup", issued_monotonic_s=0.0, preceding_provenance=provenance
    )
    result = evaluate_attempt_receipt_causality(
        receipt,
        expected_preceding_provenance=build_provenance(frame_id=2),
        used_attempt_ids=frozenset({"dup"}),
        evaluated_monotonic_s=0.0,
    )
    assert not result.accepted
    assert BankingBlocker.ATTEMPT_RECEIPT_DUPLICATE in result.blockers
    assert BankingBlocker.ATTEMPT_RECEIPT_WRONG_PROVENANCE in result.blockers


def test_evaluate_attempt_receipt_causality_accepts_deposit_receipt() -> None:
    provenance = build_provenance()
    receipt = build_deposit_attempt_receipt(preceding_provenance=provenance)
    result = evaluate_attempt_receipt_causality(
        receipt,
        expected_preceding_provenance=provenance,
        used_attempt_ids=frozenset(),
        evaluated_monotonic_s=0.0,
    )
    assert result.accepted


def test_evaluate_attempt_receipt_causality_rejects_wrong_receipt_type() -> None:
    with pytest.raises(TypeError, match="must be an exact OpenBankAttemptReceipt"):
        evaluate_attempt_receipt_causality(
            "not-a-receipt",  # type: ignore[arg-type]
            expected_preceding_provenance=build_provenance(),
            used_attempt_ids=frozenset(),
            evaluated_monotonic_s=0.0,
        )


def test_evaluate_attempt_receipt_causality_rejects_wrong_expected_provenance_type() -> None:
    with pytest.raises(TypeError, match="expected_preceding_provenance must be an exact"):
        evaluate_attempt_receipt_causality(
            build_open_bank_attempt_receipt(),
            expected_preceding_provenance="not-a-provenance",  # type: ignore[arg-type]
            used_attempt_ids=frozenset(),
            evaluated_monotonic_s=0.0,
        )


def test_evaluate_attempt_receipt_causality_rejects_wrong_used_attempt_ids_type() -> None:
    with pytest.raises(TypeError, match="used_attempt_ids must be a frozenset of str"):
        evaluate_attempt_receipt_causality(
            build_open_bank_attempt_receipt(),
            expected_preceding_provenance=build_provenance(),
            used_attempt_ids={"not", "a", "frozenset"},  # type: ignore[arg-type]
            evaluated_monotonic_s=0.0,
        )
