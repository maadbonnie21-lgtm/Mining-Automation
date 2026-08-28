from __future__ import annotations

import threading

import pytest

from mining_automation.validation.camera_input_lease import (
    CAMERA_INPUT_LEASE_NAME,
    CameraInputLeaseError,
    CameraInputLeaseHeldError,
    WindowsCameraInputLease,
)


class _FakeMutexApi:
    def __init__(self, *, acquired: bool | None = True) -> None:
        self.acquired = acquired
        self.next_handle = 100
        self.events: list[tuple[str, object]] = []
        self.create_error: BaseException | None = None
        self.release_error: BaseException | None = None

    def create_named_mutex(self, name: str) -> int:
        if self.create_error is not None:
            raise self.create_error
        handle = self.next_handle
        self.next_handle += 1
        self.events.append(("create", name))
        return handle

    def try_acquire(self, handle: int) -> bool | None:
        self.events.append(("try_acquire", handle))
        return self.acquired

    def release_mutex(self, handle: int) -> None:
        self.events.append(("release", handle))
        if self.release_error is not None:
            raise self.release_error

    def close_handle(self, handle: int) -> None:
        self.events.append(("close", handle))


def test_named_mutex_contention_fails_immediately_and_closes_handle() -> None:
    api = _FakeMutexApi(acquired=False)
    lease = WindowsCameraInputLease(api=api)

    with pytest.raises(CameraInputLeaseHeldError, match="no capture, focus, or input"):
        lease.acquire()

    assert lease.acquired is False
    assert api.events == [
        ("create", CAMERA_INPUT_LEASE_NAME),
        ("try_acquire", 100),
        ("close", 100),
    ]


def test_second_same_process_lease_cannot_exploit_recursive_windows_mutex() -> None:
    owner_api = _FakeMutexApi()
    contender_api = _FakeMutexApi()
    owner = WindowsCameraInputLease(api=owner_api)
    contender = WindowsCameraInputLease(api=contender_api)

    with owner:
        with pytest.raises(
            CameraInputLeaseHeldError,
            match="another camera validator in this process",
        ):
            contender.acquire()
        assert contender_api.events == []
        assert owner.acquired is True

    assert owner_api.events == [
        ("create", CAMERA_INPUT_LEASE_NAME),
        ("try_acquire", 100),
        ("release", 100),
        ("close", 100),
    ]


def test_abandoned_mutex_fails_closed_and_releases_transferred_ownership() -> None:
    abandoned_api = _FakeMutexApi(acquired=None)
    lease = WindowsCameraInputLease(api=abandoned_api)

    with pytest.raises(
        CameraInputLeaseError,
        match="abandoned.*state is indeterminate.*no capture, focus, or input",
    ):
        lease.acquire()

    assert lease.acquired is False
    assert abandoned_api.events == [
        ("create", CAMERA_INPUT_LEASE_NAME),
        ("try_acquire", 100),
        ("release", 100),
        ("close", 100),
    ]
    succeeding_api = _FakeMutexApi()
    with WindowsCameraInputLease(api=succeeding_api):
        pass


def test_abandoned_mutex_release_failure_retains_process_poison() -> None:
    abandoned_api = _FakeMutexApi(acquired=None)
    abandoned_api.release_error = OSError("abandoned release failed")
    lease = WindowsCameraInputLease(api=abandoned_api)

    with pytest.raises(CameraInputLeaseError, match="abandoned"):
        lease.acquire()

    assert lease.acquired is True
    assert abandoned_api.events[-1:] == [("release", 100)]
    succeeding_api = _FakeMutexApi()
    with pytest.raises(CameraInputLeaseHeldError, match="in this process"):
        WindowsCameraInputLease(api=succeeding_api).acquire()
    assert succeeding_api.events == []

    abandoned_api.release_error = None
    lease.release()
    with WindowsCameraInputLease(api=succeeding_api):
        pass


def test_release_failure_poison_retains_handle_and_process_slot() -> None:
    failing_api = _FakeMutexApi()
    failing_api.release_error = OSError("release failed")
    lease = WindowsCameraInputLease(api=failing_api)
    lease.acquire()

    with pytest.raises(CameraInputLeaseError, match="release failed"):
        lease.release()

    assert lease.acquired is True
    assert failing_api.events[-1:] == [("release", 100)]
    succeeding_api = _FakeMutexApi()
    with pytest.raises(CameraInputLeaseHeldError, match="in this process"):
        WindowsCameraInputLease(api=succeeding_api).acquire()
    assert succeeding_api.events == []

    failing_api.release_error = None
    lease.release()
    with WindowsCameraInputLease(api=succeeding_api):
        pass


def test_release_failure_overrides_body_error_for_safety_cleanup() -> None:
    api = _FakeMutexApi()
    api.release_error = OSError("release failed after body")
    lease = WindowsCameraInputLease(api=api)

    with pytest.raises(CameraInputLeaseError, match="release failed after body") as exc:
        with lease:
            raise KeyboardInterrupt("summary interrupted")

    assert isinstance(exc.value.__cause__, KeyboardInterrupt)
    assert any(
        "body also failed" in note and "summary interrupted" in note
        for note in getattr(exc.value, "__notes__", ())
    )
    assert lease.acquired is True

    api.release_error = None
    lease.release()


def test_create_base_exception_does_not_leak_process_slot() -> None:
    interrupted_api = _FakeMutexApi()
    interrupted_api.create_error = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        WindowsCameraInputLease(api=interrupted_api).acquire()

    succeeding_api = _FakeMutexApi()
    with WindowsCameraInputLease(api=succeeding_api):
        pass


def test_release_from_another_thread_poison_retains_exclusion() -> None:
    api = _FakeMutexApi()
    lease = WindowsCameraInputLease(api=api)
    lease.acquire()
    errors: list[BaseException] = []

    def release_elsewhere() -> None:
        try:
            lease.release()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=release_elsewhere)
    worker.start()
    worker.join()

    assert len(errors) == 1
    assert isinstance(errors[0], CameraInputLeaseError)
    assert "acquiring thread" in str(errors[0])
    assert lease.acquired is True
    assert not any(event[0] in {"release", "close"} for event in api.events)

    lease.release()


def test_invalid_mutex_name_is_rejected_before_api_use() -> None:
    api = _FakeMutexApi()

    with pytest.raises(ValueError, match="NUL-free"):
        WindowsCameraInputLease(api=api, name="bad\x00name")

    assert api.events == []
