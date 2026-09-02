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
from mining_automation.perception.constrained_v1_authority import (
    CONSTRAINED_V1_ACTIVATION_ALLOWED,
    CONSTRAINED_V1_AUTHORITY_SCHEMA_VERSION,
    INVENTORY_PUBLICATION_CONFIDENCE_FLOOR,
    MAX_PERCEPTION_AUTHORITY_AGE_S,
    AuthorityBlocker,
    ConstrainedV1PerceptionSnapshot,
    InventoryAuthorityEvidence,
    InventoryAuthorityIdentity,
    PerceptionCycleProvenance,
    ResourceAuthorityEvidence,
    ResourceAuthorityIdentity,
    prepare_constrained_v1_perception_snapshot,
)

_REVIEWED_AVAILABLE_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
    / "frames"
    / "available-01.raw.gz"
)
_FRAME_DIGEST = hashlib.sha256(b"constrained-v1-test-frame").hexdigest()
_VALIDATION_DIGEST = hashlib.sha256(b"unapproved-validation-claim").hexdigest()


def _authority_import_dependencies(source: str) -> tuple[set[str], set[str]]:
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    imported_symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported_modules.add(node.module)
            imported_symbols.update(alias.name for alias in node.names)
    return imported_modules, imported_symbols


def _is_prohibited_authority_dependency(module: str) -> bool:
    prohibited_segments = {
        "app",
        "application",
        "controller",
        "interaction",
        "navigation",
        "banking",
    }
    return not prohibited_segments.isdisjoint(module.split("."))


def _provenance(
    *,
    frame: FrameRef | None = None,
    cycle_id: str = "cycle-0007",
    frame_sha256: str = _FRAME_DIGEST,
    capture_configuration_id: str = "constrained-v1-replay-v1",
) -> PerceptionCycleProvenance:
    return PerceptionCycleProvenance(
        frame=frame or FrameRef(7, 10.0, 1005, 1078),
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


def _trusted_resource_result(
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
        trust_result=_trusted_resource_result(provenance),
    )


def _inventory_identity() -> InventoryAuthorityIdentity:
    return InventoryAuthorityIdentity(
        detector_id="inventory-positive-v3-candidate",
        detector_version="3.0.0-frozen-candidate",
        profile_id="inventory-live-profile-candidate",
        profile_schema_version=3,
        configuration_id="inventory-positive-v3-candidate-config",
    )


def _inventory_evidence(
    provenance: PerceptionCycleProvenance,
    *,
    occupied_slots: int | None = 5,
    confidence: float = 0.95,
    independently_validated: bool = True,
) -> InventoryAuthorityEvidence:
    return InventoryAuthorityEvidence(
        identity=_inventory_identity(),
        provenance=provenance,
        state=InventoryState(
            occupied_slots=occupied_slots,
            capacity=28,
            confidence=confidence,
        ),
        independent_validation_protocol_id=(
            "future-independent-natural-fill-v1" if independently_validated else None
        ),
        independent_validation_report_sha256=(
            _VALIDATION_DIGEST if independently_validated else None
        ),
    )


def _prepare(
    *,
    current: PerceptionCycleProvenance | None = None,
    resource: object | None = None,
    inventory: object | None = None,
    now: object = 10.25,
) -> ConstrainedV1PerceptionSnapshot:
    provenance = current or _provenance()
    return prepare_constrained_v1_perception_snapshot(
        current_provenance=provenance,
        resource_evidence=(
            _resource_evidence(provenance) if resource is None else resource
        ),
        inventory_evidence=(
            _inventory_evidence(provenance) if inventory is None else inventory
        ),
        evaluated_monotonic_s=now,
    )


def _assert_zero_authority(snapshot: ConstrainedV1PerceptionSnapshot) -> None:
    assert snapshot.resources == ()
    assert snapshot.inventory == InventoryState(None, capacity=28, confidence=0.0)
    assert snapshot.inventory.is_full is None
    assert snapshot.actionable_target_ids == ()
    assert snapshot.mining_authorized is False
    assert snapshot.banking_authorized is False
    assert snapshot.navigation_authorized is False
    assert snapshot.click_authorized is False


def test_current_schema_is_literal_non_activating_with_empty_approval_registries() -> None:
    assert CONSTRAINED_V1_ACTIVATION_ALLOWED is False
    assert CONSTRAINED_V1_AUTHORITY_SCHEMA_VERSION == 1
    assert MAX_PERCEPTION_AUTHORITY_AGE_S == 1.0
    assert INVENTORY_PUBLICATION_CONFIDENCE_FLOOR == 0.8
    assert authority_module._APPROVED_RESOURCE_IDENTITIES == frozenset()
    assert authority_module._APPROVED_INVENTORY_AUTHORITIES == frozenset()


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("CONSTRAINED_V1_ACTIVATION_ALLOWED", True),
        ("_APPROVED_RESOURCE_IDENTITIES", frozenset({_resource_identity()})),
        ("_APPROVED_INVENTORY_AUTHORITIES", frozenset({object()})),
    ],
)
def test_deny_only_schema_fails_closed_if_source_owned_gate_or_registry_changes(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    unsafe_value: object,
) -> None:
    provenance = _provenance()
    resource = _resource_evidence(provenance)
    inventory = _inventory_evidence(provenance)

    def forbidden_profile_load() -> None:
        raise AssertionError("deny-only invariant must stop before evidence evaluation")

    monkeypatch.setattr(
        authority_module,
        "load_varrock_east_iron_profile",
        forbidden_profile_load,
    )
    monkeypatch.setattr(authority_module, field_name, unsafe_value)

    with pytest.raises(RuntimeError, match="deny-only authority schema"):
        prepare_constrained_v1_perception_snapshot(
            current_provenance=provenance,
            resource_evidence=resource,
            inventory_evidence=inventory,
            evaluated_monotonic_s=10.1,
        )


