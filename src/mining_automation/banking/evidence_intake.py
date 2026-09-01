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
  a raw capture or mutable label.** :class:`ReviewedBankEvidenceCase` can
  only be constructed from a :class:`FinalizedBankEvidencePackage` and a
  :class:`ReviewerVerdict` whose ``bound_package_sha256`` matches the
  package's canonical ``package_sha256`` exactly. That digest covers the raw
  bytes digest, manifest digest, and every package metadata field. There is
  no update/replace path: a correction requires a brand-new finalized
  package and a brand-new verdict, never mutating an existing accepted case
  in place.

:func:`validate_release_evidence_case_batch` is the only public batch check.
Its required-case and freshness policy cannot be changed by a caller. An
internal policy helper exists solely so those fixed rules can be tested; it is
not exported and cannot be confused with the release-facing API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from json import dumps
from math import isfinite
from typing import Final
from unicodedata import normalize

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
    "validate_release_evidence_case_batch",
]

_RELEASE_MAX_EVIDENCE_PACKAGE_AGE_S: Final[float] = 86_400.0
MAX_EVIDENCE_PACKAGE_AGE_S: Final[float] = _RELEASE_MAX_EVIDENCE_PACKAGE_AGE_S


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


def _validate_exact_finite_timestamp(value: object, field_name: str) -> float:
    """Return a canonical finite timestamp or reject representation aliases."""

    if type(value) is not float or not isfinite(value) or value < 0.0:
        raise ValueError(f"{field_name} must be an exact finite non-negative float")
    return 0.0 if value == 0.0 else value


def _canonical_float_hex(value: float) -> str:
    return _validate_exact_finite_timestamp(value, "canonical timestamp").hex()


def _normalized_actor_identity(value: str) -> str:
    """Return the comparison form used to enforce independent review."""

    return normalize("NFKC", value.strip()).casefold()


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


