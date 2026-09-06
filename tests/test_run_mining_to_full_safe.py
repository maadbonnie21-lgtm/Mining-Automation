from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import run_mining_to_full_safe as safe_mining  # noqa: E402


def test_safe_backend_rechecks_window_after_clean_and_hover_evidence() -> None:
    source = Path(safe_mining.__file__).read_text(encoding="utf-8")
    clean_start = source.index("    def acquire_clean_observation(")
    hover_start = source.index("    def prove_hover(", clean_start)
    helper_start = source.index("\ndef main(", hover_start)
    clean = source[clean_start:hover_start]
    hover = source[hover_start:helper_start]
    assert clean.index("super().acquire_clean_observation") < clean.index(
        "_, final_window = self._verify_window()"
    )
    assert hover.index("super().prove_hover") < hover.index(
        "_, final_window = self._verify_window()"
    )
    assert hover.index("_, final_window = self._verify_window()") < hover.index(
        "root_window_at_point"
    )
    assert hover.index("root_window_at_point") < hover.index("cursor_position")


def test_safe_entry_delegates_only_after_installing_safe_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_backend = safe_mining.mining.WindowsMiningToFullBackend
    monkeypatch.setattr(
        safe_mining.mining,
        "WindowsMiningToFullBackend",
        original_backend,
    )
    calls: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        assert (
            safe_mining.mining.WindowsMiningToFullBackend
            is safe_mining.SafeWindowsMiningToFullBackend
        )
        return 7

    monkeypatch.setattr(safe_mining.mining, "main", fake_main)
    rc = safe_mining.main(["--live", "--hwnd", "42"])
    assert rc == 7
    assert calls == [["--live", "--hwnd", "42"]]


def test_safe_entry_contains_no_prep_camera_navigation_or_banking_path() -> None:
    source = Path(safe_mining.__file__).read_text(encoding="utf-8")
    forbidden = (
        "runelite_prep",
        "camera_action",
        "navigate_to_bank",
        "navigate_to_mine",
        "deposit_all",
    )
    assert not any(token in source for token in forbidden)


def test_post_click_clean_observation_discards_stale_registered_geometry() -> None:
    source = Path(safe_mining.mining.__file__).read_text(encoding="utf-8")
    start = source.index("    def acquire_clean_observation(")
    end = source.index("    def prove_hover(", start)
    block = source[start:end]
    reset = 'self.active_registration = {"pose": None, "detector": None}'
    assert block.index("if iteration > 1:") < block.index(reset)
    assert block.index(reset) < block.index("self._evaluate_resource(")
