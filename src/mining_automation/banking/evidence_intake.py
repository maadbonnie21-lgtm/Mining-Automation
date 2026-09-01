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
  bytes digest, manifest digest, and every release-bearing package metadata
  field. There is no in-place update path: a correction (including
  :func:`dataclasses.replace`) constructs a brand-new finalized package and a
  brand-new verdict, never mutating an existing accepted case in place.

:func:`validate_release_evidence_case_batch` is the only public batch check.
Its required-case and freshness policy cannot be changed by a caller. An
internal policy helper exists solely so those fixed rules can be tested; it is
not exported and cannot be confused with the release-facing API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from json import dumps
from math import isfinite
from typing import Final
from unicodedata import normalize

from .attempts import MAX_ATTEMPT_RECEIPT_AGE_S, DepositAttemptReceipt
from .contracts import (
    BankCheckpointIdentity,
    BankEvidenceProvenance,
    BankingBlocker,
    BankProfileIdentity,
    _validate_non_empty_string,
    _validate_sha256_digest,
)

__all__ = [
    "MAX_EVIDENCE_PACKAGE_AGE_S",
    "REQUIRED_BANK_EVIDENCE_CASES",
    "BankEvidenceCase",
    "CaptureEnvironmentIdentity",
    "DepositResultEvidenceRecord",
    "FinalizedBankEvidencePackage",
    "OperatorIntentLabel",
    "ReviewedBankEvidenceCase",
    "ReviewerVerdict",
    "validate_release_evidence_case_batch",
]

_RELEASE_MAX_EVIDENCE_PACKAGE_AGE_S: Final[float] = 86_400.0
MAX_EVIDENCE_PACKAGE_AGE_S: Final[float] = _RELEASE_MAX_EVIDENCE_PACKAGE_AGE_S


def _exact_finite_non_negative_float(value: object) -> float | None:
    """Reject numeric aliases whose float conversion could change chronology."""

    if type(value) is not float or not isfinite(value) or value < 0.0:
        return None
    return 0.0 if value == 0.0 else value


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


def _is_exact_snapshot(
    value: object,
    expected_types: tuple[type[object], ...],
) -> bool:
    """Validate snapshot shape without invoking attacker-controlled equality."""

    return (
        type(value) is tuple
        and len(value) == len(expected_types)
        and all(
            type(item) is expected for item, expected in zip(value, expected_types, strict=True)
        )
    )


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
        if any(0xD800 <= ord(character) <= 0xDFFF for character in self.note):
            raise ValueError("note must contain only Unicode scalar values")
        _validate_exact_finite_timestamp(self.labeled_monotonic_s, "labeled_monotonic_s")


@dataclass(frozen=True, slots=True)
class CaptureEnvironmentIdentity:
    """Identity of the exact source session, capture build, config, and environment.

    A shared ``cycle_id`` on live evidence (see
    :class:`~mining_automation.banking.contracts.BankEvidenceProvenance`) is a
    caller-chosen label -- it does not prove two archival packages came from
    the same actual capture session, build, configuration, or environment.
    This is the smallest additional identity a :class:`FinalizedBankEvidencePackage`
    binds into its own canonical digest so that claim can be checked, not
    merely asserted by an unbound caller-supplied string.
    """

    source_session_id: str
    capture_build_id: str
    capture_config_digest: str
    environment_id: str

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.source_session_id, "source_session_id")
        _validate_non_empty_string(self.capture_build_id, "capture_build_id")
        _validate_sha256_digest(self.capture_config_digest, "capture_config_digest")
        _validate_non_empty_string(self.environment_id, "environment_id")


