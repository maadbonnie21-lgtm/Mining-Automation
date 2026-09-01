from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from validation.inventory_v3_protocol_v2 import launcher
from validation.inventory_v3_protocol_v2 import protocol as v2

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_LOCKED_OUTPUT_IGNORES = frozenset(
    {
        "diagnostics/iv3v2/.gitignore",
        "diagnostics/iv3v2r/.gitignore",
    }
)
_LOCKED_OUTPUT_IGNORE_BYTES = b"*\n!.gitignore\n"
_DEVELOPMENT_MANIFEST_PATHS = frozenset(
    {
        (
            "tests/fixtures/perception/"
            "inventory-live-candidate-safety-bb0d0e3f7ff1c73b/manifest.json"
        ),
        (
            "tests/fixtures/perception/"
            "inventory-live-candidate-safety-bb0d0e3f7ff1c73b/manifest.json.sha256"
        ),
    }
)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git(
    repository_root: Path,
    *arguments: str,
    committed_at: str | None = None,
) -> str:
    environment = os.environ.copy()
    if committed_at is not None:
        environment["GIT_AUTHOR_DATE"] = committed_at
        environment["GIT_COMMITTER_DATE"] = committed_at
    completed = subprocess.run(
        ("git", "-C", str(repository_root), *arguments),
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _write(repository_root: Path, relative: str, payload: bytes) -> Path:
    path = repository_root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _expected_p2_paths() -> frozenset[str]:
    protocol_paths = getattr(v2, "_P2_CHANGED_PATHS", None)
    launcher_paths = getattr(launcher, "_P2_CHANGED_PATHS", None)
    if protocol_paths is not None:
        if launcher_paths is not None:
            assert frozenset(protocol_paths) == frozenset(launcher_paths)
        return frozenset(protocol_paths)
    return frozenset(
        {
            *v2._V2_SOURCE_PATHS,
            v2._V2_LIVE_AUTHORIZATION_PATH.as_posix(),
            v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix(),
        }
    )


def _empty_legacy_authorization() -> bytes:
    return _canonical_bytes(
        {
            "activation_allowed": False,
            "authorizations": [],
            "schema": v2.LIVE_AUTHORIZATION_SCHEMA,
        }
    )


def _empty_v2_authorization() -> bytes:
    return _canonical_bytes(
        {
            "activation_allowed": False,
            "authorizations": [],
            "schema": v2._V2_LIVE_AUTHORIZATION_SCHEMA,
        }
    )


def _source_payload(relative: str, preregistration_sidecar: bytes) -> bytes:
    if relative in _LOCKED_OUTPUT_IGNORES:
        return _LOCKED_OUTPUT_IGNORE_BYTES
    if relative == v2._V2_PREREGISTRATION_PATH.as_posix():
        return _REPOSITORY_ROOT.joinpath(*v2._V2_PREREGISTRATION_PATH.parts).read_bytes()
    if relative == v2._V2_PREREGISTRATION_SIDECAR_PATH.as_posix():
        return preregistration_sidecar
    if relative == v2._V2_LIVE_AUTHORIZATION_PATH.as_posix():
        return _empty_v2_authorization()
    if relative == v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix():
        payload = _empty_v2_authorization()
        return f"{_sha256(payload)}  {v2._V2_LIVE_AUTHORIZATION_PATH.name}\n".encode("ascii")
    source = _REPOSITORY_ROOT.joinpath(*relative.split("/"))
    if source.is_file():
        return source.read_bytes()
    return f"synthetic P2 fixture for {relative}\n".encode()


def _create_p2_repository(
    tmp_path: Path,
    *,
    preregistration_sidecar: bytes,
    extra_p2_path: str | None = None,
) -> tuple[Path, str, str]:
    root = tmp_path / "repository"
    root.mkdir()
    for arguments in (
        ("init", "--quiet"),
        ("config", "core.autocrlf", "false"),
        ("config", "user.name", "Protocol V2 Test"),
        ("config", "user.email", "protocol-v2-test@example.invalid"),
    ):
        _git(root, *arguments)

    _write(
        root,
        v2._LIVE_AUTHORIZATION_PATH.as_posix(),
        _empty_legacy_authorization(),
    )
    _write(root, "src/mining_automation/__init__.py", b"")
    for relative in _DEVELOPMENT_MANIFEST_PATHS:
        _write(root, relative, _REPOSITORY_ROOT.joinpath(*relative.split("/")).read_bytes())
    _git(root, "add", "--all")
    _git(
        root,
        "commit",
        "--quiet",
        "-m",
        "synthetic predecessor lock",
        committed_at="2025-01-01T00:00:00+00:00",
    )
    predecessor = _git(root, "rev-parse", "HEAD")

    for relative in sorted(_expected_p2_paths()):
        _write(root, relative, _source_payload(relative, preregistration_sidecar))
    fixture_launcher = root.joinpath(*launcher._EXPECTED_LAUNCHER.parts)
    launcher_payload = fixture_launcher.read_bytes()
    frozen_parent = launcher._PROTOCOL_V1_LOCK_HEAD.encode("ascii")
    assert launcher_payload.count(frozen_parent) == 1
    fixture_launcher.write_bytes(
        launcher_payload.replace(frozen_parent, predecessor.encode("ascii"))
    )
    if extra_p2_path is not None:
        _write(
            root,
            extra_p2_path,
            b"raise AssertionError('unverified import shadow executed')\n",
        )
    _git(root, "add", "--all")
    _git(
        root,
        "commit",
        "--quiet",
        "-m",
        "synthetic P2 source",
        committed_at="2025-01-01T00:00:01+00:00",
    )
    source_head = _git(root, "rev-parse", "HEAD")
    return root, predecessor, source_head


def _lock_document(
    root: Path,
    *,
    predecessor: str,
    source_head: str,
    omitted_locked_path: str | None = None,
    rebound_locked_path: str | None = None,
) -> dict[str, object]:
    legacy_payload = _empty_legacy_authorization()
    v2_authorization_payload = _empty_v2_authorization()
    locked_paths = {
        *v2._V2_SOURCE_PATHS,
        *_DEVELOPMENT_MANIFEST_PATHS,
    }
    source_bindings = [
        {
            "git_blob": (
                "0" * 40
                if relative == rebound_locked_path
                else _git(root, "rev-parse", f"{source_head}:{relative}")
            ),
            "path": relative,
        }
        for relative in sorted(locked_paths)
        if relative != omitted_locked_path
    ]
    return {
        "activation_allowed": False,
        "approved_passive_capture": {
            "build_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
            "capture_configuration_id": v2.CAPTURE_CONFIGURATION_ID,
            "legacy_live_authorization_initial_git_blob": _git(
                root,
                "rev-parse",
                f"{predecessor}:{v2._LIVE_AUTHORIZATION_PATH.as_posix()}",
            ),
            "legacy_live_authorization_initial_sha256": _sha256(legacy_payload),
            "live_authorization_path": v2._LIVE_AUTHORIZATION_PATH.as_posix(),
            "protocol_v2_live_authorization_initial_git_blob": _git(
                root,
                "rev-parse",
                f"{source_head}:{v2._V2_LIVE_AUTHORIZATION_PATH.as_posix()}",
            ),
            "protocol_v2_live_authorization_initial_sha256": _sha256(v2_authorization_payload),
            "protocol_v2_live_authorization_path": (v2._V2_LIVE_AUTHORIZATION_PATH.as_posix()),
            "protocol_v2_live_authorization_sidecar_initial_git_blob": _git(
                root,
                "rev-parse",
                f"{source_head}:{v2._V2_LIVE_AUTHORIZATION_SIDECAR_PATH.as_posix()}",
            ),
            "reservation_scope": "windows-user-local-not-host-global",
        },
        "frozen_candidate_head_sha": v2.FROZEN_V3_HEAD,
        "live_validation_authorized": False,
        "predecessor": {
            "protocol_lock_git_commit_sha": predecessor,
            "protocol_lock_sha256": v2.PROTOCOL_V1_LOCK_SHA256,
            "protocol_source_git_commit_sha": v2.PROTOCOL_V1_SOURCE_HEAD,
        },
        "preregistration_sha256": v2.PROTOCOL_V2_PREREGISTRATION_SHA256,
        "protocol": {
            "id": v2.PROTOCOL_V2_ID,
            "locked_git_blobs": source_bindings,
            "source_commit_sha": source_head,
            "version": v2.PROTOCOL_V2_VERSION,
        },
        "schema": v2.PROTOCOL_V2_LOCK_SCHEMA,
    }


def _commit_l2(
    root: Path,
    *,
    predecessor: str,
    source_head: str,
    omitted_locked_path: str | None = None,
    rebound_locked_path: str | None = None,
) -> str:
    lock_payload = _canonical_bytes(
        _lock_document(
            root,
            predecessor=predecessor,
            source_head=source_head,
            omitted_locked_path=omitted_locked_path,
            rebound_locked_path=rebound_locked_path,
        )
    )
    _write(root, v2._V2_LOCK_PATH.as_posix(), lock_payload)
    _write(
        root,
        v2._V2_LOCK_SIDECAR_PATH.as_posix(),
        f"{_sha256(lock_payload)}  {v2._V2_LOCK_PATH.name}\n".encode("ascii"),
    )
    _git(
        root,
        "add",
        "--",
        v2._V2_LOCK_PATH.as_posix(),
        v2._V2_LOCK_SIDECAR_PATH.as_posix(),
    )
    _git(
        root,
        "commit",
        "--quiet",
        "-m",
        "synthetic L2 lock",
        committed_at="2025-01-01T00:00:02+00:00",
    )
    return _git(root, "rev-parse", "HEAD")


def _patch_synthetic_predecessor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    root: Path,
    predecessor: str,
) -> None:
    monkeypatch.setattr(v2, "PROTOCOL_V1_LOCK_HEAD", predecessor)
    monkeypatch.setattr(v2, "_frozen_v1_locked_blob_map", lambda _root: {})
    monkeypatch.setattr(
        v2,
        "_verify_base_repository_state",
        lambda _root, expected_head, *, source_mode: (
            root.resolve(strict=True),
            expected_head,
        ),
    )