def test_complete_claimed_evidence_still_exposes_zero_authority() -> None:
    snapshot = _prepare()

    assert snapshot.resource_shape_valid is True
    assert snapshot.inventory_shape_valid is True
    assert snapshot.blockers == (
        AuthorityBlocker.ACTIVATION_DISABLED,
        AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING,
        AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING,
    )
    _assert_zero_authority(snapshot)


def test_actual_reviewed_resource_fixture_still_has_zero_action_authority() -> None:
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
    ) as capture_source:
        trusted = capture_detect_trust_varrock_east_iron(capture_source)
    assert trusted.accepted is True
    assert trusted.frame is not None
    provenance = _provenance(
        frame=trusted.frame,
        frame_sha256=hashlib.sha256(payload).hexdigest(),
    )

    snapshot = prepare_constrained_v1_perception_snapshot(
        current_provenance=provenance,
        resource_evidence=ResourceAuthorityEvidence(
            identity=_resource_identity(),
            provenance=provenance,
            trust_result=trusted,
        ),
        inventory_evidence=_inventory_evidence(provenance),
        evaluated_monotonic_s=10.1,
    )

    assert snapshot.resource_shape_valid is True
    assert AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING in snapshot.blockers
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize(
    "available_ids",
    [(), (VARROCK_EAST_IRON_RESOURCE_IDS[0],), VARROCK_EAST_IRON_RESOURCE_IDS],
    ids=["all-depleted", "mixed", "all-available"],
)
def test_exact_definitive_resource_states_are_shape_valid_but_never_actionable(
    available_ids: tuple[str, ...],
) -> None:
    provenance = _provenance()
    evidence = _resource_evidence(provenance)
    resources = tuple(
        replace(
            resource,
            available=resource.resource_id in available_ids,
            interaction_region=(
                resource.interaction_region
                if resource.resource_id in available_ids
                else None
            ),
        )
        for resource in evidence.trust_result.resources
    )
    changed = replace(
        evidence,
        trust_result=replace(evidence.trust_result, resources=resources),
    )

    snapshot = _prepare(current=provenance, resource=changed)

    assert snapshot.resource_shape_valid is True
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize(
    ("resource", "inventory", "blocker"),
    [
        (False, True, AuthorityBlocker.RESOURCE_EVIDENCE_MISSING),
        (True, False, AuthorityBlocker.INVENTORY_EVIDENCE_MISSING),
    ],
)
def test_missing_evidence_fails_closed(
    resource: bool,
    inventory: bool,
    blocker: AuthorityBlocker,
) -> None:
    provenance = _provenance()
    snapshot = prepare_constrained_v1_perception_snapshot(
        current_provenance=provenance,
        resource_evidence=_resource_evidence(provenance) if resource else None,
        inventory_evidence=_inventory_evidence(provenance) if inventory else None,
        evaluated_monotonic_s=10.1,
    )

    assert blocker in snapshot.blockers
    _assert_zero_authority(snapshot)