@dataclass(frozen=True, slots=True)
class FinalizedBankEvidencePackage:
    """An immutable, hash-identified bank-evidence package.

    Normal assignment is forbidden, and recursive use-time validation rejects
    one-field ``object.__setattr__`` forging against the retained construction
    snapshot. ``operator_label`` is intent only; it does not make this package
    usable as release evidence on its own (see
    :class:`ReviewedBankEvidenceCase`). ``capture_environment`` binds the
    exact source session/build/config/environment identity into this
    package's own canonical digest -- see :class:`CaptureEnvironmentIdentity`.
    """

    package_id: str
    checkpoint: BankCheckpointIdentity
    profile: BankProfileIdentity
    capture_environment: CaptureEnvironmentIdentity
    raw_sha256: str
    manifest_sha256: str
    operator_label: OperatorIntentLabel
    finalized_monotonic_s: float
    _finalized_snapshot: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def _snapshot(self) -> tuple[object, ...]:
        return (
            self.package_id,
            self.checkpoint.checkpoint_id,
            self.checkpoint.location_id,
            self.profile.profile_id,
            self.profile.profile_version,
            self.profile.schema_version,
            self.profile.frame_width,
            self.profile.frame_height,
            self.capture_environment.source_session_id,
            self.capture_environment.capture_build_id,
            self.capture_environment.capture_config_digest,
            self.capture_environment.environment_id,
            self.raw_sha256,
            self.manifest_sha256,
            self.operator_label.operator_id,
            self.operator_label.claimed_case,
            self.operator_label.note,
            self.operator_label.labeled_monotonic_s,
            self.finalized_monotonic_s,
        )

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.package_id, "package_id")
        if type(self.checkpoint) is not BankCheckpointIdentity:
            raise ValueError("checkpoint must be an exact BankCheckpointIdentity")
        if type(self.profile) is not BankProfileIdentity:
            raise ValueError("profile must be an exact BankProfileIdentity")
        if type(self.capture_environment) is not CaptureEnvironmentIdentity:
            raise ValueError("capture_environment must be an exact CaptureEnvironmentIdentity")
        BankCheckpointIdentity.__post_init__(self.checkpoint)
        BankProfileIdentity.__post_init__(self.profile)
        CaptureEnvironmentIdentity.__post_init__(self.capture_environment)
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
        finalized_snapshot = self._snapshot()
        if hasattr(self, "_finalized_snapshot"):
            retained_snapshot = self._finalized_snapshot
            if (
                not _is_exact_snapshot(
                    retained_snapshot,
                    (
                        str,
                        str,
                        str,
                        str,
                        str,
                        int,
                        int,
                        int,
                        str,
                        str,
                        str,
                        str,
                        str,
                        str,
                        str,
                        BankEvidenceCase,
                        str,
                        float,
                        float,
                    ),
                )
                or retained_snapshot != finalized_snapshot
            ):
                raise ValueError("finalized package differs from its construction-time snapshot")
        object.__setattr__(self, "_finalized_snapshot", finalized_snapshot)

    @property
    def package_sha256(self) -> str:
        """Digest the exact finalized package using a canonical v2 encoding.

        ``manifest_sha256`` alone cannot bind the surrounding package
        metadata, while ``raw_sha256`` binds only the captured bytes. This
        domain-separated digest deliberately includes every release-bearing
        package field, every field of its nested identities/label, and the
        exact source session/build/config/environment identity. The hidden
        construction-integrity snapshot is not itself release data. Times use
        ``float.hex`` so semantically equal accepted numeric inputs have one
        stable representation. ``v2`` adds ``capture_environment``; it is
        deliberately distinct from any ``v1`` digest so the two schemas can
        never collide.
        """

        FinalizedBankEvidencePackage.__post_init__(self)
        canonical_payload: dict[str, object] = {
            "capture_environment": {
                "capture_build_id": self.capture_environment.capture_build_id,
                "capture_config_digest": self.capture_environment.capture_config_digest,
                "environment_id": self.capture_environment.environment_id,
                "source_session_id": self.capture_environment.source_session_id,
            },
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
            "schema": "mining-automation.bank-evidence-package.v2",
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
    _verdict_snapshot: tuple[str, bool, BankEvidenceCase, str, float] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.reviewer_id, "reviewer_id")
        if type(self.accepted) is not bool:
            raise ValueError("accepted must be a boolean")
        if type(self.reviewed_case) is not BankEvidenceCase:
            raise ValueError("reviewed_case must be an exact BankEvidenceCase")
        _validate_sha256_digest(self.bound_package_sha256, "bound_package_sha256")
        _validate_exact_finite_timestamp(self.reviewed_monotonic_s, "reviewed_monotonic_s")
        verdict_snapshot = (
            self.reviewer_id,
            self.accepted,
            self.reviewed_case,
            self.bound_package_sha256,
            self.reviewed_monotonic_s,
        )
        if hasattr(self, "_verdict_snapshot"):
            retained_snapshot = self._verdict_snapshot
            if (
                not _is_exact_snapshot(
                    retained_snapshot,
                    (str, bool, BankEvidenceCase, str, float),
                )
                or retained_snapshot != verdict_snapshot
            ):
                raise ValueError("reviewer verdict differs from its construction-time snapshot")
        object.__setattr__(self, "_verdict_snapshot", verdict_snapshot)


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
    _review_snapshot: tuple[str, bool, BankEvidenceCase, str, float, str] = field(
        init=False,
        repr=False,
        compare=False,
    )

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
        review_snapshot = (
            self.verdict.reviewer_id,
            self.verdict.accepted,
            self.verdict.reviewed_case,
            self.verdict.bound_package_sha256,
            self.verdict.reviewed_monotonic_s,
            self.package.package_sha256,
        )
        if hasattr(self, "_review_snapshot"):
            retained_snapshot = self._review_snapshot
            if (
                not _is_exact_snapshot(
                    retained_snapshot,
                    (str, bool, BankEvidenceCase, str, float, str),
                )
                or retained_snapshot != review_snapshot
            ):
                raise ValueError("reviewer verdict differs from its construction-time snapshot")
        object.__setattr__(self, "_review_snapshot", review_snapshot)


