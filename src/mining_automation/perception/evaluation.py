"""Deterministic evaluation of detector ensembles against replay fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..contracts import Observation
from .detector import Detector, DetectorMetadata, run_detector, validate_detector
from .errors import DetectorError
from .replay import ExpectedObservation, ReplayDataset, ReplaySample

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "CaseEvaluation",
    "EvaluationIssue",
    "EvaluationReport",
    "IssueCategory",
    "evaluate_dataset",
]


REPORT_SCHEMA_VERSION: Final[int] = 1


class IssueCategory(StrEnum):
    """Stable machine-readable evaluation failure categories."""

    DETECTOR_ERROR = "detector_error"
    MISSING_OBSERVATION = "missing_observation"
    UNEXPECTED_OBSERVATION = "unexpected_observation"
    LABEL_MISMATCH = "label_mismatch"
    REGION_MISMATCH = "region_mismatch"
    CONFIDENCE_MISMATCH = "confidence_mismatch"


@dataclass(frozen=True, slots=True)
class EvaluationIssue:
    """One objective reason a fixture case failed."""

    category: IssueCategory
    message: str
    kind: str | None = None
    detector_id: str | None = None
    expectation_index: int | None = None
    observation_index: int | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "category": self.category.value,
            "message": self.message,
        }
        if self.kind is not None:
            result["kind"] = self.kind
        if self.detector_id is not None:
            result["detector_id"] = self.detector_id
        if self.expectation_index is not None:
            result["expectation_index"] = self.expectation_index
        if self.observation_index is not None:
            result["observation_index"] = self.observation_index
        return result


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    """Evaluation result for one fixture across the detector ensemble."""

    case_id: str
    tags: tuple[str, ...]
    provenance: dict[str, str]
    notes: str
    observations_produced: int
    issues: tuple[EvaluationIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "tags": list(self.tags),
            "provenance": dict(sorted(self.provenance.items())),
            "notes": self.notes,
            "observations_produced": self.observations_produced,
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Stable human- and machine-readable regression report."""

    dataset_id: str
    manifest_schema_version: int
    detectors: tuple[DetectorMetadata, ...]
    cases: tuple[CaseEvaluation, ...]
    report_schema_version: int = REPORT_SCHEMA_VERSION

    @property
    def cases_run(self) -> int:
        return len(self.cases)

    @property
    def cases_passed(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def cases_failed(self) -> int:
        return self.cases_run - self.cases_passed

    @property
    def passed(self) -> bool:
        return self.cases_failed == 0

    @property
    def failing_fixture_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases if not case.passed)

    def as_dict(self) -> dict[str, object]:
        return {
            "report_schema_version": self.report_schema_version,
            "manifest_schema_version": self.manifest_schema_version,
            "dataset_id": self.dataset_id,
            "detectors": [
                {"detector_id": item.detector_id, "version": item.version}
                for item in self.detectors
            ],
            "cases_run": self.cases_run,
            "cases_passed": self.cases_passed,
            "cases_failed": self.cases_failed,
            "passed": self.passed,
            "failing_fixture_ids": list(self.failing_fixture_ids),
            "case_results": [case.as_dict() for case in self.cases],
        }

    def to_json(self) -> str:
        """Return deterministic, newline-terminated JSON."""
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"

    def render_text(self) -> str:
        """Return a concise review-friendly report."""
        detector_text = ", ".join(
            f"{metadata.detector_id}@{metadata.version}" for metadata in self.detectors
        )
        lines = [
            f"Dataset: {self.dataset_id} (manifest schema {self.manifest_schema_version})",
            f"Detectors: {detector_text}",
            (
                f"Cases: {self.cases_run} run, {self.cases_passed} passed, "
                f"{self.cases_failed} failed"
            ),
        ]
        if self.failing_fixture_ids:
            lines.append(f"Failing fixtures: {', '.join(self.failing_fixture_ids)}")
        for case in self.cases:
            if case.passed:
                continue
            lines.append(f"- {case.case_id}: FAILED")
            lines.extend(f"  {issue.category.value}: {issue.message}" for issue in case.issues)
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class _DetectedObservation:
    detector_id: str
    observation: Observation
    index: int


