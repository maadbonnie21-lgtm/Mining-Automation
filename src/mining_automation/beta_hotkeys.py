"""GUI-owned hotkey thread. Tk cannot consume this thread's F8/F9 messages."""

from __future__ import annotations

import ctypes
import queue
import threading


class Hotkeys:
    def __init__(self) -> None:
        self.events: queue.SimpleQueue[int] = queue.SimpleQueue()
        self.ready = threading.Event()
        self.shutdown = threading.Event()
        self.failure: BaseException | None = None
        self.thread = threading.Thread(target=self._listen, name="beta-hotkeys", daemon=True)
        self.thread.start()
        if not self.ready.wait(3):
            self.close()
            raise RuntimeError("Hotkey registration did not acknowledge. No Start allowed.")
        if self.failure is not None:
            self.close()
            raise RuntimeError(f"Cannot reserve F8/F9: {self.failure}") from self.failure

    def _listen(self) -> None:
        from ctypes import wintypes

        ids: list[int] = []
        user32 = None
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.RegisterHotKey.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.RegisterHotKey.restype = wintypes.BOOL
            user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.UnregisterHotKey.restype = wintypes.BOOL
            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.PeekMessageW.restype = wintypes.BOOL
            for ident, key in ((0x4D08, 0x77), (0x4D09, 0x78)):
                if not user32.RegisterHotKey(None, ident, 0x4000, key):
                    raise RuntimeError("F8/F9 already reserved by another controller")
                ids.append(ident)
            self.ready.set()
            msg = wintypes.MSG()
            while not self.shutdown.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1):
                    self.events.put(int(msg.wParam))
                self.shutdown.wait(0.02)
        except BaseException as exc:
            self.failure = exc
            self.events.put(
                0x4D09
            )  # Lost emergency controls cannot leave an active session running.
            self.ready.set()
        finally:
            if user32 is not None:
                for ident in ids:
                    user32.UnregisterHotKey(None, ident)

    def poll(self) -> list[int]:
        events = []
        while True:
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                return events

    def close(self) -> None:
        self.shutdown.set()
        self.thread.join(timeout=1)