@dataclass(frozen=True, slots=True)
class DepositResultEvidenceRecord:
    """Binds a pre-deposit case, one exact attempt receipt, and a post-deposit
    case into a single causally-ordered deposit result.

    A batch merely containing *some* reviewed ``NON_EMPTY_BEFORE_DEPOSIT``
    case and *some* reviewed ``EMPTY_AFTER_DEPOSIT`` case is a materially
    weaker claim than "this exact deposit attempt went from non-empty to
    empty": the two samples could come from unrelated visits, or a real,
    validly-constructed receipt from a *different* attempt could be
    substituted in without anything noticing. This type makes -- and
    construction itself enforces -- the full causal chain a deposit result
    must prove:

    * ``pre_deposit`` is a reviewer-accepted ``NON_EMPTY_BEFORE_DEPOSIT`` case;
    * ``attempt_receipt`` is bound, by content hash
      (``preceding_provenance.frame_sha256 == pre_deposit.package.raw_sha256``),
      to this *exact* pre-deposit package -- not merely some receipt that
      happens to look plausible. This is what rejects a wrong or replayed
      receipt: a receipt issued against different evidence, however valid on
      its own, cannot bind here.
    * ``attempt_receipt.issued_monotonic_s`` is at or after the pre-deposit
      capture and within the same causal freshness window
      (:data:`~mining_automation.banking.attempts.MAX_ATTEMPT_RECEIPT_AGE_S`)
      used for a live attempt's own causality check.
    * ``post_deposit_provenance`` is bound, the same way, to the exact
      ``post_deposit`` package, shares the receipt's ``cycle_id`` (the same
      bank visit/session), and is captured strictly after the receipt and
      within that same freshness window -- mirroring
      :func:`~mining_automation.banking.workflow._post_attempt_freshness_blocker`'s
      live-workflow rule exactly.
    * ``post_deposit`` is a reviewer-accepted ``EMPTY_AFTER_DEPOSIT`` case,
      sharing the pre-deposit package's checkpoint/profile, and backed by
      distinct underlying evidence (the same frame cannot serve as both).

    Any violation raises at construction -- there is no way to hold a value
    of this type that does not satisfy the full chain.
    """

    pre_deposit: ReviewedBankEvidenceCase
    attempt_receipt: DepositAttemptReceipt
    post_deposit: ReviewedBankEvidenceCase
    post_deposit_provenance: BankEvidenceProvenance

    def __post_init__(self) -> None:
        if type(self.pre_deposit) is not ReviewedBankEvidenceCase:
            raise ValueError("pre_deposit must be an exact ReviewedBankEvidenceCase")
        if type(self.attempt_receipt) is not DepositAttemptReceipt:
            raise ValueError("attempt_receipt must be an exact DepositAttemptReceipt")
        if type(self.post_deposit) is not ReviewedBankEvidenceCase:
            raise ValueError("post_deposit must be an exact ReviewedBankEvidenceCase")
        if type(self.post_deposit_provenance) is not BankEvidenceProvenance:
            raise ValueError("post_deposit_provenance must be an exact BankEvidenceProvenance")
        ReviewedBankEvidenceCase.__post_init__(self.pre_deposit)
        ReviewedBankEvidenceCase.__post_init__(self.post_deposit)
        BankEvidenceProvenance.__post_init__(self.post_deposit_provenance)
        # Recomputing the receipt's canonical digest -- rather than merely
        # re-running its __post_init__ -- also enforces that every timestamp
        # on the receipt side is an exact finite non-negative float (no
        # int/NaN/Inf/negative alias) and detects any post-construction
        # object.__setattr__ forgery of the receipt's fields.
        _ = self.attempt_receipt.receipt_sha256

        if not self.pre_deposit.verdict.accepted:
            raise ValueError("pre_deposit verdict must be accepted")
        if not self.post_deposit.verdict.accepted:
            raise ValueError("post_deposit verdict must be accepted")
        if self.pre_deposit.verdict.reviewed_case is not BankEvidenceCase.NON_EMPTY_BEFORE_DEPOSIT:
            raise ValueError("pre_deposit case must be reviewed as NON_EMPTY_BEFORE_DEPOSIT")
        if self.post_deposit.verdict.reviewed_case is not BankEvidenceCase.EMPTY_AFTER_DEPOSIT:
            raise ValueError("post_deposit case must be reviewed as EMPTY_AFTER_DEPOSIT")
        if self.pre_deposit.package.checkpoint != self.post_deposit.package.checkpoint:
            raise ValueError("pre_deposit and post_deposit must share the same checkpoint")
        if self.pre_deposit.package.profile != self.post_deposit.package.profile:
            raise ValueError("pre_deposit and post_deposit must share the same profile")
        if (
            self.pre_deposit.package.capture_environment
            != self.post_deposit.package.capture_environment
        ):
            raise ValueError(
                "pre_deposit and post_deposit must share the same exact source session, "
                "capture build, capture config, and environment identity -- a shared cycle_id "
                "alone does not prove the same capture session/build/config/environment"
            )
        if self.pre_deposit.package.raw_sha256 == self.post_deposit.package.raw_sha256:
            raise ValueError(
                "pre_deposit and post_deposit must not be backed by the same underlying evidence"
            )

        preceding = self.attempt_receipt.preceding_provenance
        if preceding.frame_sha256 != self.pre_deposit.package.raw_sha256:
            raise ValueError(
                "attempt_receipt.preceding_provenance does not match the exact pre_deposit "
                "package -- the receipt must be issued against this specific pre-deposit evidence"
            )
        if self.post_deposit_provenance.frame_sha256 != self.post_deposit.package.raw_sha256:
            raise ValueError(
                "post_deposit_provenance does not match the exact post_deposit package"
            )
        if self.post_deposit_provenance.cycle_id != preceding.cycle_id:
            raise ValueError(
                "post_deposit_provenance must belong to the same visit/cycle as the attempt receipt"
            )

        issued = _validate_exact_finite_timestamp(
            self.attempt_receipt.issued_monotonic_s, "attempt_receipt.issued_monotonic_s"
        )
        preceding_captured = _validate_exact_finite_timestamp(
            preceding.frame.captured_monotonic_s, "attempt_receipt.preceding_provenance timestamp"
        )
        if issued < preceding_captured:
            raise ValueError("attempt_receipt must be issued at or after the pre_deposit evidence")
        if issued - preceding_captured > MAX_ATTEMPT_RECEIPT_AGE_S:
            raise ValueError(
                "attempt_receipt issued too long after the pre_deposit evidence to be causally bound"
            )

        post_captured = _validate_exact_finite_timestamp(
            self.post_deposit_provenance.frame.captured_monotonic_s,
            "post_deposit_provenance timestamp",
        )
        if post_captured <= issued:
            raise ValueError(
                "post_deposit_provenance must be captured strictly after the attempt receipt"
            )
        if post_captured - issued > MAX_ATTEMPT_RECEIPT_AGE_S:
            raise ValueError(
                "post_deposit_provenance exceeds the causal freshness window after the attempt receipt"
            )


