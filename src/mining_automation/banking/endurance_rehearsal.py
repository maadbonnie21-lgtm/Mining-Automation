"""Deterministic synthetic banking fault/endurance evaluator.

This is an offline safety oracle. It recognizes no real client state and has no
retry, capture, click, deposit, release, or input capability.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Literal

from .contracts import INVENTORY_CAPACITY, BankInterfaceState

__all__ = [
    "BankingEndurancePhase",
    "BankingEnduranceReport",
    "BankingFaultKind",
    "SyntheticBankingAttempt",
    "evaluate_synthetic_banking_endurance",
]

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BankingFaultKind(StrEnum):
    NONE = "none"
    INTERRUPTION = "interruption"
    STALE_EVIDENCE = "stale_evidence"
    LATE_EVIDENCE = "late_evidence"
    MIXED_EVIDENCE = "mixed_evidence"
    REPLAY = "replay"
    DUPLICATE_ROOT = "duplicate_root"
    CONTRADICTORY_NEWER_STATE = "contradictory_newer_state"
    TIMEOUT = "timeout"


class BankingEndurancePhase(StrEnum):
    COMPLETE = "complete"
    STOPPED = "stopped"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty exact string")
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class SyntheticBankingAttempt:
    attempt_id: str
    session_id: str
    evidence_package_sha256: str
    acquisition_root_sha256: str
    review_root_sha256: str
    deposit_receipt_id: str
    source_id: str
    pre_frame_id: int
    receipt_frame_id: int
    post_frame_id: int
    started_monotonic_s: float
    completed_monotonic_s: float
    bank_before: BankInterfaceState
    bank_after: BankInterfaceState
    occupied_before: int | None
    occupied_after: int | None
    fault: BankingFaultKind
    observed_success: bool
    retries: Literal[0] = field(default=0, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        text_fields: tuple[tuple[object, str], ...] = (
            (self.attempt_id, "attempt_id"),
            (self.session_id, "session_id"),
            (self.deposit_receipt_id, "deposit_receipt_id"),
            (self.source_id, "source_id"),
        )
        for value, name in text_fields:
            _text(value, name)
        digest_fields: tuple[tuple[object, str], ...] = (
            (self.evidence_package_sha256, "evidence_package_sha256"),
            (self.acquisition_root_sha256, "acquisition_root_sha256"),
            (self.review_root_sha256, "review_root_sha256"),
        )
        for value, name in digest_fields:
            _digest(value, name)
        frame_fields: tuple[tuple[object, str], ...] = (
            (self.pre_frame_id, "pre_frame_id"),
            (self.receipt_frame_id, "receipt_frame_id"),
            (self.post_frame_id, "post_frame_id"),
        )
        for value, name in frame_fields:
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive exact int")
        time_fields: tuple[tuple[object, str], ...] = (
            (self.started_monotonic_s, "started_monotonic_s"),
            (self.completed_monotonic_s, "completed_monotonic_s"),
        )
        for value, name in time_fields:
            if type(value) is not float or not isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be a finite non-negative exact float")
        if self.completed_monotonic_s < self.started_monotonic_s:
            raise ValueError("attempt completion cannot precede start")
        if type(self.bank_before) is not BankInterfaceState:
            raise ValueError("bank_before must be exact")
        if type(self.bank_after) is not BankInterfaceState:
            raise ValueError("bank_after must be exact")
        slot_fields: tuple[tuple[object, str], ...] = (
            (self.occupied_before, "occupied_before"),
            (self.occupied_after, "occupied_after"),
        )
        for value, name in slot_fields:
            if value is not None and (
                type(value) is not int or value < 0 or value > INVENTORY_CAPACITY
            ):
                raise ValueError(f"{name} must be None or 0..28 exact int")
        if type(self.fault) is not BankingFaultKind:
            raise ValueError("fault must be exact")
        if type(self.observed_success) is not bool:
            raise ValueError("observed_success must be exact bool")
        if self.retries != 0 or self.input_authority is not False:
            raise ValueError("synthetic attempt cannot retry or carry input authority")


@dataclass(frozen=True, slots=True)
class BankingEnduranceReport:
    phase: BankingEndurancePhase
    target_successes: int
    completed_successes: int
    evaluated_attempt_ids: tuple[str, ...]
    stop_reason: str | None
    report_sha256: str
    retries: Literal[0] = field(default=0, init=False)
    release_eligible: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.phase) is not BankingEndurancePhase:
            raise ValueError("phase must be exact")
        if type(self.target_successes) is not int or self.target_successes <= 0:
            raise ValueError("target_successes must be positive exact int")
        if type(self.completed_successes) is not int or not (
            0 <= self.completed_successes <= self.target_successes
        ):
            raise ValueError("completed_successes is invalid")
        if type(self.evaluated_attempt_ids) is not tuple or any(
            type(item) is not str or not item for item in self.evaluated_attempt_ids
        ):
            raise ValueError("evaluated attempt ids must be exact strings")
        if self.stop_reason is not None:
            _text(self.stop_reason, "stop_reason")
        _digest(self.report_sha256, "report_sha256")
        if self.retries != 0 or self.release_eligible is not False:
            raise ValueError("endurance report cannot retry or claim release")
        if self.input_authority is not False:
            raise ValueError("endurance report cannot carry input authority")


def _is_verified_success(attempt: SyntheticBankingAttempt) -> tuple[bool, str | None]:
    if attempt.fault is not BankingFaultKind.NONE:
        return False, attempt.fault.value
    if attempt.bank_before is not BankInterfaceState.OPEN:
        return False, "bank_not_open_before_deposit"
    if attempt.bank_after is not BankInterfaceState.OPEN:
        return False, "bank_not_open_after_deposit"
    if attempt.occupied_before is None or attempt.occupied_before <= 0:
        return False, "inventory_not_known_non_empty_before_deposit"
    if attempt.occupied_after != 0:
        return False, "inventory_not_verified_empty_after_deposit"
    if not attempt.pre_frame_id < attempt.receipt_frame_id < attempt.post_frame_id:
        return False, "post_deposit_observation_not_strictly_newer"
    if attempt.observed_success is not True:
        return False, "attempt_not_marked_observed_success"
    return True, None


def _report_digest(
    *,
    phase: BankingEndurancePhase,
    target: int,
    completed: int,
    ids: tuple[str, ...],
    reason: str | None,
) -> str:
    payload = (
        json.dumps(
            {
                "completed_successes": completed,
                "evaluated_attempt_ids": list(ids),
                "input_authority": False,
                "phase": phase.value,
                "release_eligible": False,
                "retries": 0,
                "schema": "synthetic-banking-endurance-v1",
                "stop_reason": reason,
                "target_successes": target,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    return sha256(payload).hexdigest()


def evaluate_synthetic_banking_endurance(
    attempts: tuple[SyntheticBankingAttempt, ...],
    *,
    target_successes: int = 3,
) -> BankingEnduranceReport:
    if type(attempts) is not tuple:
        raise TypeError("attempts must be an exact tuple")
    if type(target_successes) is not int or target_successes <= 0:
        raise ValueError("target_successes must be a positive exact int")

    ids: list[str] = []
    completed = 0
    seen_attempts: set[str] = set()
    seen_sessions: set[str] = set()
    seen_packages: set[str] = set()
    seen_acquisition_roots: set[str] = set()
    seen_review_roots: set[str] = set()
    seen_receipts: set[str] = set()
    previous: SyntheticBankingAttempt | None = None
    stop_reason: str | None = None

    for attempt in attempts:
        if type(attempt) is not SyntheticBankingAttempt:
            raise TypeError("each attempt must be exact")
        SyntheticBankingAttempt.__post_init__(attempt)
        ids.append(attempt.attempt_id)
        if attempt.attempt_id in seen_attempts:
            stop_reason = BankingFaultKind.REPLAY.value
        elif attempt.session_id in seen_sessions:
            stop_reason = "session_replay"
        elif attempt.evidence_package_sha256 in seen_packages:
            stop_reason = BankingFaultKind.REPLAY.value
        elif attempt.acquisition_root_sha256 in seen_acquisition_roots:
            stop_reason = BankingFaultKind.DUPLICATE_ROOT.value
        elif attempt.review_root_sha256 in seen_review_roots:
            stop_reason = BankingFaultKind.DUPLICATE_ROOT.value
        elif attempt.deposit_receipt_id in seen_receipts:
            stop_reason = "deposit_receipt_replay"
        elif previous is not None and (
            attempt.started_monotonic_s <= previous.completed_monotonic_s
            or attempt.pre_frame_id <= previous.post_frame_id
        ):
            stop_reason = "recovery_not_strictly_fresh"
        if stop_reason is not None:
            break

        seen_attempts.add(attempt.attempt_id)
        seen_sessions.add(attempt.session_id)
        seen_packages.add(attempt.evidence_package_sha256)
        seen_acquisition_roots.add(attempt.acquisition_root_sha256)
        seen_review_roots.add(attempt.review_root_sha256)
        seen_receipts.add(attempt.deposit_receipt_id)

        success, failure = _is_verified_success(attempt)
        if not success:
            stop_reason = failure
            break
        completed += 1
        previous = attempt
        if completed == target_successes:
            break

    phase = (
        BankingEndurancePhase.COMPLETE
        if completed == target_successes and stop_reason is None
        else BankingEndurancePhase.STOPPED
    )
    if phase is BankingEndurancePhase.STOPPED and stop_reason is None:
        stop_reason = "target_not_reached"
    evaluated = tuple(ids)
    return BankingEnduranceReport(
        phase=phase,
        target_successes=target_successes,
        completed_successes=completed,
        evaluated_attempt_ids=evaluated,
        stop_reason=stop_reason,
        report_sha256=_report_digest(
            phase=phase,
            target=target_successes,
            completed=completed,
            ids=evaluated,
            reason=stop_reason,
        ),
    )