def _correct_preregistration_sidecar() -> bytes:
    payload = _REPOSITORY_ROOT.joinpath(*v2._V2_PREREGISTRATION_PATH.parts).read_bytes()
    return f"{_sha256(payload)}  {v2._V2_PREREGISTRATION_PATH.name}\n".encode("ascii")


@pytest.mark.parametrize(
    "bad_sidecar",
    (
        b"0" * 64 + b"  preregistration.json\n",
        (v2.PROTOCOL_V2_PREREGISTRATION_SHA256 + "  stale-preregistration.json\n").encode("ascii"),
    ),
    ids=("mismatched-digest", "stale-filename"),
)
@pytest.mark.parametrize("phase", ("build", "verify"))
def test_build_and_verify_require_exact_preregistration_sidecar_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    bad_sidecar: bytes,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=bad_sidecar,
    )
    _patch_synthetic_predecessor(
        monkeypatch,
        root=root,
        predecessor=predecessor,
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="V2 preregistration.*sidecar differs",
    ):
        if phase == "build":
            v2.build_protocol_v2_lock(root, expected_source_head=source_head)
        else:
            lock_head = _commit_l2(
                root,
                predecessor=predecessor,
                source_head=source_head,
            )
            v2.verify_protocol_v2_repository(root, expected_head=lock_head)


