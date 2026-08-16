from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.contracts import InventoryState, Observation
from mining_automation.perception import Detector, DetectorExecutionError, run_detector
from mining_automation.perception.inventory.adapter import (
    InventoryObservationError,
    inventory_state_from_observation,
)
from mining_automation.perception.inventory.classification import (
    InventoryObstructionError,
    InventorySlotClassifier,
    SlotDecision,
    SlotOccupancy,
)
from mining_automation.perception.inventory.detector import (
    INVENTORY_EVIDENCE_SCHEMA_VERSION,
    INVENTORY_OBSERVATION_KIND,
    InventoryDetection,
    InventoryDetector,
    InventoryDetectorError,
)
from mining_automation.perception.inventory.geometry import (
    INVENTORY_CAPACITY,
    InventoryGridLayout,
    Region,
)
from mining_automation.perception.inventory.localization import (
    InventoryLocalization,
    InventoryRegionLocator,
)

LAYOUT = InventoryGridLayout(
    profile_id="synthetic-contiguous",
    column_stride=32,
    row_stride=32,
)
INVENTORY_REGION = LAYOUT.region_at(0, 0)


def _frame(*, frame_id: int = 1) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=bytes(LAYOUT.width * LAYOUT.height),
            width=LAYOUT.width,
            height=LAYOUT.height,
            pixel_format=PixelFormat.GRAY8,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id - 1),
    )


def _decisions(
    occupied: set[int] | None = None,
    *,
    uncertain: set[int] | None = None,
    confidence_overrides: dict[int, float] | None = None,
) -> tuple[SlotDecision, ...]:
    occupied = occupied or set()
    uncertain = uncertain or set()
    confidence_overrides = confidence_overrides or {}
    decisions: list[SlotDecision] = []
    for index, region in enumerate(LAYOUT.all_slot_regions(INVENTORY_REGION)):
        row, column = divmod(index, 4)
        if index in uncertain:
            state = SlotOccupancy.UNCERTAIN
            score = 0.5
            changed_fraction = 0.5
            confidence = confidence_overrides.get(index, 0.0)
        elif index in occupied:
            state = SlotOccupancy.OCCUPIED
            score = 1.0
            changed_fraction = 1.0
            confidence = confidence_overrides.get(index, 1.0)
        else:
            state = SlotOccupancy.EMPTY
            score = 0.0
            changed_fraction = 0.0
            confidence = confidence_overrides.get(index, 1.0)
        decisions.append(
            SlotDecision(
                index=index,
                row=row,
                column=column,
                region=region,
                state=state,
                confidence=confidence,
                score=score,
                changed_fraction=changed_fraction,
            )
        )
    return tuple(decisions)


@dataclass
class FakeLocator:
    result: InventoryLocalization
    calls: list[Frame] = field(default_factory=list)

    def locate(self, frame: Frame) -> InventoryLocalization:
        self.calls.append(frame)
        return self.result


@dataclass
class FakeClassifier:
    decisions: tuple[SlotDecision, ...]
    profile_id: str | None = LAYOUT.profile_id
    configuration_id: str = "fake-inventory-classifier-v1"
    calls: list[tuple[Frame, Region]] = field(default_factory=list)

    def classify(self, frame: Frame, inventory_region: Region) -> tuple[SlotDecision, ...]:
        self.calls.append((frame, inventory_region))
        return self.decisions


def _detector(
    decisions: tuple[SlotDecision, ...],
    *,
    localization: InventoryLocalization | None = None,
    minimum_slot_confidence: float = 0.8,
) -> tuple[InventoryDetector, FakeLocator, FakeClassifier]:
    locator = FakeLocator(
        localization
        or InventoryLocalization(
            INVENTORY_REGION,
            0.95,
            "synthetic exact match",
            profile_id=LAYOUT.profile_id,
        )
    )
    classifier = FakeClassifier(decisions)
    return (
        InventoryDetector(
            locator,
            classifier,
            localization_threshold=0.9,
            minimum_slot_confidence=minimum_slot_confidence,
        ),
        locator,
        classifier,
    )


def _one_observation(detector: InventoryDetector, frame: Frame | None = None) -> Observation:
    observations = run_detector(detector, frame or _frame())
    assert len(observations) == 1
    return observations[0]


def test_inventory_detector_satisfies_generic_protocol_and_metadata() -> None:
    detector, _, _ = _detector(_decisions())

    assert isinstance(detector, Detector)
    assert isinstance(detector.locator, InventoryRegionLocator)
    assert isinstance(detector.classifier, InventorySlotClassifier)
    assert detector.metadata.detector_id == "inventory-baseline"
    assert detector.metadata.version == "1.0.0"


