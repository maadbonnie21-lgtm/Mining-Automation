"""Nonactivating status panel placement. Never resizes, moves or overlays RuneLite."""

from __future__ import annotations

import ctypes
import sys
from typing import Any


def place_panel(
    work: tuple[int, int, int, int],
    game: tuple[int, int, int, int],
    width: int,
    height: int,
    gap: int = 12,
) -> tuple[int, int]:
    left, top, right, bottom = work
    gx1, gy1, gx2, gy2 = game
    candidates = (
        (gx2 + gap, top + gap),
        (gx1 - width - gap, top + gap),
        (left + gap, gy2 + gap),
        (left + gap, gy1 - height - gap),
    )
    for x, y in candidates:
        if (
            left <= x
            and top <= y
            and x + width <= right
            and y + height <= bottom
            and (
                x + width <= gx1 - gap
                or x >= gx2 + gap
                or y + height <= gy1 - gap
                or y >= gy2 + gap
            )
        ):
            return x, y
    raise RuntimeError(
        "No unobscured space for the status panel beside this RuneLite window. No game input started."
    )


class StatusPanel:
    widget: Any
    hwnd: int
    user32: Any

    def __init__(
        self,
        tk: Any,
        ttk: Any,
        parent: Any,
        game_hwnd: int,
        variables: tuple[Any, Any, Any, Any],
        stop: Any,
        emergency: Any,
    ) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The native beta status panel requires Windows")
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("monitor", wintypes.RECT),
                ("work", wintypes.RECT),
                ("flags", wintypes.DWORD),
            ]

        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        long_type = ctypes.c_ssize_t
        get_style = user32.GetWindowLongPtrW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = long_type
        set_style = user32.SetWindowLongPtrW
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, long_type]
        set_style.restype = long_type
        self.widget = tk.Toplevel(parent)
        self.widget.withdraw()
        self.widget.overrideredirect(True)
        try:
            ttk.Label(
                self.widget, text="Mining Automation â€¢ Beta", font=("Segoe UI", 12, "bold")
            ).pack(anchor="w", padx=12, pady=10)
            for variable in variables:
                ttk.Label(self.widget, textvariable=variable, wraplength=310).pack(
                    anchor="w", fill="x", padx=12, pady=6
                )
            ttk.Label(
                self.widget, text="F8: finish cycle / return â€¢ F9: emergency", wraplength=310
            ).pack(anchor="w", padx=12, pady=6)
            row = ttk.Frame(self.widget)
            row.pack(fill="x", padx=12, pady=10)
            ttk.Button(row, text="Stop / return", command=stop).pack(side="left")
            ttk.Button(row, text="Emergency Stop", command=emergency).pack(side="right")
            self.widget.update_idletasks()
            self.hwnd = int(user32.GetAncestor(self.widget.winfo_id(), 2))
            self.user32 = user32
            game, info = wintypes.RECT(), MonitorInfo()
            info.size = ctypes.sizeof(info)
            monitor = user32.MonitorFromWindow(game_hwnd, 2)
            if not user32.GetWindowRect(
                game_hwnd, ctypes.byref(game)
            ) or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                raise RuntimeError("Cannot verify an unobscured panel placement")
            width = max(340, self.widget.winfo_reqwidth())
            height = max(340, self.widget.winfo_reqheight())
            x, y = place_panel(
                (info.work.left, info.work.top, info.work.right, info.work.bottom),
                (game.left, game.top, game.right, game.bottom),
                width,
                height,
            )
            style = get_style(self.hwnd, -20)
            ctypes.set_last_error(0)
            set_style(self.hwnd, -20, style | 0x08000000 | 0x00000080)  # NOACTIVATE | TOOLWINDOW
            if ctypes.get_last_error():
                raise OSError(ctypes.get_last_error(), "Cannot make status panel nonactivating")
            # Native SW_SHOWNOACTIVATE avoids asking Tk to raise/focus the root while running.
            if not user32.SetWindowPos(
                self.hwnd, ctypes.c_void_p(-1), x, y, width, height, 0x0010 | 0x0020
            ):
                raise RuntimeError("Status panel placement failed")
            user32.ShowWindow(self.hwnd, 4)
        except Exception:
            self.widget.destroy()
            raise

    def close(self) -> None:
        self.widget.destroy()
