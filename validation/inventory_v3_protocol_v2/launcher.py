"""Isolated pre-import launcher for Inventory V3 Protocol V2 commands."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

_EXPECTED_LAUNCHER = PurePosixPath("validation/inventory_v3_protocol_v2/launcher.py")
_LOCK_PATH = PurePosixPath("validation/inventory_v3_protocol_v2/protocol-lock.json")
_LOCK_SCHEMA = "inventory-positive-v3-independent-validation-protocol-lock-v2"
_PROTOCOL_V1_LOCK_HEAD = "32764bfd82afb46d4e99292bab7d162be536e2d7"
_PREREGISTRATION_PATH = PurePosixPath("validation/inventory_v3_protocol_v2/preregistration.json")
_PREREGISTRATION_SHA256 = "debecab3c90b71dbb7746c0fbe40abdb2212651ed495358a4c10ce712971d509"
_REQUIRED_V2_SOURCE_PATHS = frozenset(
    {
        "diagnostics/iv3v2/.gitignore",
        "diagnostics/iv3v2r/.gitignore",
        "docs/INVENTORY_VALIDATION_PROTOCOL_V2.md",
        "validation/inventory_v3_protocol_v2/__init__.py",
        "validation/inventory_v3_protocol_v2/cli.py",
        "validation/inventory_v3_protocol_v2/launcher.py",
        "validation/inventory_v3_protocol_v2/package_tree.py",
        "validation/inventory_v3_protocol_v2/preregistration.json",
        "validation/inventory_v3_protocol_v2/preregistration.json.sha256",
        "validation/inventory_v3_protocol_v2/privacy.py",
        "validation/inventory_v3_protocol_v2/producer.py",
        "validation/inventory_v3_protocol_v2/protocol.py",
    }
)
_REQUIRED_DEVELOPMENT_METADATA_PATHS = frozenset(
    {
        "tests/fixtures/perception/inventory-live-candidate-safety-bb0d0e3f7ff1c73b/manifest.json",
        "tests/fixtures/perception/inventory-live-candidate-safety-bb0d0e3f7ff1c73b/"
        "manifest.json.sha256",
    }
)
_P2_CHANGED_PATHS = frozenset(
    {
        *_REQUIRED_V2_SOURCE_PATHS,
        "tests/test_inventory_v3_protocol_v2_bridge.py",
        "tests/test_inventory_v3_protocol_v2_cli.py",
        "tests/test_inventory_v3_protocol_v2_lock_shadows.py",
        "tests/test_inventory_v3_protocol_v2_package_tree.py",
        "tests/test_inventory_v3_protocol_v2_privacy.py",
        "tests/test_inventory_v3_protocol_v2_producer.py",
        "tests/test_inventory_v3_protocol_v2_protocol.py",
        "tests/test_inventory_v3_protocol_v2_transactions.py",
        "validation/inventory_v3_protocol_v2/live-campaign-authorizations.json",
        "validation/inventory_v3_protocol_v2/live-campaign-authorizations.json.sha256",
    }
)
_NATIVE_SUFFIXES = frozenset({".dll", ".pyd", ".so"})
_SOURCELESS_SUFFIXES = frozenset({".pyc", ".pyo"})
_ALLOWED_SOURCE_ROOT_NAMES = frozenset({"__pycache__", "mining_automation"})
_SHADOW_SUFFIXES = (".py", ".pyc", ".pyo", ".pyd", ".dll", ".so")


class _BootstrapError(RuntimeError):
    """The source-owned V2 pre-import boundary rejected execution."""


def _git(root: Path, *arguments: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode != 0:
        output = completed.stderr or completed.stdout
        detail = (
            output.decode("utf-8", errors="replace").strip()
            if isinstance(output, bytes)
            else output.strip()
        )
        raise _BootstrapError(f"Git pre-import verification failed: {detail}")
    stdout = completed.stdout
    if binary:
        if not isinstance(stdout, bytes):  # pragma: no cover - subprocess contract
            raise _BootstrapError("Git returned text for a binary request")
        return stdout
    if not isinstance(stdout, str):  # pragma: no cover - subprocess contract
        raise _BootstrapError("Git returned bytes for a text request")
    return stdout


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def _plain_file(root: Path, relative: str) -> Path:
    path = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise _BootstrapError(f"source path is symlinked: {relative}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _BootstrapError(f"source path is unavailable: {relative}") from exc
    if resolved != path.absolute() or not resolved.is_file():
        raise _BootstrapError(f"source path is redirected: {relative}")
    return resolved


def _reject_import_shadows(repository_root: Path) -> None:
    """Reject physical namespace/module competitors before any local import."""

    candidates = [repository_root / f"validation{suffix}" for suffix in _SHADOW_SUFFIXES]
    candidates.extend(
        repository_root / "validation" / f"__init__{suffix}" for suffix in _SHADOW_SUFFIXES
    )
    candidates.extend(
        repository_root / "validation" / f"inventory_v3_protocol_v2{suffix}"
        for suffix in _SHADOW_SUFFIXES
    )
    candidates.extend(repository_root / f"mining_automation{suffix}" for suffix in _SHADOW_SUFFIXES)
    candidates.append(repository_root / "mining_automation")
    for candidate in candidates:
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _BootstrapError("cannot inspect import-shadow candidates") from exc
        raise _BootstrapError(f"unverified import shadow is forbidden: {candidate.name}")
    for directory, module_names in (
        (repository_root, ("mining_automation", "validation")),
        (
            repository_root / "validation",
            ("__init__", "inventory_v3_protocol_v2"),
        ),
    ):
        try:
            entries = tuple(directory.iterdir())
        except OSError as exc:
            raise _BootstrapError("cannot enumerate import-shadow candidates") from exc
        for entry in entries:
            if not any(entry.name.startswith(f"{name}.") for name in module_names):
                continue
            if entry.suffix.lower() in {
                ".py",
                *_NATIVE_SUFFIXES,
                *_SOURCELESS_SUFFIXES,
            }:
                raise _BootstrapError(f"unverified tagged import shadow is forbidden: {entry.name}")


def _reject_competing_frozen_imports(
    repository_root: Path,
    locked_paths: frozenset[str],
) -> None:
    """Mirror the frozen evaluator launcher's import-tree perimeter."""

    source_root = repository_root / "src"
    try:
        resolved_source = source_root.resolve(strict=True)
        root_entries = tuple(source_root.iterdir())
        candidates = tuple(source_root.rglob("*"))
    except OSError as exc:
        raise _BootstrapError("cannot enumerate the source import tree") from exc
    if resolved_source != source_root.absolute() or not resolved_source.is_dir():
        raise _BootstrapError("source import tree is redirected")
    for path in root_entries:
        if path.name not in _ALLOWED_SOURCE_ROOT_NAMES and not path.name.endswith(
            (".dist-info", ".egg-info")
        ):
            raise _BootstrapError("source import tree contains an unexpected top-level competitor")
    for path in candidates:
        if path.is_symlink():
            raise _BootstrapError("source import tree contains a symbolic link")
        if path.suffix.lower() in _NATIVE_SUFFIXES:
            raise _BootstrapError("source import tree contains a native competitor")
        if path.suffix.lower() in _SOURCELESS_SUFFIXES and "__pycache__" not in path.parts:
            raise _BootstrapError("source import tree contains a sourceless competitor")
    for relative in locked_paths:
        pure = PurePosixPath(relative)
        if (
            len(pure.parts) < 2
            or pure.parts[0] != "src"
            or pure.suffix != ".py"
            or pure.name == "__init__.py"
        ):
            continue
        source = repository_root.joinpath(*pure.parts)
        try:
            source.with_suffix("").lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _BootstrapError("cannot inspect package competitors") from exc
        raise _BootstrapError(f"source import tree contains a package competitor: {relative}")


def _reject_v2_package_competitors(repository_root: Path) -> None:
    """Reject ignored native, sourceless, symlink, and package competitors."""

    package_root = repository_root / "validation" / "inventory_v3_protocol_v2"
    try:
        resolved_package = package_root.resolve(strict=True)
        candidates = tuple(package_root.rglob("*"))
    except OSError as exc:
        raise _BootstrapError("cannot enumerate the V2 package import tree") from exc
    if resolved_package != package_root.absolute() or not resolved_package.is_dir():
        raise _BootstrapError("V2 package import tree is redirected")
    for path in candidates:
        if path.is_symlink():
            raise _BootstrapError("V2 package import tree contains a symbolic link")
        if path.suffix.lower() in _NATIVE_SUFFIXES:
            raise _BootstrapError("V2 package import tree contains a native competitor")
        if path.suffix.lower() in _SOURCELESS_SUFFIXES and "__pycache__" not in path.parts:
            raise _BootstrapError("V2 package import tree contains a sourceless competitor")
    for relative in _REQUIRED_V2_SOURCE_PATHS:
        pure = PurePosixPath(relative)
        if (
            pure.parent.as_posix() != "validation/inventory_v3_protocol_v2"
            or pure.suffix != ".py"
            or pure.name == "__init__.py"
        ):
            continue
        source = repository_root.joinpath(*pure.parts)
        try:
            source.with_suffix("").lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _BootstrapError("cannot inspect V2 package competitors") from exc
        raise _BootstrapError(f"V2 package import tree contains a package competitor: {relative}")


def _verify_exact_p2_delta(repository_root: Path, source_head: str) -> None:
    parent = _git(repository_root, "show", "-s", "--format=%P", source_head)
    if not isinstance(parent, str) or parent.strip() != _PROTOCOL_V1_LOCK_HEAD:
        raise _BootstrapError("P2 is not a direct child of exact frozen v1 L")
    raw = _git(
        repository_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--no-renames",
        "-r",
        source_head,
    )
    if not isinstance(raw, str):
        raise _BootstrapError("Git returned non-text P2 changed paths")
    entries: list[tuple[str, str]] = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise _BootstrapError("P2 changed-path evidence is malformed")
        entries.append((fields[0], fields[1]))
    expected = {("A", path) for path in _P2_CHANGED_PATHS}
    if set(entries) != expected or len(entries) != len(expected):
        raise _BootstrapError("P2 changed paths differ from the exact allowlist")


def _verify_preimport(launcher_file: Path) -> tuple[Path, str]:
    launcher = launcher_file.resolve(strict=True)
    repository_root = launcher.parents[2]
    expected = repository_root.joinpath(*_EXPECTED_LAUNCHER.parts)
    if launcher != expected.resolve(strict=True):
        raise _BootstrapError("V2 launcher path differs from its fixed identity")
    actual_root_raw = _git(repository_root, "rev-parse", "--show-toplevel")
    if not isinstance(actual_root_raw, str):
        raise _BootstrapError("Git returned a non-text repository root")
    if Path(actual_root_raw.strip()).resolve(strict=True) != repository_root:
        raise _BootstrapError("V2 launcher is outside the exact Git root")
    head_raw = _git(repository_root, "rev-parse", "HEAD")
    if not isinstance(head_raw, str):
        raise _BootstrapError("Git returned a non-text HEAD")
    head = head_raw.strip()
    status = _git(repository_root, "status", "--porcelain=v1")
    replacements = _git(repository_root, "replace", "-l")
    shallow = _git(repository_root, "rev-parse", "--is-shallow-repository")
    if not all(isinstance(value, str) for value in (status, replacements, shallow)):
        raise _BootstrapError("Git returned non-text repository state")
    if status.strip() or replacements.strip() or shallow.strip() != "false":
        raise _BootstrapError("V2 launcher requires clean ordinary full Git history")
    grafts_raw = _git(repository_root, "rev-parse", "--git-path", "info/grafts")
    if not isinstance(grafts_raw, str):
        raise _BootstrapError("Git returned a non-text grafts path")
    grafts = Path(grafts_raw.strip())
    if not grafts.is_absolute():
        grafts = repository_root / grafts
    if grafts.is_file() and grafts.read_bytes().strip():
        raise _BootstrapError("legacy Git grafts are forbidden")
    _reject_import_shadows(repository_root)
    _reject_v2_package_competitors(repository_root)
    lock_path = _plain_file(repository_root, _LOCK_PATH.as_posix())
    lock_payload = lock_path.read_bytes()
    try:
        lock = json.loads(lock_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _BootstrapError("V2 lock is not canonical JSON") from exc
    if not isinstance(lock, dict) or _canonical_bytes(lock) != lock_payload:
        raise _BootstrapError("V2 lock is not canonical JSON")
    if lock.get("schema") != _LOCK_SCHEMA:
        raise _BootstrapError("V2 lock schema differs")
    sidecar = _plain_file(repository_root, _LOCK_PATH.with_suffix(".json.sha256").as_posix())
    digest = hashlib.sha256(lock_payload).hexdigest()
    if sidecar.read_bytes() != f"{digest}  {lock_path.name}\n".encode("ascii"):
        raise _BootstrapError("V2 lock sidecar differs")
    protocol = lock.get("protocol")
    if not isinstance(protocol, dict):
        raise _BootstrapError("V2 lock protocol is unavailable")
    source_head = protocol.get("source_commit_sha")
    raw_blobs = protocol.get("locked_git_blobs")
    if not isinstance(source_head, str) or not isinstance(raw_blobs, list):
        raise _BootstrapError("V2 source closure is unavailable")
    _verify_exact_p2_delta(repository_root, source_head)
    lock_commits_raw = _git(
        repository_root,
        "log",
        "--full-history",
        "--diff-filter=A",
        "--format=%H",
        head,
        "--",
        _LOCK_PATH.as_posix(),
    )
    if not isinstance(lock_commits_raw, str):
        raise _BootstrapError("Git returned non-text V2 lock history")
    lock_commits = lock_commits_raw.splitlines()
    if len(lock_commits) != 1:
        raise _BootstrapError("V2 lock introduction is ambiguous")
    lock_commit = lock_commits[0]
    parent = _git(repository_root, "show", "-s", "--format=%P", lock_commit)
    if not isinstance(parent, str) or parent.strip() != source_head:
        raise _BootstrapError("V2 lock is not a direct child of P2")
    changed_raw = _git(
        repository_root,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        lock_commit,
    )
    if not isinstance(changed_raw, str) or set(changed_raw.splitlines()) != {
        _LOCK_PATH.as_posix(),
        _LOCK_PATH.with_suffix(".json.sha256").as_posix(),
    }:
        raise _BootstrapError("L2 is not an exact two-file lock-only commit")
    for lock_relative in (
        _LOCK_PATH.as_posix(),
        _LOCK_PATH.with_suffix(".json.sha256").as_posix(),
    ):
        committed = _git(
            repository_root,
            "show",
            f"{lock_commit}:{lock_relative}",
            binary=True,
        )
        current = _plain_file(repository_root, lock_relative).read_bytes()
        touched = _git(
            repository_root,
            "log",
            "--full-history",
            "--format=%H",
            f"{lock_commit}..{head}",
            "--",
            lock_relative,
        )
        if (
            not isinstance(committed, bytes)
            or current != committed
            or not isinstance(touched, str)
            or touched.strip()
        ):
            raise _BootstrapError(f"V2 lock changed after L2: {lock_relative}")
    locked_paths: set[str] = set()
    for raw in raw_blobs:
        if not isinstance(raw, dict):
            continue
        relative = raw.get("path")
        if isinstance(relative, str):
            locked_paths.add(relative)
    if not (_REQUIRED_V2_SOURCE_PATHS | _REQUIRED_DEVELOPMENT_METADATA_PATHS).issubset(
        locked_paths
    ):
        raise _BootstrapError("V2 lock omits required coordinator source")
    preregistration = _plain_file(repository_root, _PREREGISTRATION_PATH.as_posix()).read_bytes()
    preregistration_sidecar = _plain_file(
        repository_root,
        _PREREGISTRATION_PATH.with_suffix(".json.sha256").as_posix(),
    ).read_bytes()
    expected_preregistration_sidecar = (
        f"{_PREREGISTRATION_SHA256}  {_PREREGISTRATION_PATH.name}\n"
    ).encode("ascii")
    if (
        hashlib.sha256(preregistration).hexdigest() != _PREREGISTRATION_SHA256
        or preregistration_sidecar != expected_preregistration_sidecar
    ):
        raise _BootstrapError("V2 preregistration or its exact sidecar differs")
    for raw in raw_blobs:
        if not isinstance(raw, dict) or set(raw) != {"git_blob", "path"}:
            raise _BootstrapError("V2 locked blob entry differs")
        relative = raw.get("path")
        expected_blob = raw.get("git_blob")
        if not isinstance(relative, str) or not isinstance(expected_blob, str):
            raise _BootstrapError("V2 locked blob identity differs")
        path = _plain_file(repository_root, relative)
        current_blob = _git(repository_root, "rev-parse", f"{head}:{relative}")
        source_bytes = _git(repository_root, "show", f"{source_head}:{relative}", binary=True)
        if (
            not isinstance(current_blob, str)
            or current_blob.strip() != expected_blob
            or not isinstance(source_bytes, bytes)
            or path.read_bytes() != source_bytes
        ):
            raise _BootstrapError(f"V2 locked source differs: {relative}")
    _reject_competing_frozen_imports(repository_root, frozenset(locked_paths))
    return repository_root, head


def _is_direct(launcher: Path) -> bool:
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not isinstance(main_file, str):
        return False
    try:
        return (
            Path(sys.argv[0]).resolve(strict=True) == launcher
            and Path(main_file).resolve(strict=True) == launcher
        )
    except OSError:
        return False


def main() -> int:
    if sys.version_info < (3, 12):  # noqa: UP036 - locked minimum is protocol identity
        print("Protocol V2 requires Python 3.12 or newer.", file=sys.stderr)
        return 2
    if not sys.flags.isolated or not sys.flags.no_site:
        print("Protocol V2 requires direct Python -I -S execution.", file=sys.stderr)
        return 2
    launcher = Path(__file__).resolve(strict=True)
    if not _is_direct(launcher):
        print("Protocol V2 requires its direct source-owned launcher.", file=sys.stderr)
        return 2
    try:
        repository_root, _ = _verify_preimport(launcher)
    except (OSError, _BootstrapError) as exc:
        print(f"Protocol V2 bootstrap rejected: {exc}", file=sys.stderr)
        return 2
    sys.pycache_prefix = tempfile.mkdtemp(prefix="inventory-v3-protocol-v2-cache-")
    sys.path.insert(0, str(repository_root / "src"))
    sys.path.append(str(repository_root))
    from validation.inventory_v3_protocol_v2.cli import main as protocol_main

    return protocol_main()


if __name__ == "__main__":
    raise SystemExit(main())
