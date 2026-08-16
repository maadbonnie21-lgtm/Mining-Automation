from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.contracts import Observation
from mining_automation.perception.detector import DetectorMetadata
from mining_automation.perception.evaluation import IssueCategory, evaluate_dataset
from mining_automation.perception.replay import (
    ConfidenceRange,
    ExpectedObservation,
    FixtureCase,
    FixtureManifest,
    FrameFixture,
    ReplayDataset,
    ReplaySample,
)


@dataclass(frozen=True)
class FunctionDetector:
    metadata: DetectorMetadata
    function: Callable[[Frame], Sequence[Observation]]

    def detect(self, frame: Frame) -> Sequence[Observation]:
        return self.function(frame)


def _observation(
    frame: Frame,
    *,
    kind: str,
    version: str,
    confidence: float = 1.0,
    label: str | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> Observation:
    evidence: dict[str, object] = {}
    if label is not None:
        evidence["label"] = label
    if region is not None:
        evidence["region"] = region
    return Observation(
        kind=kind,
        frame=frame.ref,
        confidence=confidence,
        evidence=evidence,
        detector_version=version,
    )


def _dataset(
    *expected_cases: tuple[ExpectedObservation, ...],
    dataset_id: str = "evaluation-tests",
) -> ReplayDataset:
    samples: list[ReplaySample] = []
    cases: list[FixtureCase] = []
    for index, expectations in enumerate(expected_cases):
        fixture = FrameFixture(
            path=f"frames/case-{index}.raw",
            width=4,
            height=4,
            pixel_format=PixelFormat.GRAY8,
        )
        case = FixtureCase(
            case_id=f"case-{index}",
            frame=fixture,
            expected_observations=expectations,
            tags=("synthetic",),
            provenance={"source": "unit-test", "sequence": str(index)},
            notes=f"case notes {index}",
        )
        frame = Frame.from_raw(
            RawFrame(bytes([index]) * 16, 4, 4, PixelFormat.GRAY8),
            frame_id=index + 1,
            captured_monotonic_s=float(index),
        )
        cases.append(case)
        samples.append(ReplaySample(case=case, frame=frame))
    manifest = FixtureManifest(schema_version=1, dataset_id=dataset_id, cases=tuple(cases))
    return ReplayDataset(Path("manifest.json"), manifest, tuple(samples))


def _detector(
    detector_id: str,
    version: str,
    function: Callable[[Frame], Sequence[Observation]],
) -> FunctionDetector:
    return FunctionDetector(DetectorMetadata(detector_id, version), function)


def test_combined_detector_ensemble_satisfies_generic_case() -> None:
    dataset = _dataset(
        (
            ExpectedObservation(
                "resource",
                label="iron",
                region=(0, 0, 2, 2),
                confidence=ConfidenceRange(0.8, 1.0),
            ),
            ExpectedObservation("inventory"),
        )
    )
    resource = _detector(
        "resource",
        "2.0",
        lambda frame: (
            _observation(
                frame,
                kind="resource",
                version="2.0",
                confidence=0.9,
                label="iron",
                region=(0, 0, 2, 2),
            ),
        ),
    )
    inventory = _detector(
        "inventory",
        "3.0",
        lambda frame: (_observation(frame, kind="inventory", version="3.0"),),
    )

    report = evaluate_dataset(dataset, [resource, inventory])

    assert report.passed
    assert report.cases_run == 1
    assert report.cases_passed == 1
    assert report.cases_failed == 0
    assert [item.detector_id for item in report.detectors] == ["resource", "inventory"]


def test_empty_expectations_and_empty_output_pass() -> None:
    report = evaluate_dataset(
        _dataset(()),
        [_detector("empty", "1", lambda frame: ())],
    )

    assert report.passed
    assert report.cases[0].observations_produced == 0


def test_missing_and_unexpected_observations_are_both_reported() -> None:
    report = evaluate_dataset(
        _dataset((ExpectedObservation("resource"),)),
        [
            _detector(
                "inventory",
                "1",
                lambda frame: (_observation(frame, kind="inventory", version="1"),),
            )
        ],
    )

    assert not report.passed
    assert [issue.category for issue in report.cases[0].issues] == [
        IssueCategory.MISSING_OBSERVATION,
        IssueCategory.UNEXPECTED_OBSERVATION,
    ]


def test_label_region_and_confidence_mismatches_are_specific() -> None:
    expectation = ExpectedObservation(
        "resource",
        label="iron",
        region=(0, 0, 2, 2),
        confidence=ConfidenceRange(0.8, 0.9),
    )
    report = evaluate_dataset(
        _dataset((expectation,)),
        [
            _detector(
                "resource",
                "1",
                lambda frame: (
                    _observation(
                        frame,
                        kind="resource",
                        version="1",
                        confidence=0.7,
                        label="coal",
                        region=(1, 1, 2, 2),
                    ),
                ),
            )
        ],
    )

    assert {issue.category for issue in report.cases[0].issues} == {
        IssueCategory.LABEL_MISMATCH,
        IssueCategory.REGION_MISMATCH,
        IssueCategory.CONFIDENCE_MISMATCH,
    }


@pytest.mark.parametrize("confidence", [0.8, 0.9])
def test_confidence_range_bounds_are_inclusive(confidence: float) -> None:
    expectation = ExpectedObservation("resource", confidence=ConfidenceRange(0.8, 0.9))
    report = evaluate_dataset(
        _dataset((expectation,)),
        [
            _detector(
                "resource",
                "1",
                lambda frame: (
                    _observation(
                        frame,
                        kind="resource",
                        version="1",
                        confidence=confidence,
                    ),
                ),
            )
        ],
    )

    assert report.passed


def test_minimum_cost_matching_does_not_let_broad_expectation_steal_match() -> None:
    dataset = _dataset(
        (
            ExpectedObservation("resource"),
            ExpectedObservation("resource", label="iron"),
        )
    )
    detector = _detector(
        "resource",
        "1",
        lambda frame: (
            _observation(frame, kind="resource", version="1", label="iron"),
            _observation(frame, kind="resource", version="1", label="coal"),
        ),
    )

    assert evaluate_dataset(dataset, [detector]).passed


def test_specific_expectation_wins_tie_when_observations_are_under_supplied() -> None:
    dataset = _dataset(
        (
            ExpectedObservation("resource"),
            ExpectedObservation("resource", label="iron"),
        )
    )
    detector = _detector(
        "resource",
        "1",
        lambda frame: (_observation(frame, kind="resource", version="1", label="iron"),),
    )

    report = evaluate_dataset(dataset, [detector])

    assert [issue.category for issue in report.cases[0].issues] == [
        IssueCategory.MISSING_OBSERVATION
    ]
    assert report.cases[0].issues[0].expectation_index == 0


def test_mismatch_minimization_precedes_specificity_tie_break() -> None:
    dataset = _dataset(
        (
            ExpectedObservation("resource"),
            ExpectedObservation("resource", label="iron"),
        )
    )
    detector = _detector(
        "resource",
        "1",
        lambda frame: (_observation(frame, kind="resource", version="1", label="coal"),),
    )

    report = evaluate_dataset(dataset, [detector])

    assert [issue.category for issue in report.cases[0].issues] == [
        IssueCategory.MISSING_OBSERVATION
    ]
    assert report.cases[0].issues[0].expectation_index == 1


def test_extra_same_kind_observation_is_unexpected() -> None:
    report = evaluate_dataset(
        _dataset((ExpectedObservation("resource"),)),
        [
            _detector(
                "resource",
                "1",
                lambda frame: (
                    _observation(frame, kind="resource", version="1"),
                    _observation(frame, kind="resource", version="1"),
                ),
            )
        ],
    )

    assert [issue.category for issue in report.cases[0].issues] == [
        IssueCategory.UNEXPECTED_OBSERVATION
    ]


def test_detector_exception_fails_every_case_without_stopping_replay() -> None:
    def fail(frame: Frame) -> Sequence[Observation]:
        raise RuntimeError(f"broken on {frame.frame_id}")

    report = evaluate_dataset(_dataset((), ()), [_detector("broken", "1", fail)])

    assert report.cases_run == 2
    assert report.cases_failed == 2
    assert report.failing_fixture_ids == ("case-0", "case-1")
    assert all(
        case.issues[0].category is IssueCategory.DETECTOR_ERROR for case in report.cases
    )
    assert "broken on 1" in report.cases[0].issues[0].message
    assert "broken on 2" in report.cases[1].issues[0].message


def test_malformed_observation_is_a_case_detector_error() -> None:
    detector = _detector(
        "malformed",
        "1",
        lambda frame: (
            Observation(
                kind="resource",
                frame=frame.ref,
                confidence=1.0,
                evidence=None,  # type: ignore[arg-type]
                detector_version="1",
            ),
        ),
    )

    report = evaluate_dataset(_dataset(()), [detector])

    assert [issue.category for issue in report.cases[0].issues] == [
        IssueCategory.DETECTOR_ERROR
    ]
    assert "evidence must be a Mapping" in report.cases[0].issues[0].message


def test_malformed_frame_reference_is_a_case_detector_error() -> None:
    detector = _detector(
        "malformed",
        "1",
        lambda frame: (
            Observation(
                kind="resource",
                frame="not-a-frame-ref",  # type: ignore[arg-type]
                confidence=1.0,
                detector_version="1",
            ),
        ),
    )

    report = evaluate_dataset(_dataset(()), [detector])

    assert [issue.category for issue in report.cases[0].issues] == [
        IssueCategory.DETECTOR_ERROR
    ]
    assert "frame must be FrameRef" in report.cases[0].issues[0].message


def test_detector_metadata_must_remain_stable_through_evaluation() -> None:
    class ChangingMetadataDetector:
        def __init__(self) -> None:
            self.metadata_reads = 0
            self.detect_calls = 0

        @property
        def metadata(self) -> DetectorMetadata:
            self.metadata_reads += 1
            return DetectorMetadata("changing", str(self.metadata_reads))

        def detect(self, frame: Frame) -> Sequence[Observation]:
            self.detect_calls += 1
            return ()

    detector = ChangingMetadataDetector()

    report = evaluate_dataset(_dataset(()), [detector])

    assert report.detectors == (DetectorMetadata("changing", "1"),)
    assert [issue.category for issue in report.cases[0].issues] == [
        IssueCategory.DETECTOR_ERROR
    ]
    assert "metadata changed during the evaluation run" in report.cases[0].issues[0].message
    assert detector.detect_calls == 0


def test_report_preserves_metadata_and_renders_deterministically() -> None:
    report = evaluate_dataset(
        _dataset((), dataset_id="report-dataset"),
        [_detector("empty", "2026.08", lambda frame: ())],
    )

    payload = json.loads(report.to_json())
    assert report.to_json() == report.to_json()
    assert payload["report_schema_version"] == 1
    assert payload["dataset_id"] == "report-dataset"
    assert payload["detectors"] == [{"detector_id": "empty", "version": "2026.08"}]
    assert payload["case_results"][0]["tags"] == ["synthetic"]
    assert payload["case_results"][0]["provenance"] == {
        "sequence": "0",
        "source": "unit-test",
    }
    assert payload["case_results"][0]["notes"] == "case notes 0"
    assert "Cases: 1 run, 1 passed, 0 failed" in report.render_text()


def test_evaluation_requires_detectors_with_unique_ids() -> None:
    dataset = _dataset(())
    detector = _detector("duplicate", "1", lambda frame: ())

    with pytest.raises(ValueError, match="at least one detector"):
        evaluate_dataset(dataset, [])
    with pytest.raises(ValueError, match="detector ids must be unique"):
        evaluate_dataset(dataset, [detector, detector])