def test_protocol_and_launcher_share_exact_p2_changed_path_allowlist() -> None:
    protocol_paths = frozenset(v2._P2_CHANGED_PATHS)
    launcher_paths = frozenset(launcher._P2_CHANGED_PATHS)

    assert protocol_paths == launcher_paths
    assert "tests/test_inventory_v3_protocol_v2_lock_shadows.py" in protocol_paths
    assert v2._V2_LOCK_PATH.as_posix() not in protocol_paths
    assert v2._V2_LOCK_SIDECAR_PATH.as_posix() not in protocol_paths


def test_protocol_lock_build_binds_exact_development_manifest_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
    )
    _patch_synthetic_predecessor(
        monkeypatch,
        root=root,
        predecessor=predecessor,
    )

    lock = v2.build_protocol_v2_lock(root, expected_source_head=source_head)

    protocol = lock.get("protocol")
    assert isinstance(protocol, dict)
    raw_bindings = protocol.get("locked_git_blobs")
    assert isinstance(raw_bindings, list)
    locked_paths = {
        item["path"]
        for item in raw_bindings
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    assert _DEVELOPMENT_MANIFEST_PATHS.issubset(locked_paths)
    manifest_relative = next(
        path for path in _DEVELOPMENT_MANIFEST_PATHS if path.endswith("/manifest.json")
    )
    sidecar_relative = f"{manifest_relative}.sha256"
    manifest_payload = root.joinpath(*manifest_relative.split("/")).read_bytes()
    sidecar_payload = root.joinpath(*sidecar_relative.split("/")).read_bytes()
    assert sidecar_payload == _REPOSITORY_ROOT.joinpath(*sidecar_relative.split("/")).read_bytes()
    assert sidecar_payload.rstrip(b"\r\n") == (
        f"{_sha256(manifest_payload)}  manifest.json".encode("ascii")
    )


@pytest.mark.parametrize("omitted_path", tuple(sorted(_DEVELOPMENT_MANIFEST_PATHS)))
def test_protocol_and_launcher_reject_lock_omitting_development_manifest_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    omitted_path: str,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
    )
    lock_head = _commit_l2(
        root,
        predecessor=predecessor,
        source_head=source_head,
        omitted_locked_path=omitted_path,
    )
    _patch_synthetic_predecessor(
        monkeypatch,
        root=root,
        predecessor=predecessor,
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2.verify_protocol_v2_repository(root, expected_head=lock_head)

    monkeypatch.setattr(launcher, "_PROTOCOL_V1_LOCK_HEAD", predecessor)
    launcher_path = root.joinpath(*launcher._EXPECTED_LAUNCHER.parts)
    with pytest.raises(launcher._BootstrapError):
        launcher._verify_preimport(launcher_path)


@pytest.mark.parametrize("tamper_phase", ("source-binding", "current-head"))
def test_development_manifest_source_and_current_blobs_are_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper_phase: str,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
    )
    manifest_relative = next(
        path for path in _DEVELOPMENT_MANIFEST_PATHS if path.endswith("/manifest.json")
    )
    lock_head = _commit_l2(
        root,
        predecessor=predecessor,
        source_head=source_head,
        rebound_locked_path=(manifest_relative if tamper_phase == "source-binding" else None),
    )
    expected_head = lock_head
    if tamper_phase == "current-head":
        replacement = b'{"post_lock_tamper":true}\n'
        _write(root, manifest_relative, replacement)
        _write(
            root,
            f"{manifest_relative}.sha256",
            f"{_sha256(replacement)}  manifest.json\n".encode("ascii"),
        )
        _git(root, "add", "--all")
        _git(
            root,
            "commit",
            "--quiet",
            "-m",
            "tamper locked development metadata",
            committed_at="2025-01-01T00:00:03+00:00",
        )
        expected_head = _git(root, "rev-parse", "HEAD")
    _patch_synthetic_predecessor(
        monkeypatch,
        root=root,
        predecessor=predecessor,
    )

    with pytest.raises(v2.InventoryV3ProtocolV2Error):
        v2.verify_protocol_v2_repository(root, expected_head=expected_head)

    monkeypatch.setattr(launcher, "_PROTOCOL_V1_LOCK_HEAD", predecessor)
    launcher_path = root.joinpath(*launcher._EXPECTED_LAUNCHER.parts)
    with pytest.raises(launcher._BootstrapError):
        launcher._verify_preimport(launcher_path)


