from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from validation.inventory_v3_protocol_v2.package_tree import (
    PACKAGE_TREE_SCHEMA,
    PackageTreeError,
    enumerate_package_tree,
    recheck_package_tree,
    verify_package_tree,
)


def _write(root: Path, relative_path: str, payload: bytes) -> None:
    path = root.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_enumeration_is_canonical_deterministic_and_recheckable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        _write(root, "z-last.bin", b"last")
        _write(root, "evidence/a-first.json", b"{}\n")

    allowlist = {
        "z-last.bin": "raw-evidence",
        "evidence/a-first.json": "canonical-metadata",
    }
    first_snapshot = enumerate_package_tree(first, allowlist)
    second_snapshot = enumerate_package_tree(second, dict(reversed(tuple(allowlist.items()))))

    assert [entry.path for entry in first_snapshot.entries] == [
        "evidence/a-first.json",
        "z-last.bin",
    ]
    assert first_snapshot.to_json() == second_snapshot.to_json()
    assert first_snapshot.to_json().endswith("\n")
    assert json.loads(first_snapshot.to_json()) == {
        "entries": [entry.to_dict() for entry in first_snapshot.entries],
        "schema": PACKAGE_TREE_SCHEMA,
    }
    recheck_package_tree(first_snapshot)


@pytest.mark.parametrize(
    "invalid_path",
    (
        "/absolute.bin",
        "C:/absolute.bin",
        "../escape.bin",
        "a/../escape.bin",
        "./relative.bin",
        "a//b.bin",
        "a\\b.bin",
        "stream:secret",
        "trailing.",
        "trailing ",
        "CON",
        "con.txt",
        "nested/COM1.log",
        "non-ascii-\N{LATIN SMALL LETTER E WITH ACUTE}.txt",
        "control-\x01.txt",
    ),
)
def test_allowlist_rejects_noncanonical_or_ambiguous_paths(
    tmp_path: Path,
    invalid_path: str,
) -> None:
    root = tmp_path / "package"
    root.mkdir()

    with pytest.raises(PackageTreeError):
        enumerate_package_tree(root, {invalid_path: "evidence"})


def test_allowlist_rejects_casefold_collisions(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()

    with pytest.raises(PackageTreeError, match="case-folding"):
        enumerate_package_tree(
            root,
            {
                "Evidence/A.json": "first",
                "evidence/a.json": "second",
            },
        )


def test_allowlist_rejects_casefold_colliding_directory_components(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    root.mkdir()

    with pytest.raises(PackageTreeError, match="case-folding package node"):
        enumerate_package_tree(
            root,
            {
                "Evidence/a.json": "first",
                "evidence/b.json": "second",
            },
        )


def test_exact_tree_rejects_extra_file(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    _write(root, "expected.bin", b"expected")
    _write(root, "foreign.bin", b"foreign")

    with pytest.raises(PackageTreeError, match="extra_files"):
        enumerate_package_tree(root, {"expected.bin": "evidence"})


def test_exact_tree_rejects_missing_file(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()

    with pytest.raises(PackageTreeError, match="missing_files"):
        enumerate_package_tree(root, {"missing.bin": "evidence"})


def test_exact_tree_rejects_extra_directory(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    (root / "empty-extra-directory").mkdir()

    with pytest.raises(PackageTreeError, match="extra_directories"):
        enumerate_package_tree(root, {})


def test_symlink_entry_is_rejected_where_supported(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = root / "linked.bin"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(PackageTreeError, match="symlink or reparse"):
        enumerate_package_tree(root, {"linked.bin": "evidence"})


def test_symlink_root_is_rejected_where_supported(tmp_path: Path) -> None:
    real_root = tmp_path / "real-package"
    real_root.mkdir()
    _write(real_root, "evidence.bin", b"evidence")
    linked_root = tmp_path / "linked-package"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    with pytest.raises(PackageTreeError, match="redirected or invalid"):
        enumerate_package_tree(linked_root, {"evidence.bin": "evidence"})


def test_hardlink_is_rejected_where_supported(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    first = root / "first.bin"
    second = root / "second.bin"
    first.write_bytes(b"shared inode")
    try:
        os.link(first, second)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hardlink creation is unavailable: {exc}")

    with pytest.raises(PackageTreeError, match="hard link"):
        enumerate_package_tree(
            root,
            {
                "first.bin": "first",
                "second.bin": "second",
            },
        )


def test_nonregular_file_is_rejected_where_supported(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    root = tmp_path / "package"
    root.mkdir()
    fifo = root / "pipe"
    try:
        os.mkfifo(fifo)
    except OSError as exc:
        pytest.skip(f"FIFO creation is unavailable: {exc}")

    with pytest.raises(PackageTreeError, match="not a regular file or directory"):
        enumerate_package_tree(root, {"pipe": "evidence"})


def test_verifier_requires_exact_sizes_hashes_and_canonical_order(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    _write(root, "a.bin", b"a")
    _write(root, "b.bin", b"bb")
    snapshot = enumerate_package_tree(
        root,
        {
            "a.bin": "first",
            "b.bin": "second",
        },
    )
    document = snapshot.to_document()

    assert verify_package_tree(root, document).to_json() == snapshot.to_json()

    rebound = json.loads(snapshot.to_json())
    rebound["entries"][0]["sha256"] = "0" * 64
    with pytest.raises(PackageTreeError, match="size or SHA-256"):
        verify_package_tree(root, rebound)

    reordered = json.loads(snapshot.to_json())
    reordered["entries"].reverse()
    with pytest.raises(PackageTreeError, match="canonical path order"):
        verify_package_tree(root, reordered)


def test_reserved_tree_files_are_exact_and_part_of_mutation_recheck(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    _write(root, "evidence.bin", b"evidence")
    initial = enumerate_package_tree(root, {"evidence.bin": "evidence"})
    (root / "package-tree.json").write_text(initial.to_json(), encoding="ascii")
    (root / "package-tree.json.sha256").write_text("private sidecar\n", encoding="ascii")

    verified = verify_package_tree(
        root,
        initial.to_document(),
        reserved_paths=("package-tree.json", "package-tree.json.sha256"),
    )
    verified.recheck()

    (root / "package-tree.json.sha256").write_text("mutated sidecar\n", encoding="ascii")
    with pytest.raises(PackageTreeError, match="changed after its original snapshot"):
        verified.recheck()


def test_recheck_detects_same_size_content_mutation(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    evidence = root / "evidence.bin"
    evidence.write_bytes(b"alpha")
    snapshot = enumerate_package_tree(root, {"evidence.bin": "evidence"})

    evidence.write_bytes(b"bravo")

    with pytest.raises(PackageTreeError, match="changed after its original snapshot"):
        snapshot.recheck()


def test_recheck_detects_replaced_physical_identity(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    evidence = root / "evidence.bin"
    evidence.write_bytes(b"same bytes")
    snapshot = enumerate_package_tree(root, {"evidence.bin": "evidence"})
    replacement = root / "replacement.tmp"
    replacement.write_bytes(b"same bytes")
    replacement.replace(evidence)

    with pytest.raises(PackageTreeError, match="changed after its original snapshot"):
        snapshot.recheck()


def test_recheck_detects_new_foreign_path(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    _write(root, "evidence.bin", b"evidence")
    snapshot = enumerate_package_tree(root, {"evidence.bin": "evidence"})

    _write(root, "foreign.bin", b"foreign")

    with pytest.raises(PackageTreeError, match="extra_files"):
        snapshot.recheck()
