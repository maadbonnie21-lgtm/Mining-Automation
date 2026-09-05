from __future__ import annotations

from pathlib import Path


def test_registration_reacquisition_never_falls_back_to_prior_frame_diagnostics() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/runelite_prep.py").read_text(encoding="utf-8")

    # PREP's final READY gate must be owned entirely by the final fresh frame.
    # Reusing the first registration-capture diagnostics after the second capture
    # would mix frame identities and could publish READY from stale landmark data.
    assert "first_diagnoses = diagnoses" not in source
    assert "diagnoses = first_diagnoses" not in source
