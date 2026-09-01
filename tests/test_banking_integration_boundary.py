"""Regression tests for the deliberately closed subsystem boundary."""

from __future__ import annotations

import mining_automation.banking as banking
from mining_automation.banking import integration_boundary


def test_integration_boundary_exports_no_structural_authority_adapters() -> None:
    assert integration_boundary.__all__ == []


def test_package_root_exposes_no_speculative_cross_lane_contracts() -> None:
    forbidden = {
        "ExternalApprovedInventoryResult",
        "ExternalCheckpointArrivalSource",
        "ExternalInventoryObservationShape",
        "IntegrationBoundaryContractError",
        "NonAuthoritativePostDepositInventoryObservation",
        "NonAuthoritativePreDepositInventoryObservation",
        "adapt_checkpoint_arrival",
        "adapt_post_deposit_inventory",
        "adapt_post_deposit_inventory_observation_shape",
        "adapt_pre_deposit_inventory",
        "adapt_pre_deposit_inventory_observation_shape",
    }
    assert forbidden.isdisjoint(banking.__all__)
    assert all(not hasattr(banking, name) for name in forbidden)
    assert all(not hasattr(integration_boundary, name) for name in forbidden)
