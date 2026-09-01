"""Platform-independent bank perception seam.

This module describes the evidence shape a future, reviewed bank detector
must deliver -- it does not implement one. No detector here is wired to a
live capture backend, RuneLite, or any input surface, and none is claimed to
recognize a real OSRS bank interface.

Two pure evaluators do all of the fail-closed reasoning:

* :func:`evaluate_bank_observation` turns a raw :class:`BankObservation` into
  a :class:`BankPerceptionResult` that can only ever report ``OPEN``/``CLOSED``
  when the evidence is unimpeachable, and otherwise resolves to ``UNKNOWN``.
* :func:`evaluate_inventory_observation` does the equivalent for inventory
  evidence bound to a banking cycle, without redefining inventory perception
  itself -- it consumes the shared :class:`~mining_automation.contracts.InventoryState`
  produced elsewhere in the codebase.

Required semantics (all covered by tests):

* a definite ``OPEN`` or ``CLOSED`` reading passes through untouched
* an ambiguous/unsupported reading resolves to ``UNKNOWN`` with no blocker --
  genuine uncertainty is not a contract violation
* wrong capture geometry resolves to ``UNKNOWN``
* wrong profile/version and stale evidence are rejected (blockers set, state
  forced to ``UNKNOWN``)
* evidence whose own provenance disagrees with an independently supplied
  ``current_provenance`` is rejected as mixed/smuggled evidence -- see
  :func:`evaluate_bank_observation` for why this only fires when a caller has
  a real independent source to check against
* a missing detector delivery carries zero banking authority
* a confidently-labeled but under-floor-confidence reading (a "false OPEN" or
  "false CLOSED") is rejected -- not accepted as ambiguous -- distinguishing
  it from a detector's own genuine, high-confidence ``UNKNOWN`` call
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Final, Protocol, runtime_checkable

from ..capture import Frame
from ..contracts import InventoryState
from .contracts import (
    INVENTORY_CAPACITY,
    BankCheckpointIdentity,
    BankEvidenceProvenance,
    BankingBlocker,
    BankInterfaceState,
    BankObservation,
    BankProfileIdentity,
)
from .errors import BankDetectorContractError, BankDetectorExecutionError

__all__ = [
    "BANK_PUBLICATION_CONFIDENCE_FLOOR",
    "INVENTORY_PUBLICATION_CONFIDENCE_FLOOR",
    "MAX_BANKING_EVIDENCE_AGE_S",
    "BankDetector",
    "BankDetectorMetadata",
    "BankPerceptionResult",
    "InventoryPerceptionResult",
    "evaluate_bank_observation",
    "evaluate_inventory_observation",
    "run_bank_detector",
    "validate_bank_detector",
]

MAX_BANKING_EVIDENCE_AGE_S: Final[float] = 1.0
INVENTORY_PUBLICATION_CONFIDENCE_FLOOR: Final[float] = 0.8
BANK_PUBLICATION_CONFIDENCE_FLOOR: Final[float] = 0.8


def _finite_float(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    converted = float(value)
    return converted if isfinite(converted) else None


@dataclass(frozen=True, slots=True)
class BankDetectorMetadata:
    """Stable identity and implementation version for a bank detector."""

    detector_id: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.detector_id, str) or not self.detector_id.strip():
            raise ValueError("detector_id must be a non-empty string")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("detector version must be a non-empty string")


@runtime_checkable
class BankDetector(Protocol):
    """Deterministically produce one bank observation for one owned frame."""

    @property
    def metadata(self) -> BankDetectorMetadata:
        """Return stable identity and version metadata."""
        ...

    def observe(self, frame: Frame) -> BankObservation:
        """Inspect ``frame`` and return one bank observation."""
        ...


def validate_bank_detector(detector: object) -> BankDetectorMetadata:
    """Validate runtime protocol shape and return trusted detector metadata.

    Deliberately does not invoke :meth:`BankDetector.observe`.
    """
    if not isinstance(detector, BankDetector):
        raise BankDetectorContractError(
            f"bank detector must satisfy BankDetector protocol, got {type(detector).__name__}"
        )
    try:
        metadata = detector.metadata
    except Exception as exc:
        raise BankDetectorContractError("bank detector metadata could not be read") from exc
    if not isinstance(metadata, BankDetectorMetadata):
        raise BankDetectorContractError(
            "bank detector metadata must be BankDetectorMetadata, "
            f"got {type(metadata).__name__}"
        )
    return metadata


def run_bank_detector(
    detector: BankDetector,
    frame: Frame,
    *,
    expected_metadata: BankDetectorMetadata | None = None,
) -> BankObservation:
    """Run one bank detector against one frame and validate its output.

    Detector exceptions are normalized to :class:`BankDetectorExecutionError`
    with their original exception preserved as ``__cause__``. Malformed
    output is a :class:`BankDetectorContractError`; neither can be confused
    with a genuine observation.
    """
    metadata = validate_bank_detector(detector)
    if expected_metadata is not None and metadata != expected_metadata:
        raise BankDetectorContractError(
            "bank detector metadata changed during the evaluation run: "
            f"expected {expected_metadata.detector_id!r}@{expected_metadata.version!r}, "
            f"got {metadata.detector_id!r}@{metadata.version!r}"
        )
    if not isinstance(frame, Frame):
        raise BankDetectorContractError(
            f"bank detector input must be Frame, got {type(frame).__name__}"
        )

    prefix = f"bank detector {metadata.detector_id!r} version {metadata.version!r} on frame {frame.frame_id}"
    try:
        observation = detector.observe(frame)
    except Exception as exc:
        raise BankDetectorExecutionError(
            f"{prefix} raised {type(exc).__name__}: {exc}"
        ) from exc

    if type(observation) is not BankObservation:
        raise BankDetectorContractError(
            f"{prefix} must return BankObservation, got {type(observation).__name__}"
        )
    if observation.provenance.frame != frame.ref:
        raise BankDetectorContractError(
            f"{prefix} references frame {observation.provenance.frame.frame_id}, "
            f"expected input frame {frame.frame_id}"
        )
    if observation.detector_version != metadata.version:
        raise BankDetectorContractError(
            f"{prefix} output detector_version {observation.detector_version!r} "
            f"does not match metadata version {metadata.version!r}"
        )
    return observation


@dataclass(frozen=True, slots=True)
class BankPerceptionResult:
    """A resolved bank-interface reading that can only be trusted when clean.

    ``blockers`` non-empty always forces ``interface_state`` to ``UNKNOWN``:
    a rejected observation can never smuggle through an ``OPEN`` or ``CLOSED``
    claim. An empty ``blockers`` tuple paired with ``UNKNOWN`` is a distinct,
    legitimate case: the evidence was clean but the reading was genuinely
    ambiguous.
    """

    interface_state: BankInterfaceState
    blockers: tuple[BankingBlocker, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if type(self.interface_state) is not BankInterfaceState:
            raise ValueError("interface_state must be an exact BankInterfaceState")
        if not isinstance(self.blockers, tuple) or any(
            type(blocker) is not BankingBlocker for blocker in self.blockers
        ):
            raise ValueError("blockers must be a tuple of exact BankingBlocker values")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        if self.blockers and self.interface_state is not BankInterfaceState.UNKNOWN:
            raise ValueError("a rejected bank reading must resolve to UNKNOWN")

    @property
    def accepted(self) -> bool:
        return not self.blockers


def evaluate_bank_observation(
    observation: BankObservation | None,
    *,
    expected_checkpoint: BankCheckpointIdentity,
    expected_profile: BankProfileIdentity,
    evaluated_monotonic_s: object,
    current_provenance: BankEvidenceProvenance | None = None,
    previous_provenance: BankEvidenceProvenance | None = None,
    max_age_s: float = MAX_BANKING_EVIDENCE_AGE_S,
    min_confidence: float = BANK_PUBLICATION_CONFIDENCE_FLOOR,
) -> BankPerceptionResult:
    """Resolve one bank observation into a trustworthy interface-state reading.

    ``current_provenance``, when supplied, is an *independently derived*
    claim of which exact capture this evaluation is for -- for example a
    provenance the caller computed straight from the capture layer, before
    consulting the detector's own output. The observation's own provenance
    must match it exactly; a mismatch is treated as evidence smuggled in from
    a different frame and rejected. Passing the observation's own provenance
    back as ``current_provenance`` would make this check vacuous, so callers
    that have no independent source (this package's own workflow included)
    should leave it as ``None`` rather than fake one. ``previous_provenance``,
    when supplied, guards against replaying an old or non-advancing frame as
    if it were fresh.

    A confidence below ``min_confidence`` rejects the reading regardless of
    its claimed state -- this is the guard against a "false OPEN": a
    detector confidently mislabeling CLOSED as OPEN is exactly the failure
    mode a floor on the *label itself* cannot catch, since the label already
    says OPEN. Only the detector's own admitted uncertainty (confidence) can
    catch it, so a below-floor reading is rejected (blockers set, forced to
    UNKNOWN) rather than accepted-as-ambiguous -- unlike a genuine
    UNKNOWN-state reading, which the detector itself already flagged as
    uncertain and which this function accepts with no blocker.
    """
    if type(expected_checkpoint) is not BankCheckpointIdentity:
        raise TypeError("expected_checkpoint must be an exact BankCheckpointIdentity")
    if type(expected_profile) is not BankProfileIdentity:
        raise TypeError("expected_profile must be an exact BankProfileIdentity")
    if current_provenance is not None and type(current_provenance) is not BankEvidenceProvenance:
        raise TypeError("current_provenance must be an exact BankEvidenceProvenance or None")

    if observation is None:
        return BankPerceptionResult(
            BankInterfaceState.UNKNOWN, (BankingBlocker.BANK_OBSERVATION_MISSING,)
        )
    if type(observation) is not BankObservation:
        return BankPerceptionResult(
            BankInterfaceState.UNKNOWN, (BankingBlocker.BANK_EVIDENCE_TYPE_INVALID,)
        )

    blockers: list[BankingBlocker] = []

    if current_provenance is not None and observation.provenance != current_provenance:
        blockers.append(BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH)
    if observation.identity != expected_checkpoint:
        blockers.append(BankingBlocker.CHECKPOINT_IDENTITY_MISMATCH)
    if observation.profile != expected_profile:
        blockers.append(BankingBlocker.BANK_PROFILE_MISMATCH)
    elif (
        observation.provenance.frame.width != expected_profile.frame_width
        or observation.provenance.frame.height != expected_profile.frame_height
    ):
        blockers.append(BankingBlocker.BANK_GEOMETRY_UNSUPPORTED)

    freshness_blocker = _evaluate_freshness(
        observation.provenance,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=previous_provenance,
        max_age_s=max_age_s,
        stale_blocker=BankingBlocker.BANK_EVIDENCE_STALE,
    )
    if freshness_blocker is not None:
        blockers.append(freshness_blocker)

    if observation.confidence < min_confidence:
        blockers.append(BankingBlocker.BANK_CONFIDENCE_BELOW_FLOOR)

    if blockers:
        return BankPerceptionResult(BankInterfaceState.UNKNOWN, tuple(blockers))
    # A clean, on-cycle, on-profile reading that is itself UNKNOWN is genuine
    # sensor ambiguity, not a contract violation -- it passes through with no
    # blocker. Callers that need a workflow-level reason to explain "did not
    # advance" attach one themselves (see banking.workflow).
    return BankPerceptionResult(observation.interface_state, ())


@dataclass(frozen=True, slots=True)
class InventoryPerceptionResult:
    """A resolved inventory reading bound to one banking evidence cycle.

    Mirrors :class:`BankPerceptionResult`: any blocker forces
    ``state.occupied_slots`` to ``None`` so a rejected reading can never be
    read as "known empty" or "known non-empty".
    """

    state: InventoryState
    blockers: tuple[BankingBlocker, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if type(self.state) is not InventoryState:
            raise ValueError("state must be an exact InventoryState")
        if not isinstance(self.blockers, tuple) or any(
            type(blocker) is not BankingBlocker for blocker in self.blockers
        ):
            raise ValueError("blockers must be a tuple of exact BankingBlocker values")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must be unique")
        if self.blockers and self.state.occupied_slots is not None:
            raise ValueError("a rejected inventory reading must resolve to an unknown count")

    @property
    def accepted(self) -> bool:
        return not self.blockers


def evaluate_inventory_observation(
    observation: object | None,
    *,
    evaluated_monotonic_s: object,
    current_provenance: BankEvidenceProvenance | None = None,
    previous_provenance: BankEvidenceProvenance | None = None,
    max_age_s: float = MAX_BANKING_EVIDENCE_AGE_S,
    min_confidence: float = INVENTORY_PUBLICATION_CONFIDENCE_FLOOR,
    expected_capacity: int = INVENTORY_CAPACITY,
) -> InventoryPerceptionResult:
    """Resolve one pre/post-deposit inventory observation for banking use.

    ``observation`` must expose ``.state``, ``.provenance``, ``.detector_id``,
    and ``.detector_version`` -- the shape shared by
    :class:`~mining_automation.banking.contracts.PreDepositInventoryObservation`
    and :class:`~mining_automation.banking.contracts.PostDepositInventoryObservation`.
    This function does not implement inventory perception itself; it only
    binds an already-produced :class:`~mining_automation.contracts.InventoryState`
    to the current banking evidence cycle.

    See :func:`evaluate_bank_observation` for what ``current_provenance``
    means and why a caller with no independent source should leave it
    ``None`` rather than echo the observation's own provenance back at it.
    """
    if current_provenance is not None and type(current_provenance) is not BankEvidenceProvenance:
        raise TypeError("current_provenance must be an exact BankEvidenceProvenance or None")

    if observation is None:
        return InventoryPerceptionResult(
            _unknown_inventory_state(expected_capacity),
            (BankingBlocker.INVENTORY_EVIDENCE_MISSING,),
        )

    state = getattr(observation, "state", None)
    provenance = getattr(observation, "provenance", None)
    if type(state) is not InventoryState or type(provenance) is not BankEvidenceProvenance:
        return InventoryPerceptionResult(
            _unknown_inventory_state(expected_capacity),
            (BankingBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,),
        )

    blockers: list[BankingBlocker] = []
    if current_provenance is not None and provenance != current_provenance:
        blockers.append(BankingBlocker.EVIDENCE_PROVENANCE_MISMATCH)

    freshness_blocker = _evaluate_freshness(
        provenance,
        evaluated_monotonic_s=evaluated_monotonic_s,
        previous_provenance=previous_provenance,
        max_age_s=max_age_s,
        stale_blocker=BankingBlocker.INVENTORY_EVIDENCE_STALE,
    )
    if freshness_blocker is not None:
        blockers.append(freshness_blocker)

    if state.capacity != expected_capacity:
        blockers.append(BankingBlocker.INVENTORY_LAYOUT_MISMATCH)
    if state.occupied_slots is None:
        blockers.append(BankingBlocker.INVENTORY_UNKNOWN)
    elif state.confidence < min_confidence:
        blockers.append(BankingBlocker.INVENTORY_CONFIDENCE_BELOW_FLOOR)

    if blockers:
        return InventoryPerceptionResult(_unknown_inventory_state(expected_capacity), tuple(blockers))
    return InventoryPerceptionResult(state, ())


def _unknown_inventory_state(capacity: int) -> InventoryState:
    return InventoryState(occupied_slots=None, capacity=capacity, confidence=0.0)


def _evaluate_freshness(
    provenance: BankEvidenceProvenance,
    *,
    evaluated_monotonic_s: object,
    previous_provenance: BankEvidenceProvenance | None,
    max_age_s: float,
    stale_blocker: BankingBlocker,
) -> BankingBlocker | None:
    evaluated = _finite_float(evaluated_monotonic_s)
    if evaluated is None:
        return BankingBlocker.EVALUATION_TIME_INVALID
    captured = _finite_float(provenance.frame.captured_monotonic_s)
    if captured is None:  # pragma: no cover - FrameRef already guarantees this is finite
        return BankingBlocker.EVIDENCE_TIMESTAMP_INVALID
    age_s = evaluated - captured
    if age_s < 0.0:
        return BankingBlocker.EVIDENCE_FROM_FUTURE
    if age_s > max_age_s:
        return stale_blocker
    if previous_provenance is not None and (
        provenance.frame.frame_id <= previous_provenance.frame.frame_id
        or captured < previous_provenance.frame.captured_monotonic_s
    ):
        # Both frame_id and captured_monotonic_s must advance together. Checking
        # frame_id alone would accept a crafted/malfunctioning stream where the
        # id increases but the wall-clock timestamp regresses relative to
        # evidence already accepted -- itself a replay/tamper signature, not
        # just a non-advancing frame.
        return BankingBlocker.EVIDENCE_ORDERING_REGRESSION
    return None
