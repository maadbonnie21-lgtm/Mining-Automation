from __future__ import annotations

from pathlib import Path

import pytest

from tools import p0_read_only_startup_diagnose as diagnose


def test_read_only_wrapper_never_forwards_apply_or_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr(diagnose.base, "main", fake_main)
    rc = diagnose.main(
        ["--title", "RuneLite - Test", "--output", str(tmp_path / "receipt")]
    )
    assert rc == 0
    assert calls == [
        [
            "--title",
            "RuneLite - Test",
            "--output",
            str(tmp_path / "receipt"),
        ]
    ]
    assert "--apply" not in calls[0]
    assert "--confirm" not in calls[0]
