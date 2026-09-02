from __future__ import annotations

import ast
import gzip
import hashlib
import inspect
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import pytest

from mining_automation.capture import CaptureSource, PixelFormat, RawFrame
from mining_automation.capture.testing import FakeCaptureBackend, ManualClock
from mining_automation.contracts import FrameRef, InventoryState, ResourceState
from mining_automation.perception import (
    RESOURCE_PROFILE_SCHEMA_VERSION,
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    VARROCK_EAST_IRON_PROFILE_ID,
    VARROCK_EAST_IRON_RESOURCE_IDS,
    ProductionResourceTrustResult,
    capture_detect_trust_varrock_east_iron,
    load_varrock_east_iron_profile,
)
from mining_automation.perception import constrained_v1_authority as authority_module
from mining_automation.perception import constrained_v1_same_cycle as same_cycle
from mining_automation.perception.constrained_v1_authority import (
    AuthorityBlocker,
    InventoryAuthorityEvidence,
    InventoryAuthorityIdentity,
    PerceptionCycleProvenance,
    ResourceAuthorityEvidence,
    ResourceAuthorityIdentity,
)
from mining_automation.perception.constrained_v1_same_cycle import (
    CONSTRAINED_V1_SAME_CYCLE_SCHEMA_VERSION,
    ConstrainedV1SameCycleDenial,
    prepare_constrained_v1_same_cycle_denial,
)

_REVIEWED_AVAILABLE_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
    / "frames"
    / "available-01.raw.gz"
)
_FRAME_SHA = hashlib.sha256(b"a4-owned-frame").hexdigest()
_VALIDATION_SHA = hashlib.sha256(b"unapproved-inventory-validation").hexdigest()


def _provenance(
    *,
    frame: FrameRef | None = None,
    cycle_id: str = "cycle-a4-0001",
    frame_sha256: str = _FRAME_SHA,
    capture_configuration_id: str = "source-owned-same-cycle-v1",
) -> PerceptionCycleProvenance:
    return PerceptionCycleProvenance(
        frame=frame or FrameRef(1, 10.0, 1005, 1078),
        cycle_id=cycle_id,
        frame_sha256=frame_sha256,
        capture_configuration_id=capture_configuration_id,
    )


def _resource_identity() -> ResourceAuthorityIdentity:
    return ResourceAuthorityIdentity(
        detector_id=VARROCK_EAST_IRON_DETECTOR_ID,
        detector_version=VARROCK_EAST_IRON_DETECTOR_VERSION,
        profile_id=VARROCK_EAST_IRON_PROFILE_ID,
        profile_schema_version=RESOURCE_PROFILE_SCHEMA_VERSION,
        location_id="varrock-east-mine",
    )


def _trusted_resources(
    provenance: PerceptionCycleProvenance,
) -> ProductionResourceTrustResult:
    profile = load_varrock_east_iron_profile()
    return ProductionResourceTrustResult(
        accepted=True,
        reason="trusted_complete_production_ensemble",
        frame=provenance.frame,
        resources=tuple(
            ResourceState(
                resource_id=candidate.resource_id,
                resource_type="iron",
                available=True,
                confidence=0.99,
                interaction_region=candidate.region,
            )
            for candidate in profile.candidates
        ),
    )


def _resource_evidence(
    provenance: PerceptionCycleProvenance,
) -> ResourceAuthorityEvidence:
    return ResourceAuthorityEvidence(
        identity=_resource_identity(),
        provenance=provenance,
        trust_result=_trusted_resources(provenance),
    )


def _inventory_evidence(
    provenance: PerceptionCycleProvenance,
    *,
    occupied_slots: int | None = 5,
    confidence: float = 0.95,
    validated: bool = True,
) -> InventoryAuthorityEvidence:
    return InventoryAuthorityEvidence(
        identity=InventoryAuthorityIdentity(
            detector_id="unapproved-inventory-candidate",
            detector_version="3.0.0-candidate",
            profile_id="unapproved-live-profile-candidate",
            profile_schema_version=3,
            configuration_id="unapproved-inventory-config",
        ),
        provenance=provenance,
        state=InventoryState(
            occupied_slots=occupied_slots,
            capacity=28,
            confidence=confidence,
        ),
        independent_validation_protocol_id=(
            "future-independent-protocol" if validated else None
        ),
        independent_validation_report_sha256=(
            _VALIDATION_SHA if validated else None
        ),
    )


