from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import mining_automation.perception.inventory.positive_v3_independent_validation as validation
from mining_automation.validation import inventory_v3_capture as capture

_ROOT = Path(__file__).resolve().parents[1]


def _load_tool(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _ROOT / relative)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_CAPTURE_TOOL = _load_tool(
    "inventory_v3_capture_bootstrap_test",
    "tools/capture_inventory_v3_independent.py",
)
_VALIDATION_TOOL = _load_tool(
    "inventory_v3_validation_bootstrap_test",
    "tools/inventory_v3_independent_validation.py",
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _committed_source_tree(
    tmp_path: Path,
    *,
    paths: tuple[str, ...],
    launcher: str,
) -> tuple[Path, Path]:
    root = tmp_path / "repository"
    for relative in paths:
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"committed source: {relative}\n".encode())
    launcher_path = root.joinpath(*launcher.split("/"))
    for arguments in (
        ("init", "--quiet"),
        ("config", "core.autocrlf", "false"),
        ("config", "user.name", "Bootstrap Test"),
        ("config", "user.email", "bootstrap-test@example.invalid"),
        ("add", "."),
        ("commit", "--quiet", "-m", "freeze bootstrap closure"),
    ):
        _git(root, *arguments)
    return root, launcher_path


def test_bootstrap_closures_are_hardcoded_and_exactly_match_runtime_contracts() -> None:
    assert _CAPTURE_TOOL._CAPTURE_SOURCE_PATHS == capture._CAPTURE_SOURCE_PATHS
    expected_validation = tuple(
        dict.fromkeys(
            (
                *validation._PROTOCOL_LOCKED_PATHS,
                *validation._APPROVED_CAPTURE_SOURCE_PATHS,
            )
        )
    )
    assert _VALIDATION_TOOL._VALIDATION_SOURCE_PATHS == expected_validation


@pytest.mark.parametrize(
    ("tool", "paths_attribute", "launcher", "mutated_relative"),
    [
        (
            _CAPTURE_TOOL,
            "_CAPTURE_SOURCE_PATHS",
            "tools/capture_inventory_v3_independent.py",
            "src/mining_automation/validation/inventory_v3_capture_cli.py",
        ),
        (
            _VALIDATION_TOOL,
            "_VALIDATION_SOURCE_PATHS",
            "tools/inventory_v3_independent_validation.py",
            (
                "src/mining_automation/perception/inventory/"
                "positive_v3_independent_validation_cli.py"
            ),
        ),
    ],
)
def test_preimport_attestation_ignores_index_concealment_and_checks_exact_bytes(
    tmp_path: Path,
    tool: ModuleType,
    paths_attribute: str,
    launcher: str,
    mutated_relative: str,
) -> None:
    paths = getattr(tool, paths_attribute)
    assert isinstance(paths, tuple)
    root, launcher_path = _committed_source_tree(
        tmp_path,
        paths=paths,
        launcher=launcher,
    )
    head = _git(root, "rev-parse", "HEAD")
    assert tool._verify_preimport_source(launcher_path) == (
        root.resolve(),
        (root / "src").resolve(),
        head,
    )
    mutated = root.joinpath(*mutated_relative.split("/"))
    original = mutated.read_bytes()

    for conceal, reveal in (
        ("--assume-unchanged", "--no-assume-unchanged"),
        ("--skip-worktree", "--no-skip-worktree"),
    ):
        _git(root, "update-index", conceal, "--", mutated_relative)
        mutated.write_bytes(original + b"hidden mutation\n")
        assert _git(root, "status", "--porcelain") == ""
        with pytest.raises(
            tool._BootstrapError,
            match="worktree bytes differ .* before import",
        ):
            tool._verify_preimport_source(launcher_path)
        mutated.write_bytes(original)
        _git(root, "update-index", reveal, "--", mutated_relative)


