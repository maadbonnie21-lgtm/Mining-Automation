from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from mining_automation.perception.inventory import (
    positive_v3_independent_validation as validation,
)
from mining_automation.perception.inventory import (
    positive_v3_independent_validation_cli as cli,
)
from mining_automation.perception.inventory.positive_v3_independent_validation import (
    InventoryPositiveV3IndependentValidationError,
    frozen_v3_model_binding,
)

_ROOT = Path(__file__).resolve().parent.parent
_HEAD = "a" * 40


@pytest.fixture(autouse=True)
def _allow_cli_unit_tests_to_use_an_uncommitted_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validation,
        "_verify_repository_state",
        lambda root, _expected_head: root.resolve(strict=True),
    )


def test_prepare_cli_writes_canonical_nonactivating_readiness_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_verify_clean_head", lambda expected: (_HEAD, _ROOT))
    output = tmp_path / "readiness"

    result = cli.main(
        [
            "prepare",
            "--output",
            str(output),
            "--expected-head",
            _HEAD,
        ]
    )

    assert result == 0
    expected_files = {
        "approval-registry-entry.template.json",
        "approval-registry-entry.template.json.sha256",
        "campaign-manifest.template.json",
        "campaign-manifest.template.json.sha256",
        "inventory-positive-v3-validation-readiness-report.json",
        "inventory-positive-v3-validation-readiness-report.json.sha256",
        "preregistration.json",
        "preregistration.json.sha256",
        "reviewer-truth.template.json",
        "reviewer-truth.template.json.sha256",
        "source-capture-report.template.json",
        "source-capture-report.template.json.sha256",
        "source-session-report.template.json",
        "source-session-report.template.json.sha256",
        "validation-package.template.json",
        "validation-package.template.json.sha256",
    }
    assert {path.name for path in output.iterdir()} == expected_files
    for path in output.glob("*.json"):
        payload = path.read_bytes()
        decoded = json.loads(payload)
        assert payload == (
            json.dumps(
                decoded,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        assert path.with_suffix(path.suffix + ".sha256").read_text(
            encoding="ascii"
        ) == f"{digest}  {path.name}\n"
    report = json.loads(
        (output / "inventory-positive-v3-validation-readiness-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["live_validation_performed"] is False
    assert report["campaign_execution_authorized"] is False
    assert report["activation_allowed"] is False
    assert report["independent_validation_case_count"] == 0
    stdout = capsys.readouterr().out
    assert "Inventory V3 independent-validation readiness: PASS" in stdout
    assert "Live validation authorized: false" in stdout


def test_prepare_cli_does_not_overwrite_an_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_verify_clean_head", lambda expected: (_HEAD, _ROOT))
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "winner.txt"
    sentinel.write_bytes(b"foreign-winner")

    result = cli.main(
        [
            "prepare",
            "--output",
            str(output),
            "--expected-head",
            _HEAD,
        ]
    )

    assert result == 2
    assert sentinel.read_bytes() == b"foreign-winner"
    assert "already exists" in capsys.readouterr().err


def test_artifact_writer_uses_exclusive_create_and_preserves_foreign_bytes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    target = output / "report.json"
    target.write_bytes(b"foreign-winner")
    owned: list[cli._OwnedArtifact] = []

    with pytest.raises(FileExistsError):
        cli._write_text_and_sidecar(output, "report.json", "{}\n", owned)

    assert owned == []
    assert target.read_bytes() == b"foreign-winner"


def test_cleanup_removes_only_owned_artifacts(
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    owned = output / "owned.json"
    foreign = output / "foreign.json"
    owned_records: list[cli._OwnedArtifact] = []
    cli._write_text_and_sidecar(output, "owned.json", "owned\n", owned_records)
    foreign.write_bytes(b"foreign")

    cli._remove_owned_output(output, owned_records)

    assert not owned.exists()
    assert not owned.with_suffix(".json.sha256").exists()
    assert foreign.read_bytes() == b"foreign"
    assert output.is_dir()


def test_cleanup_preserves_a_concurrent_same_path_replacement(tmp_path: Path) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    target = output / "report.json"
    owned_records: list[cli._OwnedArtifact] = []
    cli._write_text_and_sidecar(output, target.name, "owned\n", owned_records)

    # The production path keeps an identity handle open, which prevents this
    # replacement on Windows and prevents inode reuse on POSIX.  Closing this
    # one handle forces the adverse fallback case deterministically on both.
    next(record for record in owned_records if record.path == target).close()
    target.unlink()
    target.write_bytes(b"foreign-winner")
    cli._remove_owned_output(output, owned_records)

    assert target.read_bytes() == b"foreign-winner"
    assert not target.with_suffix(".json.sha256").exists()
    assert output.is_dir()


def test_cleanup_removes_an_owned_incomplete_write(tmp_path: Path) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    target = output / "partial.json"
    handle = target.open("xb")
    owned = cli._OwnedArtifact.from_open_file(target, handle, b"complete-payload")
    handle.write(b"partial")
    handle.flush()

    cli._remove_owned_output(output, [owned])

    assert not output.exists()


def test_prepare_rechecks_exact_head_after_writing_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def verify(_expected: str) -> tuple[str, Path]:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise InventoryPositiveV3IndependentValidationError(
                "worktree changed while materializing evidence"
            )
        return _HEAD, _ROOT

    monkeypatch.setattr(cli, "_verify_clean_head", verify)
    output = tmp_path / "must-roll-back"

    result = cli.main(
        [
            "prepare",
            "--output",
            str(output),
            "--expected-head",
            _HEAD,
        ]
    )

    assert result == 2
    assert calls == 3
    assert not output.exists()
    assert "worktree changed" in capsys.readouterr().err


def test_cli_rejects_candidate_configuration_override_flags() -> None:
    with pytest.raises(SystemExit):
        cli.main(
            [
                "prepare",
                "--output",
                "unused",
                "--expected-head",
                _HEAD,
                "--publication-floor",
                "0.1",
            ]
        )


def test_clean_head_verification_rejects_changed_frozen_transitive_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = iter(frozen_v3_model_binding().source_git_blobs)
    first_path, _ = next(bindings)

    def fake_git(
        *arguments: str,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del allow_failure
        if arguments == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(arguments, 0, _HEAD + "\n", "")
        if arguments == ("status", "--porcelain=v1"):
            return subprocess.CompletedProcess(arguments, 0, "", "")
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            return subprocess.CompletedProcess(arguments, 0, "", "")
        if arguments == ("rev-parse", f"HEAD:{first_path}"):
            return subprocess.CompletedProcess(arguments, 0, "f" * 40 + "\n", "")
        raise AssertionError(arguments)

    monkeypatch.setattr(cli, "_git", fake_git)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="transitive source changed",
    ):
        cli._verify_clean_head(_HEAD)


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        (("b" * 40,), "Git HEAD mismatch"),
        ((_HEAD, " M src/example.py"), "worktree changes"),
    ],
)
def test_cli_git_provenance_failure_writes_no_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    responses: tuple[str, ...],
    message: str,
) -> None:
    values: Iterator[str] = iter(responses)

    def fake_git(
        *arguments: str,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del allow_failure
        return subprocess.CompletedProcess(arguments, 0, next(values), "")

    monkeypatch.setattr(cli, "_git", fake_git)
    output = tmp_path / "must-not-exist"

    result = cli.main(
        [
            "prepare",
            "--output",
            str(output),
            "--expected-head",
            _HEAD,
        ]
    )

    assert result == 2
    assert message in capsys.readouterr().err
    assert not output.exists()