def test_locked_output_ignores_keep_each_workspace_stage_git_clean(
    tmp_path: Path,
) -> None:
    assert _LOCKED_OUTPUT_IGNORES.issubset(v2._P2_CHANGED_PATHS)
    assert _LOCKED_OUTPUT_IGNORES.issubset(launcher._P2_CHANGED_PATHS)
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
    )
    for relative in _LOCKED_OUTPUT_IGNORES:
        path = root.joinpath(*relative.split("/"))
        assert path.read_bytes() == _LOCKED_OUTPUT_IGNORE_BYTES
        assert (
            subprocess.run(
                ("git", "-C", str(root), "show", f"{source_head}:{relative}"),
                check=True,
                capture_output=True,
            ).stdout
            == _LOCKED_OUTPUT_IGNORE_BYTES
        )
    _commit_l2(root, predecessor=predecessor, source_head=source_head)
    assert _git(root, "status", "--porcelain=v1") == ""

    authorization_id = "a" * 64
    stage_outputs = (
        f"diagnostics/iv3v2/{authorization_id}/a/campaign-manifest.json",
        f"diagnostics/iv3v2/{authorization_id}/ri/reviewer-template.json",
        f"diagnostics/iv3v2/{authorization_id}/rp/validation-package.json",
        f"diagnostics/iv3v2/{authorization_id}/ar/protocol-v2-approval-request.json",
        f"diagnostics/iv3v2r/{authorization_id}/protocol-v2-result.json",
        f"diagnostics/iv3v2r/{authorization_id}/frozen-evaluator-report.json",
    )
    for index, relative in enumerate(stage_outputs, start=1):
        _write(root, relative, f"retained stage {index}\n".encode())
        assert _git(root, "status", "--porcelain=v1") == ""


