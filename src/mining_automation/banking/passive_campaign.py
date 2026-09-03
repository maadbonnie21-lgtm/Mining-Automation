"""Offline-only passive evidence campaign for constrained banking validation.

The campaign owns metadata and ordering only. It does not capture frames, open a
bank, click, move items, authorize input, or convert evidence into release
status. A caller must provide already-owned observations from an independently
controlled source.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Final, Literal

from ..capture import PixelFormat
from .contracts import INVENTORY_CAPACITY, BankInterfaceState

__all__ = [
    "BANK_EVIDENCE_CASE_ORDER",
    "BankEvidenceCampaignIdentity",
    "BankEvidenceCampaignPlan",
    "BankEvidenceCampaignReceipt",
    "BankEvidenceCaseRole",
    "BankEvidenceCaseSpec",
    "BankEvidenceObservation",
    "FinalizedBankEvidenceCampaign",
    "PassiveBankCampaignPhase",
    "PassiveBankCampaignSequencer",
]

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BankEvidenceCaseRole(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"
    OBSTRUCTION = "obstruction"
    AMBIGUITY = "ambiguity"
    WRONG_LOCATION = "wrong_location"
    NON_EMPTY_BEFORE_DEPOSIT = "non_empty_before_deposit"
    EMPTY_AFTER_DEPOSIT = "empty_after_deposit"
    NON_EMPTY_AFTER_DEPOSIT = "non_empty_after_deposit"


BANK_EVIDENCE_CASE_ORDER: Final[tuple[BankEvidenceCaseRole, ...]] = tuple(
    BankEvidenceCaseRole
)


class PassiveBankCampaignPhase(StrEnum):
    COLLECTING = "collecting"
    COMPLETE = "complete"
    FINALIZED = "finalized"
    STOPPED = "stopped"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty exact string")
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


@dataclass(frozen=True, slots=True)
class BankEvidenceCampaignIdentity:
    campaign_id: str
    session_id: str
    capture_source_id: str
    capture_build_id: str
    capture_build_sha256: str
    capture_configuration_sha256: str
    capture_environment_sha256: str
    supported_location_id: str
    frame_width: int
    frame_height: int
    pixel_format: PixelFormat

    def __post_init__(self) -> None:
        for value, name in (
            (self.campaign_id, "campaign_id"),
            (self.session_id, "session_id"),
            (self.capture_source_id, "capture_source_id"),
            (self.capture_build_id, "capture_build_id"),
            (self.supported_location_id, "supported_location_id"),
        ):
            _text(value, name)
        for value, name in (
            (self.capture_build_sha256, "capture_build_sha256"),
            (self.capture_configuration_sha256, "capture_configuration_sha256"),
            (self.capture_environment_sha256, "capture_environment_sha256"),
        ):
            _digest(value, name)
        if type(self.frame_width) is not int or self.frame_width <= 0:
            raise ValueError("frame_width must be a positive exact int")
        if type(self.frame_height) is not int or self.frame_height <= 0:
            raise ValueError("frame_height must be a positive exact int")
        if type(self.pixel_format) is not PixelFormat:
            raise ValueError("pixel_format must be an exact PixelFormat")


@dataclass(frozen=True, slots=True)
class BankEvidenceCaseSpec:
    ordinal: int
    case_id: str
    role: BankEvidenceCaseRole

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal <= 0:
            raise ValueError("case ordinal must be a positive exact int")
        _text(self.case_id, "case_id")
        if type(self.role) is not BankEvidenceCaseRole:
            raise ValueError("case role must be an exact BankEvidenceCaseRole")


@dataclass(frozen=True, slots=True)
class BankEvidenceCampaignPlan:
    identity: BankEvidenceCampaignIdentity
    cases: tuple[BankEvidenceCaseSpec, ...]
    retries_allowed: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    activation_allowed: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.identity) is not BankEvidenceCampaignIdentity:
            raise ValueError("campaign identity must be exact")
        BankEvidenceCampaignIdentity.__post_init__(self.identity)
        if type(self.cases) is not tuple:
            raise ValueError("campaign cases must be an exact tuple")
        if len(self.cases) != len(BANK_EVIDENCE_CASE_ORDER):
            raise ValueError("campaign must contain the exact fixed case count")
        seen: set[str] = set()
        roles: list[BankEvidenceCaseRole] = []
        for expected_ordinal, case in enumerate(self.cases, start=1):
            if type(case) is not BankEvidenceCaseSpec:
                raise ValueError("campaign case must be exact")
            BankEvidenceCaseSpec.__post_init__(case)
            if case.ordinal != expected_ordinal:
                raise ValueError("campaign cases must have contiguous ordinals")
            if case.case_id in seen:
                raise ValueError("campaign case ids must be unique")
            seen.add(case.case_id)
            roles.append(case.role)
        if tuple(roles) != BANK_EVIDENCE_CASE_ORDER:
            raise ValueError("campaign roles must use the fixed source order")
        if self.retries_allowed is not False:
            raise ValueError("passive campaign cannot retry")
        if self.input_authority is not False or self.activation_allowed is not False:
            raise ValueError("passive campaign cannot carry authority")


@dataclass(frozen=True, slots=True)
class BankEvidenceObservation:
    case_id: str
    role: BankEvidenceCaseRole
    campaign_id: str
    session_id: str
    capture_source_id: str
    capture_build_sha256: str
    capture_configuration_sha256: str
    capture_environment_sha256: str
    observed_location_id: str
    frame_id: int
    cycle_id: str
    captured_monotonic_s: float
    frame_width: int
    frame_height: int
    pixel_format: PixelFormat
    frame_sha256: str
    bank_state: BankInterfaceState
    inventory_occupied_slots: int | None
    confidence: float
    synthetic_or_unreleased: Literal[True] = field(default=True, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.case_id, "case_id"),
            (self.campaign_id, "campaign_id"),
            (self.session_id, "session_id"),
            (self.capture_source_id, "capture_source_id"),
            (self.observed_location_id, "observed_location_id"),
            (self.cycle_id, "cycle_id"),
        ):
            _text(value, name)
        for value, name in (
            (self.capture_build_sha256, "capture_build_sha256"),
            (self.capture_configuration_sha256, "capture_configuration_sha256"),
            (self.capture_environment_sha256, "capture_environment_sha256"),
            (self.frame_sha256, "frame_sha256"),
        ):
            _digest(value, name)
        if type(self.role) is not BankEvidenceCaseRole:
            raise ValueError("observation role must be exact")
        if type(self.frame_id) is not int or self.frame_id <= 0:
            raise ValueError("frame_id must be a positive exact int")
        if type(self.captured_monotonic_s) is not float or not isfinite(
            self.captured_monotonic_s
        ):
            raise ValueError("capture time must be a finite exact float")
        if self.captured_monotonic_s < 0.0:
            raise ValueError("capture time must be non-negative")
        if type(self.frame_width) is not int or self.frame_width <= 0:
            raise ValueError("frame_width must be a positive exact int")
        if type(self.frame_height) is not int or self.frame_height <= 0:
            raise ValueError("frame_height must be a positive exact int")
        if type(self.pixel_format) is not PixelFormat:
            raise ValueError("pixel_format must be exact")
        if type(self.bank_state) is not BankInterfaceState:
            raise ValueError("bank_state must be exact")
        slots = self.inventory_occupied_slots
        if slots is not None and (
            type(slots) is not int or slots < 0 or slots > INVENTORY_CAPACITY
        ):
            raise ValueError("inventory slots must be None or 0..28 exact int")
        if type(self.confidence) is not float or not isfinite(self.confidence):
            raise ValueError("confidence must be a finite exact float")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within 0..1")
        self._validate_role_semantics()
        if self.synthetic_or_unreleased is not True or self.input_authority is not False:
            raise ValueError("observation cannot carry release or input authority")

    def _validate_role_semantics(self) -> None:
        if self.role is BankEvidenceCaseRole.OPEN:
            if self.bank_state is not BankInterfaceState.OPEN or self.confidence < 0.8:
                raise ValueError("OPEN case requires definitive OPEN evidence")
        elif self.role is BankEvidenceCaseRole.CLOSED:
            if self.bank_state is not BankInterfaceState.CLOSED or self.confidence < 0.8:
                raise ValueError("CLOSED case requires definitive CLOSED evidence")
        elif self.role in {
            BankEvidenceCaseRole.UNKNOWN,
            BankEvidenceCaseRole.OBSTRUCTION,
            BankEvidenceCaseRole.AMBIGUITY,
            BankEvidenceCaseRole.WRONG_LOCATION,
        }:
            if self.bank_state is not BankInterfaceState.UNKNOWN:
                raise ValueError("negative bank case must remain UNKNOWN")
        elif self.role is BankEvidenceCaseRole.NON_EMPTY_BEFORE_DEPOSIT:
            if (
                self.bank_state is not BankInterfaceState.OPEN
                or self.inventory_occupied_slots is None
                or self.inventory_occupied_slots <= 0
                or self.confidence < 0.8
            ):
                raise ValueError("pre-deposit case requires OPEN and known non-empty")
        elif self.role is BankEvidenceCaseRole.EMPTY_AFTER_DEPOSIT:
            if (
                self.bank_state is not BankInterfaceState.OPEN
                or self.inventory_occupied_slots != 0
                or self.confidence < 0.8
            ):
                raise ValueError("empty-after case requires OPEN and exact empty")
        elif self.role is BankEvidenceCaseRole.NON_EMPTY_AFTER_DEPOSIT:
            if (
                self.bank_state is not BankInterfaceState.OPEN
                or self.inventory_occupied_slots is None
                or self.inventory_occupied_slots <= 0
                or self.confidence < 0.8
            ):
                raise ValueError("non-empty-after case requires OPEN and known non-empty")


@dataclass(frozen=True, slots=True)
class FinalizedBankEvidenceCampaign:
    plan: BankEvidenceCampaignPlan
    observations: tuple[BankEvidenceObservation, ...]
    package_sha256: str
    release_eligible: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.plan) is not BankEvidenceCampaignPlan:
            raise ValueError("finalized plan must be exact")
        if type(self.observations) is not tuple:
            raise ValueError("finalized observations must be an exact tuple")
        _digest(self.package_sha256, "package_sha256")
        if len(self.observations) != len(self.plan.cases):
            raise ValueError("finalized package must contain every fixed case")
        if self.release_eligible is not False or self.input_authority is not False:
            raise ValueError("finalized package cannot grant authority")
        expected = _campaign_digest(self.plan, self.observations)
        if self.package_sha256 != expected:
            raise ValueError("finalized package digest differs")


@dataclass(frozen=True, slots=True)
class BankEvidenceCampaignReceipt:
    campaign_id: str
    session_id: str
    package_sha256: str
    case_count: int
    release_eligible: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _text(self.campaign_id, "campaign_id")
        _text(self.session_id, "session_id")
        _digest(self.package_sha256, "package_sha256")
        if type(self.case_count) is not int or self.case_count != len(
            BANK_EVIDENCE_CASE_ORDER
        ):
            raise ValueError("receipt case count must match the fixed campaign")
        if self.release_eligible is not False or self.input_authority is not False:
            raise ValueError("receipt cannot grant authority")


def _plan_payload(plan: BankEvidenceCampaignPlan) -> dict[str, object]:
    identity = plan.identity
    return {
        "activation_allowed": False,
        "cases": [
            {"case_id": case.case_id, "ordinal": case.ordinal, "role": case.role.value}
            for case in plan.cases
        ],
        "identity": {
            "campaign_id": identity.campaign_id,
            "capture_build_id": identity.capture_build_id,
            "capture_build_sha256": identity.capture_build_sha256,
            "capture_configuration_sha256": identity.capture_configuration_sha256,
            "capture_environment_sha256": identity.capture_environment_sha256,
            "capture_source_id": identity.capture_source_id,
            "frame_height": identity.frame_height,
            "frame_width": identity.frame_width,
            "pixel_format": identity.pixel_format.value,
            "session_id": identity.session_id,
            "supported_location_id": identity.supported_location_id,
        },
        "input_authority": False,
        "retries_allowed": False,
        "schema": "passive-bank-evidence-campaign-v1",
    }


def _observation_payload(observation: BankEvidenceObservation) -> dict[str, object]:
    return {
        "bank_state": observation.bank_state.value,
        "campaign_id": observation.campaign_id,
        "capture_build_sha256": observation.capture_build_sha256,
        "capture_configuration_sha256": observation.capture_configuration_sha256,
        "capture_environment_sha256": observation.capture_environment_sha256,
        "capture_source_id": observation.capture_source_id,
        "captured_monotonic_s": observation.captured_monotonic_s,
        "case_id": observation.case_id,
        "confidence": observation.confidence,
        "cycle_id": observation.cycle_id,
        "frame_height": observation.frame_height,
        "frame_id": observation.frame_id,
        "frame_sha256": observation.frame_sha256,
        "frame_width": observation.frame_width,
        "input_authority": False,
        "inventory_occupied_slots": observation.inventory_occupied_slots,
        "observed_location_id": observation.observed_location_id,
        "pixel_format": observation.pixel_format.value,
        "role": observation.role.value,
        "session_id": observation.session_id,
        "synthetic_or_unreleased": True,
    }


def _campaign_digest(
    plan: BankEvidenceCampaignPlan,
    observations: tuple[BankEvidenceObservation, ...],
) -> str:
    return sha256(
        _canonical_bytes(
            {
                "observations": [_observation_payload(item) for item in observations],
                "plan": _plan_payload(plan),
                "schema": "finalized-passive-bank-evidence-v1",
            }
        )
    ).hexdigest()


class PassiveBankCampaignSequencer:
    """Absorbing, one-pass source-order reducer for externally owned observations."""

    __slots__ = ("_observations", "_phase", "_plan", "_stop_reason")

    def __init__(self, plan: BankEvidenceCampaignPlan) -> None:
        if type(plan) is not BankEvidenceCampaignPlan:
            raise TypeError("plan must be an exact BankEvidenceCampaignPlan")
        BankEvidenceCampaignPlan.__post_init__(plan)
        self._plan = plan
        self._observations: list[BankEvidenceObservation] = []
        self._phase = PassiveBankCampaignPhase.COLLECTING
        self._stop_reason: str | None = None

    @property
    def phase(self) -> PassiveBankCampaignPhase:
        return self._phase

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @property
    def observations(self) -> tuple[BankEvidenceObservation, ...]:
        return tuple(self._observations)

    def stop(self, reason: str) -> None:
        if self._phase in {
            PassiveBankCampaignPhase.FINALIZED,
            PassiveBankCampaignPhase.STOPPED,
        }:
            return
        self._stop_reason = _text(reason, "stop reason")
        self._phase = PassiveBankCampaignPhase.STOPPED

    def append(self, observation: BankEvidenceObservation) -> None:
        if self._phase is not PassiveBankCampaignPhase.COLLECTING:
            raise RuntimeError("campaign is not collecting")
        if type(observation) is not BankEvidenceObservation:
            self.stop("observation_type_invalid")
            raise TypeError("observation must be exact")
        try:
            BankEvidenceObservation.__post_init__(observation)
            expected = self._plan.cases[len(self._observations)]
            self._validate_identity(observation)
            if observation.case_id != expected.case_id or observation.role is not expected.role:
                raise ValueError("case is not the exact next source-owned case")
            if self._observations:
                previous = self._observations[-1]
                if observation.frame_id <= previous.frame_id:
                    raise ValueError("frame_id must be strictly newer")
                if observation.captured_monotonic_s <= previous.captured_monotonic_s:
                    raise ValueError("capture time must be strictly newer")
                if observation.cycle_id == previous.cycle_id:
                    raise ValueError("cycle_id must be fresh")
            self._observations.append(observation)
        except (TypeError, ValueError) as exc:
            self.stop(str(exc))
            raise
        if len(self._observations) == len(self._plan.cases):
            self._phase = PassiveBankCampaignPhase.COMPLETE

    def _validate_identity(self, observation: BankEvidenceObservation) -> None:
        identity = self._plan.identity
        if observation.campaign_id != identity.campaign_id:
            raise ValueError("campaign identity differs")
        if observation.session_id != identity.session_id:
            raise ValueError("session identity differs")
        if observation.capture_source_id != identity.capture_source_id:
            raise ValueError("capture source identity differs")
        if observation.capture_build_sha256 != identity.capture_build_sha256:
            raise ValueError("capture build identity differs")
        if (
            observation.capture_configuration_sha256
            != identity.capture_configuration_sha256
        ):
            raise ValueError("capture configuration identity differs")
        if observation.capture_environment_sha256 != identity.capture_environment_sha256:
            raise ValueError("capture environment identity differs")
        if (
            observation.frame_width != identity.frame_width
            or observation.frame_height != identity.frame_height
            or observation.pixel_format is not identity.pixel_format
        ):
            raise ValueError("frame contract differs")
        if observation.role is BankEvidenceCaseRole.WRONG_LOCATION:
            if observation.observed_location_id == identity.supported_location_id:
                raise ValueError("wrong-location case did not use a foreign location")
        elif observation.observed_location_id != identity.supported_location_id:
            raise ValueError("observation location differs")

    def finalize(
        self,
    ) -> tuple[FinalizedBankEvidenceCampaign, BankEvidenceCampaignReceipt]:
        if self._phase is not PassiveBankCampaignPhase.COMPLETE:
            raise RuntimeError("campaign must be complete before finalization")
        observations = tuple(self._observations)
        digest = _campaign_digest(self._plan, observations)
        package = FinalizedBankEvidenceCampaign(self._plan, observations, digest)
        receipt = BankEvidenceCampaignReceipt(
            campaign_id=self._plan.identity.campaign_id,
            session_id=self._plan.identity.session_id,
            package_sha256=digest,
            case_count=len(observations),
        )
        self._phase = PassiveBankCampaignPhase.FINALIZED
        return package, receipt
