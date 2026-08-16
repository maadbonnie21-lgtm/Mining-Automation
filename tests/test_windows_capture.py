from __future__ import annotations

import pytest

from mining_automation.capture import (
    CaptureBackend,
    CaptureClosedError,
    CaptureSource,
    CaptureUnavailableError,
    InvalidFrameError,
    PixelFormat,
)
from mining_automation.capture.testing import ManualClock
from mining_automation.capture.windows import (
    DEFAULT_TITLE_SUBSTRING,
    RealWin32Api,
    Win32WindowUnavailable,
    WindowInfo,
    WindowsCaptureBackend,
    select_window,
)
from mining_automation.capture.windows.dpi import STANDARD_DPI, scale_factor_for_dpi
from mining_automation.capture.windows.geometry import client_offset_within_window
from mining_automation.capture.windows.testing import FakeWin32Api, solid_pixels
from mining_automation.capture.windows.win32_api import CapturedPixels


def window(
    hwnd: int,
    title: str = "RuneLite",
    *,
    visible: bool = True,
    minimized: bool = False,
    width: int = 800,
    height: int = 600,
    class_name: str = "SunAwtFrame",
) -> WindowInfo:
    return WindowInfo(
        hwnd=hwnd,
        title=title,
        class_name=class_name,
        is_visible=visible,
        is_minimized=minimized,
        client_width=width,
        client_height=height,
    )


def pixels(width: int = 800, height: int = 600, value: int = 0x20) -> CapturedPixels:
    return CapturedPixels(payload=solid_pixels(width, height, value), width=width, height=height)


# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


def test_backend_satisfies_capture_backend_protocol() -> None:
    backend = WindowsCaptureBackend(win32_api=FakeWin32Api())
    assert isinstance(backend, CaptureBackend)


def test_backend_is_constructible_without_touching_the_real_api() -> None:
    """Must be true on any platform, including this Linux test runner."""
    backend = WindowsCaptureBackend(win32_api=FakeWin32Api())
    assert backend.name == "windows-runelite"


def test_real_win32_api_refuses_construction_off_windows() -> None:
    with pytest.raises(RuntimeError, match="requires Windows"):
        RealWin32Api()


# ---------------------------------------------------------------------------
# window_selector — pure, no OS calls
# ---------------------------------------------------------------------------


def test_select_window_successful_discovery() -> None:
    windows = [window(1, "RuneLite - Zezima")]
    selected = select_window(windows)
    assert selected is not None and selected.hwnd == 1


def test_select_window_is_case_insensitive() -> None:
    windows = [window(1, "RUNELITE - zezima")]
    assert select_window(windows) is not None


def test_select_window_no_match_returns_none() -> None:
    windows = [window(1, "Notepad"), window(2, "Chrome")]
    assert select_window(windows) is None


def test_select_window_no_candidates_at_all() -> None:
    assert select_window([]) is None


def test_select_window_ignores_invisible_windows() -> None:
    windows = [window(1, "RuneLite", visible=False)]
    assert select_window(windows) is None


def test_select_window_ignores_non_matching_titles_even_with_partial_overlap() -> None:
    windows = [window(1, "Rune"), window(2, "Lite")]
    assert select_window(windows) is None


def test_select_window_custom_title_substring() -> None:
    windows = [window(1, "My Custom Client Name")]
    assert select_window(windows, title_substring="custom client") is not None
    assert select_window(windows, title_substring="runelite") is None


# -- multiple candidates: deterministic tie-break ----------------------------


def test_multiple_candidates_prefers_non_minimized() -> None:
    windows = [
        window(1, "RuneLite", minimized=True, width=800, height=600),
        window(2, "RuneLite", minimized=False, width=400, height=300),
    ]
    selected = select_window(windows)
    assert selected is not None and selected.hwnd == 2


def test_multiple_candidates_prefers_larger_client_area() -> None:
    windows = [
        window(1, "RuneLite", width=400, height=300),
        window(2, "RuneLite", width=800, height=600),
    ]
    selected = select_window(windows)
    assert selected is not None and selected.hwnd == 2


