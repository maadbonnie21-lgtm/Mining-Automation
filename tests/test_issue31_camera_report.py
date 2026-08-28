"""Issue #31 deterministic camera-validation report regressions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from mining_automation.validation import camera_report
from mining_automation.validation.camera_report import (
    CAMERA_VALIDATION_REPORT_SCHEMA_VERSION,
    CameraReportProvenance,
    camera_validation_report_bytes,
    write_camera_validation_report,
)


@pytest.fixture
def provenance() -> CameraReportProvenance:
    return CameraReportProvenance(
        git_head_sha="0123456789abcdef0123456789abcdef01234567",
        detector_id="profiled-resource:varrock-east-iron-v1",
        detector_version="2.1.0",
        profile_id="varrock-east-iron-v1",
        plan_id="varrock-east-supported-view",
        plan_version="1.0.0",
        command_argv=("python", "-m", "tools.validate_camera_reacquisition", "--trials", "3"),
        tracked_worktree_clean=True,
    )


def test_independent_fixed_inputs_have_identical_canonical_bytes_and_digest(
    provenance: CameraReportProvenance,
) -> None:
    first = {
        "trials": [{"verdict": "DEFINITIVE", "landmarks": 6}],
        "drift": {"uncertain": 36, "false_definitive_targets": 0},
    }
    second = {
        "drift": {"false_definitive_targets": 0, "uncertain": 36},
        "trials": [{"landmarks": 6, "verdict": "DEFINITIVE"}],
    }
    independent_provenance = replace(provenance)

    first_bytes = camera_validation_report_bytes(first, provenance)
    second_bytes = camera_validation_report_bytes(second, independent_provenance)

    assert first_bytes == second_bytes
    assert first_bytes.endswith(b"\n")
    assert hashlib.sha256(first_bytes).hexdigest() == hashlib.sha256(second_bytes).hexdigest()
    assert json.loads(first_bytes)["schema_version"] == CAMERA_VALIDATION_REPORT_SCHEMA_VERSION


def test_evidence_change_changes_digest(provenance: CameraReportProvenance) -> None:
    passing = camera_validation_report_bytes({"matched_landmarks": 6}, provenance)
    failing = camera_validation_report_bytes({"matched_landmarks": 5}, provenance)

    assert hashlib.sha256(passing).digest() != hashlib.sha256(failing).digest()


def test_writer_returns_exact_report_digest_and_external_sidecar(
    tmp_path: Path,
    provenance: CameraReportProvenance,
) -> None:
    report_path = tmp_path / "nested" / "camera-validation.json"

    result = write_camera_validation_report(
        report_path,
        {"scene": {"verdict": "DEFINITIVE", "matched_zones": ["west", "east", "south"]}},
        provenance,
    )

    report_bytes = report_path.read_bytes()
    exact_digest = hashlib.sha256(report_bytes).hexdigest()
    assert result.report_path == report_path
    assert result.digest_path == Path(f"{report_path}.sha256")
    assert result.sha256 == exact_digest
    assert result.digest_path.read_text(encoding="ascii") == f"{exact_digest}\n"
    assert json.loads(report_bytes) == {
        "evidence": {
            "scene": {
                "matched_zones": ["west", "east", "south"],
                "verdict": "DEFINITIVE",
            }
        },
        "provenance": provenance.as_dict(),
        "schema_version": CAMERA_VALIDATION_REPORT_SCHEMA_VERSION,
    }
    assert "sha256" not in json.loads(report_bytes)


def test_writer_never_overwrites_existing_report(
    tmp_path: Path,
    provenance: CameraReportProvenance,
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text("keep-report", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_camera_validation_report(report_path, {"trial": 1}, provenance)

    assert report_path.read_text(encoding="utf-8") == "keep-report"
    assert not Path(f"{report_path}.sha256").exists()


def test_writer_never_overwrites_existing_digest_sidecar(
    tmp_path: Path,
    provenance: CameraReportProvenance,
) -> None:
    report_path = tmp_path / "report.json"
    digest_path = Path(f"{report_path}.sha256")
    digest_path.write_text("keep-digest", encoding="ascii")

    with pytest.raises(FileExistsError):
        write_camera_validation_report(report_path, {"trial": 1}, provenance)

    assert not report_path.exists()
    assert digest_path.read_text(encoding="ascii") == "keep-digest"


def test_sidecar_failure_removes_the_new_report_pair(
    tmp_path: Path,
    provenance: CameraReportProvenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "report.json"
    digest_path = Path(f"{report_path}.sha256")
    original_write = camera_report._exclusive_write_bytes

    def fail_sidecar(path: Path, payload: bytes) -> None:
        if path == digest_path:
            raise OSError("simulated sidecar failure")
        original_write(path, payload)

    monkeypatch.setattr(camera_report, "_exclusive_write_bytes", fail_sidecar)

    with pytest.raises(OSError, match="simulated sidecar failure"):
        write_camera_validation_report(report_path, {"trial": 1}, provenance)

    assert not report_path.exists()
    assert not digest_path.exists()


@pytest.mark.parametrize(
    "git_head_sha",
    [
        "0123456789abcdef0123456789abcdef0123456",
        "0123456789ABCDEF0123456789ABCDEF01234567",
        "g123456789abcdef0123456789abcdef01234567",
    ],
)
def test_provenance_rejects_noncanonical_git_sha(
    provenance: CameraReportProvenance,
    git_head_sha: str,
) -> None:
    with pytest.raises(ValueError, match="40-character lowercase Git SHA"):
        replace(provenance, git_head_sha=git_head_sha)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("detector_id", ""),
        ("detector_version", " 2.1.0"),
        ("profile_id", "varrock-east-iron-v1\n"),
        ("plan_id", "\x00plan"),
        ("plan_version", " "),
    ],
)
def test_provenance_rejects_invalid_identity_and_version_strings(
    provenance: CameraReportProvenance,
    field_name: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        replace(provenance, **{field_name: value})


@pytest.mark.parametrize(
    "command_argv",
    [
        (),
        ("python", ""),
        ("python", "bad\nargument"),
        cast(tuple[str, ...], ("python", 3)),
        cast(tuple[str, ...], ["python"]),
    ],
)
def test_provenance_rejects_invalid_command_argv(
    provenance: CameraReportProvenance,
    command_argv: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="command_argv"):
        replace(provenance, command_argv=command_argv)


def test_provenance_requires_an_actual_bool(provenance: CameraReportProvenance) -> None:
    with pytest.raises(ValueError, match="tracked_worktree_clean must be a bool"):
        replace(provenance, tracked_worktree_clean=cast(bool, 1))


@pytest.mark.parametrize(
    "evidence",
    [
        {"distance": float("nan")},
        {"distance": float("inf")},
        cast(dict[str, object], {1: "not-a-string-key"}),
        {"unsupported": {1, 2}},
    ],
)
def test_report_rejects_noncanonical_json_evidence(
    provenance: CameraReportProvenance,
    evidence: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        camera_validation_report_bytes(evidence, provenance)
