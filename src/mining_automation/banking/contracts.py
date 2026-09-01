"""Typed banking contracts: identity, evidence, and fail-closed results.

This module defines the vocabulary the constrained-v1 banking foundation is
built from. It intentionally does not implement detection, a workflow, or a
``WorldState`` -- see :mod:`mining_automation.banking.perception` and
:mod:`mining_automation.banking.workflow` for those. Every value type here is
frozen and validates itself in ``__post_init__`` so a malformed value cannot
be constructed and later mistaken for real evidence.

Two structural invariants run through the whole package:

* ``UNKNOWN`` is not success. A :class:`BankInterfaceState.UNKNOWN` reading is
  not "closed" and is not "open"; an inventory observation with
  ``occupied_slots is None`` is not "empty". Both are treated as no-authority
  states everywhere in this package.
* A denied result always says why. :class:`BankingVerificationResult` cannot
  be constructed in a denied state with an empty ``blockers`` tuple, and
  cannot be constructed in a verified state with a non-empty one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Final

from ..contracts import FrameRef, InventoryState

__all__ = [
    "INVENTORY_CAPACITY",
    "BankCheckpointIdentity",
    "BankEvidenceProvenance",
    "BankInterfaceState",
    "BankObservation",
    "BankProfileIdentity",
    "BankingBlocker",
    "BankingVerificationResult",
    "DepositReadiness",
    "PostDepositInventoryObservation",
    "PreDepositInventoryObservation",
]

INVENTORY_CAPACITY: Final[int] = 28


def _is_integer(value: object) -> bool:
    return type(value) is int


def _is_finite_number(value: object) -> bool:
    if type(value) is int:
        return True
    return type(value) is float and isfinite(value)


def _validate_confidence(confidence: float) -> None:
    if not _is_finite_number(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be finite and between 0.0 and 1.0 inclusive")


def _validate_non_empty_string(value: object, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_sha256_digest(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a 64-character lowercase hex digest")


class BankInterfaceState(StrEnum):
    """Tri-state reading of the bank interface.

    ``UNKNOWN`` covers both "no evidence yet" and "evidence was too ambiguous
    to call" -- callers must not distinguish those cases by treating either as
    success. It is the only state a rejected/contract-violating observation
    may resolve to.
    """

    UNKNOWN = "unknown"
    CLOSED = "closed"
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class BankCheckpointIdentity:
    """Identity of one supported bank/checkpoint target."""

    checkpoint_id: str
    location_id: str

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.checkpoint_id, "checkpoint_id")
        _validate_non_empty_string(self.location_id, "location_id")


@dataclass(frozen=True, slots=True)
class BankProfileIdentity:
    """Identity and expected capture geometry of one bank detection profile.

    Geometry is part of identity rather than a free-floating pair of ints so a
    profile mismatch and a geometry mismatch are always evaluated against the
    exact same expected profile.
    """

    profile_id: str
    profile_version: str
    schema_version: int
    frame_width: int
    frame_height: int

    def __post_init__(self) -> None:
        _validate_non_empty_string(self.profile_id, "profile_id")
        _validate_non_empty_string(self.profile_version, "profile_version")
        if not _is_integer(self.schema_version) or self.schema_version <= 0:
            raise ValueError("schema_version must be a positive integer")
        if not _is_integer(self.frame_width) or self.frame_width <= 0:
            raise ValueError("frame_width must be a positive integer")
        if not _is_integer(self.frame_height) or self.frame_height <= 0:
            raise ValueError("frame_height must be a positive integer")


@dataclass(frozen=True, slots=True)
class BankEvidenceProvenance:
    """Exact same-cycle identity shared by one piece of banking evidence.

    ``cycle_id`` names the logical perception cycle a piece of evidence was
    produced in; ``frame`` and ``frame_sha256`` pin it to one exact captured
    frame. Two evidence values are considered to come from the same capture
    only when every field matches -- there is no partial/fuzzy equality.
    """

    frame: FrameRef
    cycle_id: str
    frame_sha256: str

    def __post_init__(self) -> None:
        if type(self.frame) is not FrameRef:
            raise ValueError("frame must be an exact FrameRef")
        if (
            type(self.frame.frame_id) is not int
            or type(self.frame.captured_monotonic_s) not in (int, float)
            or type(self.frame.width) is not int
            or type(self.frame.height) is not int
        ):
            raise ValueError("frame identity fields must use exact numeric primitives")
        FrameRef.__post_init__(self.frame)
        _validate_non_empty_string(self.cycle_id, "cycle_id")
        _validate_sha256_digest(self.frame_sha256, "frame_sha256")


@dataclass(frozen=True, slots=True)
class BankObservation:
    """One detector's reading of the bank interface for one exact frame."""

    identity: BankCheckpointIdentity
    profile: BankProfileIdentity
    provenance: BankEvidenceProvenance
    interface_state: BankInterfaceState
    confidence: float
    detector_id: str
    detector_version: str

    def __post_init__(self) -> None:
        if type(self.identity) is not BankCheckpointIdentity:
            raise ValueError("identity must be an exact BankCheckpointIdentity")
        if type(self.profile) is not BankProfileIdentity:
            raise ValueError("profile must be an exact BankProfileIdentity")
        if type(self.provenance) is not BankEvidenceProvenance:
            raise ValueError("provenance must be an exact BankEvidenceProvenance")
        if type(self.interface_state) is not BankInterfaceState:
            raise ValueError("interface_state must be an exact BankInterfaceState")
        _validate_confidence(self.confidence)
        _validate_non_empty_string(self.detector_id, "detector_id")
        _validate_non_empty_string(self.detector_version, "detector_version")
        BankCheckpointIdentity.__post_init__(self.identity)
        BankProfileIdentity.__post_init__(self.profile)
        BankEvidenceProvenance.__post_init__(self.provenance)