_DEPOSIT_RESULT_CASES: Final[frozenset[BankEvidenceCase]] = frozenset(
    {BankEvidenceCase.NON_EMPTY_BEFORE_DEPOSIT, BankEvidenceCase.EMPTY_AFTER_DEPOSIT}
)


def _validate_evidence_case_batch(
    cases: tuple[ReviewedBankEvidenceCase, ...],
    *,
    expected_checkpoint: BankCheckpointIdentity,
    expected_profile: BankProfileIdentity,
    evaluated_monotonic_s: object,
    deposit_results: tuple[DepositResultEvidenceRecord, ...] = (),
    required_cases: frozenset[BankEvidenceCase] = REQUIRED_BANK_EVIDENCE_CASES,
    max_age_s: float = MAX_EVIDENCE_PACKAGE_AGE_S,
) -> tuple[BankingBlocker, ...]:
    """Apply a caller-selected evidence policy inside this module/tests only.

    An empty result means only that the batch satisfies the selected
    structural policy; this module never inspects pixels. Every applicable
    defect is returned and duplicates are never silently swallowed.

    ``NON_EMPTY_BEFORE_DEPOSIT``/``EMPTY_AFTER_DEPOSIT`` coverage cannot be
    satisfied merely by the bare presence of an accepted case with that
    label -- that would let two independently-valid packages from unrelated
    visits/sessions/attempts satisfy "deposit-result coverage" with no
    causal link between them at all. When ``required_cases`` includes either
    label, at least one ``deposit_results`` entry must be present whose
    ``pre_deposit``/``post_deposit`` are themselves members of ``cases``
    (see :class:`DepositResultEvidenceRecord` for what that record proves).
    """
    if type(expected_checkpoint) is not BankCheckpointIdentity:
        raise TypeError("expected_checkpoint must be an exact BankCheckpointIdentity")
    if type(expected_profile) is not BankProfileIdentity:
        raise TypeError("expected_profile must be an exact BankProfileIdentity")
    BankCheckpointIdentity.__post_init__(expected_checkpoint)
    BankProfileIdentity.__post_init__(expected_profile)
    if type(cases) is not tuple or any(
        type(case) is not ReviewedBankEvidenceCase for case in cases
    ):
        raise TypeError("cases must be a tuple of exact ReviewedBankEvidenceCase values")
    if type(deposit_results) is not tuple or any(
        type(record) is not DepositResultEvidenceRecord for record in deposit_results
    ):
        raise TypeError(
            "deposit_results must be a tuple of exact DepositResultEvidenceRecord values"
        )
    if type(required_cases) is not frozenset or any(
        type(case) is not BankEvidenceCase for case in required_cases
    ):
        raise TypeError("required_cases must be a frozenset of exact BankEvidenceCase values")
    maximum_age = _exact_finite_non_negative_float(max_age_s)
    if maximum_age is None:
        raise ValueError("max_age_s must be an exact finite non-negative float")

    evaluated = _exact_finite_non_negative_float(evaluated_monotonic_s)

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

    # NON_EMPTY_BEFORE_DEPOSIT/EMPTY_AFTER_DEPOSIT are deliberately excluded
    # from the bare-presence coverage check below -- see the deposit_results
    # loop further down, which is the only way to satisfy them.
    independent_required_cases = required_cases - _DEPOSIT_RESULT_CASES
    if not independent_required_cases.issubset(accepted_cases):
        blockers.append(BankingBlocker.MISSING_REQUIRED_EVIDENCE_CASE)

    deposit_result_required = bool(required_cases & _DEPOSIT_RESULT_CASES)
    if deposit_result_required:
        deposit_result_established = False
        for record in deposit_results:
            # Re-invoking __post_init__ both defends against object-level
            # forging after construction and re-proves the full causal chain
            # (pre_deposit non-empty, exact receipt binding, post_deposit
            # empty, freshness window) -- see DepositResultEvidenceRecord.
            DepositResultEvidenceRecord.__post_init__(record)
            if record.pre_deposit not in cases or record.post_deposit not in cases:
                if BankingBlocker.DEPOSIT_RESULT_PACKAGE_NOT_IN_BATCH not in blockers:
                    blockers.append(BankingBlocker.DEPOSIT_RESULT_PACKAGE_NOT_IN_BATCH)
                continue
            deposit_result_established = True
        if not deposit_result_established:
            blockers.append(BankingBlocker.DEPOSIT_RESULT_COVERAGE_MISSING)

    return tuple(blockers)


