"""Windows-only smoke coverage for the real ctypes DLL boundary."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="real ctypes smoke test requires Windows",
)


def test_real_win32_layer_imports_and_enumerates() -> None:
    """Exercise DLL loading only; RuneLite pixel validation remains local."""
    from mining_automation.capture.windows import RealWin32Api

    api = RealWin32Api()
    api.declare_dpi_awareness()
    windows = api.enumerate_windows()
    assert isinstance(windows, list)