def test_multiple_candidates_tie_breaks_on_lowest_hwnd() -> None:
    windows = [
        window(99, "RuneLite", width=800, height=600),
        window(2, "RuneLite", width=800, height=600),
    ]
    selected = select_window(windows)
    assert selected is not None and selected.hwnd == 2


def test_selection_is_deterministic_across_repeated_calls() -> None:
    windows = [
        window(5, "RuneLite - A", width=800, height=600),
        window(3, "RuneLite - B", width=800, height=600),
        window(7, "RuneLite - C", minimized=True, width=1200, height=900),
    ]
    results = {select_window(list(windows)) for _ in range(20)}
    assert len(results) == 1


def test_all_candidates_minimized_still_selects_one() -> None:
    """A session starting with RuneLite minimized should resolve to it, not
    report 'no window found' -- the backend needs the distinct minimized
    signal, not a misleading absence."""
    windows = [window(1, "RuneLite", minimized=True, width=0, height=0)]
    selected = select_window(windows)
    assert selected is not None and selected.hwnd == 1


def test_visible_non_minimized_window_with_zero_size_is_excluded() -> None:
    degenerate = window(1, "RuneLite", minimized=False, width=0, height=0)
    healthy = window(2, "RuneLite", minimized=False, width=800, height=600)
    selected = select_window([degenerate, healthy])
    assert selected is not None and selected.hwnd == 2


def test_default_title_substring_is_runelite() -> None:
    assert DEFAULT_TITLE_SUBSTRING == "runelite"


# ---------------------------------------------------------------------------
# dpi — pure arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dpi,expected",
    [(96, 1.0), (144, 1.5), (192, 2.0), (120, 1.25), (STANDARD_DPI, 1.0)],
)
def test_scale_factor_for_dpi(dpi: int, expected: float) -> None:
    assert scale_factor_for_dpi(dpi) == pytest.approx(expected)


@pytest.mark.parametrize("dpi", [0, -1, -96])
def test_scale_factor_rejects_non_positive_dpi(dpi: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        scale_factor_for_dpi(dpi)


# ---------------------------------------------------------------------------
# client-area crop offset — pure arithmetic
# ---------------------------------------------------------------------------


def test_client_offset_within_window_no_decoration() -> None:
    # Client area starts exactly at the window origin (no border/title bar).
    offset = client_offset_within_window((100, 100, 900, 700), (100, 100))
    assert offset == (0, 0)


def test_client_offset_within_window_with_decoration() -> None:
    # Typical decorated window: a border plus a title bar.
    offset = client_offset_within_window((100, 100, 900, 700), (108, 131))
    assert offset == (8, 31)


def test_client_offset_within_window_negative_desktop_origin() -> None:
    # A window on a monitor left of / above the primary display.
    offset = client_offset_within_window((-500, -300, 100, 200), (-492, -269))
    assert offset == (8, 31)


# ---------------------------------------------------------------------------
# backend: successful discovery + capture
# ---------------------------------------------------------------------------


def test_grab_discovers_and_captures() -> None:
    api = FakeWin32Api(
        windows=[window(42, "RuneLite")],
        captures={42: pixels(800, 600, 0x11)},
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    frame = backend.grab()

    assert (frame.width, frame.height) == (800, 600)
    assert frame.pixel_format is PixelFormat.BGRA8888
    assert set(frame.payload) == {0x11}
    assert api.enumerate_calls == 1
    assert api.capture_calls == [42]


def test_grab_produces_a_valid_frame_through_the_full_capture_source() -> None:
    """Integration check: the backend composes with the Issue #1 CaptureSource
    with no special-casing needed on either side."""
    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: pixels(640, 480)})
    with CaptureSource(WindowsCaptureBackend(win32_api=api), clock=ManualClock()) as source:
        frame = source.capture()
    assert frame.frame_id == 1
    assert (frame.width, frame.height) == (640, 480)


def test_open_declares_dpi_awareness_exactly_once() -> None:
    api = FakeWin32Api(windows=[window(1)], captures={1: pixels()})
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    backend.open()  # idempotent
    backend.grab()
    backend.grab()
    assert api.dpi_awareness_declared == 1