def _prepare(
    provenance: PerceptionCycleProvenance,
    *,
    resource: object | None,
    inventory: object | None,
    now: object = 10.25,
) -> ConstrainedV1SameCycleDenial:
    return prepare_constrained_v1_same_cycle_denial(
        current_provenance=provenance,
        resource_evidence=resource,
        inventory_evidence=inventory,
        evaluated_monotonic_s=now,
    )


def _assert_atomic_denial(result: ConstrainedV1SameCycleDenial) -> None:
    assert tuple(resource.resource_id for resource in result.resources) == (
        VARROCK_EAST_IRON_RESOURCE_IDS
    )
    assert len(result.resources) == 4
    assert all(
        type(resource) is ResourceState
        and resource.resource_type == "iron"
        and resource.available is None
        and resource.confidence == 0.0
        and resource.interaction_region is None
        for resource in result.resources
    )
    assert result.inventory == InventoryState(None, capacity=28, confidence=0.0)
    assert result.inventory.is_full is None
    assert result.actionable_target_ids == ()
    assert result.resource_release_bound is False
    assert result.inventory_release_bound is False
    assert result.activation_allowed is False
    assert result.mining_authorized is False
    assert result.banking_authorized is False
    assert result.navigation_authorized is False
    assert result.click_authorized is False


def test_complete_same_cycle_shape_still_projects_atomic_denial() -> None:
    provenance = _provenance()
    result = _prepare(
        provenance,
        resource=_resource_evidence(provenance),
        inventory=_inventory_evidence(provenance),
    )

    assert result.schema_version == CONSTRAINED_V1_SAME_CYCLE_SCHEMA_VERSION == 1
    assert result.resource_shape_valid is True
    assert result.inventory_shape_valid is True
    assert result.blockers == (
        AuthorityBlocker.ACTIVATION_DISABLED,
        AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING,
        AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING,
    )
    _assert_atomic_denial(result)


def test_actual_reviewed_resource_frame_cannot_escape_denial_projection() -> None:
    with gzip.open(_REVIEWED_AVAILABLE_FIXTURE, "rb") as source:
        payload = source.read()
    raw = RawFrame(
        payload=payload,
        width=1005,
        height=1078,
        pixel_format=PixelFormat.BGRA8888,
    )
    with CaptureSource(
        FakeCaptureBackend([raw]),
        clock=ManualClock(10.0),
    ) as source:
        trusted = capture_detect_trust_varrock_east_iron(source)
    assert trusted.accepted is True
    assert trusted.frame is not None
    provenance = _provenance(
        frame=trusted.frame,
        frame_sha256=hashlib.sha256(payload).hexdigest(),
    )

    result = _prepare(
        provenance,
        resource=ResourceAuthorityEvidence(
            identity=_resource_identity(),
            provenance=provenance,
            trust_result=trusted,
        ),
        inventory=_inventory_evidence(provenance),
        now=10.1,
    )

    assert result.resource_shape_valid is True
    _assert_atomic_denial(result)


@pytest.mark.parametrize(
    ("resource_factory", "expected"),
    [
        (lambda provenance: None, AuthorityBlocker.RESOURCE_EVIDENCE_MISSING),
        (
            lambda provenance: object(),
            AuthorityBlocker.RESOURCE_EVIDENCE_TYPE_INVALID,
        ),
        (
            lambda provenance: replace(
                _resource_evidence(provenance),
                trust_result=ProductionResourceTrustResult(
                    accepted=False,
                    reason="scene_not_validated",
                ),
            ),
            AuthorityBlocker.RESOURCE_TRUST_REJECTED,
        ),
        (
            lambda provenance: replace(
                _resource_evidence(provenance),
                trust_result=replace(
                    _resource_evidence(provenance).trust_result,
                    resources=(
                        replace(
                            _resource_evidence(provenance).trust_result.resources[0],
                            available=None,
                            interaction_region=None,
                        ),
                        *_resource_evidence(provenance).trust_result.resources[1:],
                    ),
                ),
            ),
            AuthorityBlocker.RESOURCE_UNCERTAIN,
        ),
    ],
    ids=["missing", "wrong-type", "rejected-scene", "uncertain"],
)
def test_resource_denials_always_expose_four_unknowns(
    resource_factory: Any,
    expected: AuthorityBlocker,
) -> None:
    provenance = _provenance()
    result = _prepare(
        provenance,
        resource=resource_factory(provenance),
        inventory=_inventory_evidence(provenance),
    )

    assert expected in result.blockers
    assert result.resource_shape_valid is False
    _assert_atomic_denial(result)


