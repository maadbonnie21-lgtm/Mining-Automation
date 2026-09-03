from __future__ import annotations

import pytest

from mining_automation.banking.compatibility import (
    EndpointKind,
    InventoryReleaseProjection,
    NavigationArrivalReceipt,
    bind_inventory_for_banking,
    handoff_navigation_arrival,
)
from mining_automation.banking.contracts import BankInterfaceState
from mining_automation.contracts import InventoryState


def _projection(*, slots: int | None = 7, confidence: float = 1.0) -> InventoryReleaseProjection:
    return InventoryReleaseProjection(
        receipt_sha256="1" * 64,
        release_source_sha="2" * 40,
        capture_source_id="inventory-source",
        capture_session_id="session-1",
        frame_id=10,
        cycle_id="cycle-10",
        frame_sha256="3" * 64,
        inventory=InventoryState(
            occupied_slots=slots,
            capacity=28,
            confidence=confidence,
        ),
        confidence=confidence,
    )


def test_generic_inventory_projection_cannot_claim_verified_release() -> None:
    projection = _projection()
    assert projection.release_verified is False

    kwargs = {
        "receipt_sha256": "1" * 64,
        "release_source_sha": "2" * 40,
        "capture_source_id": "inventory-source",
        "capture_session_id": "session-1",
        "frame_id": 10,
        "cycle_id": "cycle-10",
        "frame_sha256": "3" * 64,
        "inventory": InventoryState(occupied_slots=7, capacity=28, confidence=1.0),
        "confidence": 1.0,
        "release_verified": True,
    }
    with pytest.raises(TypeError):
        InventoryReleaseProjection(**kwargs)  # type: ignore[arg-type]


def test_generic_inventory_projection_stays_unknown_even_with_known_slots() -> None:
    bound = bind_inventory_for_banking(_projection(slots=28, confidence=1.0))
    assert bound.occupied_slots is None
    assert bound.capacity == 28
    assert bound.confidence == 0.0


def test_tampered_generic_projection_fails_closed() -> None:
    projection = _projection()
    object.__setattr__(projection, "release_verified", True)
    with pytest.raises(ValueError, match="cannot claim release"):
        bind_inventory_for_banking(projection)


def test_navigation_arrival_never_proves_bank_open_or_supported_mining_view() -> None:
    receipt = NavigationArrivalReceipt(
        route_id="fixed-route",
        route_version="v1",
        route_session_id="route-session-1",
        endpoint=EndpointKind.BANK,
        endpoint_id="bank-endpoint",
        arrival_frame_id=20,
        arrival_cycle_id="route-cycle-20",
        arrival_monotonic_s=20.0,
        evidence_sha256="4" * 64,
    )
    handoff = handoff_navigation_arrival(receipt)
    assert handoff.bank_state is BankInterfaceState.UNKNOWN
    assert handoff.supported_mining_view is False
    assert handoff.requires_fresh_bank_observation is True
    assert handoff.requires_fresh_resource_observation is True
    assert handoff.deposit_authority is False
    assert handoff.mining_authority is False
    assert handoff.input_authority is False