def test_both_evidence_inputs_missing_report_both_blockers_in_order() -> None:
    provenance = _provenance()
    snapshot = prepare_constrained_v1_perception_snapshot(
        current_provenance=provenance,
        resource_evidence=None,
        inventory_evidence=None,
        evaluated_monotonic_s=10.1,
    )

    assert snapshot.blockers == (
        AuthorityBlocker.ACTIVATION_DISABLED,
        AuthorityBlocker.RESOURCE_RELEASE_APPROVAL_MISSING,
        AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING,
        AuthorityBlocker.RESOURCE_EVIDENCE_MISSING,
        AuthorityBlocker.INVENTORY_EVIDENCE_MISSING,
    )
    _assert_zero_authority(snapshot)


def test_unknown_inventory_is_not_converted_to_not_full() -> None:
    provenance = _provenance()
    snapshot = _prepare(
        current=provenance,
        inventory=_inventory_evidence(provenance, occupied_slots=None),
    )

    assert AuthorityBlocker.INVENTORY_UNKNOWN in snapshot.blockers
    assert snapshot.inventory_shape_valid is False
    assert snapshot.inventory.occupied_slots is None
    assert snapshot.inventory.is_full is None
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize("occupied_slots", [0, 5, 27, 28])
def test_known_but_unapproved_inventory_never_grants_authority(
    occupied_slots: int,
) -> None:
    provenance = _provenance()
    snapshot = _prepare(
        current=provenance,
        inventory=_inventory_evidence(provenance, occupied_slots=occupied_slots),
    )

    assert AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING in snapshot.blockers
    assert snapshot.inventory_shape_valid is True
    _assert_zero_authority(snapshot)


def test_unvalidated_inventory_fails_closed() -> None:
    provenance = _provenance()
    snapshot = _prepare(
        current=provenance,
        inventory=_inventory_evidence(provenance, independently_validated=False),
    )

    assert AuthorityBlocker.INVENTORY_INDEPENDENT_VALIDATION_MISSING in snapshot.blockers
    assert snapshot.inventory_shape_valid is False
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize("confidence", [0.0, 0.799999])
def test_inventory_below_publication_floor_fails_closed(confidence: float) -> None:
    provenance = _provenance()
    snapshot = _prepare(
        current=provenance,
        inventory=_inventory_evidence(provenance, confidence=confidence),
    )

    assert confidence < INVENTORY_PUBLICATION_CONFIDENCE_FLOOR
    assert AuthorityBlocker.INVENTORY_CONFIDENCE_BELOW_FLOOR in snapshot.blockers
    assert snapshot.inventory_shape_valid is False
    _assert_zero_authority(snapshot)


def test_inventory_at_publication_floor_is_structurally_valid_but_unapproved() -> None:
    provenance = _provenance()
    snapshot = _prepare(
        current=provenance,
        inventory=_inventory_evidence(
            provenance,
            confidence=INVENTORY_PUBLICATION_CONFIDENCE_FLOOR,
        ),
    )

    assert snapshot.inventory_shape_valid is True
    assert AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING in snapshot.blockers
    _assert_zero_authority(snapshot)