def test_hwnd_is_cached_across_successful_captures() -> None:
    api = FakeWin32Api(windows=[window(7, "RuneLite")], captures={7: pixels()})
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    backend.grab()
    backend.grab()
    backend.grab()
    assert api.enumerate_calls == 1, "a cached handle must not re-trigger discovery"
    assert api.capture_calls == [7, 7, 7]


# ---------------------------------------------------------------------------
# backend: no matching window
# ---------------------------------------------------------------------------


def test_grab_raises_capture_unavailable_when_no_window_matches() -> None:
    api = FakeWin32Api(windows=[window(1, "Notepad")])
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    with pytest.raises(CaptureUnavailableError, match="no window found"):
        backend.grab()


def test_no_match_does_not_attempt_a_capture_call() -> None:
    api = FakeWin32Api(windows=[])
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    with pytest.raises(CaptureUnavailableError):
        backend.grab()
    assert api.capture_calls == []


def test_custom_title_substring_used_for_discovery() -> None:
    api = FakeWin32Api(
        windows=[window(1, "My Client")], captures={1: pixels()}
    )
    backend = WindowsCaptureBackend(win32_api=api, title_substring="my client")
    backend.open()
    frame = backend.grab()
    assert frame.width == 800


# ---------------------------------------------------------------------------
# backend: multiple candidates end to end
# ---------------------------------------------------------------------------


def test_grab_selects_deterministically_among_multiple_windows() -> None:
    api = FakeWin32Api(
        windows=[
            window(9, "RuneLite", minimized=True, width=800, height=600),
            window(3, "RuneLite", width=1024, height=768),
        ],
        captures={3: pixels(1024, 768)},
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    frame = backend.grab()
    assert (frame.width, frame.height) == (1024, 768)
    assert api.capture_calls == [3]


# ---------------------------------------------------------------------------
# backend: minimized / unavailable
# ---------------------------------------------------------------------------


def test_grab_raises_capture_unavailable_when_minimized() -> None:
    api = FakeWin32Api(
        windows=[window(1, "RuneLite", minimized=True, width=0, height=0)],
        captures={1: Win32WindowUnavailable("window is minimized")},
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    with pytest.raises(CaptureUnavailableError, match="minimized"):
        backend.grab()


def test_unavailable_failure_invalidates_the_cached_handle() -> None:
    api = FakeWin32Api(
        windows=[window(1, "RuneLite")],
        captures={1: Win32WindowUnavailable("window is minimized")},
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    with pytest.raises(CaptureUnavailableError):
        backend.grab()
    with pytest.raises(CaptureUnavailableError):
        backend.grab()
    assert api.enumerate_calls == 2, "a failed capture must force re-discovery next time"


def test_window_closed_between_discovery_and_capture() -> None:
    api = FakeWin32Api(
        windows=[window(1, "RuneLite")],
        captures={1: Win32WindowUnavailable("window no longer exists")},
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    with pytest.raises(CaptureUnavailableError, match="no longer exists"):
        backend.grab()


def test_recovers_once_the_window_becomes_available_again() -> None:
    api = FakeWin32Api(
        windows=[window(1, "RuneLite")],
        captures={1: Win32WindowUnavailable("window is minimized")},
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    with pytest.raises(CaptureUnavailableError):
        backend.grab()

    api.captures[1] = pixels()
    frame = backend.grab()
    assert frame.width == 800


def test_grab_before_open_is_unavailable_not_a_crash() -> None:
    backend = WindowsCaptureBackend(win32_api=FakeWin32Api())
    with pytest.raises(CaptureUnavailableError, match="not open"):
        backend.grab()


# ---------------------------------------------------------------------------
# backend: resize / move between captures
# ---------------------------------------------------------------------------


def test_resize_between_captures_is_reflected_without_reconfiguration() -> None:
    sizes = iter([(800, 600), (1024, 768), (640, 480)])

    def next_capture() -> CapturedPixels:
        w, h = next(sizes)
        return pixels(w, h)

    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: next_capture})
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()

    first = backend.grab()
    second = backend.grab()
    third = backend.grab()

    assert (first.width, first.height) == (800, 600)
    assert (second.width, second.height) == (1024, 768)
    assert (third.width, third.height) == (640, 480)
    assert api.enumerate_calls == 1, "resizing must not force re-discovery"


def test_move_between_captures_does_not_affect_the_cached_handle() -> None:
    # A move changes on-screen position, not the hwnd or client dimensions;
    # the fake models this by simply returning the same pixels repeatedly for
    # the same hwnd regardless of "position".
    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: pixels(800, 600)})
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    backend.grab()
    backend.grab()
    assert api.enumerate_calls == 1


# ---------------------------------------------------------------------------
# backend: invalid dimensions/payload propagation
# ---------------------------------------------------------------------------


def test_undersized_payload_propagates_as_invalid_frame_error() -> None:
    """The backend must not swallow or re-validate this itself -- Frame.from_raw
    (Issue #1) is the single source of truth for payload validation."""
    broken = CapturedPixels(payload=b"\x00" * 10, width=800, height=600)
    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: broken})
    with CaptureSource(WindowsCaptureBackend(win32_api=api), clock=ManualClock()) as source:
        with pytest.raises(InvalidFrameError):
            source.capture()


