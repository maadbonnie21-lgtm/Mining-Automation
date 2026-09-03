from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

from mining_automation.capture import Frame


def _load_tool() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "windows_capture_check.py"
    spec = importlib.util.spec_from_file_location("windows_capture_check_tool", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_strict_readonly_preflight_arguments_are_opt_in() -> None:
    tool = _load_tool()
    args = tool._parse_args([])

    assert args.require_width is None
    assert args.require_height is None
    assert args.require_dpi is None
    assert args.require_all_successful is False

    strict = tool._parse_args(
        [
            "--frames",
            "3",
            "--interval",
            "0.25",
            "--require-width",
            "1005",
            "--require-height",
            "1078",
            "--require-dpi",
            "96",
            "--require-all-successful",
        ]
    )
    assert strict.frames == 3
    assert strict.interval == 0.25
    assert strict.require_width == 1005
    assert strict.require_height == 1078
    assert strict.require_dpi == 96
    assert strict.require_all_successful is True


def test_frame_requirements_match_only_exact_requested_geometry() -> None:
    tool = _load_tool()
    args = tool._parse_args(["--require-width", "1005", "--require-height", "1078"])

    exact = cast(Frame, SimpleNamespace(width=1005, height=1078))
    wrong_width = cast(Frame, SimpleNamespace(width=1004, height=1078))
    wrong_height = cast(Frame, SimpleNamespace(width=1005, height=1077))

    assert tool._frame_matches_requirements(exact, args) is True
    assert tool._frame_matches_requirements(wrong_width, args) is False
    assert tool._frame_matches_requirements(wrong_height, args) is False


def test_positive_integer_parser_rejects_zero_and_negative_values() -> None:
    tool = _load_tool()

    for value in ("0", "-1"):
        try:
            tool._positive_int(value)
        except argparse.ArgumentTypeError:
            pass
        else:  # pragma: no cover - assertion explanation path
            raise AssertionError(f"expected {value!r} to be rejected")


def test_negative_interval_stops_before_windows_backend_construction(capsys: object) -> None:
    tool = _load_tool()

    assert tool.main(["--interval", "-0.1"]) == 2
