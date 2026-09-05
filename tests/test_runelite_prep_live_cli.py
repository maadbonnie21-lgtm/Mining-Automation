from __future__ import annotations

import io
import json
import sys
from dataclasses import replace
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


@pytest.mark.parametrize("encoding", ["cp1252", "ascii", "utf-8"])
@pytest.mark.parametrize("supported", [False, True])
def test_console_encoding_cannot_change_prep_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    encoding: str,
    supported: bool,
) -> None:
    # Reuse only the synthetic backend, not a real desktop or any input device.
    from test_runelite_prep_live_boundary import InnerBackend, _observation

    inner = InnerBackend()
    observation = replace(
        _observation(),
        resource_supported=supported,
        resource_view="supported" if supported else "unsupported",
    )
    monkeypatch.setattr(inner, "observe", lambda: observation)
    monkeypatch.setattr(prep_live.legacy_prep, "RealPrepBackend", lambda **kwargs: inner)
    monkeypatch.setattr(prep_live.legacy_prep, "_exact_git_sha", lambda: HEAD)
    monkeypatch.setattr(prep_live.legacy_prep, "_checkout_clean", lambda: True)
    # Also exercise a path that cannot be represented in cp1252 or ASCII.
    output = tmp_path / "result-\u77ff"
    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding=encoding, errors="strict")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdout", console)
            rc = prep_live.main(
                [
                    "--apply", "--authorize-execution-sha", HEAD,
                    "--hwnd", "42", "--confirm", prep_live.PREP_CONFIRMATION,
                    "--output", str(output),
                ]
            )
            console.flush()
        text = buffer.getvalue().decode(encoding)
        assert console.encoding == encoding
    finally:
        console.close()

    result = _read_result(output)
    assert result["ready_for_mining"] is supported
    assert rc == (0 if supported else 2)
    expected_reason = (
        prep_live.PrepStopReason.NONE if supported
        else prep_live.PrepStopReason.RESOURCE_SCENE_UNSUPPORTED
    )
    assert result["stop_reason"] == expected_reason.value
    assert "RuneLite found" in text
    assert "HWND 42" in text
    assert "Mining input authority: FALSE" in text
    assert "Traceback" not in text
    if supported:
        assert "READY FOR MINING" in text
        assert "Mining is NOT authorized" in text
    else:
        assert "NOT READY: resource_scene_unsupported" in text
        assert "READY FOR MINING" not in text
    assert not any(action.startswith("camera:") for action in inner.mutations)


def test_unicode_existing_output_error_does_not_crash_cp1252_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = _forbid_real_backend(monkeypatch)
    output = tmp_path / "existing-\u77ff"
    output.mkdir()
    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stderr", console)
            rc = prep_live.main(["--output", str(output)])
            console.flush()
        text = buffer.getvalue().decode("cp1252")
    finally:
        console.close()
    assert rc == 2
    assert calls == []
    assert "STOP: output path already exists" in text
    assert "Traceback" not in text
    assert not (output / "result.json").exists()
