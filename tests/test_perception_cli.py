from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from mining_automation.perception.cli import load_detector, main
from mining_automation.perception.errors import DetectorContractError
from mining_automation.perception.testing import EmptyDetector


def _write_manifest(tmp_path: Path, *, expected_kinds: list[str]) -> Path:
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "case.raw").write_bytes(b"\x7f")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "cli-tests",
                "cases": [
                    {
                        "case_id": "cli-case",
                        "frame": {
                            "path": "frames/case.raw",
                            "width": 1,
                            "height": 1,
                            "pixel_format": "gray8",
                        },
                        "expected_observations": [
                            {"kind": kind} for kind in expected_kinds
                        ],
                        "tags": ["synthetic"],
                        "provenance": {"source": "unit-test"},
                        "notes": "CLI fixture",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize(
    "specification",
    [
        "mining_automation.perception.testing:empty_detector",
        "mining_automation.perception.testing:EmptyDetector",
        "mining_automation.perception.testing:build_empty_detector",
    ],
)
def test_detector_loader_supports_instance_class_and_factory(specification: str) -> None:
    assert isinstance(load_detector(specification), EmptyDetector)


@pytest.mark.parametrize(
    "specification",
    [
        "missing-separator",
        "missing.perception.module:detector",
        "mining_automation.perception.testing:missing",
    ],
)
def test_detector_loader_rejects_invalid_specifications(specification: str) -> None:
    with pytest.raises(DetectorContractError):
        load_detector(specification)


def test_detector_loader_wraps_dynamic_attribute_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("broken_detector_module")

    def fail_attribute_lookup(name: str) -> object:
        raise RuntimeError(f"cannot read {name}")

    module.__getattr__ = fail_attribute_lookup
    monkeypatch.setitem(sys.modules, module.__name__, module)

    with pytest.raises(DetectorContractError, match="could not read detector attribute"):
        load_detector(f"{module.__name__}:detector")


def test_cli_returns_zero_and_writes_machine_report_on_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _write_manifest(tmp_path, expected_kinds=[])
    json_report = tmp_path / "report.json"

    result = main(
        [
            "--manifest",
            str(manifest),
            "--detector",
            "mining_automation.perception.testing:empty_detector",
            "--json-report",
            str(json_report),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Cases: 1 run, 1 passed, 0 failed" in captured.out
    assert captured.err == ""
    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["failing_fixture_ids"] == []


def test_cli_returns_one_and_still_writes_report_on_regression_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _write_manifest(tmp_path, expected_kinds=["resource"])
    json_report = tmp_path / "failure.json"

    result = main(
        [
            "--manifest",
            str(manifest),
            "--detector",
            "mining_automation.perception.testing:EmptyDetector",
            "--json-report",
            str(json_report),
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Failing fixtures: cli-case" in captured.out
    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["case_results"][0]["issues"][0]["category"] == "missing_observation"


def test_cli_returns_two_for_manifest_setup_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "broken.json"
    manifest.write_text("not json", encoding="utf-8")

    result = main(
        [
            "--manifest",
            str(manifest),
            "--detector",
            "mining_automation.perception.testing:empty_detector",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "perception evaluation error" in captured.err
