from __future__ import annotations

import sys
from dataclasses import dataclass, field

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 focus seam")


@dataclass
class _FakeUser32:
    foreground: list[int]
    attach_calls: list[tuple[int, int, bool]] = field(default_factory=list)
    bring_calls: list[int] = field(default_factory=list)

    def AttachThreadInput(self, source: int, target: int, attach: bool) -> bool:
        self.attach_calls.append((source, target, bool(attach)))
        return True

    def BringWindowToTop(self, hwnd: int) -> bool:
        self.bring_calls.append(hwnd)
        return True

    def SetForegroundWindow(self, hwnd: int) -> bool:
        self.foreground[0] = hwnd
        return True


class _FakeKernel32:
    def GetCurrentThreadId(self) -> int:
        return 100


def test_focus_attaches_foreground_and_target_then_detaches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mining_automation.validation import _camera_win32_calls as win32

    foreground = [99]
    user32 = _FakeUser32(foreground)
    monkeypatch.setattr(win32, "_user32", user32)
    monkeypatch.setattr(win32, "_kernel32", _FakeKernel32())
    monkeypatch.setattr(win32, "is_window", lambda hwnd: hwnd == 42)
    monkeypatch.setattr(win32, "foreground_window", lambda: foreground[0])
    monkeypatch.setattr(
        win32,
        "_window_owner",
        lambda hwnd: (1000 + hwnd, 200 if hwnd == 42 else 300),
    )

    assert win32.focus_window(42) is True
    assert foreground[0] == 42
    assert user32.bring_calls == [42]
    assert user32.attach_calls == [
        (100, 300, True),
        (100, 200, True),
        (100, 200, False),
        (100, 300, False),
    ]


@dataclass
class _FailingUser32(_FakeUser32):
    def SetForegroundWindow(self, hwnd: int) -> bool:
        del hwnd
        return False


def test_focus_failure_still_detaches_every_attached_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mining_automation.validation import _camera_win32_calls as win32

    foreground = [99]
    user32 = _FailingUser32(foreground)
    monkeypatch.setattr(win32, "_user32", user32)
    monkeypatch.setattr(win32, "_kernel32", _FakeKernel32())
    monkeypatch.setattr(win32, "is_window", lambda hwnd: hwnd == 42)
    monkeypatch.setattr(win32, "foreground_window", lambda: foreground[0])
    monkeypatch.setattr(
        win32,
        "_window_owner",
        lambda hwnd: (1000 + hwnd, 200 if hwnd == 42 else 300),
    )

    assert win32.focus_window(42) is False
    assert user32.attach_calls[-2:] == [(100, 200, False), (100, 300, False)]


def test_client_origin_uses_already_physical_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    from mining_automation.validation import _camera_win32_calls as win32
    from mining_automation.validation.camera_coordinates import PhysicalScreenPoint

    class Transform:
        def physical_client_origin(self, hwnd: int) -> PhysicalScreenPoint:
            assert hwnd == 42
            return PhysicalScreenPoint(458, 0)

    monkeypatch.setattr(win32, "_coordinate_transform", Transform())
    monkeypatch.setattr(win32, "physical_screen_to_physical_client", lambda h, x, y: (0, 0))
    monkeypatch.setattr(
        win32, "pointer_mapping", lambda *args: (_ for _ in ()).throw(AssertionError("not used"))
    )
    assert win32.client_to_screen(42, 0, 0) == (458, 0)


def test_client_origin_rejects_non_exact_reverse(monkeypatch: pytest.MonkeyPatch) -> None:
    from mining_automation.validation import _camera_win32_calls as win32
    from mining_automation.validation.camera_coordinates import PhysicalScreenPoint

    class Transform:
        def physical_client_origin(self, hwnd: int) -> PhysicalScreenPoint:
            return PhysicalScreenPoint(458, 0)

    monkeypatch.setattr(win32, "_coordinate_transform", Transform())
    monkeypatch.setattr(win32, "physical_screen_to_physical_client", lambda h, x, y: (1, 0))
    with pytest.raises(OSError, match="origin reverse mapping"):
        win32.client_to_screen(42, 0, 0)
