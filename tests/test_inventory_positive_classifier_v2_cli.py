from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mining_automation.perception.inventory import (
    InventoryPositiveV2EvaluationError,
    positive_v2_evaluation_cli,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "perception"
    / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)
_HEAD = "9985c9ad2522ef869efff6fe2dfd4979c69d1c79"


def test_cli_writes_canonical_failure_report_and_matching_sidecar(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        positive_v2_evaluation_cli,
        "_verify_clean_head",
        lambda expected: _HEAD if expected == _HEAD else "unexpected",
    )
    output = tmp_path / "report"

    result = positive_v2_evaluation_cli.main(
        [
            "--fixture",
            str(_FIXTURE),
            "--output",
            str(output),
            "--expected-head",
            _HEAD,
        ]
    )

    assert result == 1
    report = output / "inventory-positive-v2-report.json"
    sidecar = output / "inventory-positive-v2-report.sha256"
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    assert sidecar.read_text(encoding="ascii") == f"{digest}  {report.name}\n"
    decoded = json.loads(report.read_text(encoding="utf-8"))
    assert decoded["git_provenance"] == {
        "head_sha": _HEAD,
        "verified_by_clean_head_cli": True,
    }
    repository_evidence = decoded["calibration"]["repository_evidence"]
    assert repository_evidence["model_commit_precedes_formal_evaluator_commit"]
    assert repository_evidence["unseen_data_chronology_established"] is False
    assert "Inventory positive V2: FAIL" in capsys.readouterr().out


def test_cli_refuses_to_replace_an_existing_output_directory(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        positive_v2_evaluation_cli,
        "_verify_clean_head",
        lambda expected: _HEAD,
    )
    output = tmp_path / "existing"
    output.mkdir()

    result = positive_v2_evaluation_cli.main(
        [
            "--fixture",
            str(_FIXTURE),
            "--output",
            str(output),
            "--expected-head",
            _HEAD,
        ]
    )

    assert result == 2
    assert "cannot write V2 report directory" in capsys.readouterr().err
    assert list(output.iterdir()) == []


def test_clean_head_verification_rejects_dirty_tracked_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter((_HEAD, " M src/example.py"))
    monkeypatch.setattr(
        positive_v2_evaluation_cli,
        "_git_output",
        lambda *arguments: next(responses),
    )

    with pytest.raises(
        InventoryPositiveV2EvaluationError,
        match="tracked worktree changes",
    ):
        positive_v2_evaluation_cli._verify_clean_head(_HEAD)


def test_clean_head_verification_rejects_model_changes_after_freeze(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    responses = iter(
        (
            _HEAD,
            "",
            "",
            "",
            "src/mining_automation/perception/inventory/positive_classifier_v2.py",
        )
    )
    monkeypatch.setattr(
        positive_v2_evaluation_cli,
        "_git_output",
        lambda *arguments: next(responses),
    )

    with pytest.raises(
        InventoryPositiveV2EvaluationError,
        match="runtime behavior dependencies changed after the recorded freeze commit",
    ):
        positive_v2_evaluation_cli._verify_clean_head(_HEAD)


def test_clean_head_verification_guards_complete_runtime_behavior_closure(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, ...]] = []

    def git_output(*arguments: str) -> str:
        calls.append(arguments)
        return _HEAD if arguments == ("rev-parse", "HEAD") else ""

    monkeypatch.setattr(positive_v2_evaluation_cli, "_git_output", git_output)

    assert positive_v2_evaluation_cli._verify_clean_head(_HEAD) == _HEAD

    assert calls[2] == (
        "merge-base",
        "--is-ancestor",
        positive_v2_evaluation_cli.INVENTORY_POSITIVE_V2_MODEL_FREEZE_GIT_SHA,
        positive_v2_evaluation_cli.INVENTORY_POSITIVE_V2_FORMAL_EVALUATOR_GIT_SHA,
    )
    assert calls[3] == (
        "merge-base",
        "--is-ancestor",
        positive_v2_evaluation_cli.INVENTORY_POSITIVE_V2_FORMAL_EVALUATOR_GIT_SHA,
        _HEAD,
    )
    guarded = set(calls[4][4:])
    assert guarded == {
        "src/mining_automation/capture/__init__.py",
        "src/mining_automation/capture/errors.py",
        "src/mining_automation/capture/frame.py",
        "src/mining_automation/contracts.py",
        "src/mining_automation/perception/detector.py",
        "src/mining_automation/perception/errors.py",
        "src/mining_automation/perception/inventory/adapter.py",
        "src/mining_automation/perception/inventory/classification.py",
        "src/mining_automation/perception/inventory/configuration.py",
        "src/mining_automation/perception/inventory/detector.py",
        "src/mining_automation/perception/inventory/geometry.py",
        "src/mining_automation/perception/inventory/localization.py",
        "src/mining_automation/perception/inventory/positive_classifier_v2.py",
        "src/mining_automation/perception/inventory/positive_v2_calibration.py",
        "src/mining_automation/perception/inventory/sanitized_replay.py",
    }
