from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_safe_entry_does_not_use_feature_equivalence_as_input_authority() -> None:
    source = Path(safe_mining.__file__).read_text(encoding="utf-8")
    assert "classify_equivalent_start" not in source
    assert "proven_start_equivalence" not in source


def _scaled_candidate(name: str, matched: int) -> tuple[str, object, dict[str, int]]:
    detector = SimpleNamespace(profile=SimpleNamespace(scene_landmarks=(None,) * 6))
    return name, detector, {"matched": matched}


def test_scaled_registration_accepts_one_ordinary_gated_candidate() -> None:
    only = _scaled_candidate("only", 5)
    assert safe_mining.select_scaled_registration([only]) is only


def test_scaled_registration_accepts_unique_full_match_among_multiple() -> None:
    partial = _scaled_candidate("partial", 5)
    full = _scaled_candidate("full", 6)
    assert safe_mining.select_scaled_registration([partial, full]) is full


def test_scaled_registration_rejects_unresolved_ambiguity() -> None:
    first = _scaled_candidate("first", 6)
    second = _scaled_candidate("second", 6)
    assert safe_mining.select_scaled_registration([first, second]) is None
    assert safe_mining.select_scaled_registration([]) is None


def test_post_click_clean_observation_discards_stale_registered_geometry() -> None:
    source = Path(safe_mining.mining.__file__).read_text(encoding="utf-8")
    start = source.index("    def acquire_clean_observation(")
    end = source.index("    def prove_hover(", start)
    block = source[start:end]
    reset = 'self.active_registration = {"pose": None, "detector": None}'
    assert block.index("if iteration > 1:") < block.index(reset)
    assert block.index(reset) < block.index("self._evaluate_resource(")
