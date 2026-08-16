"""Strict conversion from generic inventory observations to shared state."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, TypeGuard

from ...contracts import FrameRef, InventoryState, Observation
from .classification import SlotDecision, SlotOccupancy
from .detector import (
    INVENTORY_EVIDENCE_SCHEMA_VERSION,
    INVENTORY_OBSERVATION_KIND,
    InventoryDetection,
)
from .geometry import INVENTORY_CAPACITY, Region

__all__ = ["InventoryObservationError", "inventory_state_from_observation"]

_EVIDENCE_KEYS = frozenset(
    {
        "evidence_schema_version",
        "label",
        "region",
        "occupied_slots",
        "capacity",
        "reason",
        "localization_confidence",
        "configuration_id",
        "profile_id",
        "slots",
    }
)
_SLOT_KEYS = frozenset(
    {
        "index",
        "row",
        "column",
        "region",
        "state",
        "confidence",
        "score",
        "changed_fraction",
    }
)


class InventoryObservationError(ValueError):
    """An observation cannot be safely interpreted as an inventory state."""


def _is_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InventoryObservationError(f"{path} must be a finite number")
    if isinstance(value, int):
        try:
            return float(value)
        except OverflowError as exc:
            raise InventoryObservationError(
                f"{path} must be a finite number"
            ) from exc
    if not math.isfinite(value):
        raise InventoryObservationError(f"{path} must be a finite number")
    return value


def _integer(value: object, path: str) -> int:
    if not _is_integer(value):
        raise InventoryObservationError(f"{path} must be an integer")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InventoryObservationError(f"{path} must be a non-empty string")
    return value


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InventoryObservationError(f"{path} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise InventoryObservationError(f"{path} keys must be strings")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], path: str) -> None:
    actual = frozenset(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append("missing " + ", ".join(missing))
    if unknown:
        details.append("unknown " + ", ".join(unknown))
    raise InventoryObservationError(f"{path} fields invalid: {'; '.join(details)}")


def _region(value: object, path: str) -> Region:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 4
    ):
        raise InventoryObservationError(f"{path} must contain four integers")
    components = tuple(_integer(component, f"{path}[{index}]") for index, component in enumerate(value))
    try:
        return Region(*components)
    except (TypeError, ValueError) as exc:
        raise InventoryObservationError(f"{path} is invalid: {exc}") from exc


def _slot(value: object, position: int) -> SlotDecision:
    path = f"evidence.slots[{position}]"
    raw = _mapping(value, path)
    _exact_keys(raw, _SLOT_KEYS, path)
    raw_state = raw["state"]
    if not isinstance(raw_state, str):
        raise InventoryObservationError(f"{path}.state must be a string")
    try:
        state = SlotOccupancy(raw_state)
    except ValueError as exc:
        raise InventoryObservationError(
            f"{path}.state is not a supported slot occupancy"
        ) from exc

    try:
        return SlotDecision(
            index=_integer(raw["index"], f"{path}.index"),
            row=_integer(raw["row"], f"{path}.row"),
            column=_integer(raw["column"], f"{path}.column"),
            region=_region(raw["region"], f"{path}.region"),
            state=state,
            confidence=_number(raw["confidence"], f"{path}.confidence"),
            score=_number(raw["score"], f"{path}.score"),
            changed_fraction=_number(
                raw["changed_fraction"], f"{path}.changed_fraction"
            ),
        )
    except (TypeError, ValueError) as exc:
        raise InventoryObservationError(f"{path} is invalid: {exc}") from exc


def inventory_state_from_observation(observation: Observation) -> InventoryState:
    """Validate an inventory observation and produce the shared state contract.

    This is intentionally the only place outside the detector that understands
    its evidence schema.  State/controller code receives :class:`InventoryState`
    and never indexes detector-private dictionaries.
    """
    if not isinstance(observation, Observation):
        raise InventoryObservationError(
            f"observation must be Observation, got {type(observation).__name__}"
        )
    if observation.kind != INVENTORY_OBSERVATION_KIND:
        raise InventoryObservationError(
            f"observation kind must be {INVENTORY_OBSERVATION_KIND!r}, "
            f"got {observation.kind!r}"
        )
    if not isinstance(observation.frame, FrameRef):
        raise InventoryObservationError(
            "observation frame must be FrameRef, "
            f"got {type(observation.frame).__name__}"
        )

    evidence = _mapping(observation.evidence, "evidence")
    _exact_keys(evidence, _EVIDENCE_KEYS, "evidence")
    schema_version = _integer(
        evidence["evidence_schema_version"], "evidence.evidence_schema_version"
    )
    if schema_version != INVENTORY_EVIDENCE_SCHEMA_VERSION:
        raise InventoryObservationError(
            "unsupported inventory evidence schema version " f"{schema_version}"
        )
    capacity = _integer(evidence["capacity"], "evidence.capacity")
    if capacity != INVENTORY_CAPACITY:
        raise InventoryObservationError(
            f"evidence.capacity must be {INVENTORY_CAPACITY}, got {capacity}"
        )

    raw_region = evidence["region"]
    parsed_region = None if raw_region is None else _region(raw_region, "evidence.region")
    if parsed_region is not None and not parsed_region.fits(
        observation.frame.width, observation.frame.height
    ):
        raise InventoryObservationError(
            f"evidence.region {parsed_region.as_tuple()} does not fit observation frame "
            f"{observation.frame.width}x{observation.frame.height}"
        )
    raw_occupied_slots = evidence["occupied_slots"]
    occupied_slots = (
        None
        if raw_occupied_slots is None
        else _integer(raw_occupied_slots, "evidence.occupied_slots")
    )
    label = _string(evidence["label"], "evidence.label")
    raw_reason = evidence["reason"]
    if raw_reason is not None and (
        not isinstance(raw_reason, str) or not raw_reason.strip()
    ):
        raise InventoryObservationError(
            "evidence.reason must be null or a non-empty string"
        )
    reason = raw_reason if isinstance(raw_reason, str) else None
    localization_confidence = _number(
        evidence["localization_confidence"], "evidence.localization_confidence"
    )
    configuration_id = _string(
        evidence["configuration_id"], "evidence.configuration_id"
    )
    raw_profile_id = evidence["profile_id"]
    profile_id = (
        None
        if raw_profile_id is None
        else _string(raw_profile_id, "evidence.profile_id")
    )
    raw_slots = evidence["slots"]
    if (
        not isinstance(raw_slots, Sequence)
        or isinstance(raw_slots, (str, bytes, bytearray))
    ):
        raise InventoryObservationError("evidence.slots must be an array")
    slots = tuple(_slot(value, index) for index, value in enumerate(raw_slots))

    try:
        detection = InventoryDetection(
            region=parsed_region,
            occupied_slots=occupied_slots,
            confidence=observation.confidence,
            label=label,
            reason=reason,
            localization_confidence=localization_confidence,
            configuration_id=configuration_id,
            profile_id=profile_id,
            slots=slots,
        )
    except (TypeError, ValueError) as exc:
        raise InventoryObservationError(f"inventory evidence is incoherent: {exc}") from exc

    return InventoryState(
        occupied_slots=detection.occupied_slots,
        capacity=INVENTORY_CAPACITY,
        confidence=detection.confidence,
    )