def test_wrong_inventory_layout_fails_closed() -> None:
    provenance = _provenance()
    inventory = _inventory_evidence(provenance)
    changed = replace(
        inventory,
        state=InventoryState(occupied_slots=5, capacity=30, confidence=0.95),
    )

    snapshot = _prepare(current=provenance, inventory=changed)

    assert AuthorityBlocker.INVENTORY_LAYOUT_MISMATCH in snapshot.blockers
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize("bad_evidence", [object(), "inventory", InventoryState(5, confidence=1.0)])
def test_duck_typed_or_development_inventory_result_fails_closed(
    bad_evidence: object,
) -> None:
    snapshot = _prepare(inventory=bad_evidence)

    assert AuthorityBlocker.INVENTORY_EVIDENCE_TYPE_INVALID in snapshot.blockers
    assert snapshot.inventory_shape_valid is False
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize("bad_evidence", [object(), "resource", ()])
def test_duck_typed_resource_result_fails_closed(bad_evidence: object) -> None:
    snapshot = _prepare(resource=bad_evidence)

    assert AuthorityBlocker.RESOURCE_EVIDENCE_TYPE_INVALID in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("detector_id", "wrong-detector"),
        ("detector_version", "wrong-version"),
        ("profile_id", "wrong-profile"),
        ("profile_schema_version", 999),
        ("location_id", "wrong-location"),
    ],
)
def test_wrong_resource_identity_fails_closed(
    field_name: str,
    replacement: object,
) -> None:
    provenance = _provenance()
    evidence = _resource_evidence(provenance)
    changed_identity = replace(evidence.identity, **{field_name: replacement})

    snapshot = _prepare(
        current=provenance,
        resource=replace(evidence, identity=changed_identity),
    )

    assert AuthorityBlocker.RESOURCE_IDENTITY_MISMATCH in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


def test_rejected_resource_trust_receipt_fails_closed() -> None:
    provenance = _provenance()
    evidence = _resource_evidence(provenance)
    changed = replace(
        evidence,
        trust_result=ProductionResourceTrustResult(
            accepted=False,
            reason="scene_not_validated",
        ),
    )

    snapshot = _prepare(current=provenance, resource=changed)

    assert AuthorityBlocker.RESOURCE_TRUST_REJECTED in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize("mode", ["duplicate", "missing", "extra", "wrong-type"])
def test_incomplete_or_wrong_resource_identity_set_fails_closed(mode: str) -> None:
    provenance = _provenance()
    evidence = _resource_evidence(provenance)
    resources = list(evidence.trust_result.resources)
    if mode == "duplicate":
        resources[1] = replace(resources[1], resource_id=resources[0].resource_id)
    elif mode == "missing":
        resources[-1] = replace(resources[-1], resource_id="missing-canonical-id")
    elif mode == "extra":
        resources[0] = replace(resources[0], resource_id="unexpected-extra-id")
    else:
        resources[0] = replace(resources[0], resource_type="copper")
    changed_result = replace(evidence.trust_result, resources=tuple(resources))

    snapshot = _prepare(
        current=provenance,
        resource=replace(evidence, trust_result=changed_result),
    )

    assert AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


def test_permuted_resource_order_fails_closed() -> None:
    provenance = _provenance()
    evidence = _resource_evidence(provenance)
    resources = list(evidence.trust_result.resources)
    resources[0], resources[1] = resources[1], resources[0]
    changed = replace(
        evidence,
        trust_result=replace(evidence.trust_result, resources=tuple(resources)),
    )

    snapshot = _prepare(current=provenance, resource=changed)

    assert AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


def test_uncertain_resource_fails_closed() -> None:
    provenance = _provenance()
    evidence = _resource_evidence(provenance)
    resources = list(evidence.trust_result.resources)
    resources[0] = replace(resources[0], available=None, interaction_region=None)
    changed = replace(
        evidence,
        trust_result=replace(evidence.trust_result, resources=tuple(resources)),
    )

    snapshot = _prepare(current=provenance, resource=changed)

    assert AuthorityBlocker.RESOURCE_UNCERTAIN in snapshot.blockers
    assert snapshot.actionable_target_ids == ()
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize(
    "changed_resource",
    [
        ResourceState(
            resource_id=VARROCK_EAST_IRON_RESOURCE_IDS[0],
            resource_type="iron",
            available=True,
            confidence=0.99,
            interaction_region=(1, 2, 3, 4),
        ),
        ResourceState(
            resource_id=VARROCK_EAST_IRON_RESOURCE_IDS[0],
            resource_type="iron",
            available=False,
            confidence=0.99,
            interaction_region=(1, 2, 3, 4),
        ),
    ],
    ids=["available-wrong-region", "depleted-region-exposed"],
)
def test_invalid_or_non_available_resource_region_fails_closed(
    changed_resource: ResourceState,
) -> None:
    provenance = _provenance()
    evidence = _resource_evidence(provenance)
    resources = list(evidence.trust_result.resources)
    resources[0] = changed_resource
    changed = replace(
        evidence,
        trust_result=replace(evidence.trust_result, resources=tuple(resources)),
    )

    snapshot = _prepare(current=provenance, resource=changed)

    assert AuthorityBlocker.RESOURCE_REGION_INVALID in snapshot.blockers
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("cycle_id", "other-cycle"),
        ("frame_sha256", "c" * 64),
        ("capture_configuration_id", "other-capture-config"),
    ],
)
def test_resource_provenance_mismatch_fails_closed(
    field_name: str,
    value: str,
) -> None:
    current = _provenance()
    other = replace(current, **{field_name: value})
    snapshot = _prepare(current=current, resource=_resource_evidence(other))

    assert AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


