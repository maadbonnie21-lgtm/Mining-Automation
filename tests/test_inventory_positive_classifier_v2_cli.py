from __future__ import annotations

import hashlib
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
        match="model files changed after the recorded freeze commit",
    ):
        positive_v2_evaluation_cli._verify_clean_head(_HEAD)
