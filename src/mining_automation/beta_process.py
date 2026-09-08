"""Exclusive launcher lease and kill-on-close ownership of beta phase children."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO, cast


class InstanceLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise RuntimeError("Another beta launcher already owns this desktop session") from exc
        self.stream = stream

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        # Do not unlink the locked inode: another launcher may already be waiting on it.


class ChildJob:
    """All child descendants die if the launcher exits. A gate blocks pre-assignment input."""

    def __init__(self) -> None:
        self.handle: Any = None
        self.child: subprocess.Popen[Any] | None = None
        if sys.platform == "win32":
            from ctypes import wintypes

            class Basic(ctypes.Structure):
                _fields_ = [
                    ("process_time", ctypes.c_longlong),
                    ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD),
                    ("min_working", ctypes.c_size_t),
                    ("max_working", ctypes.c_size_t),
                    ("active_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD),
                ]

            class IO(ctypes.Structure):
                _fields_ = [
                    (name, ctypes.c_ulonglong)
                    for name in (
                        "read_ops",
                        "write_ops",
                        "other_ops",
                        "read_bytes",
                        "write_bytes",
                        "other_bytes",
                    )
                ]

            class Extended(ctypes.Structure):
                _fields_ = [
                    ("basic", Basic),
                    ("io", IO),
                    ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t),
                ]

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            self.kernel.SetInformationJobObject.restype = wintypes.BOOL
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
            self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.kernel.TerminateJobObject.restype = wintypes.BOOL
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
            info = Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                self.close()
                raise OSError(ctypes.get_last_error(), "Job kill-on-close setup failed")

    def assign(self, child: subprocess.Popen[Any]) -> None:
        self.child = child
        if self.handle and not self.kernel.AssignProcessToJobObject(
            self.handle, int(cast(Any, child)._handle)
        ):
            child.kill()
            child.wait(timeout=5)
            raise OSError(
                ctypes.get_last_error(), "Cannot own child process tree; no input authorized"
            )

    def terminate(self) -> None:
        if self.handle:
            if not self.kernel.TerminateJobObject(self.handle, 2):
                raise OSError(ctypes.get_last_error(), "Cannot terminate owned job")
        elif self.child is not None and self.child.poll() is None:
            self.child.kill()
        if self.child is not None:
            self.child.wait(timeout=5)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def wait_for_parent_gate(gate: Path, cancel: Path, timeout_s: float = 10) -> None:
    end = time.monotonic() + timeout_s
    while not gate.is_file():
        if cancel.exists() or time.monotonic() >= end:
            raise RuntimeError("parent_input_ownership_not_confirmed")
        time.sleep(0.02)
    if cancel.exists():
        raise RuntimeError("session_cancelled_before_child_entry")
