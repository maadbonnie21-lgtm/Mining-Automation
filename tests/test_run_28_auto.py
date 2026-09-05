from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import run_28_auto

HEAD = "a" * 40
HWND = 3736178


def _ready_payload() -> dict[str, object]:
    return {
        "ready_for_mining": True,
        "git_sha": HEAD,
        "mining_input_authority": False,
        "prep_authority_relinquished": True,
        "final_window": {
            "hwnd": HWND,
            "client_width": 1005,
            "client_height": 1078,
            "dpi": 96,
            "foreground": True,
            "visible": True,
            "minimized": False,
        },
        "observations": [
            {
                "inventory_occupied": 0,
                "inventory_confidence": 1.0,
                "resource_supported": True,
                "matched_landmarks": 6,
                "matched_zones": ["north_west", "north_east", "south_west"],
            }
        ],
    }


def test_ready_handoff_requires_exact_zero_to_28_state() -> None:
    run_28_auto._require_ready_for_zero_to_28(
        _ready_payload(),
        expected_sha=HEAD,
        expected_hwnd=HWND,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ready_for_mining", False),
        ("git_sha", "b" * 40),
        ("mining_input_authority", True),
        ("prep_authority_relinquished", False),
    ],
)
def test_ready_handoff_rejects_authority_or_identity_mismatch(
    field: str,
    value: object,
) -> None:
    payload = _ready_payload()
    payload[field] = value
    with pytest.raises(RuntimeError):
        run_28_auto._require_ready_for_zero_to_28(
            payload,
            expected_sha=HEAD,
            expected_hwnd=HWND,
        )


def test_ready_handoff_rejects_nonzero_inventory() -> None:
    payload = _ready_payload()
    observations = payload["observations"]
    assert isinstance(observations, list)
    observation = observations[-1]
    assert isinstance(observation, dict)
    observation["inventory_occupied"] = 1
    with pytest.raises(RuntimeError, match="exactly 0/28"):
        run_28_auto._require_ready_for_zero_to_28(
            payload,
            expected_sha=HEAD,
            expected_hwnd=HWND,
        )


def test_ready_handoff_rejects_missing_resource_zone() -> None:
    payload = _ready_payload()
    observations = payload["observations"]
    assert isinstance(observations, list)
    observation = observations[-1]
    assert isinstance(observation, dict)
    observation["matched_zones"] = ["north_west", "south_west"]
    with pytest.raises(RuntimeError, match="zones"):
        run_28_auto._require_ready_for_zero_to_28(
            payload,
            expected_sha=HEAD,
            expected_hwnd=HWND,
        )


def test_live_entry_runs_mining_only_after_genuine_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_28_auto, "_git_head", lambda: HEAD)
    monkeypatch.setattr(run_28_auto, "_tracked_checkout_clean", lambda: True)
    mining_calls: list[list[str]] = []

    def fake_prep(argv: list[str]) -> int:
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir(parents=True)
        (output / "result.json").write_text(
            json.dumps(_ready_payload()),
            encoding="utf-8",
        )
        return 0

    def fake_mining(argv: list[str]) -> int:
        mining_calls.append(argv)
        return 0

    monkeypatch.setattr(run_28_auto.prep_auto, "main", fake_prep)
    monkeypatch.setattr(run_28_auto.mining, "main", fake_mining)
    rc = run_28_auto.main(
        [
            "--live",
            "--hwnd",
            str(HWND),
            "--authorize-execution-sha",
            HEAD,
            "--confirm",
            run_28_auto.OWNER_CONFIRMATION,
        ]
    )
    assert rc == 0
    assert len(mining_calls) == 1
    mining_argv = mining_calls[0]
    assert mining_argv[mining_argv.index("--hwnd") + 1] == str(HWND)
    assert mining_argv[mining_argv.index("--authorize-execution-sha") + 1] == HEAD
    assert (
        mining_argv[mining_argv.index("--confirm") + 1]
        == run_28_auto.mining.EXPECTED_CONFIRMATION
    )
    assert (
        run_28_auto.mining.WindowsMiningToFullBackend
        is run_28_auto.SafeWindowsMiningToFullBackend
    )


def test_live_entry_never_starts_mining_when_prep_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_28_auto, "_git_head", lambda: HEAD)
    monkeypatch.setattr(run_28_auto, "_tracked_checkout_clean", lambda: True)
    mining_called = False

    def fake_prep(argv: list[str]) -> int:
        del argv
        return 2

    def fake_mining(argv: list[str]) -> int:
        nonlocal mining_called
        del argv
        mining_called = True
        return 0

    monkeypatch.setattr(run_28_auto.prep_auto, "main", fake_prep)
    monkeypatch.setattr(run_28_auto.mining, "main", fake_mining)
    rc = run_28_auto.main(
        [
            "--live",
            "--hwnd",
            str(HWND),
            "--authorize-execution-sha",
            HEAD,
            "--confirm",
            run_28_auto.OWNER_CONFIRMATION,
        ]
    )
    assert rc == 2
    assert mining_called is False


def test_safe_backend_rechecks_window_after_clean_and_hover_evidence() -> None:
    source = Path(run_28_auto.__file__).read_text(encoding="utf-8")
    clean_start = source.index("    def acquire_clean_observation(")
    hover_start = source.index("    def prove_hover(", clean_start)
    helper_start = source.index("\ndef _git_head()", hover_start)
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