def evaluate_dataset(
    dataset: ReplayDataset,
    detectors: tuple[Detector, ...] | list[Detector],
) -> EvaluationReport:
    """Evaluate a detector ensemble over every replay case.

    Observations from all supplied detectors are combined before matching. This
    lets a generic fixture require, for example, both a resource and inventory
    observation without assigning expectations to one detector implementation.
    """
    if not detectors:
        raise ValueError("at least one detector is required")
    metadata = tuple(validate_detector(detector) for detector in detectors)
    detector_ids = [item.detector_id for item in metadata]
    if len(set(detector_ids)) != len(detector_ids):
        raise ValueError("detector ids must be unique within an evaluation run")

    case_results = tuple(
        _evaluate_sample(sample, tuple(detectors), metadata) for sample in dataset.samples
    )
    return EvaluationReport(
        dataset_id=dataset.manifest.dataset_id,
        manifest_schema_version=dataset.manifest.schema_version,
        detectors=metadata,
        cases=case_results,
    )


def _evaluate_sample(
    sample: ReplaySample,
    detectors: tuple[Detector, ...],
    metadata: tuple[DetectorMetadata, ...],
) -> CaseEvaluation:
    actual: list[_DetectedObservation] = []
    issues: list[EvaluationIssue] = []
    for detector, detector_metadata in zip(detectors, metadata, strict=True):
        try:
            observations = run_detector(
                detector,
                sample.frame,
                expected_metadata=detector_metadata,
            )
        except DetectorError as exc:
            issues.append(
                EvaluationIssue(
                    category=IssueCategory.DETECTOR_ERROR,
                    detector_id=detector_metadata.detector_id,
                    message=str(exc),
                )
            )
            continue
        for observation in observations:
            actual.append(
                _DetectedObservation(
                    detector_id=detector_metadata.detector_id,
                    observation=observation,
                    index=len(actual),
                )
            )

    issues.extend(_compare_observations(sample.case.expected_observations, tuple(actual)))
    return CaseEvaluation(
        case_id=sample.case.case_id,
        tags=sample.case.tags,
        provenance=dict(sample.case.provenance),
        notes=sample.case.notes,
        observations_produced=len(actual),
        issues=tuple(issues),
    )


def _compare_observations(
    expected: tuple[ExpectedObservation, ...],
    actual: tuple[_DetectedObservation, ...],
) -> tuple[EvaluationIssue, ...]:
    issues: list[EvaluationIssue] = []
    used_actual: set[int] = set()

    expected_kinds = dict.fromkeys(item.kind for item in expected)
    for kind in expected_kinds:
        expected_group = [(index, item) for index, item in enumerate(expected) if item.kind == kind]
        actual_group = [item for item in actual if item.observation.kind == kind]
        pairs = _minimum_cost_pairs(expected_group, actual_group)
        paired_expected = {index for index, _ in pairs}
        for expected_index, detected in pairs:
            used_actual.add(detected.index)
            expectation = expected[expected_index]
            issues.extend(_constraint_issues(expected_index, expectation, detected))
        for expected_index, expectation in expected_group:
            if expected_index not in paired_expected:
                issues.append(
                    EvaluationIssue(
                        category=IssueCategory.MISSING_OBSERVATION,
                        kind=expectation.kind,
                        expectation_index=expected_index,
                        message=f"expected {expectation.kind!r} observation was not produced",
                    )
                )

    for detected in actual:
        if detected.index in used_actual:
            continue
        issues.append(
            EvaluationIssue(
                category=IssueCategory.UNEXPECTED_OBSERVATION,
                kind=detected.observation.kind,
                detector_id=detected.detector_id,
                observation_index=detected.index,
                message=(
                    f"unexpected {detected.observation.kind!r} observation from "
                    f"{detected.detector_id!r}"
                ),
            )
        )
    return tuple(issues)


def _minimum_cost_pairs(
    expected: list[tuple[int, ExpectedObservation]],
    actual: list[_DetectedObservation],
) -> tuple[tuple[int, _DetectedObservation], ...]:
    if not expected or not actual:
        return ()
    pair_count = min(len(expected), len(actual))
    # There are three optional constraints. Make one mismatch more expensive
    # than every possible specificity tie-break across the whole assignment.
    mismatch_weight = pair_count * 3 + 1
    if len(expected) <= len(actual):
        costs = [
            [
                _assignment_cost(
                    expectation,
                    detected.observation,
                    mismatch_weight=mismatch_weight,
                )
                for detected in actual
            ]
            for _, expectation in expected
        ]
        columns = _hungarian(costs)
        pairs = [(expected[row][0], actual[column]) for row, column in enumerate(columns)]
    else:
        costs = [
            [
                _assignment_cost(
                    expectation,
                    detected.observation,
                    mismatch_weight=mismatch_weight,
                )
                for _, expectation in expected
            ]
            for detected in actual
        ]
        columns = _hungarian(costs)
        pairs = [(expected[column][0], actual[row]) for row, column in enumerate(columns)]
    return tuple(sorted(pairs, key=lambda pair: pair[0]))