@pytest.mark.parametrize(
    ("inventory_factory", "expected"),
    [
        (lambda provenance: None, AuthorityBlocker.INVENTORY_EVIDENCE_MISSING),
        (
            lambda provenance: object(),
            AuthorityBlocker.INVENTORY_EVIDENCE_TYPE_INVALID,
        ),
        (
            lambda provenance: _inventory_evidence(
                provenance,
                occupied_slots=None,
            ),
            AuthorityBlocker.INVENTORY_UNKNOWN,
        ),
        (
            lambda provenance: _inventory_evidence(provenance, confidence=0.79),
            AuthorityBlocker.INVENTORY_CONFIDENCE_BELOW_FLOOR,
        ),
        (
            lambda provenance: _inventory_evidence(provenance, validated=False),
            AuthorityBlocker.INVENTORY_INDEPENDENT_VALIDATION_MISSING,
        ),
    ],
    ids=["missing", "wrong-type", "unknown", "below-floor", "unvalidated"],
)
def test_inventory_denials_never_become_not_full_or_authority(
    inventory_factory: Any,
    expected: AuthorityBlocker,
) -> None:
    provenance = _provenance()
    result = _prepare(
        provenance,
        resource=_resource_evidence(provenance),
        inventory=inventory_factory(provenance),
    )

    assert expected in result.blockers
    assert result.inventory_shape_valid is False
    _assert_atomic_denial(result)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("detector_id", "wrong-detector"),
        ("detector_version", "wrong-version"),
        ("profile_id", "wrong-profile"),
        ("profile_schema_version", 99),
        ("location_id", "wrong-location"),
    ],
)
def test_wrong_resource_lineage_projects_only_unknowns(
    field_name: str,
    replacement: object,
) -> None:
    provenance = _provenance()
    evidence = _resource_evidence(provenance)
    changed = replace(
        evidence,
        identity=replace(evidence.identity, **{field_name: replacement}),
    )

    result = _prepare(
        provenance,
        resource=changed,
        inventory=_inventory_evidence(provenance),
    )

    assert AuthorityBlocker.RESOURCE_IDENTITY_MISMATCH in result.blockers
    _assert_atomic_denial(result)


@pytest.mark.parametrize(
    ("provenance_change", "expected"),
    [
        ({"cycle_id": "other-cycle"}, AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH),
        ({"frame_sha256": "f" * 64}, AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH),
        (
            {"capture_configuration_id": "other-config"},
            AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH,
        ),
        (
            {"frame": FrameRef(2, 10.0, 1005, 1078)},
            AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH,
        ),
        (
            {"frame": FrameRef(1, 10.1, 1005, 1078)},
            AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH,
        ),
        (
            {"frame": FrameRef(1, 10.0, 1004, 1078)},
            AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH,
        ),
        (
            {"frame": FrameRef(1, 10.0, 1005, 1077)},
            AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH,
        ),
    ],
    ids=[
        "cycle-id",
        "frame-sha",
        "capture-config",
        "frame-id",
        "timestamp",
        "width",
        "height",
    ],
)
def test_each_mixed_cycle_member_is_denied_atomically(
    provenance_change: dict[str, object],
    expected: AuthorityBlocker,
) -> None:
    current = _provenance()
    other = replace(current, **provenance_change)
    result = _prepare(
        current,
        resource=_resource_evidence(other),
        inventory=_inventory_evidence(current),
    )

    assert expected in result.blockers
    assert AuthorityBlocker.MIXED_PERCEPTION_PROVENANCE in result.blockers
    _assert_atomic_denial(result)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (9.9, AuthorityBlocker.EVIDENCE_FROM_FUTURE),
        (11.01, AuthorityBlocker.EVIDENCE_STALE),
        (float("nan"), AuthorityBlocker.CURRENT_TIME_INVALID),
        (float("inf"), AuthorityBlocker.CURRENT_TIME_INVALID),
    ],
)
def test_stale_future_or_invalid_cycle_stays_atomic_denial(
    now: object,
    expected: AuthorityBlocker,
) -> None:
    provenance = _provenance()
    result = _prepare(
        provenance,
        resource=_resource_evidence(provenance),
        inventory=_inventory_evidence(provenance),
        now=now,
    )

    assert expected in result.blockers
    assert result.resource_shape_valid is False
    assert result.inventory_shape_valid is False
    _assert_atomic_denial(result)


