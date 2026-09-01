"""Deterministic closed-tree snapshots for Inventory V3 protocol V2.

The protocol treats a package directory as evidence, not as a convenient bag of
files.  Callers therefore provide the complete allowlist up front.  This module
rejects ambiguous paths and filesystem indirection, hashes every allowed regular
file, and can later prove that the same physical tree is still present.

The helper deliberately performs no package interpretation and imports no model
or evaluator code.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = [
    "PACKAGE_TREE_SCHEMA",
    "PackageTreeEntry",
    "PackageTreeError",
    "PackageTreeSnapshot",
    "enumerate_package_tree",
    "recheck_package_tree",
    "verify_package_tree",
]

PACKAGE_TREE_SCHEMA: Final[str] = "inventory-positive-v3-independent-package-tree-v1"
_HASH_CHUNK_SIZE: Final[int] = 1024 * 1024
_REPARSE_POINT_ATTRIBUTE: Final[int] = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x0400,
)
_WINDOWS_DEVICE_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


class PackageTreeError(RuntimeError):
    """The directory cannot establish one unambiguous closed evidence tree."""


@dataclass(frozen=True, slots=True)
class PackageTreeEntry:
    """One deterministic public entry in a package-tree document."""

    path: str
    role: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "role": self.role,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class _NodeSnapshot:
    relative_path: str
    kind: str
    device: int
    inode: int
    mode: int
    link_count: int
    size_bytes: int
    modified_ns: int
    changed_ns: int
    file_attributes: int
    sha256: str | None


@dataclass(frozen=True, slots=True)
class PackageTreeSnapshot:
    """A closed tree plus the private physical identity needed for rechecking."""

    root: Path
    entries: tuple[PackageTreeEntry, ...]
    _roles: tuple[tuple[str, str], ...] = field(repr=False)
    _reserved_paths: tuple[str, ...] = field(repr=False)
    _nodes: tuple[_NodeSnapshot, ...] = field(repr=False)

    def to_document(self) -> dict[str, object]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "schema": PACKAGE_TREE_SCHEMA,
        }

    def to_json(self) -> str:
        """Return canonical ASCII JSON with exactly one trailing LF."""

        return (
            json.dumps(
                self.to_document(),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )

    def recheck(self) -> None:
        """Fail if any path, byte, metadata, or physical identity changed."""

        current = _snapshot_tree(
            self.root,
            dict(self._roles),
            self._reserved_paths,
        )
        if current.entries != self.entries or current._nodes != self._nodes:
            raise PackageTreeError("package tree changed after its original snapshot")


@dataclass(frozen=True, slots=True)
class _ScannedNode:
    relative_path: str
    absolute_path: Path
    kind: str
    stat_result: os.stat_result


def enumerate_package_tree(
    root: Path,
    allowlist: Mapping[str, str],
) -> PackageTreeSnapshot:
    """Hash an exact allowlisted tree and return its canonical snapshot.

    ``allowlist`` maps canonical relative POSIX paths to semantic roles.  Every
    physical file must be listed, and every implied directory must contain an
    allowlisted descendant.  The mapping order does not affect output bytes.
    """

    roles = _normalize_roles(allowlist)
    return _snapshot_tree(root, roles, ())


def verify_package_tree(
    root: Path,
    document: Mapping[str, object],
    *,
    reserved_paths: Iterable[str] = (),
) -> PackageTreeSnapshot:
    """Verify a canonical tree document against the exact physical directory.

    ``reserved_paths`` is an explicit escape hatch for the tree document and
    its sidecar, which cannot hash themselves.  Reserved files are still
    required to exist, must pass every filesystem check, and are included in
    later mutation rechecks; they are merely omitted from ``document.entries``.
    """

    expected_entries = _parse_document(document)
    roles = {entry.path: entry.role for entry in expected_entries}
    reserved = _normalize_reserved_paths(reserved_paths, roles)
    actual = _snapshot_tree(root, roles, reserved)
    if actual.entries != expected_entries:
        raise PackageTreeError("package file size or SHA-256 differs from package tree")
    return actual


def recheck_package_tree(snapshot: PackageTreeSnapshot) -> None:
    """Functional wrapper around :meth:`PackageTreeSnapshot.recheck`."""

    if not isinstance(snapshot, PackageTreeSnapshot):
        raise TypeError("snapshot must be PackageTreeSnapshot")
    snapshot.recheck()


def _snapshot_tree(
    root: Path,
    roles: Mapping[str, str],
    reserved_paths: Sequence[str],
) -> PackageTreeSnapshot:
    package_root = _absolute_root(root)
    allowed_files = frozenset((*roles, *reserved_paths))
    expected_directories = _expected_directories(allowed_files)

    first_files, first_directories = _scan_structure(package_root)
    _require_exact_paths(
        actual_files=frozenset(first_files),
        actual_directories=frozenset(first_directories),
        expected_files=allowed_files,
        expected_directories=expected_directories,
    )

    file_snapshots: dict[str, _NodeSnapshot] = {}
    physical_files: dict[tuple[int, int], str] = {}
    for relative_path in sorted(first_files, key=_ascii_sort_key):
        scanned = first_files[relative_path]
        snapshot = _hash_stable_file(scanned)
        identity = (snapshot.device, snapshot.inode)
        previous = physical_files.get(identity)
        if previous is not None:
            raise PackageTreeError(
                f"package paths share one physical file identity: {previous!r}, {relative_path!r}"
            )
        physical_files[identity] = relative_path
        file_snapshots[relative_path] = snapshot

    second_files, second_directories = _scan_structure(package_root)
    _require_exact_paths(
        actual_files=frozenset(second_files),
        actual_directories=frozenset(second_directories),
        expected_files=allowed_files,
        expected_directories=expected_directories,
    )
    for relative_path, before in first_files.items():
        after = second_files[relative_path]
        if _stat_fingerprint(before.stat_result) != _stat_fingerprint(after.stat_result):
            raise PackageTreeError(f"package file changed while being snapshotted: {relative_path}")
        if _node_fingerprint(file_snapshots[relative_path]) != _stat_fingerprint(after.stat_result):
            raise PackageTreeError(f"package file changed while being hashed: {relative_path}")
    for relative_path, before in first_directories.items():
        after = second_directories[relative_path]
        if _stat_fingerprint(before.stat_result) != _stat_fingerprint(after.stat_result):
            label = relative_path or "."
            raise PackageTreeError(f"package directory changed while being snapshotted: {label}")

    directory_snapshots = tuple(
        _node_snapshot(scanned, sha256=None)
        for _, scanned in sorted(
            second_directories.items(), key=lambda item: _ascii_sort_key(item[0])
        )
    )
    ordered_file_snapshots = tuple(
        file_snapshots[path] for path in sorted(file_snapshots, key=_ascii_sort_key)
    )
    entries = tuple(
        PackageTreeEntry(
            path=path,
            role=roles[path],
            size_bytes=file_snapshots[path].size_bytes,
            sha256=_required_snapshot_sha(file_snapshots[path]),
        )
        for path in sorted(roles, key=_ascii_sort_key)
    )
    return PackageTreeSnapshot(
        root=package_root,
        entries=entries,
        _roles=tuple(sorted(roles.items(), key=lambda item: _ascii_sort_key(item[0]))),
        _reserved_paths=tuple(sorted(reserved_paths, key=_ascii_sort_key)),
        _nodes=(*directory_snapshots, *ordered_file_snapshots),
    )


def _scan_structure(
    root: Path,
) -> tuple[dict[str, _ScannedNode], dict[str, _ScannedNode]]:
    files: dict[str, _ScannedNode] = {}
    directories: dict[str, _ScannedNode] = {}

    root_stat = _checked_lstat(root, ".")
    _require_plain_directory(root_stat, ".")
    directories[""] = _ScannedNode("", root, "directory", root_stat)

    def visit(relative_directory: str, absolute_directory: Path) -> None:
        before = _checked_lstat(absolute_directory, relative_directory or ".")
        _require_plain_directory(before, relative_directory or ".")
        try:
            with os.scandir(absolute_directory) as iterator:
                discovered = list(iterator)
        except OSError as exc:
            raise PackageTreeError(
                f"cannot enumerate package directory: {relative_directory or '.'}"
            ) from exc

        normalized: list[tuple[str, os.DirEntry[str]]] = []
        for item in discovered:
            relative_path = (
                item.name if not relative_directory else f"{relative_directory}/{item.name}"
            )
            normalized.append(
                (_canonical_relative_path(relative_path, "physical package path"), item)
            )
        normalized.sort(key=lambda item: _ascii_sort_key(item[0]))

        for relative_path, item in normalized:
            absolute_path = absolute_directory / item.name
            node_stat = _checked_lstat(absolute_path, relative_path)
            if _is_link_or_reparse(node_stat):
                raise PackageTreeError(
                    f"package path is a symlink or reparse point: {relative_path}"
                )
            if stat.S_ISDIR(node_stat.st_mode):
                directories[relative_path] = _ScannedNode(
                    relative_path,
                    absolute_path,
                    "directory",
                    node_stat,
                )
                visit(relative_path, absolute_path)
            elif stat.S_ISREG(node_stat.st_mode):
                _require_single_link(node_stat, relative_path)
                files[relative_path] = _ScannedNode(
                    relative_path,
                    absolute_path,
                    "file",
                    node_stat,
                )
            else:
                raise PackageTreeError(
                    f"package path is not a regular file or directory: {relative_path}"
                )

        after = _checked_lstat(absolute_directory, relative_directory or ".")
        _require_plain_directory(after, relative_directory or ".")
        if _stat_fingerprint(before) != _stat_fingerprint(after):
            raise PackageTreeError(
                f"package directory changed during enumeration: {relative_directory or '.'}"
            )

    visit("", root)
    return files, directories


def _hash_stable_file(scanned: _ScannedNode) -> _NodeSnapshot:
    relative_path = scanned.relative_path
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(scanned.absolute_path, flags)
    except OSError as exc:
        raise PackageTreeError(f"cannot open package file: {relative_path}") from exc

    try:
        opened = os.fstat(descriptor)
        _require_plain_regular_file(opened, relative_path)
        if _stat_fingerprint(scanned.stat_result) != _stat_fingerprint(opened):
            raise PackageTreeError(f"package file identity changed before hashing: {relative_path}")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, _HASH_CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
        after_read = os.fstat(descriptor)
        _require_plain_regular_file(after_read, relative_path)
        if _stat_fingerprint(opened) != _stat_fingerprint(after_read):
            raise PackageTreeError(f"package file changed while hashing: {relative_path}")
    except OSError as exc:
        raise PackageTreeError(f"cannot hash package file: {relative_path}") from exc
    finally:
        os.close(descriptor)

    after_path = _checked_lstat(scanned.absolute_path, relative_path)
    _require_plain_regular_file(after_path, relative_path)
    if _stat_fingerprint(after_read) != _stat_fingerprint(after_path):
        raise PackageTreeError(f"package file identity changed after hashing: {relative_path}")
    return _node_snapshot(scanned, sha256=digest.hexdigest(), stat_result=after_path)


def _parse_document(document: Mapping[str, object]) -> tuple[PackageTreeEntry, ...]:
    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")
    if set(document) != {"entries", "schema"}:
        raise PackageTreeError("package-tree document has unexpected keys")
    if document.get("schema") != PACKAGE_TREE_SCHEMA:
        raise PackageTreeError("package-tree schema is not supported")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, Sequence) or isinstance(
        raw_entries,
        (str, bytes, bytearray),
    ):
        raise PackageTreeError("package-tree entries must be a sequence")

    entries: list[PackageTreeEntry] = []
    seen_paths: set[str] = set()
    seen_casefolded: dict[str, str] = {}
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, Mapping):
            raise PackageTreeError(f"package-tree entry {index} must be an object")
        if set(raw) != {"path", "role", "sha256", "size_bytes"}:
            raise PackageTreeError(f"package-tree entry {index} has unexpected keys")
        path = _canonical_relative_path(raw.get("path"), f"package-tree entry {index} path")
        _record_unique_path(path, seen_paths, seen_casefolded)
        role = _canonical_role(raw.get("role"), f"package-tree entry {index} role")
        size = raw.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise PackageTreeError(f"package-tree entry {index} has invalid size")
        digest = raw.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise PackageTreeError(f"package-tree entry {index} has invalid SHA-256")
        entries.append(
            PackageTreeEntry(
                path=path,
                role=role,
                size_bytes=size,
                sha256=digest,
            )
        )

    ordered = tuple(sorted(entries, key=lambda entry: _ascii_sort_key(entry.path)))
    if tuple(entries) != ordered:
        raise PackageTreeError("package-tree entries are not in canonical path order")
    _require_no_casefold_node_collisions(entry.path for entry in ordered)
    return ordered


def _normalize_roles(allowlist: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(allowlist, Mapping):
        raise TypeError("allowlist must be a mapping of paths to roles")
    result: dict[str, str] = {}
    seen_casefolded: dict[str, str] = {}
    for raw_path, raw_role in allowlist.items():
        path = _canonical_relative_path(raw_path, "allowlist path")
        _record_unique_path(path, set(result), seen_casefolded)
        result[path] = _canonical_role(raw_role, f"role for {path}")
    _require_no_casefold_node_collisions(result)
    return result


def _normalize_reserved_paths(
    values: Iterable[str],
    roles: Mapping[str, str],
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError("reserved_paths must be an iterable of paths")
    paths: list[str] = []
    seen = set(roles)
    seen_casefolded = {path.casefold(): path for path in roles}
    for raw in values:
        path = _canonical_relative_path(raw, "reserved package path")
        _record_unique_path(path, seen, seen_casefolded)
        seen.add(path)
        paths.append(path)
    _require_no_casefold_node_collisions((*roles, *paths))
    return tuple(sorted(paths, key=_ascii_sort_key))


def _canonical_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PackageTreeError(f"{label} must be non-empty text")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PackageTreeError(f"{label} must contain ASCII only") from exc
    if any(byte < 0x20 or byte > 0x7E for byte in encoded):
        raise PackageTreeError(f"{label} contains a non-printable character")
    if "\\" in value:
        raise PackageTreeError(f"{label} must use POSIX separators")
    if ":" in value:
        raise PackageTreeError(f"{label} cannot contain a colon or alternate stream")
    parts = value.split("/")
    if any(not part for part in parts):
        raise PackageTreeError(f"{label} is absolute or contains an empty component")
    for component in parts:
        if component in {".", ".."}:
            raise PackageTreeError(f"{label} contains a dot component")
        if component.endswith((".", " ")):
            raise PackageTreeError(f"{label} has a trailing dot or space")
        basename = component.split(".", maxsplit=1)[0].casefold()
        if basename in _WINDOWS_DEVICE_BASENAMES:
            raise PackageTreeError(f"{label} contains a reserved device name")
    return value


def _canonical_role(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PackageTreeError(f"{label} must be non-empty canonical text")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PackageTreeError(f"{label} must contain ASCII only") from exc
    if any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise PackageTreeError(f"{label} contains unsupported whitespace")
    return value


def _record_unique_path(
    path: str,
    seen_paths: set[str],
    seen_casefolded: dict[str, str],
) -> None:
    if path in seen_paths:
        raise PackageTreeError(f"duplicate package path: {path}")
    folded = path.casefold()
    previous = seen_casefolded.get(folded)
    if previous is not None:
        raise PackageTreeError(f"case-folding package path collision: {previous!r}, {path!r}")
    seen_casefolded[folded] = path


def _require_no_casefold_node_collisions(paths: Iterable[str]) -> None:
    """Reject collisions in both files and their implied directory prefixes."""

    seen: dict[str, str] = {}
    for path in paths:
        parts = path.split("/")
        for count in range(1, len(parts) + 1):
            node = "/".join(parts[:count])
            folded = node.casefold()
            previous = seen.get(folded)
            if previous is not None and previous != node:
                raise PackageTreeError(
                    f"case-folding package node collision: {previous!r}, {node!r}"
                )
            seen[folded] = node


def _absolute_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise TypeError("root must be pathlib.Path")
    return Path(os.path.abspath(root))


def _expected_directories(paths: Iterable[str]) -> frozenset[str]:
    directories = {""}
    for path in paths:
        parts = path.split("/")
        for count in range(1, len(parts)):
            directories.add("/".join(parts[:count]))
    return frozenset(directories)


def _require_exact_paths(
    *,
    actual_files: frozenset[str],
    actual_directories: frozenset[str],
    expected_files: frozenset[str],
    expected_directories: frozenset[str],
) -> None:
    missing_files = sorted(expected_files - actual_files, key=_ascii_sort_key)
    extra_files = sorted(actual_files - expected_files, key=_ascii_sort_key)
    missing_directories = sorted(
        expected_directories - actual_directories,
        key=_ascii_sort_key,
    )
    extra_directories = sorted(
        actual_directories - expected_directories,
        key=_ascii_sort_key,
    )
    if missing_files or extra_files or missing_directories or extra_directories:
        raise PackageTreeError(
            "package physical tree differs from explicit allowlist: "
            f"missing_files={missing_files}, extra_files={extra_files}, "
            f"missing_directories={missing_directories}, "
            f"extra_directories={extra_directories}"
        )


def _checked_lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise PackageTreeError(f"cannot inspect package path: {label}") from exc


def _is_link_or_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        int(getattr(value, "st_file_attributes", 0)) & _REPARSE_POINT_ATTRIBUTE
    )


def _require_plain_directory(value: os.stat_result, label: str) -> None:
    if _is_link_or_reparse(value) or not stat.S_ISDIR(value.st_mode):
        raise PackageTreeError(f"package directory is redirected or invalid: {label}")


def _require_single_link(value: os.stat_result, label: str) -> None:
    if value.st_nlink != 1:
        raise PackageTreeError(f"package regular file has a hard link: {label}")


def _require_plain_regular_file(value: os.stat_result, label: str) -> None:
    if _is_link_or_reparse(value) or not stat.S_ISREG(value.st_mode):
        raise PackageTreeError(f"package path is not a plain regular file: {label}")
    _require_single_link(value, label)


def _stat_fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_size),
        int(value.st_mtime_ns),
        _changed_ns(value),
        int(getattr(value, "st_file_attributes", 0)),
    )


def _changed_ns(value: os.stat_result) -> int:
    """Normalize Windows' path-stat/handle-stat change-time distinction."""

    if os.name == "nt":
        return int(getattr(value, "st_birthtime_ns", value.st_ctime_ns))
    return int(value.st_ctime_ns)


