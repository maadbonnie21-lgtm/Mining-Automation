from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from mining_automation.contracts import FrameRef, Observation
from mining_automation.perception import (
    RESOURCE_PROFILE_SCHEMA_VERSION,
    VARROCK_EAST_IRON_DETECTOR_ID,
    VARROCK_EAST_IRON_DETECTOR_VERSION,
    VARROCK_EAST_IRON_PROFILE_ID,
    VARROCK_EAST_IRON_RESOURCE_IDS,
    build_varrock_east_iron_detector,
    load_replay_dataset,
    load_resource_detector_profile,
    materialize_gzip_replay_dataset,
    resource_states_from_observations,
)
from mining_automation.perception import (
    trust_varrock_east_iron_observations as _production_trust,
)

_FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "perception"
    / "varrock-east-iron-v1"
)
_DEFAULT_CURRENT_FRAME = FrameRef(
    frame_id=1,
    captured_monotonic_s=0.0,
    width=1005,
    height=1078,
)


def trust_varrock_east_iron_observations(
    observations: Any,
    *,
    current_frame: FrameRef | None = None,
):
    """Keep counterexample setup concise while always supplying current identity."""

    if current_frame is None and isinstance(observations, (tuple, list)):
        first = next(
            (item for item in observations if isinstance(item, Observation)),
            None,
        )
        if first is not None and isinstance(first.frame, FrameRef):
            current_frame = first.frame
    return _production_trust(
        observations,
        current_frame=current_frame or _DEFAULT_CURRENT_FRAME,
    )


@pytest.fixture(scope="module")
def production_cases(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, tuple[Observation, ...]]:
    destination = tmp_path_factory.mktemp("resource-production-trust")
    dataset = load_replay_dataset(
        materialize_gzip_replay_dataset(_FIXTURE_ROOT / "manifest.json", destination)
    )
    detector = build_varrock_east_iron_detector()
    return {
        sample.case.case_id: detector.detect(sample.frame) for sample in dataset.samples
    }


def _evidence_observation(
    observation: Observation,
    *,
    remove: str | None = None,
    **updates: Any,
) -> Observation:
    evidence = dict(observation.evidence)
    if remove is not None:
        evidence.pop(remove, None)
    evidence.update(updates)
    return replace(observation, evidence=evidence)


def _replace_one(
    observations: tuple[Observation, ...],
    index: int,
    replacement: Observation,
) -> tuple[Observation, ...]:
    changed = list(observations)
    changed[index] = replacement
    return tuple(changed)


