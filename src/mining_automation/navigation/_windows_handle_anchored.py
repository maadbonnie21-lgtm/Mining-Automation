"""Native Windows handle-relative namespace for navigation evidence.

Imported lazily and only by :mod:`handle_anchored_route_evidence` after the
public boundary has confirmed ``sys.platform == "win32"``.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import stat
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, Final

from .contracts import Sha256Digest
from .durable_route_evidence import (
    _MAX_MANIFEST_BYTES,
    DurableEvidenceCollisionError,
    DurableEvidenceError,
    _physical_identity_sha256,
    _writer_directory_identity,
)
from .handle_anchored_route_evidence import HandleAnchoredEvidenceCapabilityError
from .route_evidence_loader import (
    _assert_exact_tree,
    _cross_handle_identity,
    _is_link_or_reparse,
    _stat_signature,
    _strict_relative_path,
)

if sys.platform != "win32":
    raise ImportError("Windows handle-anchored backend imported on a non-Windows host")


def _load_dll(name: str) -> Any:  # noqa: ANN401 - ctypes DLL handles are untyped
    loader: Any = vars(ctypes)["WinDLL"]
    return loader(name, use_last_error=True)


_kernel32 = _load_dll("kernel32")
_ntdll = _load_dll("ntdll")

# Win32 / NT access and creation constants.
_FILE_LIST_DIRECTORY: Final[int] = 0x0001
_FILE_ADD_FILE: Final[int] = 0x0002
_FILE_ADD_SUBDIRECTORY: Final[int] = 0x0004
_FILE_TRAVERSE: Final[int] = 0x0020
_FILE_READ_ATTRIBUTES: Final[int] = 0x0080
_SYNCHRONIZE: Final[int] = 0x00100000
_GENERIC_READ: Final[int] = 0x80000000
_GENERIC_WRITE: Final[int] = 0x40000000

_FILE_SHARE_READ: Final[int] = 0x00000001
_FILE_SHARE_WRITE: Final[int] = 0x00000002
_FILE_SHARE_DELETE: Final[int] = 0x00000004
_DIRECTORY_SHARE: Final[int] = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
_FILE_SHARE: Final[int] = _FILE_SHARE_READ

_OPEN_EXISTING: Final[int] = 3
_FILE_OPEN: Final[int] = 1
_FILE_CREATE: Final[int] = 2
_FILE_OPENED: Final[int] = 1
_FILE_CREATED: Final[int] = 2

_FILE_ATTRIBUTE_NORMAL: Final[int] = 0x00000080
_FILE_ATTRIBUTE_REPARSE_POINT: Final[int] = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS: Final[int] = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT: Final[int] = 0x00200000

_FILE_DIRECTORY_FILE: Final[int] = 0x00000001
_FILE_WRITE_THROUGH: Final[int] = 0x00000002
_FILE_SYNCHRONOUS_IO_NONALERT: Final[int] = 0x00000020
_FILE_NON_DIRECTORY_FILE: Final[int] = 0x00000040
_FILE_OPEN_REPARSE_POINT: Final[int] = 0x00200000

_OBJ_CASE_INSENSITIVE: Final[int] = 0x00000040
_FILE_TYPE_DISK: Final[int] = 0x0001
_DRIVE_FIXED: Final[int] = 3
_FILE_STANDARD_INFO_CLASS: Final[int] = 1
_FILE_ATTRIBUTE_TAG_INFO_CLASS: Final[int] = 9
_FILE_ID_INFO_CLASS: Final[int] = 18
_DUPLICATE_SAME_ACCESS: Final[int] = 0x00000002
_HANDLE_FLAG_PROTECT_FROM_CLOSE: Final[int] = 0x00000002
_STATUS_OBJECT_NAME_COLLISION: Final[int] = 0xC0000035
_INVALID_HANDLE_VALUE: Final[int] = int(ctypes.c_void_p(-1).value or -1)

_DIRECTORY_READ_ACCESS: Final[int] = _FILE_READ_ATTRIBUTES
_DIRECTORY_WRITE_ACCESS: Final[int] = (
    _FILE_READ_ATTRIBUTES | _FILE_ADD_FILE | _FILE_ADD_SUBDIRECTORY
)
_FILE_ACCESS: Final[int] = _GENERIC_READ | _GENERIC_WRITE | _SYNCHRONIZE
_DIRECTORY_OPEN_OPTIONS: Final[int] = _FILE_DIRECTORY_FILE | _FILE_OPEN_REPARSE_POINT
_DIRECTORY_CREATE_OPTIONS: Final[int] = _DIRECTORY_OPEN_OPTIONS | _FILE_WRITE_THROUGH
_FILE_CREATE_OPTIONS: Final[int] = (
    _FILE_NON_DIRECTORY_FILE
    | _FILE_WRITE_THROUGH
    | _FILE_SYNCHRONOUS_IO_NONALERT
    | _FILE_OPEN_REPARSE_POINT
)


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    )


class _OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    )


class _IO_STATUS_VALUE(ctypes.Union):
    _fields_ = (("Status", wintypes.LONG), ("Pointer", ctypes.c_void_p))


class _IO_STATUS_BLOCK(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = (("value", _IO_STATUS_VALUE), ("Information", ctypes.c_size_t))


class _FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = (
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    )


class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = (("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD))


class _FILE_ID_128(ctypes.Structure):
    _fields_ = (("Identifier", ctypes.c_ubyte * 16),)


class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = (
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", _FILE_ID_128),
    )


_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetFileType.restype = wintypes.DWORD
_kernel32.GetFileType.argtypes = [wintypes.HANDLE]
_kernel32.GetDriveTypeW.restype = wintypes.UINT
_kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetVolumeInformationByHandleW.restype = wintypes.BOOL
_kernel32.GetVolumeInformationByHandleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    wintypes.DWORD,
]
_kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
_kernel32.GetFileInformationByHandleEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetHandleInformation.restype = wintypes.BOOL
_kernel32.GetHandleInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.SetHandleInformation.restype = wintypes.BOOL
_kernel32.SetHandleInformation.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
]
_kernel32.DuplicateHandle.restype = wintypes.BOOL
_kernel32.DuplicateHandle.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]

_ntdll.NtCreateFile.restype = wintypes.LONG
_ntdll.NtCreateFile.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    ctypes.POINTER(_OBJECT_ATTRIBUTES),
    ctypes.POINTER(_IO_STATUS_BLOCK),
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.ULONG,
]
_ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
_ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]


@dataclass(frozen=True, slots=True)
class _NativeInfo:
    volume_serial: int
    file_id: bytes
    number_of_links: int
    size: int
    is_directory: bool
    attributes: int
    reparse_tag: int

    @property
    def identity(self) -> tuple[int, bytes]:
        return self.volume_serial, self.file_id

    @property
    def inode(self) -> int:
        return int.from_bytes(self.file_id, "little")


@dataclass(slots=True)
class _OwnedDirectoryHandle:
    relative_path: str
    component: str
    path: Path
    handle: int
    native_identity: tuple[int, bytes]
    writer_identity: tuple[int, ...]


@dataclass(slots=True)
class _OwnedFileHandle:
    relative_path: str
    component: str
    parent_relative_path: str
    path: Path
    handle: int
    native_identity: tuple[int, bytes]
    signature: tuple[int, ...]


def _handle_value(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    raw = getattr(value, "value", None)
    return int(raw) if raw is not None else 0


def _raw_close_handle_quietly(handle: int) -> None:
    """Close a handle that has not yet entered the protected retained ledger."""

    if handle:
        _kernel32.CloseHandle(wintypes.HANDLE(handle))


def _handle_flags(handle: int, context: str) -> int:
    flags = wintypes.DWORD()
    if not handle or not _kernel32.GetHandleInformation(
        wintypes.HANDLE(handle),
        ctypes.byref(flags),
    ):
        raise DurableEvidenceError(f"cannot query {context} protection: {ctypes.WinError()}")
    return int(flags.value)


def _require_protected_handle(handle: int, context: str) -> None:
    if not _handle_flags(handle, context) & _HANDLE_FLAG_PROTECT_FROM_CLOSE:
        raise DurableEvidenceError(f"{context} lost close protection")


def _protect_handle(handle: int, context: str) -> None:
    if not handle or not _kernel32.SetHandleInformation(
        wintypes.HANDLE(handle),
        _HANDLE_FLAG_PROTECT_FROM_CLOSE,
        _HANDLE_FLAG_PROTECT_FROM_CLOSE,
    ):
        error = ctypes.WinError()
        _raw_close_handle_quietly(handle)
        raise HandleAnchoredEvidenceCapabilityError(
            f"cannot protect {context} from close/reuse: {error}"
        )
    try:
        _require_protected_handle(handle, context)
    except BaseException:
        _kernel32.SetHandleInformation(
            wintypes.HANDLE(handle),
            _HANDLE_FLAG_PROTECT_FROM_CLOSE,
            0,
        )
        _raw_close_handle_quietly(handle)
        raise


def _close_handle(handle: int) -> None:
    """Release only a still-protected handle owned by this namespace."""

    if not handle:
        return
    _require_protected_handle(handle, "owned Windows handle")
    if not _kernel32.SetHandleInformation(
        wintypes.HANDLE(handle),
        _HANDLE_FLAG_PROTECT_FROM_CLOSE,
        0,
    ):
        raise DurableEvidenceError(
            f"cannot remove owned Windows handle protection: {ctypes.WinError()}"
        )
    if not _kernel32.CloseHandle(wintypes.HANDLE(handle)):
        error = ctypes.WinError()
        _kernel32.SetHandleInformation(
            wintypes.HANDLE(handle),
            _HANDLE_FLAG_PROTECT_FROM_CLOSE,
            _HANDLE_FLAG_PROTECT_FROM_CLOSE,
        )
        raise DurableEvidenceError(f"cannot close owned Windows handle: {error}")


def _close_handle_quietly(handle: int) -> None:
    try:
        _close_handle(handle)
    except BaseException:
        pass


def _raise_last_error(context: str) -> None:
    raise HandleAnchoredEvidenceCapabilityError(f"{context}: {ctypes.WinError()}")


def _raise_nt_status(status: int, context: str, *, collision: bool) -> None:
    unsigned = status & 0xFFFFFFFF
    if collision and unsigned == _STATUS_OBJECT_NAME_COLLISION:
        raise DurableEvidenceCollisionError(f"{context} was already claimed")
    error_code = int(_ntdll.RtlNtStatusToDosError(wintypes.LONG(status)))
    raise DurableEvidenceError(f"{context}: {ctypes.WinError(error_code)}")


def _native_info(handle: int, context: str) -> _NativeInfo:
    if not handle or _kernel32.GetFileType(wintypes.HANDLE(handle)) != _FILE_TYPE_DISK:
        raise HandleAnchoredEvidenceCapabilityError(f"{context} is not a disk handle")
    _require_protected_handle(handle, context)
    standard = _FILE_STANDARD_INFO()
    attributes = _FILE_ATTRIBUTE_TAG_INFO()
    identity = _FILE_ID_INFO()
    for info_class, value, label in (
        (_FILE_STANDARD_INFO_CLASS, standard, "standard"),
        (_FILE_ATTRIBUTE_TAG_INFO_CLASS, attributes, "attribute-tag"),
        (_FILE_ID_INFO_CLASS, identity, "file-id"),
    ):
        if not _kernel32.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle),
            info_class,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            raise HandleAnchoredEvidenceCapabilityError(
                f"cannot query {context} {label} information: {ctypes.WinError()}"
            )
    file_id = bytes(identity.FileId.Identifier)
    if not any(file_id):
        raise HandleAnchoredEvidenceCapabilityError(f"{context} has no stable file identity")
    if attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT or attributes.ReparseTag:
        raise HandleAnchoredEvidenceCapabilityError(f"{context} is a reparse point")
    if standard.DeletePending:
        raise DurableEvidenceError(f"{context} is pending deletion")
    return _NativeInfo(
        volume_serial=int(identity.VolumeSerialNumber),
        file_id=file_id,
        number_of_links=int(standard.NumberOfLinks),
        size=int(standard.EndOfFile),
        is_directory=bool(standard.Directory),
        attributes=int(attributes.FileAttributes),
        reparse_tag=int(attributes.ReparseTag),
    )


def _cleanup_native_identity(handle: int, context: str) -> tuple[tuple[int, bytes], bool]:
    """Identify a retained handle without rejecting mutable owned metadata."""

    if not handle or _kernel32.GetFileType(wintypes.HANDLE(handle)) != _FILE_TYPE_DISK:
        raise DurableEvidenceError(f"{context} is not a retained disk handle")
    _require_protected_handle(handle, context)
    standard = _FILE_STANDARD_INFO()
    identity = _FILE_ID_INFO()
    for info_class, value, label in (
        (_FILE_STANDARD_INFO_CLASS, standard, "standard"),
        (_FILE_ID_INFO_CLASS, identity, "file-id"),
    ):
        if not _kernel32.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle),
            info_class,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            raise DurableEvidenceError(
                f"cannot query {context} {label} information: {ctypes.WinError()}"
            )
    file_id = bytes(identity.FileId.Identifier)
    if not any(file_id):
        raise DurableEvidenceError(f"{context} has no stable file identity")
    return (int(identity.VolumeSerialNumber), file_id), bool(standard.Directory)


def _path_signature(path: Path, info: _NativeInfo, *, directory: bool) -> tuple[int, ...]:
    try:
        value = path.lstat()
    except OSError as exc:
        raise DurableEvidenceError(f"cannot inspect handle-owned path {path}: {exc}") from exc
    if _is_link_or_reparse(value):
        raise DurableEvidenceError(f"handle-owned path became a symlink or reparse point: {path}")
    if directory != stat.S_ISDIR(value.st_mode) or (
        not directory and not stat.S_ISREG(value.st_mode)
    ):
        raise DurableEvidenceError(f"handle-owned path changed type: {path}")
    if value.st_ino == 0 or value.st_ino != info.inode:
        raise DurableEvidenceError(f"handle-owned path identity differs: {path}")
    if int(getattr(value, "st_file_attributes", 0)) != info.attributes:
        raise DurableEvidenceError(f"handle-owned path attributes differ: {path}")
    if int(getattr(value, "st_reparse_tag", 0)) != info.reparse_tag:
        raise DurableEvidenceError(f"handle-owned path reparse identity differs: {path}")
    if not directory and (value.st_nlink != info.number_of_links or value.st_size != info.size):
        raise DurableEvidenceError(f"handle-owned file metadata differs: {path}")
    return _stat_signature(value)


def _validate_component(value: str, context: str) -> str:
    safe = _strict_relative_path(value, context)
    pure = PurePosixPath(safe)
    if len(pure.parts) != 1 or pure.as_posix() != value:
        raise HandleAnchoredEvidenceCapabilityError(f"{context} must be one component")
    return value


def _unicode_name(value: str) -> tuple[ctypes.Array[ctypes.c_wchar], _UNICODE_STRING]:
    if "\x00" in value:
        raise DurableEvidenceError("Windows native name contains NUL")
    encoded_length = len(value.encode("utf-16-le"))
    if encoded_length > 0xFFFC:
        raise DurableEvidenceError("Windows native name is too long")
    buffer = ctypes.create_unicode_buffer(value)
    name = _UNICODE_STRING(
        Length=encoded_length,
        MaximumLength=encoded_length + 2,
        Buffer=ctypes.cast(buffer, wintypes.LPWSTR),
    )
    return buffer, name


def _nt_relative_open(
    parent_handle: int,
    component: str,
    *,
    directory: bool,
    create: bool,
    writable_directory: bool = False,
) -> int:
    _buffer, name = _unicode_name(component)
    attributes = _OBJECT_ATTRIBUTES(
        Length=ctypes.sizeof(_OBJECT_ATTRIBUTES),
        RootDirectory=wintypes.HANDLE(parent_handle),
        ObjectName=ctypes.pointer(name),
        Attributes=_OBJ_CASE_INSENSITIVE,
        SecurityDescriptor=None,
        SecurityQualityOfService=None,
    )
    io_status = _IO_STATUS_BLOCK()
    result_handle = wintypes.HANDLE()
    if directory:
        desired_access = _DIRECTORY_WRITE_ACCESS if writable_directory else _DIRECTORY_READ_ACCESS
        options = _DIRECTORY_CREATE_OPTIONS if create else _DIRECTORY_OPEN_OPTIONS
        file_attributes = 0
        share_access = _DIRECTORY_SHARE
    else:
        desired_access = _FILE_ACCESS if create else _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        options = (
            _FILE_CREATE_OPTIONS
            if create
            else (
                _FILE_NON_DIRECTORY_FILE | _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT
            )
        )
        file_attributes = _FILE_ATTRIBUTE_NORMAL if create else 0
        share_access = _FILE_SHARE if create else _DIRECTORY_SHARE
    disposition = _FILE_CREATE if create else _FILE_OPEN
    status = int(
        _ntdll.NtCreateFile(
            ctypes.byref(result_handle),
            desired_access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            file_attributes,
            share_access,
            disposition,
            options,
            None,
            0,
        )
    )
    if status < 0:
        _raise_nt_status(status, f"Windows native child {component!r}", collision=create)
    handle = _handle_value(result_handle)
    expected_information = _FILE_CREATED if create else _FILE_OPENED
    if not handle or int(io_status.Information) != expected_information:
        _raw_close_handle_quietly(handle)
        raise DurableEvidenceError(
            f"Windows native child {component!r} returned an unexpected create result"
        )
    _protect_handle(handle, f"Windows native child {component!r}")
    return handle


def _open_existing_directory(path: Path, *, writable: bool) -> int:
    raw = _kernel32.CreateFileW(
        str(path),
        _DIRECTORY_WRITE_ACCESS if writable else _DIRECTORY_READ_ACCESS,
        _DIRECTORY_SHARE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    handle = _handle_value(raw)
    if not handle or handle == _INVALID_HANDLE_VALUE:
        _raise_last_error("cannot open existing directory without following reparse points")
    _protect_handle(handle, "existing ancestry directory handle")
    return handle


def _assert_supported_volume(path: Path, parent_handle: int) -> None:
    if _kernel32.GetDriveTypeW(path.anchor) != _DRIVE_FIXED:
        raise HandleAnchoredEvidenceCapabilityError(
            "handle-anchored writer requires a fixed local drive"
        )
    volume_name = ctypes.create_unicode_buffer(261)
    filesystem_name = ctypes.create_unicode_buffer(261)
    volume_serial = wintypes.DWORD()
    maximum_component_length = wintypes.DWORD()
    filesystem_flags = wintypes.DWORD()
    if not _kernel32.GetVolumeInformationByHandleW(
        wintypes.HANDLE(parent_handle),
        volume_name,
        len(volume_name),
        ctypes.byref(volume_serial),
        ctypes.byref(maximum_component_length),
        ctypes.byref(filesystem_flags),
        filesystem_name,
        len(filesystem_name),
    ):
        _raise_last_error("cannot query transaction parent filesystem")
    if filesystem_name.value.casefold() != "ntfs":
        raise HandleAnchoredEvidenceCapabilityError(
            "handle-anchored writer requires the reviewed NTFS capability envelope"
        )


def _duplicate_handle(handle: int) -> int:
    process = _kernel32.GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not _kernel32.DuplicateHandle(
        process,
        wintypes.HANDLE(handle),
        process,
        ctypes.byref(duplicate),
        0,
        False,
        _DUPLICATE_SAME_ACCESS,
    ):
        raise DurableEvidenceError(f"cannot duplicate owned file handle: {ctypes.WinError()}")
    result = _handle_value(duplicate)
    if not result:
        raise DurableEvidenceError("Windows duplicated an invalid file handle")
    try:
        flags = _handle_flags(result, "duplicated file handle")
        if flags & _HANDLE_FLAG_PROTECT_FROM_CLOSE and not _kernel32.SetHandleInformation(
            wintypes.HANDLE(result),
            _HANDLE_FLAG_PROTECT_FROM_CLOSE,
            0,
        ):
            raise DurableEvidenceError(
                f"cannot make duplicated file handle descriptor-closeable: {ctypes.WinError()}"
            )
    except BaseException:
        _kernel32.SetHandleInformation(
            wintypes.HANDLE(result),
            _HANDLE_FLAG_PROTECT_FROM_CLOSE,
            0,
        )
        _raw_close_handle_quietly(result)
        raise
    return result


def _descriptor_from_duplicate(handle: int) -> int:
    msvcrt = importlib.import_module("msvcrt")
    duplicate = _duplicate_handle(handle)
    try:
        descriptor = int(msvcrt.open_osfhandle(duplicate, os.O_BINARY | os.O_RDWR))
    except BaseException:
        _raw_close_handle_quietly(duplicate)
        raise
    if descriptor < 0:
        _raw_close_handle_quietly(duplicate)
        raise DurableEvidenceError("cannot convert an owned Windows handle to a descriptor")
    return descriptor


def _write_and_readback(handle: int, payload: bytes, relative_path: str) -> os.stat_result:
    descriptor = _descriptor_from_duplicate(handle)
    try:
        before = os.fstat(descriptor)
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise DurableEvidenceError(f"short handle-anchored write: {relative_path}")
            offset += written
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        readback = bytearray()
        while len(readback) <= len(payload):
            chunk = os.read(descriptor, len(payload) + 1 - len(readback))
            if not chunk:
                break
            readback.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if before.st_ino != after.st_ino or bytes(readback) != payload:
        raise DurableEvidenceError(f"handle-anchored file changed during write: {relative_path}")
    return after


def _read_from_handle(
    handle: int, max_bytes: int, relative_path: str
) -> tuple[bytes, os.stat_result]:
    descriptor = _descriptor_from_duplicate(handle)
    try:
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise DurableEvidenceError(
                    f"handle-anchored file exceeds its read limit: {relative_path}"
                )
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _cross_handle_identity(before) != _cross_handle_identity(after):
        raise DurableEvidenceError(f"handle-anchored file changed during read: {relative_path}")
    return b"".join(chunks), after


class _WindowsHandleAnchoredNamespace:
    """Fresh Windows directory tree owned through retained native handles."""

    __slots__ = (
        "_aliases",
        "_ancestors",
        "_closed",
        "_directories",
        "_files",
        "_root",
        "_root_identity",
        "_root_path",
    )

    def __init__(self, root: Path) -> None:
        self._ancestors: list[_OwnedDirectoryHandle] = []
        self._directories: dict[str, _OwnedDirectoryHandle] = {}
        self._files: dict[str, _OwnedFileHandle] = {}
        self._aliases: set[str] = set()
        self._closed = False
        self._root_path = root.absolute()
        self._root = _OwnedDirectoryHandle("", "", self._root_path, 0, (0, b""), ())
        self._root_identity: tuple[int, ...] = ()
        root_handle = 0
        try:
            parent = self._open_ancestor_chain(self._root_path.parent)
            component = _validate_component(self._root_path.name, "transaction root name")
            root_handle = _nt_relative_open(
                parent.handle,
                component,
                directory=True,
                create=True,
                writable_directory=True,
            )
            info = _native_info(root_handle, "new transaction root")
            if not info.is_directory:
                raise DurableEvidenceError("new transaction root is not a directory")
            signature = _path_signature(self._root_path, info, directory=True)
            self._root = _OwnedDirectoryHandle(
                "",
                component,
                self._root_path,
                root_handle,
                info.identity,
                _writer_directory_identity(signature),
            )
            root_handle = 0
            self._root_identity = self._root.writer_identity
            self._assert_owned_graph()
        except BaseException:
            _close_handle_quietly(root_handle)
            self._close_quietly()
            raise

    @property
    def root(self) -> Path:
        return self._root_path

    @property
    def root_identity(self) -> tuple[int, ...]:
        return self._root_identity

    def _open_ancestor_chain(self, parent: Path) -> _OwnedDirectoryHandle:
        if not parent.is_absolute() or parent.anchor.startswith("\\\\"):
            raise HandleAnchoredEvidenceCapabilityError(
                "handle-anchored writer requires a local absolute drive path"
            )
        if len(parent.drive) != 2 or parent.drive[1] != ":":
            raise HandleAnchoredEvidenceCapabilityError(
                "handle-anchored writer requires a drive-letter path"
            )
        anchor = Path(parent.anchor)
        current_path = anchor
        snapshots: list[tuple[str, Path, tuple[int, ...]]] = []
        for index, component in enumerate(parent.parts):
            if index:
                safe_component = _validate_component(
                    component,
                    "transaction parent ancestry component",
                )
                current_path /= safe_component
            else:
                safe_component = ""
            try:
                value = current_path.lstat()
            except OSError as exc:
                raise HandleAnchoredEvidenceCapabilityError(
                    f"cannot inspect transaction ancestry {current_path}: {exc}"
                ) from exc
            if _is_link_or_reparse(value) or not stat.S_ISDIR(value.st_mode):
                raise HandleAnchoredEvidenceCapabilityError(
                    f"transaction ancestry is not a no-follow directory: {current_path}"
                )
            snapshots.append(
                (
                    safe_component,
                    current_path,
                    _writer_directory_identity(_stat_signature(value)),
                )
            )

        previous: _OwnedDirectoryHandle | None = None
        for index, (component, path, expected_identity) in enumerate(snapshots):
            handle = 0
            try:
                final_parent = index == len(snapshots) - 1
                if previous is None:
                    handle = _open_existing_directory(path, writable=final_parent)
                    context = "transaction parent" if final_parent else "transaction drive root"
                else:
                    handle = _nt_relative_open(
                        previous.handle,
                        component,
                        directory=True,
                        create=False,
                        writable_directory=final_parent,
                    )
                    context = (
                        "transaction parent"
                        if final_parent
                        else f"transaction ancestry component {component!r}"
                    )
                info = _native_info(handle, context)
                if not info.is_directory:
                    raise HandleAnchoredEvidenceCapabilityError(
                        "transaction ancestry handle is not a directory"
                    )
                if previous is None:
                    _assert_supported_volume(parent, handle)
                signature = _path_signature(path, info, directory=True)
                if _writer_directory_identity(signature) != expected_identity:
                    raise HandleAnchoredEvidenceCapabilityError(
                        f"transaction ancestry changed while opening {path}"
                    )
                current = _OwnedDirectoryHandle(
                    path.relative_to(anchor).as_posix() if previous is not None else "",
                    component,
                    path,
                    handle,
                    info.identity,
                    _writer_directory_identity(signature),
                )
                self._ancestors.append(current)
                previous = current
                handle = 0
            finally:
                _close_handle_quietly(handle)
        if previous is None:
            raise HandleAnchoredEvidenceCapabilityError(
                "transaction parent ancestry produced no directory handle"
            )
        return previous

    def _require_open(self) -> None:
        if self._closed:
            raise DurableEvidenceError("handle-anchored namespace is closed")

    def _parent_for(self, pure: PurePosixPath) -> tuple[_OwnedDirectoryHandle, str]:
        parent_relative = pure.parent.as_posix()
        if parent_relative == ".":
            return self._root, ""
        parent = self._directories.get(parent_relative)
        if parent is None:
            raise DurableEvidenceError("handle-anchored file parent is not invocation-owned")
        return parent, parent_relative

    def _claim_alias(self, relative_path: str) -> None:
        alias = relative_path.casefold()
        if alias in self._aliases:
            raise DurableEvidenceCollisionError(
                f"handle-anchored path alias was already claimed: {relative_path}"
            )
        self._aliases.add(alias)

    def _validate_directory(
        self,
        directory: _OwnedDirectoryHandle,
        parent: _OwnedDirectoryHandle | None,
    ) -> None:
        retained = _native_info(directory.handle, f"owned directory {directory.relative_path!r}")
        if not retained.is_directory or retained.identity != directory.native_identity:
            raise DurableEvidenceError(
                f"owned handle-anchored directory changed: {directory.relative_path}"
            )
        signature = _path_signature(directory.path, retained, directory=True)
        if _writer_directory_identity(signature) != directory.writer_identity:
            raise DurableEvidenceError(
                f"owned handle-anchored directory path changed: {directory.relative_path}"
            )
        if parent is not None:
            reopened = _nt_relative_open(
                parent.handle,
                directory.component,
                directory=True,
                create=False,
            )
            try:
                if _native_info(reopened, "reopened owned directory").identity != (
                    directory.native_identity
                ):
                    raise DurableEvidenceError(
                        f"owned directory name was rebound: {directory.relative_path}"
                    )
            finally:
                _close_handle(reopened)

    def _validate_file(self, owned: _OwnedFileHandle) -> None:
        retained = _native_info(owned.handle, f"owned file {owned.relative_path!r}")
        if (
            retained.is_directory
            or retained.identity != owned.native_identity
            or retained.number_of_links != 1
        ):
            raise DurableEvidenceError(f"owned handle-anchored file changed: {owned.relative_path}")
        signature = _path_signature(owned.path, retained, directory=False)
        if signature != owned.signature:
            raise DurableEvidenceError(
                f"owned handle-anchored file path changed: {owned.relative_path}"
            )
        parent = (
            self._root
            if not owned.parent_relative_path
            else self._directories[owned.parent_relative_path]
        )
        reopened = _nt_relative_open(
            parent.handle,
            owned.component,
            directory=False,
            create=False,
        )
        try:
            if _native_info(reopened, "reopened owned file").identity != owned.native_identity:
                raise DurableEvidenceError(f"owned file name was rebound: {owned.relative_path}")
        finally:
            _close_handle(reopened)

    def _assert_owned_graph(self) -> None:
        self._require_open()
        previous: _OwnedDirectoryHandle | None = None
        for ancestor in self._ancestors:
            self._validate_directory(ancestor, previous)
            previous = ancestor
        parent = self._ancestors[-1]
        self._validate_directory(self._root, parent)
        for relative in sorted(self._directories, key=lambda item: (item.count("/"), item)):
            directory = self._directories[relative]
            pure = PurePosixPath(relative)
            parent_relative = pure.parent.as_posix()
            directory_parent = (
                self._root if parent_relative == "." else self._directories[parent_relative]
            )
            self._validate_directory(directory, directory_parent)
        for relative in sorted(self._files):
            self._validate_file(self._files[relative])

    def mkdir(self, relative_path: str) -> None:
        self._assert_owned_graph()
        component = _validate_component(relative_path, "handle-anchored directory")
        self._claim_alias(relative_path)
        handle = 0
        try:
            handle = _nt_relative_open(
                self._root.handle,
                component,
                directory=True,
                create=True,
                writable_directory=True,
            )
            info = _native_info(handle, f"new directory {relative_path!r}")
            if not info.is_directory:
                raise DurableEvidenceError("new handle-anchored directory changed type")
            path = self._root_path / component
            signature = _path_signature(path, info, directory=True)
            self._directories[relative_path] = _OwnedDirectoryHandle(
                relative_path,
                component,
                path,
                handle,
                info.identity,
                _writer_directory_identity(signature),
            )
            handle = 0
            self._assert_owned_graph()
        except BaseException:
            _close_handle_quietly(handle)
            raise

    def mkdir_child(self, parent: str, child: str) -> str:
        self._assert_owned_graph()
        owned_parent = self._directories.get(parent)
        if owned_parent is None:
            raise DurableEvidenceError("handle-anchored child parent is not invocation-owned")
        component = _validate_component(child, "handle-anchored child directory")
        relative = f"{parent}/{component}"
        self._claim_alias(relative)
        handle = 0
        try:
            handle = _nt_relative_open(
                owned_parent.handle,
                component,
                directory=True,
                create=True,
                writable_directory=True,
            )
            info = _native_info(handle, f"new child directory {relative!r}")
            if not info.is_directory:
                raise DurableEvidenceError("new handle-anchored child changed type")
            path = owned_parent.path / component
            signature = _path_signature(path, info, directory=True)
            self._directories[relative] = _OwnedDirectoryHandle(
                relative,
                component,
                path,
                handle,
                info.identity,
                _writer_directory_identity(signature),
            )
            handle = 0
            self._assert_owned_graph()
            return relative
        except BaseException:
            _close_handle_quietly(handle)
            raise

    def write(
        self,
        relative_path: str,
        payload: bytes,
        *,
        max_bytes: int = _MAX_MANIFEST_BYTES,
    ) -> Sha256Digest:
        if type(payload) is not bytes:
            raise TypeError("handle-anchored payload must be exact immutable bytes")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("handle-anchored write limit must be a positive exact integer")
        if len(payload) > max_bytes:
            raise DurableEvidenceError(
                f"handle-anchored payload exceeds its write limit: {relative_path}"
            )
        self._assert_owned_graph()
        safe = _strict_relative_path(relative_path, "handle-anchored file path")
        pure = PurePosixPath(safe)
        parent, parent_relative = self._parent_for(pure)
        component = _validate_component(pure.name, "handle-anchored file name")
        self._claim_alias(relative_path)
        handle = 0
        try:
            handle = _nt_relative_open(
                parent.handle,
                component,
                directory=False,
                create=True,
            )
            created = _native_info(handle, f"new file {relative_path!r}")
            if created.is_directory or created.number_of_links != 1 or created.size != 0:
                raise DurableEvidenceError("new handle-anchored file has invalid initial state")
            descriptor_stat = _write_and_readback(handle, payload, relative_path)
            after = _native_info(handle, f"written file {relative_path!r}")
            if (
                after.identity != created.identity
                or after.is_directory
                or after.number_of_links != 1
                or after.size != len(payload)
                or descriptor_stat.st_ino != after.inode
            ):
                raise DurableEvidenceError(
                    f"handle-anchored file changed during write: {relative_path}"
                )
            path = parent.path / component
            signature = _path_signature(path, after, directory=False)
            self._files[relative_path] = _OwnedFileHandle(
                relative_path,
                component,
                parent_relative,
                path,
                handle,
                after.identity,
                signature,
            )
            handle = 0
            self._assert_owned_graph()
            return Sha256Digest.from_bytes(payload)
        except BaseException:
            _close_handle_quietly(handle)
            raise

    def read_owned(self, relative_path: str, max_bytes: int) -> bytes:
        self._assert_owned_graph()
        owned = self._files.get(relative_path)
        if owned is None:
            raise DurableEvidenceError(
                f"handle-anchored file is not invocation-owned: {relative_path}"
            )
        payload, descriptor_stat = _read_from_handle(owned.handle, max_bytes, relative_path)
        info = _native_info(owned.handle, f"read file {relative_path!r}")
        if descriptor_stat.st_ino != info.inode or info.identity != owned.native_identity:
            raise DurableEvidenceError(f"handle-anchored read identity differs: {relative_path}")
        self._assert_owned_graph()
        return payload

    def assert_exact_tree(self, expected_files: set[str]) -> None:
        self._assert_owned_graph()
        _assert_exact_tree(self._root_path, expected_files)
        self._assert_owned_graph()

    def physical_identity_sha256(self, expected_files: set[str]) -> Sha256Digest:
        self._assert_owned_graph()
        initial_tree = _assert_exact_tree(self._root_path, expected_files)
        initial_root = _stat_signature(self._root_path.lstat())
        initial = _physical_identity_sha256(initial_root, initial_tree)
        self._assert_owned_graph()
        final_tree = _assert_exact_tree(self._root_path, expected_files)
        final_root = _stat_signature(self._root_path.lstat())
        if _physical_identity_sha256(final_root, final_tree) != initial:
            raise DurableEvidenceError(
                "handle-anchored physical identity changed while it was pinned"
            )
        self._assert_owned_graph()
        return initial

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        handles = [
            *(
                (owned.handle, owned.native_identity, False, owned.relative_path)
                for owned in self._files.values()
            ),
            *(
                (owned.handle, owned.native_identity, True, owned.relative_path)
                for _, owned in sorted(
                    self._directories.items(),
                    key=lambda item: (item[0].count("/"), item[0]),
                    reverse=True,
                )
            ),
            (self._root.handle, self._root.native_identity, True, "transaction root"),
            *(
                (owned.handle, owned.native_identity, True, owned.path.as_posix())
                for owned in reversed(self._ancestors)
            ),
        ]
        self._files.clear()
        self._directories.clear()
        self._ancestors.clear()
        failures = 0
        for handle, expected_identity, expected_directory, context in handles:
            if not handle:
                continue
            try:
                current_identity, current_directory = _cleanup_native_identity(
                    handle,
                    f"cleanup handle {context!r}",
                )
            except BaseException:
                failures += 1
                continue
            if current_identity != expected_identity or current_directory is not expected_directory:
                failures += 1
                continue
            try:
                _close_handle(handle)
            except BaseException:
                failures += 1
        if failures:
            raise DurableEvidenceError(f"could not close {failures} owned Windows handle(s)")

    def _close_quietly(self) -> None:
        try:
            self.close()
        except BaseException:
            pass

    def __enter__(self) -> _WindowsHandleAnchoredNamespace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self._close_quietly()

    def __del__(self) -> None:
        self._close_quietly()