@pytest.mark.parametrize("phase", ("build", "verify"))
def test_build_and_verify_reject_extra_committed_p2_import_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
        extra_p2_path="validation.py",
    )
    _patch_synthetic_predecessor(
        monkeypatch,
        root=root,
        predecessor=predecessor,
    )

    with pytest.raises(
        v2.InventoryV3ProtocolV2Error,
        match="fixed Protocol V2 source/test allowlist",
    ):
        if phase == "build":
            v2.build_protocol_v2_lock(root, expected_source_head=source_head)
        else:
            lock_head = _commit_l2(
                root,
                predecessor=predecessor,
                source_head=source_head,
            )
            v2.verify_protocol_v2_repository(root, expected_head=lock_head)


def test_launcher_preimport_rejects_committed_p2_import_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
        extra_p2_path="validation.py",
    )
    _commit_l2(root, predecessor=predecessor, source_head=source_head)
    monkeypatch.setattr(launcher, "_PROTOCOL_V1_LOCK_HEAD", predecessor)
    launcher_path = root.joinpath(*launcher._EXPECTED_LAUNCHER.parts)

    with pytest.raises(launcher._BootstrapError, match="import shadow"):
        launcher._verify_preimport(launcher_path)


def test_launcher_preimport_rejects_every_ignored_import_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
    )
    _commit_l2(root, predecessor=predecessor, source_head=source_head)
    monkeypatch.setattr(launcher, "_PROTOCOL_V1_LOCK_HEAD", predecessor)
    launcher_path = root.joinpath(*launcher._EXPECTED_LAUNCHER.parts)
    assert launcher._verify_preimport(launcher_path) == (
        root.resolve(strict=True),
        _git(root, "rev-parse", "HEAD"),
    )

    shadow_paths = (
        "validation.py",
        "validation.pyc",
        "validation.cp312-win_amd64.pyd",
        "validation/__init__.py",
        "validation/__init__.pyc",
        "validation/inventory_v3_protocol_v2.py",
        "validation/inventory_v3_protocol_v2.pyc",
        "validation/inventory_v3_protocol_v2.cp312-win_amd64.pyd",
        "mining_automation.py",
        "mining_automation.pyc",
    )
    exclude = root / ".git" / "info" / "exclude"
    exclude.write_text(
        "\n".join(f"/{relative}" for relative in shadow_paths) + "\n",
        encoding="utf-8",
    )

    for relative in shadow_paths:
        shadow = _write(
            root,
            relative,
            b"raise AssertionError('unverified import shadow executed')\n",
        )
        assert _git(root, "status", "--porcelain=v1") == ""
        with pytest.raises(launcher._BootstrapError, match="import shadow"):
            launcher._verify_preimport(launcher_path)
        shadow.unlink()