def test_exact_production_ensemble_is_trusted_in_source_owned_order(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = tuple(reversed(production_cases["available-01"]))

    result = trust_varrock_east_iron_observations(observations)

    assert result.accepted is True
    assert result.reason == "trusted_complete_production_ensemble"
    assert result.frame == observations[0].frame
    assert tuple(resource.resource_id for resource in result.resources) == (
        VARROCK_EAST_IRON_RESOURCE_IDS
    )
    assert tuple(resource.resource_id for resource in result.actionable_targets) == (
        VARROCK_EAST_IRON_RESOURCE_IDS
    )
    assert all(target.interaction_region is not None for target in result.actionable_targets)


def test_exact_current_frame_is_required_for_acceptance(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]

    result = _production_trust(
        observations,
        current_frame=observations[0].frame,
    )

    assert result.accepted is True
    assert result.frame == observations[0].frame
    assert len(result.actionable_targets) == 4


def test_current_frame_argument_cannot_be_omitted(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]

    with pytest.raises(TypeError, match="current_frame"):
        cast(Any, _production_trust)(observations)


def test_self_consistent_previous_frame_is_rejected_as_stale(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    current_frame = replace(
        observations[0].frame,
        frame_id=observations[0].frame.frame_id + 1,
        captured_monotonic_s=observations[0].frame.captured_monotonic_s + 1.0,
    )

    result = _production_trust(observations, current_frame=current_frame)

    assert result.accepted is False
    assert result.reason == "stale_resource_ensemble"
    assert result.frame is None
    assert result.resources == ()
    assert result.actionable_targets == ()


def test_same_id_and_geometry_with_different_capture_time_is_stale(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    current_frame = replace(
        observations[0].frame,
        captured_monotonic_s=observations[0].frame.captured_monotonic_s + 0.001,
    )

    result = _production_trust(observations, current_frame=current_frame)

    assert result.accepted is False
    assert result.reason == "stale_resource_ensemble"
    assert result.frame is None
    assert result.resources == ()
    assert result.actionable_targets == ()


def test_only_available_states_survive_as_actionable_targets(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    result = trust_varrock_east_iron_observations(
        production_cases["lower-left-full-cycle-020"]
    )

    assert result.accepted is True
    assert tuple(resource.resource_id for resource in result.actionable_targets) == (
        "varrock-east-iron-northwest",
        "varrock-east-iron-center",
        "varrock-east-iron-northeast",
    )
    southwest = result.resources[1]
    assert southwest.available is False
    assert southwest.interaction_region is None


@pytest.mark.parametrize(
    ("case_id", "expected_availability"),
    [
        ("available-01", (True, True, True, True)),
        ("lower-left-full-cycle-019", (True, True, True, True)),
        ("lower-left-full-cycle-020", (True, False, True, True)),
        ("lower-left-full-cycle-028", (True, False, True, True)),
        ("lower-left-full-cycle-029", (True, True, True, True)),
    ],
)
def test_every_reviewed_replay_case_crosses_the_complete_production_gate(
    production_cases: dict[str, tuple[Observation, ...]],
    case_id: str,
    expected_availability: tuple[bool, bool, bool, bool],
) -> None:
    result = trust_varrock_east_iron_observations(production_cases[case_id])

    assert result.accepted is True
    assert tuple(resource.available for resource in result.resources) == (
        expected_availability
    )
    assert len(result.actionable_targets) == sum(expected_availability)


def test_complete_uncertain_ensemble_is_non_actionable_without_identity_loss(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = tuple(
        replace(
            _evidence_observation(observation, state="uncertain"),
            kind="resource.uncertain",
        )
        for observation in production_cases["available-01"]
    )

    result = trust_varrock_east_iron_observations(observations)

    assert result.accepted is True
    assert all(resource.available is None for resource in result.resources)
    assert result.actionable_targets == ()


def test_detector_stamps_exact_production_identity_and_schema(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    for observation in production_cases["available-01"]:
        assert observation.evidence["detector_id"] == VARROCK_EAST_IRON_DETECTOR_ID
        assert observation.detector_version == VARROCK_EAST_IRON_DETECTOR_VERSION
        assert observation.evidence["profile_id"] == VARROCK_EAST_IRON_PROFILE_ID
        assert observation.evidence["location_id"] == "varrock-east-mine"
        assert (
            observation.evidence["profile_schema_version"]
            == RESOURCE_PROFILE_SCHEMA_VERSION
        )


def test_historical_schema_v2_identity_remains_explicit() -> None:
    profile = load_resource_detector_profile(_FIXTURE_ROOT / "profile-schema-v2.json")

    assert profile.schema_version == 2


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("detector_id", "profiled-resource:other", "detector_identity_mismatch"),
        ("profile_id", "other-profile", "profile_identity_mismatch"),
        ("location_id", "other-location", "location_identity_mismatch"),
        ("profile_schema_version", 2, "profile_schema_mismatch"),
        ("profile_schema_version", True, "profile_schema_mismatch"),
        ("label", "copper", "resource_type_mismatch"),
        ("resource_id", "varrock-east-iron-extra", "unexpected_resource_id"),
    ],
)
def test_identity_counterexamples_fail_closed_to_zero_targets(
    production_cases: dict[str, tuple[Observation, ...]],
    field: str,
    value: object,
    reason: str,
) -> None:
    observations = production_cases["available-01"]
    changed = _replace_one(
        observations,
        0,
        _evidence_observation(observations[0], **{field: value}),
    )

    result = trust_varrock_east_iron_observations(changed)

    assert result.accepted is False
    assert result.reason == reason
    assert result.frame is None
    assert result.resources == ()
    assert result.actionable_targets == ()


def test_detector_version_mismatch_fails_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    changed = _replace_one(
        observations,
        0,
        replace(observations[0], detector_version="2.0.0"),
    )

    result = trust_varrock_east_iron_observations(changed)

    assert result.accepted is False
    assert result.reason == "detector_version_mismatch"
    assert result.actionable_targets == ()


def test_missing_identity_field_fails_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    changed = _replace_one(
        observations,
        0,
        _evidence_observation(observations[0], remove="detector_id"),
    )

    result = trust_varrock_east_iron_observations(changed)

    assert result.accepted is False
    assert result.reason == "detector_identity_mismatch"
    assert result.actionable_targets == ()


@pytest.mark.parametrize("count", [0, 1, 3])
def test_incomplete_ensemble_fails_closed(
    production_cases: dict[str, tuple[Observation, ...]],
    count: int,
) -> None:
    result = trust_varrock_east_iron_observations(
        production_cases["available-01"][:count]
    )

    assert result.accepted is False
    assert result.reason == "incomplete_resource_ensemble"
    assert result.actionable_targets == ()


def test_extra_ensemble_member_fails_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]

    result = trust_varrock_east_iron_observations((*observations, observations[0]))

    assert result.accepted is False
    assert result.reason == "incomplete_resource_ensemble"
    assert result.actionable_targets == ()


def test_duplicate_resource_id_fails_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    duplicate = _evidence_observation(
        observations[1],
        resource_id=observations[0].evidence["resource_id"],
        region=observations[0].evidence["region"],
    )

    result = trust_varrock_east_iron_observations(
        _replace_one(observations, 1, duplicate)
    )

    assert result.accepted is False
    assert result.reason == "duplicate_resource_id"
    assert result.actionable_targets == ()


def test_mixed_frame_ensemble_fails_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    other_frame = replace(observations[0].frame, frame_id=999)

    result = trust_varrock_east_iron_observations(
        _replace_one(observations, 0, replace(observations[0], frame=other_frame))
    )

    assert result.accepted is False
    assert result.reason == "mixed_frame_ensemble"
    assert result.actionable_targets == ()


def test_wrong_but_internally_identical_frame_geometry_fails_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    wrong_geometry = replace(observations[0].frame, width=1004)
    changed = tuple(replace(observation, frame=wrong_geometry) for observation in observations)

    result = trust_varrock_east_iron_observations(changed)

    assert result.accepted is False
    assert result.reason == "frame_geometry_mismatch"
    assert result.actionable_targets == ()


@pytest.mark.parametrize(
    ("region", "reason"),
    [
        (None, "available_region_missing_or_invalid"),
        ((1, 2, 3, 4), "candidate_region_mismatch"),
        ((1, 2, "bad", 4), "malformed_resource_observation"),
    ],
)
def test_available_region_counterexamples_fail_closed(
    production_cases: dict[str, tuple[Observation, ...]],
    region: object,
    reason: str,
) -> None:
    observations = production_cases["available-01"]
    changed = _replace_one(
        observations,
        0,
        _evidence_observation(observations[0], region=region),
    )

    result = trust_varrock_east_iron_observations(changed)

    assert result.accepted is False
    assert result.reason == reason
    assert result.actionable_targets == ()


def test_kind_state_disagreement_fails_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    changed = _replace_one(
        observations,
        0,
        _evidence_observation(observations[0], state="depleted"),
    )

    result = trust_varrock_east_iron_observations(changed)

    assert result.accepted is False
    assert result.reason == "malformed_resource_observation"
    assert result.actionable_targets == ()


def test_malformed_evidence_and_frame_fail_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    observations = production_cases["available-01"]
    bad_evidence = replace(observations[0], evidence=cast(Any, None))
    evidence_result = trust_varrock_east_iron_observations(
        _replace_one(observations, 0, bad_evidence)
    )

    bad_frame = replace(observations[0], frame=cast(Any, "not-a-frame"))
    frame_result = trust_varrock_east_iron_observations(
        _replace_one(observations, 0, bad_frame)
    )

    assert evidence_result.accepted is False
    assert evidence_result.reason == "malformed_evidence"
    assert evidence_result.actionable_targets == ()
    assert frame_result.accepted is False
    assert frame_result.reason == "malformed_frame_ref"
    assert frame_result.actionable_targets == ()


def test_non_sequence_and_non_observation_inputs_fail_closed(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    not_a_sequence = trust_varrock_east_iron_observations(cast(Any, None))
    observations = production_cases["available-01"]
    malformed_items = trust_varrock_east_iron_observations(
        cast(Any, (observations[0], observations[1], observations[2], object()))
    )

    assert not_a_sequence.accepted is False
    assert not_a_sequence.reason == "observations_not_a_sequence"
    assert not_a_sequence.actionable_targets == ()
    assert malformed_items.accepted is False
    assert malformed_items.reason == "malformed_observation"
    assert malformed_items.actionable_targets == ()


def test_generic_diagnostic_adapter_does_not_replace_production_completeness_gate(
    production_cases: dict[str, tuple[Observation, ...]],
) -> None:
    one_observation = production_cases["available-01"][:1]

    generic = resource_states_from_observations(list(one_observation))
    production = trust_varrock_east_iron_observations(one_observation)

    assert len(generic) == 1
    assert next(iter(generic.values())).interaction_region is not None
    assert production.accepted is False
    assert production.resources == ()
    assert production.actionable_targets == ()
