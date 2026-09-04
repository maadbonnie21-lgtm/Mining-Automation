#!/usr/bin/env python3
"""Cursor-only hover calibration for the three iron models at the center pose."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mining_automation.capture import CaptureSource
from mining_automation.capture.windows import WindowsCaptureBackend
from mining_automation.controlled_mining_runner import RealWin32MiningInputDevice
from mining_automation.validation.windows_camera import RealWindowsCameraApi
from run_proven_mining_loop import mine_hover_signature

HWND = 3736178
OUTPUT = Path("diagnostics/three-rock-continuous-20260903/current-pose-hover-calibration")
CANDIDATES = {
    "northwest": ((460, 390), (480, 390), (500, 390), (480, 410)),
    "southwest": ((540, 390), (560, 390), (580, 390), (560, 410)),
    "center": ((460, 450), (480, 450), (500, 450), (480, 470)),
}


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    api = RealWindowsCameraApi()
    RealWin32MiningInputDevice().verify_target_window()
    if api.foreground_window() != HWND:
        return 2
    source = CaptureSource(WindowsCaptureBackend(title_substring="RuneLite"))
    evidence = []
    source.open()
    try:
        for rock, points in CANDIDATES.items():
            for index, point in enumerate(points, start=1):
                screen = api.pointer_mapping(HWND, *point).physical_screen.pair
                if api.foreground_window() != HWND or api.root_window_at_point(*screen) != HWND:
                    raise RuntimeError("window safety changed")
                if not api.move_cursor(*screen):
                    raise RuntimeError("cursor move failed")
                time.sleep(0.45)
                frame = source.capture()
                path = OUTPUT / f"{rock}-{index:02d}.bgra"
                path.write_bytes(frame.payload)
                proof = mine_hover_signature(frame.payload, frame.width)
                evidence.append({"rock": rock, "client_point": point, "frame": str(path), **proof})
                (OUTPUT / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                if proof["proven_mine_iron_rocks"]:
                    break
        neutral = api.pointer_mapping(HWND, 100, 100).physical_screen.pair
        if api.root_window_at_point(*neutral) == HWND:
            api.move_cursor(*neutral)
        print(json.dumps(evidence, indent=2))
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
