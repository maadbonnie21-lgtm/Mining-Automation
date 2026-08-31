from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Barrier, Event
from typing import BinaryIO, cast

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


class _StallingWriter:
    """Delegate one partial write, then report zero progress without raising."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self._write_calls = 0

    def write(self, payload: bytes) -> int:
        self._write_calls += 1
        if self._write_calls > 1:
            return 0
        return self._handle.write(payload[: max(1, len(payload) // 2)])

    def fileno(self) -> int:
        return self._handle.fileno()

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    @property
    def closed(self) -> bool:
        return self._handle.closed


class _CloseErrorAfterRelease:
    """Close the real handle, then report one synthetic close failure."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self._handle.close()
        if self.close_calls == 1:
            raise OSError("synthetic close failure")

    @property
    def closed(self) -> bool:
        return self._handle.closed


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
    owned_output = cli._OwnedOutputDirectory.from_path(output)
    owned = output / "owned.json"
    foreign = output / "foreign.json"
    owned_records: list[cli._OwnedArtifact] = []
    cli._write_text_and_sidecar(output, "owned.json", "owned\n", owned_records)
    foreign.write_bytes(b"foreign")

    cli._remove_owned_output(owned_output, owned_records)

    assert not owned.exists()
    assert not owned.with_suffix(".json.sha256").exists()
    assert foreign.read_bytes() == b"foreign"
    assert output.is_dir()


def test_cleanup_preserves_a_concurrent_same_path_replacement(tmp_path: Path) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    owned_output = cli._OwnedOutputDirectory.from_path(output)
    target = output / "report.json"
    owned_records: list[cli._OwnedArtifact] = []
    cli._write_text_and_sidecar(output, target.name, "owned\n", owned_records)

    # The production path keeps an identity handle open, which prevents this
    # replacement on Windows and prevents inode reuse on POSIX.  Closing this
    # one handle forces the adverse fallback case deterministically on both.
    next(record for record in owned_records if record.path == target).close()
    target.unlink()
    target.write_bytes(b"foreign-winner")
    cli._remove_owned_output(owned_output, owned_records)

    assert target.read_bytes() == b"foreign-winner"
    assert not target.with_suffix(".json.sha256").exists()
    assert output.is_dir()


def test_cleanup_removes_an_owned_incomplete_write(tmp_path: Path) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    owned_output = cli._OwnedOutputDirectory.from_path(output)
    target = output / "partial.json"
    handle = target.open("xb")
    owned = cli._OwnedArtifact.from_open_file(target, handle, b"complete-payload")
    handle.write(b"partial")
    handle.flush()

    cli._remove_owned_output(owned_output, [owned])

    assert not output.exists()


def test_cleanup_preserves_a_replaced_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "owned-output"
    owned_output = cli._create_output_directory(output)
    output.rmdir()
    output.mkdir()
    foreign = output / "foreign-winner.txt"
    foreign.write_bytes(b"foreign-winner")

    cli._remove_owned_output(owned_output, [])

    assert foreign.read_bytes() == b"foreign-winner"
    assert output.is_dir()


@pytest.mark.parametrize("stall_sidecar", [False, True])
def test_short_write_never_marks_complete_and_rolls_back_owned_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stall_sidecar: bool,
) -> None:
    output = tmp_path / "owned-output"
    owned_output = cli._create_output_directory(output)
    owned_records: list[cli._OwnedArtifact] = []
    real_open = cli._open_exclusive

    def open_with_stall(path: Path) -> BinaryIO:
        handle = real_open(path)
        is_sidecar = path.name.endswith(".sha256")
        if is_sidecar == stall_sidecar:
            return cast(BinaryIO, _StallingWriter(handle))
        return handle

    monkeypatch.setattr(cli, "_open_exclusive", open_with_stall)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="short write",
    ):
        cli._write_text_and_sidecar(output, "report.json", "payload\n", owned_records)
    assert any(not record.complete for record in owned_records)

    cli._remove_owned_output(owned_output, owned_records)

    assert all(record.handle.closed for record in owned_records)
    assert not output.exists()


def test_successful_materialization_releases_every_identity_handle(tmp_path: Path) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    owned_records: list[cli._OwnedArtifact] = []

    cli._write_text_and_sidecar(output, "report.json", "payload\n", owned_records)
    assert all(not record.handle.closed for record in owned_records)

    cli._release_owned_output(owned_records)

    assert all(record.handle.closed for record in owned_records)


def test_pre_registration_failure_closes_the_exclusive_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    opened: list[BinaryIO] = []
    real_open = cli._open_exclusive

    def tracked_open(path: Path) -> BinaryIO:
        handle = real_open(path)
        opened.append(handle)
        return handle

    def reject_registration(
        _cls: type[cli._OwnedArtifact],
        _path: Path,
        _handle: BinaryIO,
        _payload: bytes,
    ) -> cli._OwnedArtifact:
        raise OSError("synthetic fstat failure")

    monkeypatch.setattr(cli, "_open_exclusive", tracked_open)
    monkeypatch.setattr(
        cli._OwnedArtifact,
        "from_open_file",
        classmethod(reject_registration),
    )

    with pytest.raises(OSError, match="synthetic fstat failure"):
        cli._write_text_and_sidecar(output, "report.json", "payload\n", [])

    assert len(opened) == 1
    assert opened[0].closed
    assert (output / "report.json").is_file()


def test_release_failure_is_reported_after_every_handle_is_attempted(tmp_path: Path) -> None:
    output = tmp_path / "owned-output"
    output.mkdir()
    owned_records: list[cli._OwnedArtifact] = []
    cli._write_text_and_sidecar(output, "report.json", "payload\n", owned_records)
    proxy = _CloseErrorAfterRelease(owned_records[-1].handle)
    owned_records[-1].handle = cast(BinaryIO, proxy)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="artifact handle release failed",
    ):
        cli._release_owned_output(owned_records)

    assert proxy.close_calls >= 2
    assert all(record.handle.closed for record in owned_records)


def test_two_public_cli_invocations_share_one_output_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_verify_clean_head", lambda expected: (_HEAD, _ROOT))
    output = tmp_path / "one-winner"
    start = Barrier(2)
    winner_reserved = Event()
    release_winner = Event()
    real_create = cli._create_output_directory

    def create_and_hold(path: Path) -> cli._OwnedOutputDirectory:
        owned_output = real_create(path)
        winner_reserved.set()
        if not release_winner.wait(timeout=10):
            raise RuntimeError("test did not release the output reservation")
        return owned_output

    monkeypatch.setattr(cli, "_create_output_directory", create_and_hold)

    def invoke() -> int:
        start.wait()
        return cli.main(
            [
                "prepare",
                "--output",
                str(output),
                "--expected-head",
                _HEAD,
            ]
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(invoke) for _index in range(2))
        assert winner_reserved.wait(timeout=10)
        finished, _pending = wait(
            futures,
            timeout=10,
            return_when=FIRST_COMPLETED,
        )
        try:
            assert len(finished) == 1
            assert next(iter(finished)).result() == 2
        finally:
            release_winner.set()
        results = tuple(future.result(timeout=20) for future in futures)

    assert sorted(results) == [0, 2]
    report = output / "inventory-positive-v3-validation-readiness-report.json"
    assert report.is_file()
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    assert report.with_suffix(".json.sha256").read_text(encoding="ascii") == (
        f"{digest}  {report.name}\n"
    )


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
