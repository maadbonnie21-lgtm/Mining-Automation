from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import run_28_auto  # noqa: E402

HEAD = "a" * 40
HWND = 3736178


def _ready_payload() -> dict[str, object]:
    return {
        "ready_for_mining": True,
        "git_sha": HEAD,
        "prep_authority_relinquished": True,
        "mining_input_authority": False,
        "navigation_authority": False,
        "banking_authority": False,
        "inventory_release_authority": False,
        "resource_release_authority": False,
        "resource_threshold": 0.12,
        "resource_landmark_count": 6,
        "resource_landmark_quorum": 5,
        "resource_required_zone_count": 3,
        "inventory_floor": 0.8,
        "inventory_capacity": 28,
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
                "gameplay_ready": True,
                "inventory_occupied": 0,
                "inventory_confidence": 1.0,
                "resource_supported": True,
                "matched_landmarks": 6,
                "matched_zones": ["north_west", "north_east", "south_west"],
                "landmark_distances": [
                    [f"landmark-{index}", 0.01] for index in range(6)
                ],
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
        ("prep_authority_relinquished", False),
        ("mining_input_authority", True),
        ("navigation_authority", True),
        ("banking_authority", True),
        ("inventory_release_authority", True),
        ("resource_release_authority", True),
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


def test_ready_handoff_rejects_bad_landmark_distances() -> None:
    payload = _ready_payload()
    observations = payload["observations"]
    assert isinstance(observations, list)
    observation = observations[-1]
    assert isinstance(observation, dict)
    observation["landmark_distances"] = [
        [f"landmark-{index}", 0.13 if index < 5 else 0.5]
        for index in range(6)
    ]
    with pytest.raises(RuntimeError, match="0.12 / 5-of-6"):
        run_28_auto._require_ready_for_zero_to_28(
            payload,
            expected_sha=HEAD,
            expected_hwnd=HWND,
        )


def test_ready_handoff_rejects_gameplay_not_ready() -> None:
    payload = _ready_payload()
    observations = payload["observations"]
    assert isinstance(observations, list)
    observation = observations[-1]
    assert isinstance(observation, dict)
    observation["gameplay_ready"] = False
    with pytest.raises(RuntimeError, match="gameplay"):
        run_28_auto._require_ready_for_zero_to_28(
            payload,
            expected_sha=HEAD,
            expected_hwnd=HWND,
        )


def test_live_entry_stops_after_genuine_ready_and_never_starts_mining(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(run_28_auto, "_git_head", lambda: HEAD)
    monkeypatch.setattr(run_28_auto, "_tracked_checkout_clean", lambda: True)
    mining_called = False

    def fake_prep(argv: list[str]) -> int:
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir(parents=True)
        (output / "result.json").write_text(
            json.dumps(_ready_payload()),
            encoding="utf-8",
        )
        return 0

    def fake_mining(argv: list[str]) -> int:
        nonlocal mining_called
        del argv
        mining_called = True
        return 0

    monkeypatch.setattr(run_28_auto.prep_live, "main", fake_prep)
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
    assert mining_called is False
    output = capsys.readouterr().out
    assert "MINING NOT STARTED" in output
    assert "run_mining_to_full_safe.py" in output
    assert HEAD in output
    assert str(HWND) in output
    assert run_28_auto.mining.EXPECTED_CONFIRMATION in output


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

    monkeypatch.setattr(run_28_auto.prep_live, "main", fake_prep)
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


def test_legacy_auto_source_cannot_cross_prep_to_mining_boundary() -> None:
    source = Path(run_28_auto.__file__).read_text(encoding="utf-8")
    assert "mining.main(" not in source
    assert "prep_auto" not in source
    assert "prep_live.main(" in source
    assert "run_mining_to_full_safe.py" in source
    assert "AUTO_PREP_TO_READY_ONLY" in source