def _validate_inventory_observation_fields(
    state: InventoryState,
    provenance: BankEvidenceProvenance,
    detector_id: str,
    detector_version: str,
) -> None:
    if type(state) is not InventoryState:
        raise ValueError("state must be an exact InventoryState")
    if (
        (state.occupied_slots is not None and type(state.occupied_slots) is not int)
        or type(state.capacity) is not int
        or type(state.confidence) not in (int, float)
    ):
        raise ValueError("inventory state fields must use exact numeric primitives")
    InventoryState.__post_init__(state)
    if type(provenance) is not BankEvidenceProvenance:
        raise ValueError("provenance must be an exact BankEvidenceProvenance")
    BankEvidenceProvenance.__post_init__(provenance)
    _validate_non_empty_string(detector_id, "detector_id")
    _validate_non_empty_string(detector_version, "detector_version")


@dataclass(frozen=True, slots=True)
class PreDepositInventoryObservation:
    """A known-cycle inventory reading captured before a deposit is attempted.

    This is a distinct type from :class:`PostDepositInventoryObservation` on
    purpose, mirroring how ``capture.RawFrame``/``capture.Frame`` separate a
    backend buffer from an owned one: the two readings answer different
    questions (is there anything to deposit vs. did the deposit work) and a
    caller that mixes them up silently is exactly the bug this package exists
    to prevent.
    """

    state: InventoryState
    provenance: BankEvidenceProvenance
    detector_id: str
    detector_version: str

    def __post_init__(self) -> None:
        _validate_inventory_observation_fields(
            self.state, self.provenance, self.detector_id, self.detector_version
        )


@dataclass(frozen=True, slots=True)
class PostDepositInventoryObservation:
    """A known-cycle inventory reading captured after a deposit is attempted."""

    state: InventoryState
    provenance: BankEvidenceProvenance
    detector_id: str
    detector_version: str

    def __post_init__(self) -> None:
        _validate_inventory_observation_fields(
            self.state, self.provenance, self.detector_id, self.detector_version
        )


class DepositReadiness(StrEnum):
    """Whether a fresh, jointly-verified bank+inventory reading permits a deposit attempt."""

    NOT_READY = "not_ready"
    READY = "ready"


