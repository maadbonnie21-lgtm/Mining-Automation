"""Exclusive cross-process lease for real camera-validation input.

The real validator is intentionally single-owner.  A Windows named mutex keeps
two independent Python processes from capturing, focusing, or sending camera
input to the same interactive desktop at the same time.  Win32 loading remains
lazy so the contract and its deterministic fakes are importable on Linux CI.
"""

from __future__ import annotations

import sys
import threading
from types import TracebackType
from typing import Any, Final, Literal, Protocol, Self

__all__ = [
    "CAMERA_INPUT_LEASE_NAME",
    "CameraInputLeaseApi",
    "CameraInputLeaseError",
    "CameraInputLeaseHeldError",
    "WindowsCameraInputLease",
]


CAMERA_INPUT_LEASE_NAME: Final[str] = (
    r"Global\MiningAutomation.VarrockEastCameraValidationInput.v1"
)
_PROCESS_LEASE_LOCK: Final = threading.Lock()


class CameraInputLeaseError(RuntimeError):
    """The camera-input lease could not be acquired, maintained, or released."""


class CameraInputLeaseHeldError(CameraInputLeaseError):
    """Another process already owns the camera-input lease."""


class CameraInputLeaseApi(Protocol):
    """Minimal named-mutex seam used by :class:`WindowsCameraInputLease`."""

    def create_named_mutex(self, name: str) -> int:
        """Create or open an unowned named mutex and return its handle."""

    def try_acquire(self, handle: int) -> bool | None:
        """Return true when acquired, false when held, or none when abandoned."""

    def release_mutex(self, handle: int) -> None:
        """Release one mutex owned by the current thread."""

    def close_handle(self, handle: int) -> None:
        """Close one process-local mutex handle."""


class _RealWindowsCameraInputLeaseApi:
    """Lazy Windows implementation that keeps Win32 imports out of Linux CI."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                "WindowsCameraInputLease requires Windows "
                "(sys.platform == 'win32'); "
                f"got {sys.platform!r}. Inject a CameraInputLeaseApi fake in tests."
            )
        from . import _camera_input_lease_win32

        self._calls: Any = _camera_input_lease_win32

    def create_named_mutex(self, name: str) -> int:
        result: int = self._calls.create_named_mutex(name)
        return result

    def try_acquire(self, handle: int) -> bool | None:
        result: bool | None = self._calls.try_acquire(handle)
        return result

    def release_mutex(self, handle: int) -> None:
        self._calls.release_mutex(handle)

    def close_handle(self, handle: int) -> None:
        self._calls.close_handle(handle)


class WindowsCameraInputLease:
    """One immediate, exclusive lease over real camera-validation input."""

    def __init__(
        self,
        *,
        api: CameraInputLeaseApi | None = None,
        name: str = CAMERA_INPUT_LEASE_NAME,
    ) -> None:
        if not name or "\x00" in name:
            raise ValueError("camera-input lease name must be non-empty and NUL-free")
        self._api = api if api is not None else _RealWindowsCameraInputLeaseApi()
        self._name = name
        self._handle: int | None = None
        self._owner_thread_id: int | None = None
        self._owns_process_slot = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        """Acquire immediately; never wait for another validator process."""

        if self._handle is not None:
            raise CameraInputLeaseError("camera-input lease is already acquired")
        if not _PROCESS_LEASE_LOCK.acquire(blocking=False):
            raise CameraInputLeaseHeldError(
                "another camera validator in this process already owns the input "
                "lease; no capture, focus, or input was attempted"
            )
        self._owns_process_slot = True
        try:
            handle = self._api.create_named_mutex(self._name)
        except Exception as exc:
            self._release_process_slot()
            raise CameraInputLeaseError(
                f"could not create camera-input lease {self._name!r}: {exc}"
            ) from exc
        except BaseException:
            self._release_process_slot()
            raise
        if handle <= 0:
            self._release_process_slot()
            raise CameraInputLeaseError("named-mutex API returned an invalid handle")
        try:
            acquired = self._api.try_acquire(handle)
        except Exception as exc:
            try:
                self._close_unacquired(handle)
            finally:
                self._release_process_slot()
            raise CameraInputLeaseError(
                f"could not acquire camera-input lease {self._name!r}: {exc}"
            ) from exc
        except BaseException:
            try:
                self._close_unacquired(handle)
            finally:
                self._release_process_slot()
            raise
        if acquired is None:
            # WAIT_ABANDONED transfers ownership to this thread. Model that as
            # a normal owned lease before attempting cleanup so ReleaseMutex
            # failure retains the handle and in-process exclusion guard.
            self._handle = handle
            self._owner_thread_id = threading.get_ident()
            try:
                self.release()
            except CameraInputLeaseError as cleanup_error:
                error = CameraInputLeaseError(
                    "the previous camera validator abandoned the process-global "
                    "input lease; global key/button state is indeterminate, so "
                    "no capture, focus, or input was attempted"
                )
                error.add_note(str(cleanup_error))
                raise error from cleanup_error
            error = CameraInputLeaseError(
                "the previous camera validator abandoned the process-global input "
                "lease; global key/button state is indeterminate, so no capture, "
                "focus, or input was attempted"
            )
            raise error
        if acquired is False:
            try:
                self._close_unacquired(handle)
            finally:
                self._release_process_slot()
            raise CameraInputLeaseHeldError(
                "another camera validator already owns the process-global input "
                f"lease {self._name!r}; no capture, focus, or input was attempted"
            )
        self._handle = handle
        self._owner_thread_id = threading.get_ident()

    def release(self) -> None:
        """Release and close the owned mutex handle exactly once."""

        handle = self._handle
        if handle is None:
            return
        if threading.get_ident() != self._owner_thread_id:
            raise CameraInputLeaseError(
                "camera-input lease release must run on its acquiring thread; "
                "the process remains poisoned against another validator"
            )
        try:
            self._api.release_mutex(handle)
        except BaseException as exc:
            # Win32 mutex ownership is thread-affine. Until ReleaseMutex is
            # proven successful, keep both the handle and process guard so a
            # second local validator cannot run beside an indeterminate owner.
            raise CameraInputLeaseError(
                f"could not release camera-input lease {self._name!r}: {exc}; "
                "the process remains poisoned against another validator"
            ) from exc
        self._handle = None
        self._owner_thread_id = None
        close_error: BaseException | None = None
        try:
            self._api.close_handle(handle)
        except BaseException as exc:
            close_error = exc
        self._release_process_slot()
        if close_error is not None:
            raise CameraInputLeaseError(
                f"released camera-input lease {self._name!r}, but could not close "
                f"its process-local handle: {close_error}"
            ) from close_error

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, traceback
        try:
            self.release()
        except CameraInputLeaseError as cleanup_error:
            if exc is not None:
                cleanup_error.add_note(
                    "camera-validation body also failed before lease release: "
                    f"{type(exc).__name__}: {exc}"
                )
                raise cleanup_error from exc
            raise
        return False

    def _close_unacquired(self, handle: int) -> None:
        try:
            self._api.close_handle(handle)
        except BaseException as exc:
            raise CameraInputLeaseError(
                f"could not close unacquired camera-input lease handle: {exc}"
            ) from exc

    def _release_process_slot(self) -> None:
        if self._owns_process_slot:
            self._owns_process_slot = False
            _PROCESS_LEASE_LOCK.release()