_RELEASE_REQUIRED_BANK_EVIDENCE_CASES: Final[frozenset[BankEvidenceCase]] = frozenset(
    BankEvidenceCase
)
REQUIRED_BANK_EVIDENCE_CASES: Final[frozenset[BankEvidenceCase]] = (
    _RELEASE_REQUIRED_BANK_EVIDENCE_CASES
)


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
        if type(self.note) is not str:
            raise ValueError("note must be a string")
        _validate_exact_finite_timestamp(self.labeled_monotonic_s, "labeled_monotonic_s")


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
        BankCheckpointIdentity.__post_init__(self.checkpoint)
        BankProfileIdentity.__post_init__(self.profile)
        _validate_sha256_digest(self.raw_sha256, "raw_sha256")
        _validate_sha256_digest(self.manifest_sha256, "manifest_sha256")
        if type(self.operator_label) is not OperatorIntentLabel:
            raise ValueError("operator_label must be an exact OperatorIntentLabel")
        OperatorIntentLabel.__post_init__(self.operator_label)
        finalized = _validate_exact_finite_timestamp(
            self.finalized_monotonic_s, "finalized_monotonic_s"
        )
        labeled = _validate_exact_finite_timestamp(
            self.operator_label.labeled_monotonic_s,
            "operator_label.labeled_monotonic_s",
        )
        if finalized <= labeled:
            raise ValueError("finalized_monotonic_s must follow operator labeling")

    @property
    def package_sha256(self) -> str:
        """Digest the exact finalized package using a canonical v1 encoding.

        ``manifest_sha256`` alone cannot bind the surrounding package
        metadata, while ``raw_sha256`` binds only the captured bytes. This
        domain-separated digest deliberately includes every dataclass field
        in this package and every field of its nested identities/label. Times
        use ``float.hex`` so semantically equal accepted numeric inputs have
        one stable representation.
        """

        canonical_payload: dict[str, object] = {
            "checkpoint": {
                "checkpoint_id": self.checkpoint.checkpoint_id,
                "location_id": self.checkpoint.location_id,
            },
            "finalized_monotonic_s": _canonical_float_hex(self.finalized_monotonic_s),
            "manifest_sha256": self.manifest_sha256,
            "operator_label": {
                "claimed_case": self.operator_label.claimed_case.value,
                "labeled_monotonic_s": _canonical_float_hex(
                    self.operator_label.labeled_monotonic_s
                ),
                "note": self.operator_label.note,
                "operator_id": self.operator_label.operator_id,
            },
            "package_id": self.package_id,
            "profile": {
                "frame_height": self.profile.frame_height,
                "frame_width": self.profile.frame_width,
                "profile_id": self.profile.profile_id,
                "profile_version": self.profile.profile_version,
                "schema_version": self.profile.schema_version,
            },
            "raw_sha256": self.raw_sha256,
            "schema": "mining-automation.bank-evidence-package.v1",
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
        if type(self.accepted) is not bool:
            raise ValueError("accepted must be a boolean")
        if type(self.reviewed_case) is not BankEvidenceCase:
            raise ValueError("reviewed_case must be an exact BankEvidenceCase")
        _validate_sha256_digest(self.bound_package_sha256, "bound_package_sha256")
        _validate_exact_finite_timestamp(self.reviewed_monotonic_s, "reviewed_monotonic_s")


@dataclass(frozen=True, slots=True)
class ReviewedBankEvidenceCase:
    """Reviewer truth for one finalized package -- the only releasable unit.

    Construction itself enforces the cryptographic binding: a ``verdict``
    whose ``bound_package_sha256`` does not match ``package.package_sha256``
    cannot produce a value of this type at all. It also enforces independent
    actor identity and operator -> finalization -> review ordering.
    """

    package: FinalizedBankEvidencePackage
    verdict: ReviewerVerdict

    def __post_init__(self) -> None:
        if type(self.package) is not FinalizedBankEvidencePackage:
            raise ValueError("package must be an exact FinalizedBankEvidencePackage")
        if type(self.verdict) is not ReviewerVerdict:
            raise ValueError("verdict must be an exact ReviewerVerdict")
        FinalizedBankEvidencePackage.__post_init__(self.package)
        ReviewerVerdict.__post_init__(self.verdict)
        if _normalized_actor_identity(self.package.operator_label.operator_id) == (
            _normalized_actor_identity(self.verdict.reviewer_id)
        ):
            raise ValueError("operator_id and reviewer_id must identify different actors")
        if self.verdict.reviewed_monotonic_s <= self.package.finalized_monotonic_s:
            raise ValueError("reviewed_monotonic_s must follow package finalization")
        if self.verdict.bound_package_sha256 != self.package.package_sha256:
            raise ValueError(
                "verdict.bound_package_sha256 does not match package.package_sha256 -- "
                "a reviewer verdict must be bound to the exact package it reviewed"
            )


def _validate_evidence_case_batch(
    cases: tuple[ReviewedBankEvidenceCase, ...],
    *,
    expected_checkpoint: BankCheckpointIdentity,
    expected_profile: BankProfileIdentity,
    evaluated_monotonic_s: object,
    required_cases: frozenset[BankEvidenceCase] = REQUIRED_BANK_EVIDENCE_CASES,
    max_age_s: float = MAX_EVIDENCE_PACKAGE_AGE_S,
) -> tuple[BankingBlocker, ...]:
    """Apply a caller-selected evidence policy inside this module/tests only.

    An empty result means only that the batch satisfies the selected
    structural policy; this module never inspects pixels. Every applicable
    defect is returned and duplicates are never silently swallowed.
    """
    if type(expected_checkpoint) is not BankCheckpointIdentity:
        raise TypeError("expected_checkpoint must be an exact BankCheckpointIdentity")
    if type(expected_profile) is not BankProfileIdentity:
        raise TypeError("expected_profile must be an exact BankProfileIdentity")
    if not isinstance(cases, tuple) or any(
        type(case) is not ReviewedBankEvidenceCase for case in cases
    ):
        raise TypeError("cases must be a tuple of exact ReviewedBankEvidenceCase values")
    if not isinstance(required_cases, frozenset) or any(
        type(case) is not BankEvidenceCase for case in required_cases
    ):
        raise TypeError("required_cases must be a frozenset of exact BankEvidenceCase values")
    maximum_age = _finite_float(max_age_s)
    if maximum_age is None or maximum_age < 0.0:
        raise ValueError("max_age_s must be a finite non-negative number")

    evaluated = _finite_float(evaluated_monotonic_s)

    blockers: list[BankingBlocker] = []
    seen_package_ids: set[str] = set()
    seen_package_digests: set[str] = set()
    seen_raw_digests: set[str] = set()
    accepted_cases: set[BankEvidenceCase] = set()

    if evaluated is None:
        blockers.append(BankingBlocker.EVALUATION_TIME_INVALID)

    for case in cases:
        # Exact types are required at the boundary; rerun every nested
        # invariant as defense against object-level forging after construction.
        ReviewedBankEvidenceCase.__post_init__(case)
        package_digest = case.package.package_sha256

        if (
            case.package.package_id in seen_package_ids
            or package_digest in seen_package_digests
            or case.package.raw_sha256 in seen_raw_digests
        ):
            if BankingBlocker.DUPLICATE_EVIDENCE_PACKAGE not in blockers:
                blockers.append(BankingBlocker.DUPLICATE_EVIDENCE_PACKAGE)
        seen_package_ids.add(case.package.package_id)
        seen_package_digests.add(package_digest)
        seen_raw_digests.add(case.package.raw_sha256)

        if case.package.checkpoint != expected_checkpoint:
            if BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH not in blockers:
                blockers.append(BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH)
        if case.package.profile != expected_profile:
            if BankingBlocker.BANK_PROFILE_MISMATCH not in blockers:
                blockers.append(BankingBlocker.BANK_PROFILE_MISMATCH)

        if not case.verdict.accepted:
            if BankingBlocker.REJECTED_EVIDENCE_PACKAGE_INCLUDED not in blockers:
                blockers.append(BankingBlocker.REJECTED_EVIDENCE_PACKAGE_INCLUDED)

        if evaluated is not None:
            if (
                case.package.operator_label.labeled_monotonic_s > evaluated
                or case.package.finalized_monotonic_s > evaluated
                or case.verdict.reviewed_monotonic_s > evaluated
            ):
                if BankingBlocker.EVIDENCE_FROM_FUTURE not in blockers:
                    blockers.append(BankingBlocker.EVIDENCE_FROM_FUTURE)
            age_s = evaluated - case.package.finalized_monotonic_s
            if age_s > maximum_age and BankingBlocker.EVIDENCE_PACKAGE_STALE not in blockers:
                blockers.append(BankingBlocker.EVIDENCE_PACKAGE_STALE)

        if case.verdict.accepted:
            accepted_cases.add(case.verdict.reviewed_case)

    if not required_cases.issubset(accepted_cases):
        blockers.append(BankingBlocker.MISSING_REQUIRED_EVIDENCE_CASE)

    return tuple(blockers)


def validate_release_evidence_case_batch(
    cases: tuple[ReviewedBankEvidenceCase, ...],
    *,
    expected_checkpoint: BankCheckpointIdentity,
    expected_profile: BankProfileIdentity,
    evaluated_monotonic_s: object,
) -> tuple[BankingBlocker, ...]:
    """Apply fixed coverage/freshness rules to one caller-selected target.

    The target checkpoint/profile remain explicit caller inputs until a future
    source-owned deployment policy exists. Complete truth-case coverage and
    the freshness ceiling are private fixed rules: callers cannot request
    partial coverage or extend package lifetime.
    """

    return _validate_evidence_case_batch(
        cases,
        expected_checkpoint=expected_checkpoint,
        expected_profile=expected_profile,
        evaluated_monotonic_s=evaluated_monotonic_s,
        required_cases=_RELEASE_REQUIRED_BANK_EVIDENCE_CASES,
        max_age_s=_RELEASE_MAX_EVIDENCE_PACKAGE_AGE_S,
    )
