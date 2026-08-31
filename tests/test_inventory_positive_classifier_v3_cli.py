from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from mining_automation.perception.inventory import positive_v3_evaluation_cli

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "perception"
    / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
)
_HEAD = "d" * 40


def test_v3_cli_writes_nonactivating_canonical_report_and_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        positive_v3_evaluation_cli,
        "_verify_clean_head",
        lambda expected: _HEAD if expected == _HEAD else "unexpected",
    )
    output = tmp_path / "v3-development"

    result = positive_v3_evaluation_cli.main(
        [
            "--fixture",
            str(_FIXTURE),
            "--output",
            str(output),
            "--expected-head",
            _HEAD,
        ]
    )

    assert result == 0
    report_path = output / "inventory-positive-v3-development-report.json"
    sidecar = output / "inventory-positive-v3-development-report.sha256"
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert sidecar.read_text(encoding="ascii") == f"{digest}  {report_path.name}\n"
    decoded = json.loads(report_path.read_text(encoding="utf-8"))
    assert decoded["activation_allowed"] is False
    assert decoded["validation_status"] == "independent-campaign-required"
    assert decoded["validation_case_ids"] == []
    assert decoded["independent_validation_case_count"] == 0
    assert decoded["generalization_unproven"] is True
    stdout = capsys.readouterr().out
    assert "Inventory positive V3 development regressions: PASS" in stdout
    assert "Validation status: independent-campaign-required" in stdout
    assert "Activation allowed: false" in stdout


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        (("e" * 40,), "Git HEAD mismatch"),
        ((_HEAD, " M src/example.py"), "worktree changes"),
    ],
)
def test_v3_cli_provenance_failure_writes_no_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    responses: tuple[str, ...],
    message: str,
) -> None:
    values: Iterator[str] = iter(responses)
    monkeypatch.setattr(
        positive_v3_evaluation_cli,
        "_git_output",
        lambda *arguments: next(values),
    )
    output = tmp_path / "must-not-exist"

    result = positive_v3_evaluation_cli.main(
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
    assert message in capsys.readouterr().err
    assert not output.exists()