def _node_fingerprint(value: _NodeSnapshot) -> tuple[int, ...]:
    return (
        value.device,
        value.inode,
        value.mode,
        value.link_count,
        value.size_bytes,
        value.modified_ns,
        value.changed_ns,
        value.file_attributes,
    )


def _node_snapshot(
    scanned: _ScannedNode,
    *,
    sha256: str | None,
    stat_result: os.stat_result | None = None,
) -> _NodeSnapshot:
    value = scanned.stat_result if stat_result is None else stat_result
    return _NodeSnapshot(
        relative_path=scanned.relative_path,
        kind=scanned.kind,
        device=int(value.st_dev),
        inode=int(value.st_ino),
        mode=int(value.st_mode),
        link_count=int(value.st_nlink),
        size_bytes=int(value.st_size),
        modified_ns=int(value.st_mtime_ns),
        changed_ns=_changed_ns(value),
        file_attributes=int(getattr(value, "st_file_attributes", 0)),
        sha256=sha256,
    )


def _required_snapshot_sha(value: _NodeSnapshot) -> str:
    if value.sha256 is None:  # pragma: no cover - construction invariant
        raise AssertionError("file snapshot omitted SHA-256")
    return value.sha256


def _ascii_sort_key(value: str) -> bytes:
    return value.encode("ascii")