def test_a3_proposal_shaped_mapping_is_not_a_resource_release_receipt() -> None:
    provenance = _provenance()
    proposed_not_granted: dict[str, object] = {
        "status": "PROPOSED_NOT_GRANTED",
        "release_eligible": False,
        "activation_allowed": False,
    }

    result = _prepare(
        provenance,
        resource=proposed_not_granted,
        inventory=_inventory_evidence(provenance),
    )

    assert AuthorityBlocker.RESOURCE_EVIDENCE_TYPE_INVALID in result.blockers
    assert AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING in result.blockers
    _assert_atomic_denial(result)


def test_wrong_inventory_layout_and_provenance_remain_atomic_denials() -> None:
    current = _provenance()
    other = replace(current, frame_sha256="e" * 64)
    inventory = _inventory_evidence(other)
    inventory = replace(
        inventory,
        state=InventoryState(5, capacity=30, confidence=0.95),
    )

    result = _prepare(
        current,
        resource=_resource_evidence(current),
        inventory=inventory,
    )

    assert AuthorityBlocker.INVENTORY_PROVENANCE_MISMATCH in result.blockers
    assert AuthorityBlocker.INVENTORY_LAYOUT_MISMATCH in result.blockers
    assert AuthorityBlocker.MIXED_PERCEPTION_PROVENANCE in result.blockers
    _assert_atomic_denial(result)


def test_resource_receipt_frame_and_region_mismatch_remain_atomic_denial() -> None:
    current = _provenance()
    evidence = _resource_evidence(current)
    resources = list(evidence.trust_result.resources)
    resources[0] = replace(resources[0], interaction_region=(1, 2, 3, 4))
    changed = replace(
        evidence,
        trust_result=replace(
            evidence.trust_result,
            frame=replace(current.frame, frame_id=2),
            resources=tuple(resources),
        ),
    )

    result = _prepare(
        current,
        resource=changed,
        inventory=_inventory_evidence(current),
    )

    assert AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH in result.blockers
    assert AuthorityBlocker.RESOURCE_REGION_INVALID in result.blockers
    _assert_atomic_denial(result)


def test_nested_trust_frame_subclass_cannot_spoof_provenance_equality() -> None:
    class SpoofedFrameRef(FrameRef):
        def __eq__(self, other: object) -> bool:
            del other
            raise AssertionError("subtype equality must never run")

        def __ne__(self, other: object) -> bool:
            del other
            raise AssertionError("subtype inequality must never run")

    current = _provenance()
    evidence = _resource_evidence(current)
    spoofed = SpoofedFrameRef(999, 999.0, 1005, 1078)
    changed = replace(
        evidence,
        trust_result=replace(evidence.trust_result, frame=spoofed),
    )

    result = _prepare(
        current,
        resource=changed,
        inventory=_inventory_evidence(current),
    )

    assert AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH in result.blockers
    assert result.resource_shape_valid is False
    _assert_atomic_denial(result)


def test_nested_resource_container_subclass_is_rejected_before_iteration() -> None:
    class ExplosiveResources(tuple[ResourceState, ...]):
        def __iter__(self) -> Any:
            raise AssertionError("subtype iteration must never run")

    current = _provenance()
    evidence = _resource_evidence(current)
    object.__setattr__(
        evidence.trust_result,
        "resources",
        ExplosiveResources(evidence.trust_result.resources),
    )

    result = _prepare(
        current,
        resource=evidence,
        inventory=_inventory_evidence(current),
    )

    assert AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID in result.blockers
    assert result.resource_shape_valid is False
    _assert_atomic_denial(result)


def test_nested_resource_state_subclass_is_never_forwarded() -> None:
    class SpoofedResourceState(ResourceState):
        pass

    current = _provenance()
    evidence = _resource_evidence(current)
    first = evidence.trust_result.resources[0]
    spoofed = SpoofedResourceState(
        resource_id=first.resource_id,
        resource_type=first.resource_type,
        available=first.available,
        confidence=first.confidence,
        interaction_region=first.interaction_region,
    )
    resources = (spoofed, *evidence.trust_result.resources[1:])
    changed = replace(
        evidence,
        trust_result=replace(evidence.trust_result, resources=resources),
    )

    result = _prepare(
        current,
        resource=changed,
        inventory=_inventory_evidence(current),
    )

    assert AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID in result.blockers
    assert result.resource_shape_valid is False
    _assert_atomic_denial(result)


