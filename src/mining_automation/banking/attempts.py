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
from hashlib import sha256
from json import dumps
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


def _is_exact_snapshot(value: object, expected_types: tuple[type[object], ...]) -> bool:
    """Validate snapshot shape without invoking attacker-controlled equality."""

    return (
        type(value) is tuple
        and len(value) == len(expected_types)
        and all(
            type(item) is expected for item, expected in zip(value, expected_types, strict=True)
        )
    )


def _validate_exact_finite_timestamp(value: object, field_name: str) -> float:
    """Return a canonical finite timestamp or reject representation aliases.

    Deliberately stricter than :func:`_finite_float`: an ``int`` (however
    small) or a ``float`` that is negative/NaN/infinite is rejected outright
    rather than coerced, so a release-bearing digest cannot alias two
    numerically-equal-but-differently-typed inputs (or lose precision on a
    huge integer) across the fixed causal freshness boundary.
    """

    if type(value) is not float or not isfinite(value) or value < 0.0:
        raise ValueError(f"{field_name} must be an exact finite non-negative float")
    return 0.0 if value == 0.0 else value


def _canonical_float_hex(value: float, field_name: str) -> str:
    return _validate_exact_finite_timestamp(value, field_name).hex()


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

    Retains an exact construction-time snapshot and rejects a later
    ``object.__setattr__`` mutation of any field (a frozen dataclass alone
    does not prevent this) -- re-invoking :meth:`__post_init__`, as a
    release-facing consumer such as
    :class:`~mining_automation.banking.evidence_intake.DepositResultEvidenceRecord`
    does, raises if the object no longer matches what was originally
    constructed. :attr:`receipt_sha256` is this receipt's canonical,
    domain-separated identity digest -- computing it also enforces that
    every timestamp involved is an exact finite non-negative ``float``,
    never an ``int``/NaN/Inf/negative alias.
    """

    attempt_id: str
    issued_monotonic_s: float
    preceding_provenance: BankEvidenceProvenance
    _receipt_snapshot: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def _snapshot(self) -> tuple[object, ...]:
        provenance = self.preceding_provenance
        return (
            self.attempt_id,
            self.issued_monotonic_s,
            provenance.frame.frame_id,
            provenance.frame.captured_monotonic_s,
            provenance.frame.width,
            provenance.frame.height,
            provenance.cycle_id,
            provenance.frame_sha256,
        )

    def __post_init__(self) -> None:
        _validate_receipt_fields(
            self.attempt_id, self.issued_monotonic_s, self.preceding_provenance
        )
        snapshot = self._snapshot()
        if hasattr(self, "_receipt_snapshot"):
            retained_snapshot = self._receipt_snapshot
            if (
                not _is_exact_snapshot(
                    retained_snapshot,
                    (str, float, int, float, int, int, str, str),
                )
                or retained_snapshot != snapshot
            ):
                raise ValueError(
                    "deposit attempt receipt differs from its construction-time snapshot"
                )
        object.__setattr__(self, "_receipt_snapshot", snapshot)

    @property
    def receipt_sha256(self) -> str:
        """Canonical digest binding this exact attempt identity and evidence.

        Requires every timestamp involved to be an exact finite
        non-negative ``float`` -- an ``int``/NaN/Inf/negative alias raises
        immediately, before any anti-forging check runs, so the failure
        reason is unambiguous. Recomputing this after a forged
        ``object.__setattr__`` mutation raises via the anti-forging
        re-validation in :meth:`__post_init__`.
        """
        provenance = self.preceding_provenance
        issued_hex = _canonical_float_hex(self.issued_monotonic_s, "issued_monotonic_s")
        captured_hex = _canonical_float_hex(
            provenance.frame.captured_monotonic_s, "preceding_provenance.captured_monotonic_s"
        )
        DepositAttemptReceipt.__post_init__(self)
        canonical_payload: dict[str, object] = {
            "schema": "mining-automation.deposit-attempt-receipt.v1",
            "attempt_id": self.attempt_id,
            "issued_monotonic_s": issued_hex,
            "preceding_provenance": {
                "frame_id": provenance.frame.frame_id,
                "captured_monotonic_s": captured_hex,
                "width": provenance.frame.width,
                "height": provenance.frame.height,
                "cycle_id": provenance.cycle_id,
                "frame_sha256": provenance.frame_sha256,
            },
        }
        encoded = dumps(
            canonical_payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


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