def test_resource_receipt_frame_mismatch_fails_closed() -> None:
    current = _provenance()
    evidence = _resource_evidence(current)
    wrong_frame = replace(current.frame, frame_id=current.frame.frame_id + 1)
    changed_result = replace(evidence.trust_result, frame=wrong_frame)

    snapshot = _prepare(
        current=current,
        resource=replace(evidence, trust_result=changed_result),
    )

    assert AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH in snapshot.blockers
    _assert_zero_authority(snapshot)


def test_resource_receipt_wrong_geometry_fails_closed() -> None:
    current = _provenance(frame=FrameRef(7, 10.0, 1, 1))
    evidence = _resource_evidence(current)

    snapshot = _prepare(current=current, resource=evidence)

    assert AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


def test_resource_receipt_noncanonical_success_reason_fails_closed() -> None:
    current = _provenance()
    evidence = _resource_evidence(current)
    changed = replace(
        evidence,
        trust_result=replace(evidence.trust_result, reason="caller_claimed_success"),
    )

    snapshot = _prepare(current=current, resource=changed)

    assert AuthorityBlocker.RESOURCE_ENSEMBLE_INVALID in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


def test_packaged_resource_profile_unavailable_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = _provenance()
    resource = _resource_evidence(provenance)
    inventory = _inventory_evidence(provenance)

    def unavailable_profile() -> None:
        raise OSError("packaged profile missing")

    monkeypatch.setattr(
        authority_module,
        "load_varrock_east_iron_profile",
        unavailable_profile,
    )
    snapshot = prepare_constrained_v1_perception_snapshot(
        current_provenance=provenance,
        resource_evidence=resource,
        inventory_evidence=inventory,
        evaluated_monotonic_s=10.1,
    )

    assert AuthorityBlocker.RESOURCE_PACKAGED_PROFILE_UNAVAILABLE in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("cycle_id", "other-cycle"),
        ("frame_sha256", "d" * 64),
        ("capture_configuration_id", "other-capture-config"),
    ],
)
def test_inventory_provenance_mismatch_fails_closed(
    field_name: str,
    value: str,
) -> None:
    current = _provenance()
    other = replace(current, **{field_name: value})
    snapshot = _prepare(current=current, inventory=_inventory_evidence(other))

    assert AuthorityBlocker.INVENTORY_PROVENANCE_MISMATCH in snapshot.blockers
    assert snapshot.inventory_shape_valid is False
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("frame_id", 8),
        ("captured_monotonic_s", 10.1),
        ("width", 1004),
        ("height", 1077),
    ],
)
@pytest.mark.parametrize("evidence_kind", ["resource", "inventory"])
def test_each_frame_ref_member_participates_in_exact_cycle_coherence(
    field_name: str,
    value: int | float,
    evidence_kind: str,
) -> None:
    current = _provenance()
    other = replace(current, frame=replace(current.frame, **{field_name: value}))
    resource = (
        _resource_evidence(other)
        if evidence_kind == "resource"
        else _resource_evidence(current)
    )
    inventory = (
        _inventory_evidence(other)
        if evidence_kind == "inventory"
        else _inventory_evidence(current)
    )

    snapshot = _prepare(
        current=current,
        resource=resource,
        inventory=inventory,
    )

    expected = (
        AuthorityBlocker.RESOURCE_PROVENANCE_MISMATCH
        if evidence_kind == "resource"
        else AuthorityBlocker.INVENTORY_PROVENANCE_MISMATCH
    )
    assert expected in snapshot.blockers
    assert AuthorityBlocker.MIXED_PERCEPTION_PROVENANCE in snapshot.blockers
    _assert_zero_authority(snapshot)