def test_both_inputs_on_same_foreign_cycle_are_still_denied_against_current() -> None:
    current = _provenance()
    foreign = replace(current, cycle_id="same-foreign-cycle")

    result = _prepare(
        current,
        resource=_resource_evidence(foreign),
        inventory=_inventory_evidence(foreign),
    )

    assert AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH in result.blockers
    assert AuthorityBlocker.INVENTORY_PROVENANCE_MISMATCH in result.blockers
    assert AuthorityBlocker.MIXED_PERCEPTION_PROVENANCE not in result.blockers
    _assert_atomic_denial(result)


def test_wrapper_has_no_release_approval_policy_or_target_injection_seam() -> None:
    signature = inspect.signature(prepare_constrained_v1_same_cycle_denial)
    assert tuple(signature.parameters) == (
        "current_provenance",
        "resource_evidence",
        "inventory_evidence",
        "evaluated_monotonic_s",
    )
    assert {
        "release_record",
        "release_sha256",
        "approved",
        "activation",
        "policy",
        "threshold",
        "targets",
        "resources",
    }.isdisjoint(signature.parameters)

    constructor = inspect.signature(ConstrainedV1SameCycleDenial).parameters
    assert {
        "resources",
        "inventory",
        "actionable_target_ids",
        "resource_release_bound",
        "inventory_release_bound",
        "activation_allowed",
        "click_authorized",
    }.isdisjoint(constructor)

    provenance = _provenance()
    with pytest.raises(TypeError):
        cast(Any, prepare_constrained_v1_same_cycle_denial)(
            current_provenance=provenance,
            resource_evidence=_resource_evidence(provenance),
            inventory_evidence=_inventory_evidence(provenance),
            evaluated_monotonic_s=10.1,
            release_record={"status": "APPROVED"},
            approved=True,
        )


def test_result_retains_no_input_evidence_and_is_frozen_and_sealed() -> None:
    provenance = _provenance()
    result = _prepare(
        provenance,
        resource=_resource_evidence(provenance),
        inventory=_inventory_evidence(provenance),
    )

    assert not hasattr(result, "resource_evidence")
    assert not hasattr(result, "inventory_evidence")
    assert not hasattr(result, "trust_result")
    with pytest.raises(FrozenInstanceError):
        cast(Any, result).blockers = ()
    with pytest.raises(TypeError, match="is sealed"):

        class ForgedResult(ConstrainedV1SameCycleDenial):
            @property
            def click_authorized(self) -> bool:
                return True


def test_denial_constructor_rejects_blocker_tuple_subclasses() -> None:
    class SpoofedBlockers(tuple[AuthorityBlocker, ...]):
        pass

    with pytest.raises(ValueError, match="non-empty exact tuple"):
        ConstrainedV1SameCycleDenial(
            provenance=_provenance(),
            blockers=cast(
                Any,
                SpoofedBlockers(
                    (
                        AuthorityBlocker.ACTIVATION_DISABLED,
                        AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING,
                        AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING,
                    )
                ),
            ),
            resource_shape_valid=False,
            inventory_shape_valid=False,
        )


def test_source_gate_corruption_raises_instead_of_projecting_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = _provenance()
    monkeypatch.setattr(
        authority_module,
        "CONSTRAINED_V1_ACTIVATION_ALLOWED",
        True,
    )

    with pytest.raises(RuntimeError, match="deny-only authority schema"):
        _prepare(
            provenance,
            resource=_resource_evidence(provenance),
            inventory=_inventory_evidence(provenance),
        )


def test_module_has_no_controller_worldstate_navigation_banking_or_input_import() -> None:
    tree = ast.parse(inspect.getsource(same_cycle))
    imported: set[str] = set()
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported.add(node.module)
            symbols.update(alias.name for alias in node.names)

    forbidden = {
        "controller",
        "WorldState",
        "ActionIntent",
        "interaction",
        "navigation",
        "banking",
        "input",
    }
    combined = imported | symbols
    assert all(
        not any(part in forbidden for part in item.split("."))
        for item in combined
    )
    assert not hasattr(ConstrainedV1SameCycleDenial, "to_world_state")
    assert not hasattr(ConstrainedV1SameCycleDenial, "to_action_intent")


def test_same_cycle_denial_is_not_reexported_as_production_api() -> None:
    import mining_automation.perception as perception

    assert not hasattr(perception, "prepare_constrained_v1_same_cycle_denial")
    assert not hasattr(perception, "ConstrainedV1SameCycleDenial")