def test_launcher_preimport_rejects_ignored_v2_package_competitors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
    )
    _commit_l2(root, predecessor=predecessor, source_head=source_head)
    monkeypatch.setattr(launcher, "_PROTOCOL_V1_LOCK_HEAD", predecessor)
    launcher_path = root.joinpath(*launcher._EXPECTED_LAUNCHER.parts)
    assert launcher._verify_preimport(launcher_path) == (
        root.resolve(strict=True),
        _git(root, "rev-parse", "HEAD"),
    )

    competitors = (
        ("validation/inventory_v3_protocol_v2/protocol.pyd", False),
        ("validation/inventory_v3_protocol_v2/protocol.pyc", False),
        ("validation/inventory_v3_protocol_v2/__init__.pyd", False),
        ("validation/inventory_v3_protocol_v2/protocol", True),
    )
    exclude = root / ".git" / "info" / "exclude"
    exclude.write_text(
        "\n".join(f"/{relative}" for relative, _is_directory in competitors) + "\n",
        encoding="utf-8",
    )

    for relative, is_directory in competitors:
        competitor = root.joinpath(*relative.split("/"))
        if is_directory:
            competitor.mkdir()
        else:
            competitor.write_bytes(b"unverified V2 package competitor\n")
        assert _git(root, "status", "--porcelain=v1") == ""
        with pytest.raises(
            launcher._BootstrapError,
            match="V2 package import tree contains .* competitor",
        ):
            launcher._verify_preimport(launcher_path)
        if is_directory:
            competitor.rmdir()
        else:
            competitor.unlink()


def test_isolated_launcher_keeps_ignored_root_argparse_behind_stdlib(
    tmp_path: Path,
) -> None:
    root, predecessor, source_head = _create_p2_repository(
        tmp_path,
        preregistration_sidecar=_correct_preregistration_sidecar(),
    )
    _commit_l2(root, predecessor=predecessor, source_head=source_head)
    launcher_path = root.joinpath(*launcher._EXPECTED_LAUNCHER.parts)
    marker = tmp_path / "argparse-shadow-executed"
    malicious = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "raise AssertionError('root argparse shadow executed')\n"
    ).encode()
    _write(root, "argparse.py", malicious)
    exclude = root / ".git" / "info" / "exclude"
    exclude.write_text("/argparse.py\n", encoding="utf-8")
    assert _git(root, "status", "--porcelain=v1") == ""

    completed = subprocess.run(
        (sys.executable, "-I", "-S", str(launcher_path), "--help"),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout
    assert not marker.exists()


def test_launcher_shadow_scan_precedes_unverified_cli_import() -> None:
    source = Path(launcher.__file__).read_text(encoding="utf-8")

    verify_call = "_reject_import_shadows(repository_root)"
    cli_import = "from validation.inventory_v3_protocol_v2.cli import main as protocol_main"
    assert verify_call in source
    assert source.index(verify_call) < source.index(cli_import)
