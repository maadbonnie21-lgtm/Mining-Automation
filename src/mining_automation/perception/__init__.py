"""Platform-neutral detector evaluation and saved-frame replay.

The perception package defines detector contracts and a deterministic regression
harness. It consumes owned capture :class:`~mining_automation.capture.Frame`
values but does not implement or emulate a live capture backend.
"""

from __future__ import annotations

from .detector import Detector, DetectorMetadata, run_detector, validate_detector
from .errors import (
    CorruptFixtureError,
    DetectorContractError,
    DetectorError,
    DetectorExecutionError,
    ManifestError,
    MissingFixtureError,
    PerceptionError,
    ReplayError,
    UnsupportedManifestVersionError,
)
from .evaluation import (
    REPORT_SCHEMA_VERSION,
    CaseEvaluation,
    EvaluationIssue,
    EvaluationReport,
    IssueCategory,
    evaluate_dataset,
)
from .replay import (
    MANIFEST_SCHEMA_VERSION,
    ConfidenceRange,
    ExpectedObservation,
    FixtureCase,
    FixtureManifest,
    FrameFixture,
    ReplayDataset,
    ReplaySample,
    load_fixture_manifest,
    load_replay_dataset,
)

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "CaseEvaluation",
    "ConfidenceRange",
    "CorruptFixtureError",
    "Detector",
    "DetectorContractError",
    "DetectorError",
    "DetectorExecutionError",
    "DetectorMetadata",
    "EvaluationIssue",
    "EvaluationReport",
    "ExpectedObservation",
    "FixtureCase",
    "FixtureManifest",
    "FrameFixture",
    "IssueCategory",
    "ManifestError",
    "MissingFixtureError",
    "PerceptionError",
    "ReplayDataset",
    "ReplayError",
    "ReplaySample",
    "UnsupportedManifestVersionError",
    "evaluate_dataset",
    "load_fixture_manifest",
    "load_replay_dataset",
    "run_detector",
    "validate_detector",
]
