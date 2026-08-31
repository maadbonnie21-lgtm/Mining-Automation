"""Regression guard for Issue #28 reviewed UI/privacy exclusions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from mining_automation.perception import (
    VARROCK_EAST_IRON_FIXED_UI_REGIONS,
    load_varrock_east_iron_profile,
)


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


def test_wide_tool_excludes_candidates_and_all_reviewed_fixed_ui() -> None:
    tool = _load_tool_module()
    profile = load_varrock_east_iron_profile()

    exclusions = tool._excluded_regions(profile)

    assert exclusions[: len(profile.candidates)] == tuple(
        candidate.region for candidate in profile.candidates
    )
    assert exclusions[len(profile.candidates) :] == VARROCK_EAST_IRON_FIXED_UI_REGIONS
    assert (545, 34, 222, 220) in exclusions
    assert (520, 500, 485, 350) in exclusions
