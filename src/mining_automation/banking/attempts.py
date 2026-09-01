"""Future attempt-receipt causality contracts for banking input attempts.

This module is data-only and non-input: it does not click anything, does not
know how an open-bank or deposit interaction would actually be issued, and
cannot be wired to RuneLite. It exists so a future orchestrator that *does*
issue a real click can attach a typed receipt describing *when* the attempt
was issued and *against which already-accepted evidence* -- purely for
causality bookkeeping (duplicate-attempt detection, stale-attempt detection,
detecting an attempt issued against evidence the workflow no longer holds).

The central guarantee, enforced by construction rather than by convention:

    an accepted attempt receipt never proves the attempted action succeeded.

:class:`AttemptCausalityResult` can only ever say whether a *receipt* is
trustworthy bookkeeping (not a duplicate, bound to the right evidence, not
stale). It has no field, constructor, or code path that can represent
:class:`~mining_automation.banking.contracts.BankInterfaceState` or
:class:`~mining_automation.contracts.InventoryState`. Proof that an
open-bank or deposit attempt actually worked can only ever come from a
subsequent fresh :class:`~mining_automation.banking.workflow.BankObservationEvidence`
or inventory observation event, evaluated exactly as
:mod:`mining_automation.banking.workflow` already requires. A duplicate,
wrong-provenance, or stale receipt must stop the calling transition rather
than being silently ignored -- see
:func:`mining_automation.banking.workflow.advance_banking_workflow`, which
wires this evaluator into the ``OpenBankAttempted``/``DepositAttempted``
transitions when a receipt is supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Final

from .contracts import BankEvidenceProvenance, BankingBlocker

__all__ = [
    "MAX_ATTEMPT_RECEIPT_AGE_S",
    "AttemptCausalityResult",
    "DepositAttemptReceipt",
    "OpenBankAttemptReceipt",
    "evaluate_attempt_receipt_causality",
]

MAX_ATTEMPT_RECEIPT_AGE_S: Final[float] = 1.0


def _finite_float(value: object) -> float | None:
    if type(value) is int:
        try:
            return float(value)
        except OverflowError:
            return None
    if type(value) is float:
        converted = value
        return converted if isfinite(converted) else None
    return None


def _validate_receipt_fields(
    attempt_id: str, issued_monotonic_s: float, preceding_provenance: BankEvidenceProvenance
) -> None:
    if type(attempt_id) is not str or not attempt_id.strip():
        raise ValueError("attempt_id must be a non-empty string")
    if _finite_float(issued_monotonic_s) is None:
        raise ValueError("issued_monotonic_s must be a finite number")
    if type(preceding_provenance) is not BankEvidenceProvenance:
        raise ValueError("preceding_provenance must be an exact BankEvidenceProvenance")
    BankEvidenceProvenance.__post_init__(preceding_provenance)


@dataclass(frozen=True, slots=True)
class OpenBankAttemptReceipt:
    """A causal record that an open-bank interaction was issued.

    Not evidence of the interaction's outcome. ``preceding_provenance`` names
    the exact evidence (the accepted ``BANK_CLOSED_VERIFIED`` reading) the
    attempt was issued against -- a receipt bound to different evidence is a
    wrong-provenance receipt, not a valid one for the workflow's current
    state.
    """

    attempt_id: str
    issued_monotonic_s: float
    preceding_provenance: BankEvidenceProvenance

    def __post_init__(self) -> None:
        _validate_receipt_fields(
            self.attempt_id, self.issued_monotonic_s, self.preceding_provenance
        )


@dataclass(frozen=True, slots=True)
class DepositAttemptReceipt:
    """A causal record that a deposit interaction was issued.

    Not evidence of the interaction's outcome. See
    :class:`OpenBankAttemptReceipt` for what ``preceding_provenance`` means.
    """

    attempt_id: str
    issued_monotonic_s: float
    preceding_provenance: BankEvidenceProvenance

    def __post_init__(self) -> None:
        _validate_receipt_fields(
            self.attempt_id, self.issued_monotonic_s, self.preceding_provenance
        )


@dataclass(frozen=True, slots=True)
class AttemptCausalityResult:
    """Whether an attempt receipt is trustworthy bookkeeping -- nothing more.

    ``accepted=True`` means only: this receipt is not a duplicate, is bound
    to the evidence the workflow currently holds, and was issued within the
    freshness window. It never means the attempted action worked.
    """

    accepted: bool
    blockers: tuple[BankingBlocker, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be a boolean")
        if not isinstance(self.blockers, tuple) or any(
            type(blocker) is not BankingBlocker for blocker in self.blockers
        ):
            raise ValueError("blockers must be a tuple of exact BankingBlocker values")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        if self.accepted and self.blockers:
            raise ValueError("an accepted causality result cannot carry blockers")
        if not self.accepted and not self.blockers:
            raise ValueError("a rejected causality result must carry at least one blocker")


def evaluate_attempt_receipt_causality(
    receipt: OpenBankAttemptReceipt | DepositAttemptReceipt,
    *,
    expected_preceding_provenance: BankEvidenceProvenance,
    used_attempt_ids: frozenset[str],
    evaluated_monotonic_s: object,
    max_age_s: float = MAX_ATTEMPT_RECEIPT_AGE_S,
) -> AttemptCausalityResult:
    """Resolve one attempt receipt into a trustworthy-bookkeeping verdict.

    Duplicate (``attempt_id`` already in ``used_attempt_ids``), wrong-provenance
    (bound to evidence other than ``expected_preceding_provenance``), receipts
    claiming to predate that evidence, and stale or from-the-future receipts
    are all rejected. A caller must never advance workflow state on an accepted
    result alone -- see the module docstring.
    """
    if type(receipt) is OpenBankAttemptReceipt:
        OpenBankAttemptReceipt.__post_init__(receipt)
    elif type(receipt) is DepositAttemptReceipt:
        DepositAttemptReceipt.__post_init__(receipt)
    else:
        raise TypeError("receipt must be an exact OpenBankAttemptReceipt or DepositAttemptReceipt")
    if type(expected_preceding_provenance) is not BankEvidenceProvenance:
        raise TypeError("expected_preceding_provenance must be an exact BankEvidenceProvenance")
    BankEvidenceProvenance.__post_init__(expected_preceding_provenance)
    if type(used_attempt_ids) is not frozenset or any(
        type(item) is not str or not item.strip() for item in used_attempt_ids
    ):
        raise TypeError("used_attempt_ids must be a frozenset of exact non-empty str")
    maximum_age = _finite_float(max_age_s)
    if maximum_age is None or maximum_age < 0.0:
        raise ValueError("max_age_s must be finite and non-negative")

    blockers: list[BankingBlocker] = []

    if receipt.attempt_id in used_attempt_ids:
        blockers.append(BankingBlocker.ATTEMPT_RECEIPT_DUPLICATE)
    if receipt.preceding_provenance != expected_preceding_provenance:
        blockers.append(BankingBlocker.ATTEMPT_RECEIPT_WRONG_PROVENANCE)
    issued = _finite_float(receipt.issued_monotonic_s)
    preceding_captured = _finite_float(receipt.preceding_provenance.frame.captured_monotonic_s)
    if issued is None or preceding_captured is None:  # revalidation above makes this defensive
        raise ValueError("receipt timestamps must use exact finite numeric primitives")
    if issued < preceding_captured:
        blockers.append(BankingBlocker.ATTEMPT_RECEIPT_PRECEDES_EVIDENCE)
    elif issued - preceding_captured > maximum_age:
        blockers.append(BankingBlocker.ATTEMPT_PRECEDING_EVIDENCE_STALE)

    evaluated = _finite_float(evaluated_monotonic_s)
    if evaluated is None:
        blockers.append(BankingBlocker.ATTEMPT_RECEIPT_EVALUATION_TIME_INVALID)
    else:
        age_s = evaluated - issued
        if age_s < 0.0:
            blockers.append(BankingBlocker.ATTEMPT_RECEIPT_FROM_FUTURE)
        elif age_s > maximum_age:
            blockers.append(BankingBlocker.ATTEMPT_RECEIPT_STALE)

    if blockers:
        return AttemptCausalityResult(False, tuple(blockers))
    return AttemptCausalityResult(True, ())