def test_zero_dimension_capture_propagates_as_invalid_frame_error() -> None:
    broken = CapturedPixels(payload=b"", width=0, height=0)
    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: broken})
    with CaptureSource(WindowsCaptureBackend(win32_api=api), clock=ManualClock()) as source:
        with pytest.raises(InvalidFrameError):
            source.capture()


# ---------------------------------------------------------------------------
# backend: resource open/close behaviour
# ---------------------------------------------------------------------------


def test_open_and_close_are_idempotent() -> None:
    api = FakeWin32Api(windows=[window(1)], captures={1: pixels()})
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    backend.open()
    backend.close()
    backend.close()
    assert api.dpi_awareness_declared == 1


def test_close_clears_the_cached_window() -> None:
    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: pixels()})
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    backend.grab()
    assert api.enumerate_calls == 1

    backend.close()
    backend.open()
    backend.grab()
    assert api.enumerate_calls == 2, "reopening must not trust a handle cached before close"


def test_close_without_open_does_not_raise() -> None:
    WindowsCaptureBackend(win32_api=FakeWin32Api()).close()


def test_capture_source_governs_open_close_through_the_backend() -> None:
    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: pixels()})
    source = CaptureSource(WindowsCaptureBackend(win32_api=api), clock=ManualClock())
    source.open()
    source.capture()
    source.close()
    with pytest.raises(CaptureClosedError):
        source.capture()


# ---------------------------------------------------------------------------
# backend: exceptions mapped into the existing capture taxonomy
# ---------------------------------------------------------------------------


def test_win32_window_unavailable_maps_to_capture_unavailable_error() -> None:
    api = FakeWin32Api(
        windows=[window(1, "RuneLite")],
        captures={1: Win32WindowUnavailable("simulated failure")},
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    with pytest.raises(CaptureUnavailableError):
        backend.grab()


def test_unexpected_api_exception_is_normalised_by_capture_source() -> None:
    """An exception the backend does not recognise is not this module's job to
    handle -- CaptureSource's existing normalisation (Issue #1) already wraps
    it into CaptureBackendError. Proven here as an integration guarantee."""
    from mining_automation.capture import CaptureBackendError

    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: OSError("gdi explosion")})
    with CaptureSource(WindowsCaptureBackend(win32_api=api), clock=ManualClock()) as source:
        with pytest.raises(CaptureBackendError) as info:
            source.capture()
    assert isinstance(info.value.__cause__, OSError)


