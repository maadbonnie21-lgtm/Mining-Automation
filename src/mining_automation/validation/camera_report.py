"""Deterministic, provenance-bound reports for real camera validation.

The report digest is intentionally kept outside the report. This makes the
SHA-256 describe the exact bytes on disk without introducing a recursive
self-hash field.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CAMERA_VALIDATION_REPORT_SCHEMA_VERSION = 1

_FULL_LOWERCASE_GIT_SHA = re.compile(r"[0-9a-f]{40}")

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class CameraReportProvenance:
    """Immutable provenance required for a camera-validation report."""

    git_head_sha: str
    detector_id: str
    detector_version: str
    profile_id: str
    plan_id: str
    plan_version: str
    command_argv: tuple[str, ...]
    tracked_worktree_clean: bool

    def __post_init__(self) -> None:
        if _FULL_LOWERCASE_GIT_SHA.fullmatch(self.git_head_sha) is None:
            raise ValueError("git_head_sha must be a full 40-character lowercase Git SHA")

        for field_name in (
            "detector_id",
            "detector_version",
            "profile_id",
            "plan_id",
            "plan_version",
        ):
            _validate_provenance_string(field_name, getattr(self, field_name))

        if not isinstance(self.command_argv, tuple) or not self.command_argv:
            raise ValueError("command_argv must be a non-empty tuple of strings")
        for index, argument in enumerate(self.command_argv):
            if not isinstance(argument, str) or not argument.strip():
                raise ValueError(f"command_argv[{index}] must be a non-empty string")
            if "\x00" in argument or "\r" in argument or "\n" in argument:
                raise ValueError(
                    f"command_argv[{index}] must not contain NUL or line-break characters"
                )

        if type(self.tracked_worktree_clean) is not bool:
            raise ValueError("tracked_worktree_clean must be a bool")

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a detached JSON-compatible representation."""

        return {
            "command_argv": list(self.command_argv),
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "git_head_sha": self.git_head_sha,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "profile_id": self.profile_id,
            "tracked_worktree_clean": self.tracked_worktree_clean,
        }


@dataclass(frozen=True, slots=True)
class CameraReportWriteResult:
    """Paths and exact digest produced by :func:`write_camera_validation_report`."""

    report_path: Path
    digest_path: Path
    sha256: str


def camera_validation_report_bytes(
    evidence: Mapping[str, object],
    provenance: CameraReportProvenance,
) -> bytes:
    """Build canonical UTF-8 JSON bytes for camera-validation evidence."""

    if not isinstance(evidence, Mapping):
        raise TypeError("evidence must be a mapping")

    payload: dict[str, JsonValue] = {
        "evidence": _normalize_json_mapping(evidence, "evidence"),
        "provenance": provenance.as_dict(),
        "schema_version": CAMERA_VALIDATION_REPORT_SCHEMA_VERSION,
    }
    text = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"
    return text.encode("utf-8")


def write_camera_validation_report(
    report_path: Path,
    evidence: Mapping[str, object],
    provenance: CameraReportProvenance,
) -> CameraReportWriteResult:
    """Write a report and digest sidecar without overwriting either target.

    The sidecar path is ``<report filename>.sha256`` and contains the lowercase
    SHA-256 followed by one newline. If either target already exists, nothing
    is overwritten. If sidecar creation fails after report creation, the new
    report is removed so a caller never receives a successful partial pair.
    """

    if not isinstance(report_path, Path):
        raise TypeError("report_path must be a pathlib.Path")

    digest_path = report_path.with_name(f"{report_path.name}.sha256")
    if report_path.exists():
        raise FileExistsError(report_path)
    if digest_path.exists():
        raise FileExistsError(digest_path)

    report_bytes = camera_validation_report_bytes(evidence, provenance)
    digest = hashlib.sha256(report_bytes).hexdigest()
    digest_bytes = f"{digest}\n".encode("ascii")

    _exclusive_write_bytes(report_path, report_bytes)
    try:
        _exclusive_write_bytes(digest_path, digest_bytes)
    except BaseException:
        report_path.unlink(missing_ok=True)
        raise

    return CameraReportWriteResult(
        report_path=report_path,
        digest_path=digest_path,
        sha256=digest,
    )


def _validate_provenance_string(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty, trimmed string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")


def _normalize_json_mapping(value: Mapping[str, object], path: str) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{path} keys must be strings")
        normalized[key] = _normalize_json_value(item, f"{path}.{key}")
    return normalized


def _normalize_json_value(value: object, path: str) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain a non-finite float")
        return value
    if isinstance(value, Mapping):
        return _normalize_json_mapping(value, path)
    if isinstance(value, (list, tuple)):
        return [
            _normalize_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"{path} contains unsupported JSON value {type(value).__name__}")


def _exclusive_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise
