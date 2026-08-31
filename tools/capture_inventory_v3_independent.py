"""Hardened stdlib bootstrap for the locked passive Inventory V3 campaign."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

_CAPTURE_SOURCE_PATHS = (
    ".gitattributes",
    ".gitignore",
    "src/mining_automation/__init__.py",
    "src/mining_automation/capture/__init__.py",
    "src/mining_automation/capture/backend.py",
    "src/mining_automation/capture/errors.py",
    "src/mining_automation/capture/frame.py",
    "src/mining_automation/capture/source.py",
    "src/mining_automation/capture/windows/__init__.py",
    "src/mining_automation/capture/windows/_win32_calls.py",
    "src/mining_automation/capture/windows/backend.py",
    "src/mining_automation/capture/windows/gdi_resources.py",
    "src/mining_automation/capture/windows/geometry.py",
    "src/mining_automation/capture/windows/win32_api.py",
    "src/mining_automation/capture/windows/window_selector.py",
    "src/mining_automation/contracts.py",
    "src/mining_automation/diagnostics.py",
    "src/mining_automation/validation/__init__.py",
    "src/mining_automation/validation/inventory_v3_capture.py",
    "src/mining_automation/validation/inventory_v3_capture_cli.py",
    "tools/capture_inventory_v3_independent.py",
)
_EXPECTED_LAUNCHER = PurePosixPath("tools/capture_inventory_v3_independent.py")
_NATIVE_SUFFIXES = frozenset({".dll", ".pyd", ".so"})
_SOURCELESS_SUFFIXES = frozenset({".pyc", ".pyo"})
_ALLOWED_SOURCE_ROOT_NAMES = frozenset({"__pycache__", "mining_automation"})


class _BootstrapError(RuntimeError):
    """The source-owned launcher could not establish its pre-import boundary."""


def _git(root: Path, *arguments: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode != 0:
        stderr = completed.stderr
        stdout = completed.stdout
        if isinstance(stderr, bytes):
            detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
        else:
            detail = (stderr or stdout).strip()
        raise _BootstrapError(f"Git pre-import attestation failed: {detail}")
    return completed.stdout


def _unredirected_file(root: Path, relative: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise _BootstrapError(f"source path traverses a symbolic link: {relative}")
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise _BootstrapError(f"source path is unavailable: {relative}") from exc
    if resolved != current.absolute() or not resolved.is_file():
        raise _BootstrapError(f"source path is redirected or not a file: {relative}")
    return resolved


def _reject_git_history_overrides(root: Path) -> None:
    replacements = _git(root, "replace", "-l")
    if not isinstance(replacements, str):  # pragma: no cover - binary=False
        raise _BootstrapError("Git returned non-text replacement-ref evidence")
    if replacements.strip():
        raise _BootstrapError(
            "Git replacement refs are forbidden before capture imports"
        )
    grafts_text = _git(root, "rev-parse", "--git-path", "info/grafts")
    if not isinstance(grafts_text, str):  # pragma: no cover - binary=False
        raise _BootstrapError("Git returned a non-text legacy-grafts path")
    grafts = Path(grafts_text.strip())
    if not grafts.is_absolute():
        grafts = root / grafts
    try:
        nonempty_grafts = grafts.is_file() and bool(grafts.read_bytes().strip())
    except OSError as exc:
        raise _BootstrapError("cannot inspect the legacy Git grafts file") from exc
    if nonempty_grafts:
        raise _BootstrapError(
            "nonempty legacy Git grafts are forbidden before capture imports"
        )


def _reject_competing_imports(source_root: Path) -> None:
    try:
        root_entries = tuple(source_root.iterdir())
        candidates = tuple(source_root.rglob("*"))
    except OSError as exc:
        raise _BootstrapError("cannot enumerate the source import tree") from exc
    for path in root_entries:
        if (
            path.name not in _ALLOWED_SOURCE_ROOT_NAMES
            and not path.name.endswith((".dist-info", ".egg-info"))
        ):
            raise _BootstrapError(
                "source import tree contains an unexpected top-level competitor"
            )
    for path in candidates:
        if path.is_symlink():
            raise _BootstrapError("source import tree contains a symbolic link")
        if path.suffix.lower() in _NATIVE_SUFFIXES:
            raise _BootstrapError("source import tree contains a native competitor")
        if (
            path.suffix.lower() in _SOURCELESS_SUFFIXES
            and "__pycache__" not in path.parts
        ):
            raise _BootstrapError("source import tree contains a sourceless competitor")
    for relative in _CAPTURE_SOURCE_PATHS:
        source = source_root.parent.joinpath(*PurePosixPath(relative).parts)
        if source.suffix == ".py" and source.name != "__init__.py":
            if (source.parent / source.stem).exists():
                raise _BootstrapError(
                    f"source import tree contains a package competitor: {relative}"
                )


def _verify_preimport_source(launcher_file: Path) -> tuple[Path, Path, str]:
    launcher = launcher_file.resolve(strict=True)
    repository_root = launcher.parents[1]
    expected_launcher = repository_root.joinpath(*_EXPECTED_LAUNCHER.parts)
    if launcher != expected_launcher.resolve(strict=True):
        raise _BootstrapError("capture launcher path differs from its fixed identity")
    actual_root_text = _git(repository_root, "rev-parse", "--show-toplevel")
    if not isinstance(actual_root_text, str):  # pragma: no cover - binary=False
        raise _BootstrapError("Git returned non-text repository identity")
    actual_root = Path(actual_root_text.strip()).resolve(strict=True)
    if actual_root != repository_root:
        raise _BootstrapError("capture launcher is not in the exact Git worktree root")
    _reject_git_history_overrides(repository_root)
    head_text = _git(repository_root, "rev-parse", "HEAD")
    if not isinstance(head_text, str):  # pragma: no cover - binary=False
        raise _BootstrapError("Git returned non-text HEAD identity")
    head = head_text.strip()
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise _BootstrapError("capture launcher HEAD is not a full lowercase Git SHA")
    for relative in _CAPTURE_SOURCE_PATHS:
        path = _unredirected_file(repository_root, relative)
        committed = _git(repository_root, "show", f"{head}:{relative}", binary=True)
        if not isinstance(committed, bytes):  # pragma: no cover - binary=True
            raise _BootstrapError("Git returned non-binary source bytes")
        if path.read_bytes() != committed:
            raise _BootstrapError(
                f"worktree bytes differ from capture HEAD before import: {relative}"
            )
    source_root = repository_root / "src"
    _reject_competing_imports(source_root)
    return repository_root, source_root, head


def _is_direct_launcher(launcher: Path, argv_zero: str, main_file: object) -> bool:
    if not isinstance(main_file, str):
        return False
    try:
        return (
            Path(argv_zero).resolve(strict=True) == launcher
            and Path(main_file).resolve(strict=True) == launcher
        )
    except OSError:
        return False


def main() -> int:
    if not sys.flags.isolated or not sys.flags.no_site:
        print(
            "Inventory V3 capture requires the locked Python -I -S launcher.",
            file=sys.stderr,
        )
        return 2
    launcher = Path(__file__).resolve(strict=True)
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not _is_direct_launcher(launcher, sys.argv[0], main_file):
        print(
            "Inventory V3 capture requires direct execution of the locked launcher.",
            file=sys.stderr,
        )
        return 2
    try:
        _repository_root, source_root, _head = _verify_preimport_source(launcher)
    except (OSError, _BootstrapError) as exc:
        print(f"Inventory V3 capture bootstrap rejected: {exc}", file=sys.stderr)
        return 2
    cache = tempfile.mkdtemp(prefix="inventory-v3-capture-cache-")
    sys.pycache_prefix = cache
    sys.path.insert(0, str(source_root))
    from mining_automation.validation.inventory_v3_capture_cli import (
        main as capture_main,
    )

    # The private cache is intentionally not cleaned up after capture_main:
    # no fallible filesystem operation may follow a finalized completion seal.
    return capture_main()


if __name__ == "__main__":
    raise SystemExit(main())