def test_capture_failures_are_capped_by_the_existing_threshold_policy() -> None:
    """Another Issue #1 guarantee exercised end to end with a real backend
    rather than the fake capture backend it was originally proven with."""
    from mining_automation.capture import CaptureFailureThresholdExceeded

    api = FakeWin32Api(windows=[])  # never matches -> always CaptureUnavailableError
    source = CaptureSource(
        WindowsCaptureBackend(win32_api=api), clock=ManualClock(), max_consecutive_failures=3
    )
    source.open()
    for _ in range(2):
        with pytest.raises(CaptureUnavailableError):
            source.capture()
    with pytest.raises(CaptureFailureThresholdExceeded):
        source.capture()


# ---------------------------------------------------------------------------
# coverage completion: FakeWin32Api's own branches, selector edge case
# ---------------------------------------------------------------------------


def test_fake_get_dpi_for_window_default_and_scripted() -> None:
    api = FakeWin32Api(dpi_by_hwnd={5: 144})
    assert api.get_dpi_for_window(5) == 144
    assert api.get_dpi_for_window(999) == 96


def test_fake_capture_client_area_rejects_an_unscripted_entry_type() -> None:
    api = FakeWin32Api(windows=[window(1)], captures={1: "not a valid entry"})
    with pytest.raises(TypeError, match="unexpected scripted capture entry"):
        api.capture_client_area(1)


def test_matches_rejects_empty_title_substring() -> None:
    from mining_automation.capture.windows.window_selector import matches

    assert matches(window(1, "RuneLite"), "") is False


# ---------------------------------------------------------------------------
# RealWin32Api delegation, tested with the platform guard bypassed
#
# These do not exercise a real Windows API call -- they cannot, on this
# runner -- but they do prove RealWin32Api's own wrapper/delegation code
# forwards arguments and return values correctly to whatever `_win32_calls`
# resolves to, independent of that module's actual OS behaviour.
# ---------------------------------------------------------------------------


class _StubCalls:
    def __init__(self) -> None:
        self.declare_calls = 0

    def declare_dpi_awareness(self) -> None:
        self.declare_calls += 1

    def enumerate_windows(self) -> list[WindowInfo]:
        return [window(1, "Stub")]

    def get_dpi_for_window(self, hwnd: int) -> int:
        return 120

    def capture_client_area(self, hwnd: int) -> CapturedPixels:
        return pixels(1, 1)


def _make_real_api_with_stubbed_calls(monkeypatch: pytest.MonkeyPatch) -> RealWin32Api:
    import sys
    import types

    stub = _StubCalls()
    fake_module = types.ModuleType("mining_automation.capture.windows._win32_calls")
    fake_module.declare_dpi_awareness = stub.declare_dpi_awareness  # type: ignore[attr-defined]
    fake_module.enumerate_windows = stub.enumerate_windows  # type: ignore[attr-defined]
    fake_module.get_dpi_for_window = stub.get_dpi_for_window  # type: ignore[attr-defined]
    fake_module.capture_client_area = stub.capture_client_area  # type: ignore[attr-defined]

    # The real import path (ctypes.WinDLL(...)) genuinely cannot run on this
    # platform, so the module is pre-seeded in sys.modules: `from . import
    # _win32_calls` inside RealWin32Api.__init__ finds it already cached and
    # never attempts the real import.
    monkeypatch.setitem(
        sys.modules, "mining_automation.capture.windows._win32_calls", fake_module
    )
    monkeypatch.setattr(sys, "platform", "win32")
    api = RealWin32Api()
    api._stub = stub  # type: ignore[attr-defined]
    return api


def test_real_win32_api_delegates_declare_dpi_awareness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _make_real_api_with_stubbed_calls(monkeypatch)
    api.declare_dpi_awareness()
    assert api._stub.declare_calls == 1  # type: ignore[attr-defined]


def test_real_win32_api_delegates_enumerate_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_real_api_with_stubbed_calls(monkeypatch)
    result = api.enumerate_windows()
    assert result == [window(1, "Stub")]