def validate_release_evidence_case_batch(
    cases: tuple[ReviewedBankEvidenceCase, ...],
    *,
    expected_checkpoint: BankCheckpointIdentity,
    expected_profile: BankProfileIdentity,
    evaluated_monotonic_s: object,
    deposit_results: tuple[DepositResultEvidenceRecord, ...] = (),
) -> tuple[BankingBlocker, ...]:
    """Apply fixed coverage/freshness rules to one caller-selected target.

    The target checkpoint/profile remain explicit caller inputs until a future
    source-owned deployment policy exists. Complete truth-case coverage and
    the freshness ceiling are private fixed rules: callers cannot request
    partial coverage or extend package lifetime. ``NON_EMPTY_BEFORE_DEPOSIT``/
    ``EMPTY_AFTER_DEPOSIT`` coverage additionally requires at least one valid,
    batch-referencing ``DepositResultEvidenceRecord`` in ``deposit_results``
    -- see :func:`_validate_evidence_case_batch`.
    """

    return _validate_evidence_case_batch(
        cases,
        expected_checkpoint=expected_checkpoint,
        expected_profile=expected_profile,
        evaluated_monotonic_s=evaluated_monotonic_s,
        deposit_results=deposit_results,
        required_cases=_RELEASE_REQUIRED_BANK_EVIDENCE_CASES,
        max_age_s=_RELEASE_MAX_EVIDENCE_PACKAGE_AGE_S,
    )