@pytest.mark.parametrize(
    ("occupied", "expected_count", "expected_label"),
    [
        (set(), 0, "empty"),
        ({0, 7, 14, 27}, 4, "partial"),
        (set(range(INVENTORY_CAPACITY)), INVENTORY_CAPACITY, "full"),
    ],
)
def test_detector_produces_known_empty_partial_and_full_states(
    occupied: set[int], expected_count: int, expected_label: str
) -> None:
    detector, locator, classifier = _detector(_decisions(occupied))
    frame = _frame()

    observation = _one_observation(detector, frame)

    assert observation.kind == INVENTORY_OBSERVATION_KIND
    assert observation.detector_version == detector.metadata.version
    assert observation.confidence == 0.95
    assert observation.evidence["occupied_slots"] == expected_count
    assert observation.evidence["label"] == expected_label
    assert observation.evidence["region"] == INVENTORY_REGION.as_tuple()
    assert observation.evidence["reason"] is None
    assert observation.evidence["configuration_id"] == "fake-inventory-classifier-v1"
    assert observation.evidence["profile_id"] == LAYOUT.profile_id
    assert len(cast(tuple[object, ...], observation.evidence["slots"])) == 28
    assert inventory_state_from_observation(observation) == InventoryState(
        expected_count,
        capacity=28,
        confidence=0.95,
    )
    assert locator.calls == [frame]
    assert classifier.calls == [(frame, INVENTORY_REGION)]


def test_uncertain_slot_preserves_diagnostics_without_inventing_count() -> None:
    detector, _, _ = _detector(_decisions({0, 1}, uncertain={2}))

    observation = _one_observation(detector)

    assert observation.confidence == 0.0
    assert observation.evidence["occupied_slots"] is None
    assert observation.evidence["label"] == "unknown"
    assert observation.evidence["reason"] == "uncertain_slots: 2"
    assert len(cast(tuple[object, ...], observation.evidence["slots"])) == 28
    assert inventory_state_from_observation(observation) == InventoryState(
        None,
        capacity=28,
        confidence=0.0,
    )


def test_low_slot_confidence_preserves_diagnostics_without_inventing_count() -> None:
    detector, _, _ = _detector(_decisions({3}, confidence_overrides={11: 0.79}))

    observation = _one_observation(detector)

    assert observation.evidence["occupied_slots"] is None
    assert observation.evidence["reason"] == "slot_confidence_below_threshold: 11"
    assert observation.confidence == 0.0


@pytest.mark.parametrize(
    "localization",
    [
        InventoryLocalization(None, 0.0, "unsupported frame dimensions"),
        InventoryLocalization(INVENTORY_REGION, 0.89, "ambiguous inventory anchor"),
    ],
)
def test_localization_miss_returns_unknown_without_running_classifier(
    localization: InventoryLocalization,
) -> None:
    detector, _, classifier = _detector(_decisions(), localization=localization)

    observation = _one_observation(detector)

    assert observation.evidence["occupied_slots"] is None
    assert observation.evidence["label"] == "unknown"
    assert observation.confidence == 0.0
    assert observation.evidence["slots"] == ()
    assert classifier.calls == []
    assert inventory_state_from_observation(observation).occupied_slots is None


def test_explicitly_unguarded_classifier_cannot_publish_a_count() -> None:
    class UnguardedClassifier(FakeClassifier):
        has_obstruction_guard = False

    locator = FakeLocator(
        InventoryLocalization(
            INVENTORY_REGION,
            1.0,
            "geometry-only profile",
            profile_id=LAYOUT.profile_id,
        )
    )
    classifier = UnguardedClassifier(_decisions())
    detector = InventoryDetector(locator, classifier)

    observation = _one_observation(detector)

    assert observation.evidence["occupied_slots"] is None
    assert observation.evidence["reason"] == (
        "obstruction_guard_unavailable: localized layout has no horizontal "
        "row-gutter obstruction guard"
    )
    assert classifier.calls == []


def test_direct_detector_call_rejects_non_frame_input() -> None:
    detector, _, _ = _detector(_decisions())

    with pytest.raises(InventoryDetectorError, match="input must be Frame"):
        detector.detect(cast(Frame, object()))


