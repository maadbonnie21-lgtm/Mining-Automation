from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import runelite_prep_live as prep_live  # noqa: E402

HEAD = "a" * 40


def _forbid_real_backend(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    calls: list[bool] = []

    def forbidden_backend(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append(True)
        raise AssertionError("real PREP backend must not be constructed")

    monkeypatch.setattr(prep_live.legacy_prep, "RealPrepBackend", forbidden_backend)
    return calls


def _read_result(output: Path) -> dict[str, object]:
    payload = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_apply_without_authorized_sha_stops_before_real_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(prep_live.legacy_prep, "_exact_git_sha", lambda: HEAD)
    monkeypatch.setattr(prep_live.legacy_prep, "_checkout_clean", lambda: True)
    calls = _forbid_real_backend(monkeypatch)
    output = tmp_path / "missing-sha"

    rc = prep_live.main(
        [
            "--apply",
            "--hwnd",
            "42",
            "--confirm",
            prep_live.PREP_CONFIRMATION,
            "--output",
            str(output),
        ]
    )

    assert rc == 2
    assert calls == []
    result = _read_result(output)
    assert result["ready_for_mining"] is False
    assert result["stop_reason"] == "prep_confirmation_required"
    assert "--authorize-execution-sha" in str(result["detail"])
    assert "zero input" in str(result["detail"]).lower()


def test_apply_with_wrong_authorized_sha_stops_before_real_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(prep_live.legacy_prep, "_exact_git_sha", lambda: HEAD)
    monkeypatch.setattr(prep_live.legacy_prep, "_checkout_clean", lambda: True)
    calls = _forbid_real_backend(monkeypatch)
    output = tmp_path / "wrong-sha"

    rc = prep_live.main(
        [
            "--apply",
            "--authorize-execution-sha",
            "b" * 40,
            "--hwnd",
            "42",
            "--confirm",
            prep_live.PREP_CONFIRMATION,
            "--output",
            str(output),
        ]
    )

    assert rc == 2
    assert calls == []
    result = _read_result(output)
    assert result["ready_for_mining"] is False
    assert HEAD in str(result["detail"])
    assert "zero input" in str(result["detail"]).lower()


def test_apply_without_hwnd_stops_before_real_backend_when_sha_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(prep_live.legacy_prep, "_exact_git_sha", lambda: HEAD)
    monkeypatch.setattr(prep_live.legacy_prep, "_checkout_clean", lambda: True)
    calls = _forbid_real_backend(monkeypatch)
    output = tmp_path / "missing-hwnd"

    rc = prep_live.main(
        [
            "--apply",
            "--authorize-execution-sha",
            HEAD,
            "--confirm",
            prep_live.PREP_CONFIRMATION,
            "--output",
            str(output),
        ]
    )

    assert rc == 2
    assert calls == []
    result = _read_result(output)
    assert result["ready_for_mining"] is False
    assert result["stop_reason"] == "window_identity_changed"
    assert "--hwnd" in str(result["detail"])
    assert "zero input" in str(result["detail"]).lower()


def test_read_only_mode_does_not_require_execution_sha() -> None:
    args = prep_live._parse_args([])
    assert args.apply is False
    assert args.authorize_execution_sha is None
    assert args.hwnd is None