def test_mixed_resource_and_inventory_cycle_is_explicitly_rejected() -> None:
    current = _provenance()
    other = replace(current, cycle_id="other-cycle")
    snapshot = _prepare(
        current=current,
        resource=_resource_evidence(current),
        inventory=_inventory_evidence(other),
    )

    assert AuthorityBlocker.INVENTORY_PROVENANCE_MISMATCH in snapshot.blockers
    assert AuthorityBlocker.MIXED_PERCEPTION_PROVENANCE in snapshot.blockers
    _assert_zero_authority(snapshot)


@pytest.mark.parametrize(
    ("now", "blocker"),
    [
        (9.999, AuthorityBlocker.EVIDENCE_FROM_FUTURE),
        (11.000001, AuthorityBlocker.EVIDENCE_STALE),
        (float("nan"), AuthorityBlocker.CURRENT_TIME_INVALID),
        (float("inf"), AuthorityBlocker.CURRENT_TIME_INVALID),
        (True, AuthorityBlocker.CURRENT_TIME_INVALID),
    ],
)
def test_stale_future_or_invalid_time_fails_closed(
    now: object,
    blocker: AuthorityBlocker,
) -> None:
    snapshot = _prepare(now=now)

    assert blocker in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    assert snapshot.inventory_shape_valid is False
    _assert_zero_authority(snapshot)


def test_unrepresentable_frame_timestamp_fails_closed_without_overflow() -> None:
    provenance = _provenance(
        frame=FrameRef(7, 10**400, 1005, 1078),
    )

    snapshot = _prepare(current=provenance, now=10.0)

    assert AuthorityBlocker.EVIDENCE_TIMESTAMP_INVALID in snapshot.blockers
    assert snapshot.resource_shape_valid is False
    assert snapshot.inventory_shape_valid is False
    _assert_zero_authority(snapshot)


def test_exact_freshness_boundary_is_inclusive_but_still_non_activating() -> None:
    provenance = _provenance()
    snapshot = _prepare(
        current=provenance,
        now=provenance.frame.captured_monotonic_s + MAX_PERCEPTION_AUTHORITY_AGE_S,
    )

    assert AuthorityBlocker.EVIDENCE_STALE not in snapshot.blockers
    assert snapshot.resource_shape_valid is True
    assert snapshot.inventory_shape_valid is True
    _assert_zero_authority(snapshot)


def test_caller_claimed_validation_cannot_remove_source_owned_blockers() -> None:
    provenance = _provenance()
    claimed = _inventory_evidence(provenance, independently_validated=True)

    snapshot = _prepare(current=provenance, inventory=claimed)

    assert AuthorityBlocker.ACTIVATION_DISABLED in snapshot.blockers
    assert AuthorityBlocker.INVENTORY_RELEASE_APPROVAL_MISSING in snapshot.blockers
    _assert_zero_authority(snapshot)


def test_public_assembler_accepts_no_threshold_policy_approval_or_target_overrides() -> None:
    signature = inspect.signature(prepare_constrained_v1_perception_snapshot)

    assert tuple(signature.parameters) == (
        "current_provenance",
        "resource_evidence",
        "inventory_evidence",
        "evaluated_monotonic_s",
    )
    forbidden = {
        "threshold",
        "policy",
        "approved",
        "activation",
        "resource_ids",
        "targets",
    }
    assert forbidden.isdisjoint(signature.parameters)


def test_denied_snapshot_constructor_has_no_capability_or_observation_parameters() -> None:
    parameters = inspect.signature(ConstrainedV1PerceptionSnapshot).parameters

    assert "resources" not in parameters
    assert "inventory" not in parameters
    assert "actionable_target_ids" not in parameters
    assert "mining_authorized" not in parameters
    assert "banking_authorized" not in parameters
    assert "navigation_authorized" not in parameters
    assert "click_authorized" not in parameters

    with pytest.raises(TypeError):
        cast(Any, ConstrainedV1PerceptionSnapshot)(
            provenance=_provenance(),
            blockers=(AuthorityBlocker.ACTIVATION_DISABLED,),
            resource_shape_valid=False,
            inventory_shape_valid=False,
            click_authorized=True,
        )