def test_known_confidence_is_the_weakest_localization_or_slot_confidence() -> None:
    detector, _, _ = _detector(
        _decisions({4}, confidence_overrides={4: 0.91}),
        minimum_slot_confidence=0.9,
    )

    assert _one_observation(detector).confidence == 0.91


def test_detection_evidence_is_deterministic_and_json_serializable() -> None:
    detector, _, _ = _detector(_decisions({0, 13, 27}))
    frame = _frame()

    first = detector.detect(frame)[0]
    second = detector.detect(frame)[0]

    assert first == second
    assert json.dumps(first.evidence, sort_keys=True) == json.dumps(
        second.evidence, sort_keys=True
    )
    assert first.evidence["evidence_schema_version"] == INVENTORY_EVIDENCE_SCHEMA_VERSION


class ExplodingLocator:
    def locate(self, frame: Frame) -> InventoryLocalization:
        raise RuntimeError("anchor matcher broke")


class ExplodingClassifier:
    def classify(self, frame: Frame, inventory_region: Region) -> tuple[SlotDecision, ...]:
        raise RuntimeError("pixel reader broke")


class ObstructedClassifier:
    profile_id = LAYOUT.profile_id
    configuration_id = "obstruction-aware-test-classifier"

    def classify(
        self, frame: Frame, inventory_region: Region
    ) -> tuple[SlotDecision, ...]:
        raise InventoryObstructionError("opaque overlay covers the inventory")


def test_locator_failure_is_an_explicit_typed_detector_error() -> None:
    detector = InventoryDetector(ExplodingLocator(), FakeClassifier(_decisions()))

    with pytest.raises(InventoryDetectorError, match="inventory locator failed") as caught:
        detector.detect(_frame())

    assert isinstance(caught.value.__cause__, RuntimeError)
    with pytest.raises(DetectorExecutionError) as guarded:
        run_detector(detector, _frame())
    assert isinstance(guarded.value.__cause__, InventoryDetectorError)


def test_classifier_failure_is_an_explicit_typed_detector_error() -> None:
    detector = InventoryDetector(
        FakeLocator(InventoryLocalization(INVENTORY_REGION, 1.0, "exact")),
        ExplodingClassifier(),
    )

    with pytest.raises(InventoryDetectorError, match="slot classification failed") as caught:
        detector.detect(_frame())

    assert isinstance(caught.value.__cause__, RuntimeError)


def test_inventory_obstruction_becomes_explicit_unknown_observation() -> None:
    detector = InventoryDetector(
        FakeLocator(
            InventoryLocalization(
                INVENTORY_REGION,
                1.0,
                "exact",
                profile_id=LAYOUT.profile_id,
            )
        ),
        ObstructedClassifier(),
    )

    observation = _one_observation(detector)

    assert observation.confidence == 0.0
    assert observation.evidence["occupied_slots"] is None
    assert observation.evidence["label"] == "unknown"
    assert observation.evidence["region"] == INVENTORY_REGION.as_tuple()
    assert observation.evidence["slots"] == ()
    assert observation.evidence["reason"] == (
        "inventory_obstructed: opaque overlay covers the inventory"
    )
    assert observation.evidence["configuration_id"] == (
        "obstruction-aware-test-classifier"
    )
    assert observation.evidence["profile_id"] == LAYOUT.profile_id
    assert inventory_state_from_observation(observation).occupied_slots is None


def test_localizer_and_classifier_profile_mismatch_is_an_explicit_error() -> None:
    classifier = FakeClassifier(_decisions(), profile_id="different-profile")
    detector = InventoryDetector(
        FakeLocator(
            InventoryLocalization(
                INVENTORY_REGION,
                1.0,
                "exact",
                profile_id=LAYOUT.profile_id,
            )
        ),
        classifier,
    )

    with pytest.raises(InventoryDetectorError, match="profile mismatch"):
        detector.detect(_frame())

    assert classifier.calls == []


def test_custom_classifier_without_identity_uses_stable_evidence_fallback() -> None:
    class CustomClassifier:
        def classify(
            self, frame: Frame, inventory_region: Region
        ) -> tuple[SlotDecision, ...]:
            return _decisions()

    detector = InventoryDetector(
        FakeLocator(InventoryLocalization(INVENTORY_REGION, 1.0, "custom")),
        CustomClassifier(),
    )

    observation = _one_observation(detector)

    assert observation.evidence["configuration_id"] == (
        "unidentified-custom-classifier"
    )
    assert observation.evidence["profile_id"] is None


