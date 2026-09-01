"""Immutable future bank-evidence intake / reviewer-package design (data-only).

This module collects, labels, and reviews nothing. It defines the typed
shape a future real bank-evidence pipeline must implement before any bank
fixture may be trusted as release evidence, mirroring the operator/reviewer
separation used elsewhere in this project's resource and inventory
release-evidence work. No pixels, real or synthetic, are captured here --
only the identity and binding rules a package of pixels would have to
satisfy.

Two structural invariants, both enforced by construction:

* **Operator labels are not reviewer truth.** :class:`OperatorIntentLabel` is
  what the person who captured a fixture *believes* it shows. It is embedded
  in a :class:`FinalizedBankEvidencePackage` as an inert record of intent --
  nothing in this module promotes it to an accepted case automatically. Only
  a :class:`ReviewerVerdict` can do that, and it carries its own,
  independently-set ``reviewed_case``.
* **Reviewer truth is cryptographically bound to a finalized package, not to
  a mutable label.** :class:`ReviewedBankEvidenceCase` can only be
  constructed from a :class:`FinalizedBankEvidencePackage` and a
  :class:`ReviewerVerdict` whose ``bound_package_sha256`` matches the
  package's own ``raw_sha256`` exactly. There is no update/replace path: a
  correction requires a brand-new finalized package and a brand-new verdict,
  never mutating an existing accepted case in place.

:func:`validate_evidence_case_batch` is the pure adversarial check a future
release gate would run over a proposed batch of reviewed cases: duplicate
package identity, a rejected verdict smuggled into an "accepted evidence"
batch, a verdict claiming to review a package before that package was even
finalized, checkpoint/profile foreign to the expected target, staleness, and
missing required case coverage all reject with zero release authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Final

from .contracts import (
    BankCheckpointIdentity,
    BankingBlocker,
    BankProfileIdentity,
    _validate_non_empty_string,
    _validate_sha256_digest,
)

__all__ = [
    "MAX_EVIDENCE_PACKAGE_AGE_S",
    "REQUIRED_BANK_EVIDENCE_CASES",
    "BankEvidenceCase",
    "FinalizedBankEvidencePackage",
    "OperatorIntentLabel",
    "ReviewedBankEvidenceCase",
    "ReviewerVerdict",
    "validate_evidence_case_batch",
]

MAX_EVIDENCE_PACKAGE_AGE_S: Final[float] = 86_400.0


def _finite_float(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    converted = float(value)
    return converted if isfinite(converted) else None


class BankEvidenceCase(StrEnum):
    """The minimum evidence categories ``docs/BANKING.md`` requires.

    See ``docs/BANKING.md`` section "Future real-evidence specification" for
    the full description of what each case must show.
    """

    ARRIVAL = "arrival"
    VALID_TARGET_PRESENTATION = "valid_target_presentation"
    CLOSED = "closed"
    OPEN = "open"
    NON_EMPTY_BEFORE_DEPOSIT = "non_empty_before_deposit"
    EMPTY_AFTER_DEPOSIT = "empty_after_deposit"
    OBSTRUCTED_AMBIGUOUS = "obstructed_ambiguous"
    WRONG_LOCATION_NEGATIVE = "wrong_location_negative"


REQUIRED_BANK_EVIDENCE_CASES: Final[frozenset[BankEvidenceCase]] = frozenset(BankEvidenceCase)


@dataclass(frozen=True, slots=True)
class OperatorIntentLabel:
    """What the operator who captured a fixture believes it shows.

    Inert record of intent, not release truth -- see the module docstring.
    """

    operator_id: str
    claimed_case: BankEvidenceCase
    note: str
    labeled_monotonic_s: float

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.operator_id, "operator_id")
        if type(self.claimed_case) is not BankEvidenceCase:
            raise ValueError("claimed_case must be an exact BankEvidenceCase")
        if not isinstance(self.note, str):
            raise ValueError("note must be a string")
        if _finite_float(self.labeled_monotonic_s) is None:
            raise ValueError("labeled_monotonic_s must be a finite number")


@dataclass(frozen=True, slots=True)
class FinalizedBankEvidencePackage:
    """An immutable, hash-identified bank-evidence package.

    Once constructed, a package cannot be mutated -- there is no setter and
    no update method. ``operator_label`` is intent only; it does not make
    this package usable as release evidence on its own (see
    :class:`ReviewedBankEvidenceCase`).
    """

    package_id: str
    checkpoint: BankCheckpointIdentity
    profile: BankProfileIdentity
    raw_sha256: str
    manifest_sha256: str
    operator_label: OperatorIntentLabel
    finalized_monotonic_s: float

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.package_id, "package_id")
        if type(self.checkpoint) is not BankCheckpointIdentity:
            raise ValueError("checkpoint must be an exact BankCheckpointIdentity")
        if type(self.profile) is not BankProfileIdentity:
            raise ValueError("profile must be an exact BankProfileIdentity")
        _validate_sha256_digest(self.raw_sha256, "raw_sha256")
        _validate_sha256_digest(self.manifest_sha256, "manifest_sha256")
        if type(self.operator_label) is not OperatorIntentLabel:
            raise ValueError("operator_label must be an exact OperatorIntentLabel")
        if _finite_float(self.finalized_monotonic_s) is None:
            raise ValueError("finalized_monotonic_s must be a finite number")


@dataclass(frozen=True, slots=True)
class ReviewerVerdict:
    """An independent reviewer's determination, bound to one exact package.

    ``reviewed_case`` is the reviewer's own classification and may disagree
    with the originating :class:`OperatorIntentLabel`; that disagreement is
    expected and is exactly why the two are separate types.
    """

    reviewer_id: str
    accepted: bool
    reviewed_case: BankEvidenceCase
    bound_package_sha256: str
    reviewed_monotonic_s: float

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.reviewer_id, "reviewer_id")
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be a boolean")
        if type(self.reviewed_case) is not BankEvidenceCase:
            raise ValueError("reviewed_case must be an exact BankEvidenceCase")
        _validate_sha256_digest(self.bound_package_sha256, "bound_package_sha256")
        if _finite_float(self.reviewed_monotonic_s) is None:
            raise ValueError("reviewed_monotonic_s must be a finite number")


@dataclass(frozen=True, slots=True)
class ReviewedBankEvidenceCase:
    """Reviewer truth for one finalized package -- the only releasable unit.

    Construction itself enforces the cryptographic binding: a ``verdict``
    whose ``bound_package_sha256`` does not match ``package.raw_sha256``
    cannot produce a value of this type at all.
    """

    package: FinalizedBankEvidencePackage
    verdict: ReviewerVerdict

    def __post_init__(self) -> None:
        if type(self.package) is not FinalizedBankEvidencePackage:
            raise ValueError("package must be an exact FinalizedBankEvidencePackage")
        if type(self.verdict) is not ReviewerVerdict:
            raise ValueError("verdict must be an exact ReviewerVerdict")
        if self.verdict.bound_package_sha256 != self.package.raw_sha256:
            raise ValueError(
                "verdict.bound_package_sha256 does not match package.raw_sha256 -- "
                "a reviewer verdict must be bound to the exact package it reviewed"
            )


def validate_evidence_case_batch(
    cases: tuple[ReviewedBankEvidenceCase, ...],
    *,
    expected_checkpoint: BankCheckpointIdentity,
    expected_profile: BankProfileIdentity,
    evaluated_monotonic_s: object,
    required_cases: frozenset[BankEvidenceCase] = REQUIRED_BANK_EVIDENCE_CASES,
    max_age_s: float = MAX_EVIDENCE_PACKAGE_AGE_S,
) -> tuple[BankingBlocker, ...]:
    """Return every reason ``cases`` is not yet usable as release evidence.

    An empty result means the batch is structurally acceptable -- it does
    not mean the pixels inside it are real or correct; this function never
    inspects pixels at all. Every applicable defect is returned (not just
    the first), matching the readiness-audit shape a future release gate
    would need, and duplicates are never silently swallowed.
    """
    if type(expected_checkpoint) is not BankCheckpointIdentity:
        raise TypeError("expected_checkpoint must be an exact BankCheckpointIdentity")
    if type(expected_profile) is not BankProfileIdentity:
        raise TypeError("expected_profile must be an exact BankProfileIdentity")
    if not isinstance(cases, tuple) or any(
        type(case) is not ReviewedBankEvidenceCase for case in cases
    ):
        raise TypeError("cases must be a tuple of exact ReviewedBankEvidenceCase values")

    evaluated = _finite_float(evaluated_monotonic_s)

    blockers: list[BankingBlocker] = []
    seen_package_ids: set[str] = set()
    accepted_cases: set[BankEvidenceCase] = set()

    for case in cases:
        if case.package.package_id in seen_package_ids:
            if BankingBlocker.DUPLICATE_EVIDENCE_PACKAGE not in blockers:
                blockers.append(BankingBlocker.DUPLICATE_EVIDENCE_PACKAGE)
        seen_package_ids.add(case.package.package_id)

        if case.package.checkpoint != expected_checkpoint:
            if BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH not in blockers:
                blockers.append(BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH)
        if case.package.profile != expected_profile:
            if BankingBlocker.BANK_PROFILE_MISMATCH not in blockers:
                blockers.append(BankingBlocker.BANK_PROFILE_MISMATCH)

        if not case.verdict.accepted:
            if BankingBlocker.REJECTED_EVIDENCE_PACKAGE_INCLUDED not in blockers:
                blockers.append(BankingBlocker.REJECTED_EVIDENCE_PACKAGE_INCLUDED)
            continue

        if case.verdict.reviewed_monotonic_s < case.package.finalized_monotonic_s:
            if BankingBlocker.REVIEWER_VERDICT_PRECEDES_FINALIZATION not in blockers:
                blockers.append(BankingBlocker.REVIEWER_VERDICT_PRECEDES_FINALIZATION)

        if evaluated is not None:
            age_s = evaluated - case.package.finalized_monotonic_s
            if age_s > max_age_s and BankingBlocker.EVIDENCE_PACKAGE_STALE not in blockers:
                blockers.append(BankingBlocker.EVIDENCE_PACKAGE_STALE)

        accepted_cases.add(case.verdict.reviewed_case)

    if not required_cases.issubset(accepted_cases):
        blockers.append(BankingBlocker.MISSING_REQUIRED_EVIDENCE_CASE)

    return tuple(blockers)
