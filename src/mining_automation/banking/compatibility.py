"""Read-only endpoint compatibility contracts for banking integration.

Navigation may prove arrival, but it cannot prove that the bank interface is
OPEN or that the mining view is supported. Generic Inventory compatibility is
structurally non-released: only a future adapter consuming the genuine
source-owned C release receipt may provide known slot state. These adapters
issue no action authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Literal

from ..contracts import InventoryState
from .contracts import INVENTORY_CAPACITY, BankInterfaceState

__all__ = [
    "BankArrivalHandoff",
    "EndpointKind",
    "InventoryReleaseProjection",
    "NavigationArrivalReceipt",
    "bind_inventory_for_banking",
    "handoff_navigation_arrival",
]

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EndpointKind(StrEnum):
    BANK = "bank"
    MINE = "mine"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty exact string")
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class NavigationArrivalReceipt:
    route_id: str
    route_version: str
    route_session_id: str
    endpoint: EndpointKind
    endpoint_id: str
    arrival_frame_id: int
    arrival_cycle_id: str
    arrival_monotonic_s: float
    evidence_sha256: str
    live_navigation_authorized: Literal[False] = field(default=False, init=False)
    bank_open_authority: Literal[False] = field(default=False, init=False)
    supported_mining_view_authority: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        text_fields: tuple[tuple[object, str], ...] = (
            (self.route_id, "route_id"),
            (self.route_version, "route_version"),
            (self.route_session_id, "route_session_id"),
            (self.endpoint_id, "endpoint_id"),
            (self.arrival_cycle_id, "arrival_cycle_id"),
        )
        for value, name in text_fields:
            _text(value, name)
        if type(self.endpoint) is not EndpointKind:
            raise ValueError("endpoint must be exact")
        if type(self.arrival_frame_id) is not int or self.arrival_frame_id <= 0:
            raise ValueError("arrival_frame_id must be a positive exact int")
        if type(self.arrival_monotonic_s) is not float or not isfinite(
            self.arrival_monotonic_s
        ):
            raise ValueError("arrival time must be a finite exact float")
        if self.arrival_monotonic_s < 0.0:
            raise ValueError("arrival time must be non-negative")
        _digest(self.evidence_sha256, "evidence_sha256")
        if any(
            value is not False
            for value in (
                self.live_navigation_authorized,
                self.bank_open_authority,
                self.supported_mining_view_authority,
                self.input_authority,
            )
        ):
            raise ValueError("arrival receipt cannot carry endpoint or input authority")


@dataclass(frozen=True, slots=True)
class BankArrivalHandoff:
    endpoint: EndpointKind
    endpoint_id: str
    arrival_frame_id: int
    arrival_cycle_id: str
    bank_state: BankInterfaceState = field(default=BankInterfaceState.UNKNOWN, init=False)
    supported_mining_view: Literal[False] = field(default=False, init=False)
    requires_fresh_bank_observation: bool = field(default=True, init=False)
    requires_fresh_resource_observation: bool = field(default=True, init=False)
    deposit_authority: Literal[False] = field(default=False, init=False)
    mining_authority: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class InventoryReleaseProjection:
    """Non-authorizing Inventory compatibility metadata.

    This type deliberately cannot represent a verified C release. The future
    banking adapter must consume the genuine accepted source-owned Inventory
    receipt rather than upgrading this generic projection.
    """

    receipt_sha256: str
    release_source_sha: str
    capture_source_id: str
    capture_session_id: str
    frame_id: int
    cycle_id: str
    frame_sha256: str
    inventory: InventoryState
    confidence: float
    release_verified: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        digest_fields: tuple[tuple[object, str], ...] = (
            (self.receipt_sha256, "receipt_sha256"),
            (self.frame_sha256, "frame_sha256"),
        )
        for value, name in digest_fields:
            _digest(value, name)
        if type(self.release_source_sha) is not str or not re.fullmatch(
            r"[0-9a-f]{40}", self.release_source_sha
        ):
            raise ValueError("release_source_sha must be lowercase Git SHA")
        text_fields: tuple[tuple[object, str], ...] = (
            (self.capture_source_id, "capture_source_id"),
            (self.capture_session_id, "capture_session_id"),
            (self.cycle_id, "cycle_id"),
        )
        for value, name in text_fields:
            _text(value, name)
        if type(self.frame_id) is not int or self.frame_id <= 0:
            raise ValueError("frame_id must be a positive exact int")
        if type(self.inventory) is not InventoryState:
            raise ValueError("inventory must be exact InventoryState")
        if self.inventory.capacity != INVENTORY_CAPACITY:
            raise ValueError("inventory capacity must remain 28")
        slots = self.inventory.occupied_slots
        if slots is not None and (
            type(slots) is not int or slots < 0 or slots > INVENTORY_CAPACITY
        ):
            raise ValueError("inventory slots must be None or 0..28")
        if type(self.confidence) is not float or not isfinite(self.confidence):
            raise ValueError("confidence must be a finite exact float")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within 0..1")
        if self.release_verified is not False:
            raise ValueError("generic inventory projection cannot claim release")
        if self.input_authority is not False:
            raise ValueError("inventory projection cannot carry input authority")


def handoff_navigation_arrival(receipt: NavigationArrivalReceipt) -> BankArrivalHandoff:
    if type(receipt) is not NavigationArrivalReceipt:
        raise TypeError("arrival receipt must be exact")
    NavigationArrivalReceipt.__post_init__(receipt)
    return BankArrivalHandoff(
        endpoint=receipt.endpoint,
        endpoint_id=receipt.endpoint_id,
        arrival_frame_id=receipt.arrival_frame_id,
        arrival_cycle_id=receipt.arrival_cycle_id,
    )


def bind_inventory_for_banking(projection: InventoryReleaseProjection) -> InventoryState:
    """Keep generic compatibility fail-closed until genuine C release exists."""

    if type(projection) is not InventoryReleaseProjection:
        raise TypeError("inventory projection must be exact")
    InventoryReleaseProjection.__post_init__(projection)
    return InventoryState(
        occupied_slots=None,
        capacity=INVENTORY_CAPACITY,
        confidence=0.0,
    )