class BankingBlocker(StrEnum):
    """Deterministic, machine-readable reasons banking authority was denied.

    Every member is single-purpose and every denial in this package must
    carry at least one of them -- see :class:`BankingVerificationResult`.
    """

    EVALUATION_TIME_INVALID = "evaluation_time_invalid"
    EVIDENCE_TIMESTAMP_INVALID = "evidence_timestamp_invalid"
    EVIDENCE_FROM_FUTURE = "evidence_from_future"
    EVIDENCE_ORDERING_REGRESSION = "evidence_ordering_regression"
    EVIDENCE_PROVENANCE_MISMATCH = "evidence_provenance_mismatch"
    SUPPORTING_EVIDENCE_STALE = "supporting_evidence_stale"

    ARRIVAL_EVIDENCE_MISSING = "arrival_evidence_missing"
    ARRIVAL_EVIDENCE_TYPE_INVALID = "arrival_evidence_type_invalid"
    ARRIVAL_EVIDENCE_STALE = "arrival_evidence_stale"
    ARRIVAL_SUBSTITUTED_FOR_OBSERVATION = "arrival_substituted_for_observation"

    CHECKPOINT_IDENTITY_MISMATCH = "checkpoint_identity_mismatch"

    BANK_OBSERVATION_MISSING = "bank_observation_missing"
    BANK_EVIDENCE_TYPE_INVALID = "bank_evidence_type_invalid"
    BANK_PROFILE_MISMATCH = "bank_profile_mismatch"
    BANK_GEOMETRY_UNSUPPORTED = "bank_geometry_unsupported"
    BANK_EVIDENCE_STALE = "bank_evidence_stale"
    BANK_DETECTOR_ID_MISMATCH = "bank_detector_id_mismatch"
    BANK_DETECTOR_VERSION_MISMATCH = "bank_detector_version_mismatch"
    BANK_STATE_UNKNOWN = "bank_state_unknown"
    BANK_CONFIDENCE_BELOW_FLOOR = "bank_confidence_below_floor"
    DUPLICATE_CONFLICTING_BANK_OBSERVATIONS = "duplicate_conflicting_bank_observations"
    OPEN_ATTEMPT_WITHOUT_VERIFICATION = "open_attempt_without_verification"

    ATTEMPT_RECEIPT_MISSING = "attempt_receipt_missing"
    ATTEMPT_RECEIPT_TYPE_INVALID = "attempt_receipt_type_invalid"
    ATTEMPT_RECEIPT_EVALUATION_TIME_INVALID = "attempt_receipt_evaluation_time_invalid"
    ATTEMPT_RECEIPT_FROM_FUTURE = "attempt_receipt_from_future"
    ATTEMPT_RECEIPT_STALE = "attempt_receipt_stale"
    ATTEMPT_RECEIPT_PRECEDES_EVIDENCE = "attempt_receipt_precedes_evidence"
    ATTEMPT_PRECEDING_EVIDENCE_STALE = "attempt_preceding_evidence_stale"
    ATTEMPT_RECEIPT_WRONG_PROVENANCE = "attempt_receipt_wrong_provenance"
    ATTEMPT_RECEIPT_DUPLICATE = "attempt_receipt_duplicate"
    POST_ATTEMPT_EVIDENCE_NOT_FRESH = "post_attempt_evidence_not_fresh"
    POST_ATTEMPT_EVIDENCE_STALE = "post_attempt_evidence_stale"

    DUPLICATE_EVIDENCE_PACKAGE = "duplicate_evidence_package"
    REJECTED_EVIDENCE_PACKAGE_INCLUDED = "rejected_evidence_package_included"
    MISSING_REQUIRED_EVIDENCE_CASE = "missing_required_evidence_case"
    EVIDENCE_PACKAGE_STALE = "evidence_package_stale"

    INVENTORY_EVIDENCE_MISSING = "inventory_evidence_missing"
    INVENTORY_EVIDENCE_TYPE_INVALID = "inventory_evidence_type_invalid"
    INVENTORY_EVIDENCE_STALE = "inventory_evidence_stale"
    INVENTORY_UNKNOWN = "inventory_unknown"
    INVENTORY_LAYOUT_MISMATCH = "inventory_layout_mismatch"
    INVENTORY_CONFIDENCE_BELOW_FLOOR = "inventory_confidence_below_floor"
    INVENTORY_ALREADY_EMPTY = "inventory_already_empty"
    DUPLICATE_CONFLICTING_INVENTORY_OBSERVATIONS = "duplicate_conflicting_inventory_observations"
    DEPOSIT_WITHOUT_INVENTORY_VERIFICATION = "deposit_without_inventory_verification"
    DEPOSIT_INVENTORY_STILL_NON_EMPTY = "deposit_inventory_still_non_empty"
    POST_DEPOSIT_INVENTORY_UNKNOWN = "post_deposit_inventory_unknown"

    UNEXPECTED_EVENT_FOR_STATE = "unexpected_event_for_state"


@dataclass(frozen=True, slots=True)
class BankingVerificationResult:
    """A generic verified/denied result that always explains a denial.

    ``verified=True`` requires an empty ``blockers`` tuple; ``verified=False``
    requires at least one. This is deny-first by construction: there is no
    way to build a "verified" result that skipped stating why, and no way to
    build a "denied" result that looks silently successful.
    """

    verified: bool
    blockers: tuple[BankingBlocker, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.verified, bool):
            raise ValueError("verified must be a boolean")
        if not isinstance(self.blockers, tuple) or any(
            type(blocker) is not BankingBlocker for blocker in self.blockers
        ):
            raise ValueError("blockers must be a tuple of exact BankingBlocker values")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        if self.verified and self.blockers:
            raise ValueError("a verified result cannot carry blockers")
        if not self.verified and not self.blockers:
            raise ValueError("a denied result must carry at least one blocker")