def test_snapshot_requires_all_source_owned_blockers_even_when_manually_constructed() -> None:
    with pytest.raises(ValueError, match="source-owned blocker"):
        ConstrainedV1PerceptionSnapshot(
            provenance=_provenance(),
            blockers=(AuthorityBlocker.ACTIVATION_DISABLED,),
            resource_shape_valid=True,
            inventory_shape_valid=True,
        )


def test_snapshot_is_frozen_and_cannot_be_rebound_to_authority() -> None:
    snapshot = _prepare()

    with pytest.raises(FrozenInstanceError):
        cast(Any, snapshot).blockers = ()
    assert isinstance(type(snapshot).click_authorized, property)
    assert type(snapshot).click_authorized.fset is None
    _assert_zero_authority(snapshot)


def test_snapshot_is_runtime_sealed_against_authority_overrides() -> None:
    with pytest.raises(TypeError, match="is sealed"):

        class ForgedSnapshot(ConstrainedV1PerceptionSnapshot):
            @property
            def click_authorized(self) -> bool:
                return True


def test_module_has_no_controller_world_state_action_or_input_dependency() -> None:
    source = inspect.getsource(authority_module)
    imported_modules, imported_symbols = _authority_import_dependencies(source)

    assert not any(_is_prohibited_authority_dependency(item) for item in imported_modules)
    assert not any(_is_prohibited_authority_dependency(item) for item in imported_symbols)
    assert "WorldState" not in imported_symbols
    assert "ActionIntent" not in imported_symbols
    assert not hasattr(ConstrainedV1PerceptionSnapshot, "to_world_state")
    assert not hasattr(ConstrainedV1PerceptionSnapshot, "to_action_intent")


@pytest.mark.parametrize(
    "source",
    [
        "from mining_automation.controller import MiningController",
        "from mining_automation.interaction.boundary import execute",
        "import mining_automation.navigation.route",
        "from mining_automation.banking import BankState",
        "from mining_automation.app import main",
        "from mining_automation.application.service import ApplicationService",
        "from mining_automation import controller",
        "from . import interaction",
    ],
)
def test_import_boundary_audit_detects_absolute_prohibited_dependencies(source: str) -> None:
    modules, symbols = _authority_import_dependencies(source)

    assert any(
        _is_prohibited_authority_dependency(item) for item in (*modules, *symbols)
    )


def test_deny_only_boundary_is_not_reexported_as_production_perception_api() -> None:
    import mining_automation.perception as perception

    assert not hasattr(perception, "prepare_constrained_v1_perception_snapshot")
    assert not hasattr(perception, "ConstrainedV1PerceptionSnapshot")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"cycle_id": ""}, "cycle_id"),
        ({"frame_sha256": "A" * 64}, "frame_sha256"),
        ({"frame_sha256": "abc"}, "frame_sha256"),
        ({"capture_configuration_id": " "}, "capture_configuration_id"),
    ],
)
def test_cycle_provenance_rejects_malformed_identity(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "frame": FrameRef(7, 10.0, 1005, 1078),
        "cycle_id": "cycle-0007",
        "frame_sha256": _FRAME_DIGEST,
        "capture_configuration_id": "constrained-v1-replay-v1",
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=message):
        PerceptionCycleProvenance(**cast(Any, values))


def test_inventory_validation_protocol_and_digest_are_atomic() -> None:
    provenance = _provenance()
    common = {
        "identity": _inventory_identity(),
        "provenance": provenance,
        "state": InventoryState(5, confidence=0.95),
    }

    with pytest.raises(ValueError, match="must be supplied together"):
        InventoryAuthorityEvidence(
            **common,
            independent_validation_protocol_id="protocol-v1",
        )
    with pytest.raises(ValueError, match="must be supplied together"):
        InventoryAuthorityEvidence(
            **common,
            independent_validation_report_sha256=_VALIDATION_DIGEST,
        )


def test_duck_typed_current_provenance_is_rejected_before_snapshot_creation() -> None:
    with pytest.raises(TypeError, match="exact PerceptionCycleProvenance"):
        prepare_constrained_v1_perception_snapshot(
            current_provenance=cast(Any, object()),
            resource_evidence=None,
            inventory_evidence=None,
            evaluated_monotonic_s=10.0,
        )
