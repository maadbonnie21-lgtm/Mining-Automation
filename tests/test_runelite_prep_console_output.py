from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import runelite_prep as prep  # noqa: E402


def test_safe_console_print_handles_strict_cp1252(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    prep._safe_console_print("✓ RuneLite — Ω")
    stream.flush()

    rendered = raw.getvalue().decode("cp1252")
    assert "RuneLite" in rendered
    assert "?" in rendered
