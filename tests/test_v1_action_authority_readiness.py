from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

import mining_automation.app as app_module
import mining_automation.controller as controller_module
from mining_automation.contracts import ActionIntent, WorldState
from mining_automation.perception.v1_action_authority_readiness import (
    UnactivatedV1PerceptionEvidence,
    V1ActionAuthorityReadiness,
    assess_unactivated_v1_action_authority,
)


def _future_complete_evidence() -> UnactivatedV1PerceptionEvidence:
    return UnactivatedV1PerceptionEvidence(
        inventory_label="partial",
        inventory_frame_id=42,
        scene_frame_id=42,
        resource_frame_ids=(42, 42, 42, 42),
        scene_supported=True,
        resource_evidence_fresh=True,
        resource_identity_approved=True,
        resource_ensemble_complete=True,
        resource_states_definitive=True,
        interaction_regions_valid=True,
    )


def _assert_zero_authority(result: V1ActionAuthorityReadiness) -> None:
    assert result.mining_authority is False
    assert result.banking_authority is False
    assert result.click_authority is False
    assert result.target_ids == ()
    assert result.to_dict()["runtime_activation_implemented"] is False


def test_unknown_inventory_has_no_mining_or_banking_authority() -> None:
    result = assess_unactivated_v1_action_authority(
        replace(_future_complete_evidence(), inventory_label="unknown")
    )

    _assert_zero_authority(result)
    assert result.future_snapshot_prerequisites_satisfied is False
    assert "inventory_unknown" in result.blocking_reasons


def test_unvalidated_v3_has_no_mining_or_banking_authority() -> None:
    result = assess_unactivated_v1_action_authority(
        _future_complete_evidence()
    )

    _assert_zero_authority(result)
    assert result.future_snapshot_prerequisites_satisfied is False
    assert "inventory_not_independently_approved" in result.blocking_reasons


def test_stale_resource_evidence_has_no_click_authority() -> None:
    result = assess_unactivated_v1_action_authority(
        replace(_future_complete_evidence(), resource_evidence_fresh=False)
    )

    _assert_zero_authority(result)
    assert "resource_evidence_stale" in result.blocking_reasons


def test_mixed_frame_evidence_has_no_click_authority() -> None:
    result = assess_unactivated_v1_action_authority(
        replace(_future_complete_evidence(), resource_frame_ids=(42, 41, 42, 42))
    )

    _assert_zero_authority(result)
    assert "mixed_or_invalid_frame_identity" in result.blocking_reasons


def test_wrong_or_incomplete_resource_evidence_has_no_click_authority() -> None:
    result = assess_unactivated_v1_action_authority(
        replace(
            _future_complete_evidence(),
            resource_identity_approved=False,
            resource_ensemble_complete=False,
            resource_states_definitive=False,
            interaction_regions_valid=False,
        )
    )

    _assert_zero_authority(result)
    assert "resource_identity_not_approved" in result.blocking_reasons
    assert "resource_ensemble_incomplete" in result.blocking_reasons
    assert "resource_states_not_definitive" in result.blocking_reasons
    assert "interaction_regions_not_validated" in result.blocking_reasons


def test_caller_complete_claims_cannot_satisfy_source_owned_prerequisites() -> None:
    result = assess_unactivated_v1_action_authority(_future_complete_evidence())

    _assert_zero_authority(result)
    assert result.future_snapshot_prerequisites_satisfied is False
    assert result.blocking_reasons == (
        "inventory_not_independently_approved",
        "production_activation_record_absent",
        "runtime_activation_not_implemented",
    )


def test_readiness_result_cannot_be_forged_without_source_owned_blockers() -> None:
    with pytest.raises(ValueError, match="source-owned blockers"):
        V1ActionAuthorityReadiness(blocking_reasons=())


def test_evidence_rejects_untyped_or_incomplete_caller_claims() -> None:
    with pytest.raises(TypeError, match="frame identities"):
        replace(_future_complete_evidence(), inventory_frame_id=True)
    with pytest.raises(ValueError, match="all four resources"):
        replace(_future_complete_evidence(), resource_frame_ids=())
    with pytest.raises(ValueError, match="supported readiness label"):
        replace(_future_complete_evidence(), inventory_label="lead-approved")


def test_readiness_contract_is_not_action_world_state_or_controller_wiring() -> None:
    result = assess_unactivated_v1_action_authority(_future_complete_evidence())

    assert not isinstance(result, ActionIntent)
    assert not isinstance(result, WorldState)
    assert not hasattr(result, "interaction_region")
    assert not hasattr(result, "to_action_intent")
    for module in (app_module, controller_module):
        source = inspect.getsource(module)
        assert "v1_action_authority_readiness" not in source
        assert "V1ActionAuthorityReadiness" not in source
