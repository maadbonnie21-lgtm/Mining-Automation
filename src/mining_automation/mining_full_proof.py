"""Validate one uninterrupted real mining-only result to exact 28/28.

The validator accepts only the concise JSON emitted by
``tools/run_mining_to_full.py``.  It grants no release or input authority and
never upgrades replay/synthetic data into real-client evidence.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, final

_EXPECTED_GEOMETRY: Final[tuple[int, int]] = (1005, 1078)
_EXPECTED_DPI: Final[int] = 96
_EXPECTED_ACTION: Final[str] = "Mine Iron rocks"
_EXPECTED_RESOURCE_THRESHOLD: Final[float] = 0.12
_EXPECTED_RESOURCE_LANDMARKS: Final[int] = 6
_EXPECTED_RESOURCE_QUORUM: Final[int] = 5
_EXPECTED_RESOURCE_ZONES: Final[int] = 3
_EXPECTED_INVENTORY_FLOOR: Final[float] = 0.8
_EXPECTED_INVENTORY_CAPACITY: Final[int] = 28
_GIT_SHA_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")


class MiningFullProofError(ValueError):
    """Raised when a claimed real 28/28 proof is incomplete or inconsistent."""


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MiningFullProofError(f"{label} must be an object")
    if any(type(key) is not str for key in value):
        raise MiningFullProofError(f"{label} keys must be exact strings")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise MiningFullProofError(f"{label} must be an exact list or tuple")
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise MiningFullProofError(f"{label} must be a non-empty exact string")
    return value


def _int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise MiningFullProofError(
            f"{label} must be an exact integer >= {minimum}"
        )
    return value


def _float(value: object, label: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise MiningFullProofError(f"{label} must be a finite exact float")
    return value


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise MiningFullProofError(f"{label} must be an exact bool")
    return value


def _required_equal(
    record: Mapping[str, object],
    key: str,
    expected: object,
    *,
    label: str,
) -> None:
    if record.get(key) != expected or type(record.get(key)) is not type(expected):
        raise MiningFullProofError(
            f"{label}.{key} must equal exact {expected!r}"
        )


def _event_list(
    events: Sequence[object],
    kind: str,
) -> list[Mapping[str, object]]:
    result: list[Mapping[str, object]] = []
    for index, value in enumerate(events):
        event = _mapping(value, f"events[{index}]")
        if event.get("kind") == kind:
            result.append(event)
    return result


def _one_event_per_iteration(
    events: list[Mapping[str, object]],
    kind: str,
    expected_count: int,
) -> dict[int, Mapping[str, object]]:
    if len(events) != expected_count:
        raise MiningFullProofError(
            f"{kind} event count {len(events)} != {expected_count}"
        )
    indexed: dict[int, Mapping[str, object]] = {}
    for event in events:
        iteration = _int(event.get("iteration"), f"{kind}.iteration", minimum=1)
        if iteration in indexed:
            raise MiningFullProofError(
                f"{kind} contains duplicate iteration {iteration}"
            )
        indexed[iteration] = event
    expected = set(range(1, expected_count + 1))
    if set(indexed) != expected:
        raise MiningFullProofError(
            f"{kind} iterations {sorted(indexed)} != {sorted(expected)}"
        )
    return indexed


@final
@dataclass(frozen=True, slots=True)
class MiningFullProofReceipt:
    run_id: str
    git_sha: str
    runelite_hwnd: int
    start_inventory: int
    end_inventory: Literal[28]
    verified_ores: int
    click_count: int
    target_sequence: tuple[str, ...]
    dispatch_ids: tuple[str, ...]
    distinct_targets: tuple[str, ...]
    final_inventory_confidence: float
    source_result_sha256: str | None = None
    real_client_proof: Literal[True] = field(default=True, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    navigation_authority: Literal[False] = field(default=False, init=False)
    banking_authority: Literal[False] = field(default=False, init=False)
    release_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        if _GIT_SHA_RE.fullmatch(self.git_sha) is None:
            raise MiningFullProofError("git_sha must be a lowercase exact Git SHA")
        _int(self.runelite_hwnd, "runelite_hwnd", minimum=1)
        if not 0 <= self.start_inventory < _EXPECTED_INVENTORY_CAPACITY:
            raise MiningFullProofError("start_inventory must be in 0..27")
        if self.end_inventory != _EXPECTED_INVENTORY_CAPACITY:
            raise MiningFullProofError("end_inventory must equal exact 28")
        expected = self.end_inventory - self.start_inventory
        if self.verified_ores != expected or self.click_count != expected:
            raise MiningFullProofError("proof ore/click accounting is inconsistent")
        if len(self.target_sequence) != expected:
            raise MiningFullProofError("target_sequence length is inconsistent")
        if len(self.dispatch_ids) != expected or len(set(self.dispatch_ids)) != expected:
            raise MiningFullProofError("dispatch_ids must be unique and complete")
        if tuple(sorted(set(self.target_sequence))) != self.distinct_targets:
            raise MiningFullProofError("distinct_targets does not match target_sequence")
        if not _EXPECTED_INVENTORY_FLOOR <= self.final_inventory_confidence <= 1.0:
            raise MiningFullProofError("final Inventory confidence is below floor")
        if (
            self.source_result_sha256 is not None
            and _SHA256_RE.fullmatch(self.source_result_sha256) is None
        ):
            raise MiningFullProofError("source_result_sha256 must be lowercase SHA-256")
        if (
            self.real_client_proof is not True
            or self.input_authority is not False
            or self.navigation_authority is not False
            or self.banking_authority is not False
            or self.release_authority is not False
        ):
            raise MiningFullProofError("proof receipt cannot create downstream authority")


def validate_mining_to_full_result(
    payload: object,
    *,
    expected_git_sha: str | None = None,
    source_result_sha256: str | None = None,
) -> MiningFullProofReceipt:
    """Validate the complete real-client result graph and return a deny-only receipt."""

    result = _mapping(payload, "result")
    run_id = _text(result.get("run_id"), "result.run_id")
    git_sha = _text(result.get("git_sha"), "result.git_sha")
    if _GIT_SHA_RE.fullmatch(git_sha) is None:
        raise MiningFullProofError("result.git_sha must be a lowercase exact Git SHA")
    if expected_git_sha is not None and git_sha != expected_git_sha:
        raise MiningFullProofError("result.git_sha does not match expected exact head")
    runelite_hwnd = _int(result.get("runelite_hwnd"), "result.runelite_hwnd", minimum=1)

    geometry = _sequence(
        result.get("expected_client_geometry"),
        "result.expected_client_geometry",
    )
    if tuple(geometry) != _EXPECTED_GEOMETRY:
        raise MiningFullProofError("client geometry must be exact 1005x1078")
    if result.get("expected_dpi") != _EXPECTED_DPI:
        raise MiningFullProofError("expected_dpi must equal exact 96")

    _required_equal(result, "success", True, label="result")
    _required_equal(result, "real_client_success", True, label="result")
    _required_equal(result, "evidence_origin", "real_client_live_run", label="result")
    _required_equal(result, "raw_frames_committed", False, label="result")
    _required_equal(result, "phase", "complete", label="result")
    _required_equal(result, "stop_reason", "inventory_full", label="result")
    _required_equal(result, "state_stop_reason", "inventory_full", label="result")

    start = _int(result.get("start_inventory"), "result.start_inventory")
    if start >= _EXPECTED_INVENTORY_CAPACITY:
        raise MiningFullProofError("result.start_inventory must be below 28")
    end = _int(result.get("end_inventory"), "result.end_inventory")
    if end != _EXPECTED_INVENTORY_CAPACITY:
        raise MiningFullProofError("result.end_inventory must equal exact 28")
    confidence = _float(
        result.get("inventory_confidence"),
        "result.inventory_confidence",
    )
    if not _EXPECTED_INVENTORY_FLOOR <= confidence <= 1.0:
        raise MiningFullProofError("final Inventory confidence is below 0.8")

    expected_ores = end - start
    verified_ores = _int(result.get("verified_ores"), "result.verified_ores")
    click_count = _int(result.get("click_count"), "result.click_count")
    attempt_count = _int(result.get("attempt_count"), "result.attempt_count")
    if not (
        verified_ores == click_count == attempt_count == expected_ores
    ):
        raise MiningFullProofError(
            "verified_ores, click_count, attempt_count, and Inventory delta must match"
        )

    target_values = _sequence(result.get("target_sequence"), "result.target_sequence")
    targets = tuple(
        _text(value, f"result.target_sequence[{index}]")
        for index, value in enumerate(target_values)
    )
    if len(targets) != expected_ores:
        raise MiningFullProofError("target sequence is not complete")
    if any("iron" not in target.lower() for target in targets):
        raise MiningFullProofError("target sequence contains a non-iron identity")

    distinct_values = _sequence(result.get("distinct_targets"), "result.distinct_targets")
    distinct = tuple(
        _text(value, f"result.distinct_targets[{index}]")
        for index, value in enumerate(distinct_values)
    )
    if distinct != tuple(sorted(set(targets))):
        raise MiningFullProofError("distinct_targets does not match target_sequence")

    dispatch_values = _sequence(result.get("dispatch_ids"), "result.dispatch_ids")
    dispatch_ids = tuple(
        _text(value, f"result.dispatch_ids[{index}]")
        for index, value in enumerate(dispatch_values)
    )
    if len(dispatch_ids) != expected_ores or len(set(dispatch_ids)) != expected_ores:
        raise MiningFullProofError("dispatch IDs are replayed or incomplete")

    invariants = _mapping(result.get("invariants"), "result.invariants")
    exact_invariants: tuple[tuple[str, object], ...] = (
        ("resource_threshold", _EXPECTED_RESOURCE_THRESHOLD),
        ("resource_landmarks", _EXPECTED_RESOURCE_LANDMARKS),
        ("resource_quorum", _EXPECTED_RESOURCE_QUORUM),
        ("resource_zones_required", _EXPECTED_RESOURCE_ZONES),
        ("inventory_floor", _EXPECTED_INVENTORY_FLOOR),
        ("inventory_capacity", _EXPECTED_INVENTORY_CAPACITY),
        ("exact_hover_action", _EXPECTED_ACTION),
        ("maximum_clicks_per_attempt", 1),
        ("blind_retry", False),
        ("navigation_started_on_full", False),
    )
    for key, expected in exact_invariants:
        _required_equal(invariants, key, expected, label="result.invariants")

    events = _sequence(result.get("events"), "result.events")
    hovers = _one_event_per_iteration(
        _event_list(events, "hover_proof"),
        "hover_proof",
        expected_ores,
    )
    clicks = _one_event_per_iteration(
        _event_list(events, "single_click_attempt"),
        "single_click_attempt",
        expected_ores,
    )
    progress = _one_event_per_iteration(
        _event_list(events, "verified_progress"),
        "verified_progress",
        expected_ores,
    )

    initial_clean = _event_list(events, "initial_clean_observation")
    reacquired = _event_list(events, "post_progress_clean_reacquisition")
    if len(initial_clean) != 1 or len(reacquired) != expected_ores:
        raise MiningFullProofError(
            "every attempt requires one initial/fresh clean observation graph"
        )
    initial = initial_clean[0]
    if initial.get("inventory_occupied") != start:
        raise MiningFullProofError("initial clean Inventory does not match start")
    if initial.get("publication_status") != "ready":
        raise MiningFullProofError("initial clean state was not READY")
    _required_equal(initial, "neutral_cursor_proven", True, label="initial_clean")
    if initial.get("selected_target_id") != targets[0]:
        raise MiningFullProofError("initial clean selected target is inconsistent")

    passive_events = _event_list(events, "passive_verification")
    passive_by_iteration: dict[int, list[Mapping[str, object]]] = {}
    for event in passive_events:
        iteration = _int(
            event.get("iteration"),
            "passive_verification.iteration",
            minimum=1,
        )
        passive_by_iteration.setdefault(iteration, []).append(event)

    for iteration in range(1, expected_ores + 1):
        target = targets[iteration - 1]
        dispatch_id = dispatch_ids[iteration - 1]
        hover = hovers[iteration]
        click = clicks[iteration]
        verified = progress[iteration]

        if hover.get("target_id") != target:
            raise MiningFullProofError(f"iteration {iteration} hover target is crossed")
        _required_equal(hover, "action_text", _EXPECTED_ACTION, label="hover")
        _required_equal(hover, "interaction_proven", True, label="hover")
        _required_equal(hover, "cursor_matches_target", True, label="hover")
        if (
            hover.get("window_hwnd") != runelite_hwnd
            or hover.get("foreground_hwnd") != runelite_hwnd
            or hover.get("root_window_hwnd") != runelite_hwnd
        ):
            raise MiningFullProofError(
                f"iteration {iteration} hover lost exact RuneLite HWND"
            )

        if click.get("target_id") != target or click.get("dispatch_id") != dispatch_id:
            raise MiningFullProofError(f"iteration {iteration} click identity is crossed")
        _required_equal(click, "click_count", 1, label="click")
        _required_equal(click, "dispatch_succeeded", True, label="click")
        _required_equal(
            click,
            "coordinate_round_trip_exact",
            True,
            label="click",
        )
        if (
            click.get("window_hwnd") != runelite_hwnd
            or click.get("foreground_hwnd") != runelite_hwnd
            or click.get("root_window_hwnd") != runelite_hwnd
        ):
            raise MiningFullProofError(
                f"iteration {iteration} click lost exact RuneLite HWND"
            )
        if click.get("client_point") != hover.get("client_point"):
            raise MiningFullProofError(f"iteration {iteration} client point changed")
        if click.get("screen_point") != hover.get("screen_point"):
            raise MiningFullProofError(f"iteration {iteration} screen point changed")

        before = start + iteration - 1
        after = before + 1
        if (
            verified.get("target_id") != target
            or verified.get("dispatch_id") != dispatch_id
            or verified.get("inventory_before") != before
            or verified.get("inventory_after") != after
        ):
            raise MiningFullProofError(
                f"iteration {iteration} verified progress accounting is inconsistent"
            )
        if verified.get("progress_kind") not in {
            "inventory_incremented",
            "resource_depleted_and_inventory_incremented",
        }:
            raise MiningFullProofError(
                f"iteration {iteration} lacks accepted exact +1 progress"
            )
        expected_phase = "complete" if iteration == expected_ores else "ready"
        if verified.get("next_phase") != expected_phase:
            raise MiningFullProofError(
                f"iteration {iteration} next phase is not {expected_phase}"
            )
        if iteration == expected_ores and verified.get("next_target_id") is not None:
            raise MiningFullProofError("FULL result exposes a 29th target")

        passive = passive_by_iteration.get(iteration, [])
        if not passive:
            raise MiningFullProofError(
                f"iteration {iteration} has no passive post-click evidence"
            )
        last = passive[-1]
        if last.get("inventory_occupied") != after or last.get("inventory_delta") != 1:
            raise MiningFullProofError(
                f"iteration {iteration} final passive evidence is not exact +1"
            )
        passive_frames: list[int] = []
        for observation in passive:
            occupied = observation.get("inventory_occupied")
            delta = observation.get("inventory_delta")
            if type(occupied) is not int or type(delta) is not int:
                raise MiningFullProofError(
                    f"iteration {iteration} passive Inventory became UNKNOWN"
                )
            if delta not in {0, 1}:
                raise MiningFullProofError(
                    f"iteration {iteration} passive Inventory delta is ambiguous"
                )
            observed_confidence = _float(
                observation.get("inventory_confidence"),
                "passive.inventory_confidence",
            )
            if observed_confidence < _EXPECTED_INVENTORY_FLOOR:
                raise MiningFullProofError(
                    f"iteration {iteration} passive Inventory fell below floor"
                )
            passive_frames.append(
                _int(observation.get("frame_id"), "passive.frame_id")
            )
        if passive_frames != sorted(set(passive_frames)):
            raise MiningFullProofError(
                f"iteration {iteration} passive frame identities replay or go backward"
            )

        clean = reacquired[iteration - 1]
        if clean.get("inventory_occupied") != after:
            raise MiningFullProofError(
                f"iteration {iteration} clean reacquisition does not retain +1"
            )
        _required_equal(clean, "neutral_cursor_proven", True, label="reacquired")
        expected_status = "full" if iteration == expected_ores else "ready"
        if clean.get("publication_status") != expected_status:
            raise MiningFullProofError(
                f"iteration {iteration} clean publication is not {expected_status}"
            )
        if iteration == expected_ores:
            if clean.get("selected_target_id") is not None:
                raise MiningFullProofError("final 28/28 clean state exposes a target")
        elif clean.get("selected_target_id") != targets[iteration]:
            raise MiningFullProofError(
                f"iteration {iteration} did not reacquire the next current-state target"
            )

    return MiningFullProofReceipt(
        run_id=run_id,
        git_sha=git_sha,
        runelite_hwnd=runelite_hwnd,
        start_inventory=start,
        end_inventory=28,
        verified_ores=verified_ores,
        click_count=click_count,
        target_sequence=targets,
        dispatch_ids=dispatch_ids,
        distinct_targets=distinct,
        final_inventory_confidence=confidence,
        source_result_sha256=source_result_sha256,
    )