def test_real_win32_api_delegates_get_dpi_for_window(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_real_api_with_stubbed_calls(monkeypatch)
    assert api.get_dpi_for_window(1) == 120


def test_real_win32_api_delegates_capture_client_area(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_real_api_with_stubbed_calls(monkeypatch)
    result = api.capture_client_area(1)
    assert (result.width, result.height) == (1, 1)


def test_fake_capture_client_area_without_a_scripted_entry_is_unavailable() -> None:
    api = FakeWin32Api(windows=[window(1)])  # no captures dict entry for hwnd 1
    with pytest.raises(Win32WindowUnavailable, match="no scripted capture"):
        api.capture_client_area(1)


# ---------------------------------------------------------------------------
# selected_window: exposes window identity for diagnostics/tooling
# ---------------------------------------------------------------------------


def test_selected_window_is_none_before_any_capture() -> None:
    backend = WindowsCaptureBackend(win32_api=FakeWin32Api())
    assert backend.selected_window is None


def test_selected_window_reports_title_after_successful_discovery() -> None:
    api = FakeWin32Api(windows=[window(1, "RuneLite - Zezima")], captures={1: pixels()})
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    backend.grab()
    selected = backend.selected_window
    assert selected is not None
    assert selected.title == "RuneLite - Zezima"
    assert selected.hwnd == 1


def test_selected_window_clears_on_capture_failure() -> None:
    api = FakeWin32Api(
        windows=[window(1, "RuneLite")],
        captures={1: Win32WindowUnavailable("window is minimized")},
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    with pytest.raises(CaptureUnavailableError):
        backend.grab()
    assert backend.selected_window is None, "a failed capture must not report a stale window"


def test_selected_window_survives_close_only_after_a_reopen_resolves_it_again() -> None:
    api = FakeWin32Api(windows=[window(1, "RuneLite")], captures={1: pixels()})
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    backend.grab()
    assert backend.selected_window is not None

    backend.close()
    assert backend.selected_window is None


# ---------------------------------------------------------------------------
# bmp: diagnostic frame encoder (stdlib only, no image dependency)
# ---------------------------------------------------------------------------


def test_write_bgra_bmp_round_trips_headers_and_pixels(tmp_path) -> None:
    import struct

    from mining_automation.capture.windows.bmp import write_bgra_bmp

    payload = solid_pixels(3, 2, 0x7F)
    out = tmp_path / "frame.bmp"
    write_bgra_bmp(out, width=3, height=2, bgra_payload=payload)

    data = out.read_bytes()
    sig, filesize, _, _, offset = struct.unpack("<2sIHHI", data[:14])
    assert sig == b"BM"
    assert filesize == len(data)
    assert offset == 54

    hdrsize, w, h, planes, bpp, comp, imgsize, *_ = struct.unpack("<IiiHHIIiiII", data[14:54])
    assert (hdrsize, w, h, planes, bpp, comp) == (40, 3, -2, 1, 32, 0)
    assert imgsize == len(payload)
    assert data[54:] == payload


def test_write_bgra_bmp_rejects_a_mismatched_payload_size(tmp_path) -> None:
    from mining_automation.capture.windows.bmp import write_bgra_bmp

    with pytest.raises(ValueError, match="payload size"):
        write_bgra_bmp(tmp_path / "bad.bmp", width=4, height=4, bgra_payload=b"\x00" * 10)


def test_write_bgra_bmp_produces_correct_total_size_for_a_real_frame(tmp_path) -> None:
    from mining_automation.capture.windows.bmp import write_bgra_bmp

    payload = solid_pixels(64, 48, 0x10)
    out = tmp_path / "frame.bmp"
    write_bgra_bmp(out, width=64, height=48, bgra_payload=payload)
    assert out.stat().st_size == 14 + 40 + len(payload)


# ---------------------------------------------------------------------------
# current_dpi
# ---------------------------------------------------------------------------


def test_current_dpi_is_none_before_any_capture() -> None:
    backend = WindowsCaptureBackend(win32_api=FakeWin32Api())
    assert backend.current_dpi is None


def test_current_dpi_reports_the_selected_windows_dpi() -> None:
    api = FakeWin32Api(
        windows=[window(1, "RuneLite")], captures={1: pixels()}, dpi_by_hwnd={1: 144}
    )
    backend = WindowsCaptureBackend(win32_api=api)
    backend.open()
    backend.grab()
    assert backend.current_dpi == 144