def test_out_of_frame_localized_region_is_an_explicit_error() -> None:
    outside = Region(1, 0, LAYOUT.width, LAYOUT.height)
    detector = InventoryDetector(
        FakeLocator(InventoryLocalization(outside, 1.0, "bad profile")),
        FakeClassifier(_decisions()),
    )

    with pytest.raises(InventoryDetectorError, match="outside frame"):
        detector.detect(_frame())


@pytest.mark.parametrize(
    "malformed",
    [
        _decisions()[:-1],
        (_decisions()[1], *_decisions()[1:]),
    ],
    ids=["wrong-count", "wrong-order"],
)
def test_malformed_classifier_decisions_are_explicit_errors(
    malformed: tuple[SlotDecision, ...],
) -> None:
    detector, _, _ = _detector(malformed)

    with pytest.raises(InventoryDetectorError, match="invalid inventory slot decisions"):
        detector.detect(_frame())


def test_classifier_must_return_an_immutable_tuple() -> None:
    class ListClassifier:
        def classify(
            self, frame: Frame, inventory_region: Region
        ) -> tuple[SlotDecision, ...]:
            return cast(tuple[SlotDecision, ...], list(_decisions()))

    detector = InventoryDetector(
        FakeLocator(InventoryLocalization(INVENTORY_REGION, 1.0, "exact")),
        ListClassifier(),
    )

    with pytest.raises(InventoryDetectorError, match="must return tuple"):
        detector.detect(_frame())


def test_overlapping_slot_geometry_is_an_explicit_error() -> None:
    decisions = list(_decisions())
    original = decisions[1]
    decisions[1] = SlotDecision(
        index=original.index,
        row=original.row,
        column=original.column,
        region=Region(16, 0, 32, 32),
        state=original.state,
        confidence=original.confidence,
        score=original.score,
        changed_fraction=original.changed_fraction,
    )
    detector, _, _ = _detector(tuple(decisions))

    with pytest.raises(InventoryDetectorError, match="non-overlapping stride"):
        detector.detect(_frame())


@pytest.mark.parametrize("field", ["localization_threshold", "minimum_slot_confidence"])
@pytest.mark.parametrize("value", [-0.1, 0.0, 1.1, float("nan"), 10**400, True])
def test_detector_rejects_invalid_confidence_policy(field: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "locator": FakeLocator(InventoryLocalization(INVENTORY_REGION, 1.0, "exact")),
        "classifier": FakeClassifier(_decisions()),
        field: value,
    }
    with pytest.raises(ValueError, match="must be between"):
        InventoryDetector(**kwargs)  # type: ignore[arg-type]


def _replace_observation(
    observation: Observation,
    *,
    evidence: object | None = None,
    kind: str | None = None,
    confidence: float | None = None,
) -> Observation:
    return Observation(
        kind=observation.kind if kind is None else kind,
        frame=observation.frame,
        confidence=observation.confidence if confidence is None else confidence,
        evidence=observation.evidence if evidence is None else evidence,  # type: ignore[arg-type]
        detector_version=observation.detector_version,
    )


def test_adapter_rejects_wrong_observation_kind_and_non_mapping_evidence() -> None:
    observation = _one_observation(_detector(_decisions({0}))[0])

    with pytest.raises(InventoryObservationError, match="observation kind"):
        inventory_state_from_observation(_replace_observation(observation, kind="inventory"))
    with pytest.raises(InventoryObservationError, match="evidence must be a mapping"):
        inventory_state_from_observation(_replace_observation(observation, evidence=[]))


def test_adapter_rejects_non_frame_reference() -> None:
    observation = _one_observation(_detector(_decisions({0}))[0])
    malformed = Observation(
        kind=observation.kind,
        frame=cast(object, "not-a-frame-ref"),  # type: ignore[arg-type]
        confidence=observation.confidence,
        evidence=observation.evidence,
        detector_version=observation.detector_version,
    )

    with pytest.raises(InventoryObservationError, match="frame must be FrameRef"):
        inventory_state_from_observation(malformed)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("evidence_schema_version", 2, "unsupported inventory evidence schema"),
        ("evidence_schema_version", True, "must be an integer"),
        ("capacity", 27, "capacity must be 28"),
        ("occupied_slots", True, "occupied_slots must be an integer"),
        ("label", "full", "inconsistent with occupied_slots"),
        ("region", None, "known inventory must include"),
        ("localization_confidence", float("nan"), "must be a finite number"),
        ("localization_confidence", 10**400, "must be a finite number"),
        ("configuration_id", None, "must be a non-empty string"),
        ("profile_id", 9, "must be a non-empty string"),
        ("slots", (), "known inventory must include all slot decisions"),
    ],
)
def test_adapter_rejects_malformed_or_incoherent_top_level_evidence(
    key: str, value: object, message: str
) -> None:
    observation = _one_observation(_detector(_decisions({0}))[0])
    evidence = dict(observation.evidence)
    evidence[key] = value

    with pytest.raises(InventoryObservationError, match=message):
        inventory_state_from_observation(_replace_observation(observation, evidence=evidence))


