"""Regression guard for Issue #28 reviewed UI/privacy exclusions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from mining_automation.perception import load_varrock_east_iron_profile


def _load_tool_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "diagnose_varrock_east_wide.py"
    specification = importlib.util.spec_from_file_location(
        "issue28_wide_exclusion_tool", path
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_wide_tool_excludes_candidates_and_reviewed_sanitized_ui_bands() -> None:
    tool = _load_tool_module()
    profile = load_varrock_east_iron_profile()

    exclusions = tool._excluded_regions(profile)

    assert exclusions[: len(profile.candidates)] == tuple(
        candidate.region for candidate in profile.candidates
    )
    assert exclusions[-2:] == (
        (0, 0, profile.frame_width, 34),
        (0, 850, profile.frame_width, profile.frame_height - 850),
    )
