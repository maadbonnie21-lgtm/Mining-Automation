import sys
from pathlib import Path
from types import SimpleNamespace

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import run_mining_to_full as mining  # noqa: E402


class _Api:
    def __init__(self) -> None:
        self.foreground = 99
        self.focus_calls: list[int] = []

    def foreground_window(self) -> int:
        return self.foreground

    def focus_window(self, hwnd: int) -> bool:
        self.focus_calls.append(hwnd)
        self.foreground = hwnd
        return True


class _Device:
    def verify_target_window(self, title: str):
        assert title == "RuneLite - Chief Luma"
        return SimpleNamespace(
            hwnd=42, client_width=1005, client_height=1078,
            dpi=96, is_visible=True, is_minimized=False,
        )


class _CaptureBackend:
    def __init__(self, *, title_substring: str) -> None:
        assert title_substring == "RuneLite - Chief Luma"


def test_verify_window_refocuses_exact_runelite(monkeypatch) -> None:
    monkeypatch.setattr(mining, "RealWin32MiningInputDevice", lambda: _Device())
    monkeypatch.setattr(mining, "RealWindowsCameraApi", lambda: _Api())
    monkeypatch.setattr(mining, "WindowsCaptureBackend", _CaptureBackend)
    monkeypatch.setattr(mining.time, "sleep", lambda _: None)
    backend = mining.WindowsMiningToFullBackend(
        expected_hwnd=42,
        output=Path("unused"),
        session_id="focus-recovery",
        title_substring="RuneLite - Chief Luma",
        neutral_settle_s=0.0,
        hover_settle_s=0.0,
        passive_interval_s=0.0,
    )
    api = _Api()
    backend.api = api
    assert api.foreground == 99
    _, snapshot = backend._verify_window()
    assert api.focus_calls == [42]
    assert snapshot.foreground_hwnd == 42