@pytest.mark.parametrize(
    ("tool", "paths_attribute", "launcher"),
    [
        (
            _CAPTURE_TOOL,
            "_CAPTURE_SOURCE_PATHS",
            "tools/capture_inventory_v3_independent.py",
        ),
        (
            _VALIDATION_TOOL,
            "_VALIDATION_SOURCE_PATHS",
            "tools/inventory_v3_independent_validation.py",
        ),
    ],
)
def test_preimport_attestation_rejects_replacement_refs_and_legacy_grafts(
    tmp_path: Path,
    tool: ModuleType,
    paths_attribute: str,
    launcher: str,
) -> None:
    paths = getattr(tool, paths_attribute)
    assert isinstance(paths, tuple)
    root, launcher_path = _committed_source_tree(
        tmp_path,
        paths=paths,
        launcher=launcher,
    )
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", f"{head}^{{tree}}")
    replacement = _git(root, "commit-tree", tree, "-m", "replacement commit")
    _git(root, "replace", head, replacement)
    assert _git(root, "status", "--porcelain") == ""
    with pytest.raises(tool._BootstrapError, match="replacement refs"):
        tool._verify_preimport_source(launcher_path)
    _git(root, "replace", "-d", head)

    grafts_text = _git(root, "rev-parse", "--git-path", "info/grafts")
    grafts = Path(grafts_text)
    if not grafts.is_absolute():
        grafts = root / grafts
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{head}\n", encoding="ascii")
    assert _git(root, "status", "--porcelain") == ""
    with pytest.raises(tool._BootstrapError, match="legacy Git grafts"):
        tool._verify_preimport_source(launcher_path)


@pytest.mark.parametrize(
    ("tool", "paths_attribute"),
    [
        (_CAPTURE_TOOL, "_CAPTURE_SOURCE_PATHS"),
        (_VALIDATION_TOOL, "_VALIDATION_SOURCE_PATHS"),
    ],
)
def test_preimport_attestation_rejects_native_cache_and_package_competitors(
    tmp_path: Path,
    tool: ModuleType,
    paths_attribute: str,
) -> None:
    source_root = tmp_path / "src"
    module_parent = source_root / "mining_automation" / "validation"
    module_parent.mkdir(parents=True)

    native = module_parent / "inventory_v3_capture_cli.pyd"
    native.write_bytes(b"native competitor")
    with pytest.raises(tool._BootstrapError, match="native competitor"):
        tool._reject_competing_imports(source_root)
    native.unlink()

    cache = module_parent / "inventory_v3_capture_cli.pyc"
    cache.write_bytes(b"sourceless cache competitor")
    with pytest.raises(tool._BootstrapError, match="sourceless competitor"):
        tool._reject_competing_imports(source_root)
    cache.unlink()

    paths = getattr(tool, paths_attribute)
    module_relative = next(
        relative
        for relative in paths
        if relative.endswith("inventory_v3_capture_cli.py")
    )
    module_path = source_root.parent.joinpath(*module_relative.split("/"))
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.with_suffix("").mkdir()
    with pytest.raises(tool._BootstrapError, match="package competitor"):
        tool._reject_competing_imports(source_root)
    module_path.with_suffix("").rmdir()

    (source_root / "argparse.py").write_bytes(b"stdlib shadow")
    with pytest.raises(tool._BootstrapError, match="top-level competitor"):
        tool._reject_competing_imports(source_root)


@pytest.mark.parametrize("tool", [_CAPTURE_TOOL, _VALIDATION_TOOL])
def test_preimport_attestation_rejects_redirected_source_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: ModuleType,
) -> None:
    root = tmp_path / "root"
    redirected = root / "redirected"
    target = redirected / "source.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"source")
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path == redirected or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    with pytest.raises(tool._BootstrapError, match="symbolic link"):
        tool._unredirected_file(root, "redirected/source.py")


@pytest.mark.parametrize("tool", [_CAPTURE_TOOL, _VALIDATION_TOOL])
def test_direct_launcher_identity_binds_argv_and_main_module(
    tmp_path: Path,
    tool: ModuleType,
) -> None:
    launcher = tmp_path / "launcher.py"
    launcher.write_bytes(b"launcher")
    other = tmp_path / "other.py"
    other.write_bytes(b"other")

    assert tool._is_direct_launcher(launcher, str(launcher), str(launcher))
    assert not tool._is_direct_launcher(launcher, str(other), str(launcher))
    assert not tool._is_direct_launcher(launcher, str(launcher), str(other))
    assert not tool._is_direct_launcher(launcher, str(launcher), None)