def test_adapter_rejects_missing_and_unknown_fields() -> None:
    observation = _one_observation(_detector(_decisions({0}))[0])
    missing = dict(observation.evidence)
    del missing["capacity"]
    unknown = dict(observation.evidence)
    unknown["guessed_slots"] = 1

    with pytest.raises(InventoryObservationError, match="missing capacity"):
        inventory_state_from_observation(_replace_observation(observation, evidence=missing))
    with pytest.raises(InventoryObservationError, match="unknown guessed_slots"):
        inventory_state_from_observation(_replace_observation(observation, evidence=unknown))


def test_adapter_rejects_malformed_slot_state_and_order() -> None:
    observation = _one_observation(_detector(_decisions({0}))[0])
    evidence = dict(observation.evidence)
    slots = [dict(slot) for slot in cast(tuple[dict[str, object], ...], evidence["slots"])]
    slots[0]["state"] = "probably"
    evidence["slots"] = slots

    with pytest.raises(InventoryObservationError, match="supported slot occupancy"):
        inventory_state_from_observation(_replace_observation(observation, evidence=evidence))

    slots[0]["state"] = "occupied"
    slots[0]["index"] = 1
    with pytest.raises(InventoryObservationError, match="row-major"):
        inventory_state_from_observation(_replace_observation(observation, evidence=evidence))


def test_adapter_rejects_observation_confidence_that_disagrees_with_evidence() -> None:
    observation = _one_observation(_detector(_decisions({0}))[0])

    with pytest.raises(InventoryObservationError, match="weakest"):
        inventory_state_from_observation(_replace_observation(observation, confidence=0.5))


def test_adapter_rejects_region_outside_observation_frame() -> None:
    observation = _one_observation(_detector(_decisions({0}))[0])
    evidence = dict(observation.evidence)
    evidence["region"] = (
        INVENTORY_REGION.x + 1,
        INVENTORY_REGION.y,
        INVENTORY_REGION.width,
        INVENTORY_REGION.height,
    )

    with pytest.raises(InventoryObservationError, match="does not fit observation frame"):
        inventory_state_from_observation(
            _replace_observation(observation, evidence=evidence)
        )


def test_inventory_detection_rejects_guessed_unknown_count_semantics() -> None:
    with pytest.raises(ValueError, match="unknown inventory must have zero confidence"):
        InventoryDetection(
            region=INVENTORY_REGION,
            occupied_slots=None,
            confidence=0.5,
            label="unknown",
            reason="uncertain",
            localization_confidence=1.0,
            configuration_id="test-classifier-v1",
            profile_id=LAYOUT.profile_id,
            slots=_decisions(uncertain={0}),
        )


def test_inventory_detection_rejects_non_region_value() -> None:
    with pytest.raises(ValueError, match="region must be Region or None"):
        InventoryDetection(
            region=cast(Region, object()),
            occupied_slots=None,
            confidence=0.0,
            label="unknown",
            reason="not localized",
            localization_confidence=0.0,
            configuration_id="test-classifier-v1",
            profile_id=None,
        )


def test_inventory_detection_rejects_zero_confidence_known_state() -> None:
    with pytest.raises(ValueError, match="known inventory must have confidence greater"):
        InventoryDetection(
            region=INVENTORY_REGION,
            occupied_slots=0,
            confidence=0.0,
            label="empty",
            reason=None,
            localization_confidence=1.0,
            configuration_id="test-classifier-v1",
            profile_id=LAYOUT.profile_id,
            slots=_decisions(),
        )


def test_inventory_detection_rejects_zero_confidence_localized_region() -> None:
    with pytest.raises(ValueError, match="localized region must have localization confidence"):
        InventoryDetection(
            region=INVENTORY_REGION,
            occupied_slots=None,
            confidence=0.0,
            label="unknown",
            reason="untrusted localization",
            localization_confidence=0.0,
            configuration_id="test-classifier-v1",
            profile_id=LAYOUT.profile_id,
        )