def _hungarian(costs: list[list[int]]) -> tuple[int, ...]:
    """Minimum-cost row-to-unique-column assignment for rows <= columns."""
    row_count = len(costs)
    column_count = len(costs[0])
    if row_count > column_count:  # pragma: no cover - caller invariant
        raise ValueError("Hungarian assignment requires rows <= columns")
    potentials_rows = [0] * (row_count + 1)
    potentials_columns = [0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    path = [0] * (column_count + 1)

    for row in range(1, row_count + 1):
        matched_row[0] = row
        column = 0
        minimum = [10**9] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[column] = True
            current_row = matched_row[column]
            delta = 10**9
            next_column = 0
            for candidate in range(1, column_count + 1):
                if used[candidate]:
                    continue
                reduced = (
                    costs[current_row - 1][candidate - 1]
                    - potentials_rows[current_row]
                    - potentials_columns[candidate]
                )
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    path[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(column_count + 1):
                if used[candidate]:
                    potentials_rows[matched_row[candidate]] += delta
                    potentials_columns[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            previous = path[column]
            matched_row[column] = matched_row[previous]
            column = previous
            if column == 0:
                break

    assignment = [0] * row_count
    for column in range(1, column_count + 1):
        if matched_row[column] != 0:
            assignment[matched_row[column] - 1] = column - 1
    return tuple(assignment)


def _assignment_cost(
    expectation: ExpectedObservation,
    observation: Observation,
    *,
    mismatch_weight: int,
) -> int:
    mismatches = sum(
        (
            expectation.label is not None and _observation_label(observation) != expectation.label,
            expectation.region is not None and _observation_region(observation) != expectation.region,
            expectation.confidence is not None
            and not expectation.confidence.contains(observation.confidence),
        )
    )
    specificity = sum(
        constraint is not None
        for constraint in (expectation.label, expectation.region, expectation.confidence)
    )
    return mismatches * mismatch_weight + (3 - specificity)


def _constraint_issues(
    expectation_index: int,
    expectation: ExpectedObservation,
    detected: _DetectedObservation,
) -> tuple[EvaluationIssue, ...]:
    observation = detected.observation
    issues: list[EvaluationIssue] = []
    actual_label = _observation_label(observation)
    if expectation.label is not None and actual_label != expectation.label:
        issues.append(
            EvaluationIssue(
                category=IssueCategory.LABEL_MISMATCH,
                kind=expectation.kind,
                detector_id=detected.detector_id,
                expectation_index=expectation_index,
                observation_index=detected.index,
                message=f"expected label {expectation.label!r}, got {actual_label!r}",
            )
        )
    actual_region = _observation_region(observation)
    if expectation.region is not None and actual_region != expectation.region:
        issues.append(
            EvaluationIssue(
                category=IssueCategory.REGION_MISMATCH,
                kind=expectation.kind,
                detector_id=detected.detector_id,
                expectation_index=expectation_index,
                observation_index=detected.index,
                message=f"expected region {expectation.region!r}, got {actual_region!r}",
            )
        )
    if expectation.confidence is not None and not expectation.confidence.contains(
        observation.confidence
    ):
        issues.append(
            EvaluationIssue(
                category=IssueCategory.CONFIDENCE_MISMATCH,
                kind=expectation.kind,
                detector_id=detected.detector_id,
                expectation_index=expectation_index,
                observation_index=detected.index,
                message=(
                    f"confidence {observation.confidence} outside inclusive range "
                    f"{expectation.confidence.describe()}"
                ),
            )
        )
    return tuple(issues)


def _observation_label(observation: Observation) -> str | None:
    value = observation.evidence.get("label")
    return value if isinstance(value, str) else None


def _observation_region(observation: Observation) -> tuple[int, int, int, int] | None:
    value = observation.evidence.get("region")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(not isinstance(component, int) or isinstance(component, bool) for component in value):
        return None
    return value[0], value[1], value[2], value[3]
