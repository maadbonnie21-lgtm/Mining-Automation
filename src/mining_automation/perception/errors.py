"""Typed failures for detector execution and saved-frame replay."""

from __future__ import annotations

__all__ = [
    "CorruptFixtureError",
    "DetectorContractError",
    "DetectorError",
    "DetectorExecutionError",
    "ManifestError",
    "MissingFixtureError",
    "PerceptionError",
    "ReplayError",
    "UnsupportedManifestVersionError",
]


class PerceptionError(Exception):
    """Base class for failures at the perception infrastructure boundary."""


class DetectorError(PerceptionError):
    """Base class for detector contract or execution failures."""


class DetectorContractError(DetectorError):
    """A detector or one of its outputs violated the public detector contract."""


class DetectorExecutionError(DetectorError):
    """A detector raised while processing a frame.

    The original exception is retained as ``__cause__`` by the guarded runner.
    """


class ReplayError(PerceptionError):
    """Base class for saved-frame replay failures."""


class ManifestError(ReplayError):
    """A replay manifest is unreadable or fails schema validation."""


class UnsupportedManifestVersionError(ManifestError):
    """A replay manifest uses a schema version this build cannot interpret."""


class MissingFixtureError(ReplayError):
    """A manifest refers to a fixture payload that does not exist."""


class CorruptFixtureError(ReplayError):
    """A fixture payload disagrees with its declared frame metadata."""
