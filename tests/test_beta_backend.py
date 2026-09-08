"""Adapter tests. All phase execution is replaced; no capture or game input."""

from __future__ import annotations

import json

import pytest

from mining_automation.beta_backend import PhaseBackend
from mining_automation.beta_session import (
    BetaSession,
    SessionControls,
    SessionSettings,
    SessionUnproven,
)
from mining_automation.full_cycle import FullCycleError


def make(tmp_path):
    control = SessionControls()
    backend = PhaseBackend(
        tmp_path, tmp_path / "run", 42, "RuneLite - test", "a" * 40, SessionSettings(), control
    )
    payloads = {
        "mine": dict(start_inventory=0, end_inventory=28, stop_reason="inventory_full"),
        "outbound": dict(
            completed_checkpoints=["bank_interior_endpoint"],
            item_actions=0,
            bank_interface_opened=False,
        ),
        "bank": dict(
            before_ore_count=28, after_ore_count=0, deposit_verified=True, bank_closed_verified=True
        ),
        "return": dict(completed_checkpoints=["mine_start"], window_unchanged=True),
    }
    calls = []

    def run(kind, path, heartbeat=None, **kwargs):
        control.check(authentication=kind in ("login", "logout"))
        calls.append(kind)
        if heartbeat:
            heartbeat(kind)
        return dict(
            success=True,
            receipt_path=str(path / "result.json"),
            receipt_sha256="b" * 64,
            **payloads[kind],
        )

    backend._run = run
    return backend, control, payloads, calls


def test_cycle_reuses_existing_phase_order_and_receipt_validators(tmp_path):
    backend, controls, payloads, calls = make(tmp_path)
    result = backend.cycle(7, lambda phase: None)
    assert calls == ["mine", "outbound", "bank", "return"]
    assert result["success"] and result["cycle"] == 7
    assert backend.ore_deposited == result["deposited_ore"] == 28
    assert [r["phase"] for r in result["phase_receipts"]] == calls


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("mine", "end_inventory", 27),
        ("outbound", "completed_checkpoints", ["near_bank"]),
        ("outbound", "item_actions", 1),
        ("bank", "bank_closed_verified", False),
        ("bank", "after_ore_count", 1),
        ("return", "completed_checkpoints", ["near_mine"]),
        ("return", "window_unchanged", False),
    ],
)
def test_invalid_receipt_does_not_authorize_the_next_phase(tmp_path, kind, field, value):
    backend, controls, payloads, calls = make(tmp_path)
    payloads[kind][field] = value
    with pytest.raises(FullCycleError):
        backend.cycle(1, lambda phase: None)
    assert calls[-1] == kind
    assert backend.ore_deposited == (28 if kind == "return" else 0)


@pytest.mark.parametrize("phase", ["mine", "outbound", "bank", "return"])
def test_normal_stop_does_not_kill_the_inflight_safe_cycle(tmp_path, phase):
    backend, controls, payloads, calls = make(tmp_path)

    def heartbeat(kind):
        if kind == phase:
            backend.request_stop()

    result = backend.cycle(1, heartbeat)
    assert result["success"]
    assert calls == ["mine", "outbound", "bank", "return"]
    assert controls.stop.is_set() and not controls.emergency.is_set()
    assert not backend.output.exists()  # Stop before run mkdir does not create a race.


def test_emergency_file_from_child_propagates_to_parent_latch(tmp_path):
    backend, controls, payloads, calls = make(tmp_path)
    backend.output.mkdir()
    backend.cancel_file.touch()
    backend.refresh_latches()
    assert controls.emergency.is_set()


def test_bank_success_is_visible_even_when_return_failed(tmp_path):
    backend, controls, payloads, calls = make(tmp_path)
    payloads["return"]["window_unchanged"] = False
    proof = dict(
        mine_arrival_verified=True,
        inventory_empty_verified=True,
        bank_closed_verified=True,
        window_unchanged=True,
        stationary_verified=True,
        fresh=True,
        fresh_rocks_verified=True,
    )
    backend.verify_home = lambda **kw: proof
    session = BetaSession(
        SessionSettings(mode="finite", cycles=1), controls, backend, backend.output, sha="a" * 40
    )
    result = session.run()
    assert result["state"] == "ERROR"
    assert result["ore_deposited"] == 28
    assert result["cycles_completed"] == 0
    assert result["home_proof"] is None
    assert not result["success"]


def test_normal_stop_file_cancels_auth_but_not_route(tmp_path):
    backend, controls, payloads, calls = make(tmp_path)
    backend.output.mkdir()
    backend.request_stop()
    assert backend.auth_cancel_file.is_file()
    assert not backend.cancel_file.exists()
    controls.check()
    with pytest.raises(RuntimeError, match="cancelled_login_or_logout"):
        controls.check(authentication=True)


def test_source_change_prevents_any_child_spawn(tmp_path, monkeypatch):
    backend, controls, payloads, calls = make(tmp_path)
    import subprocess

    responses = iter(["b" * 40])
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: type("Result", (), {"stdout": next(responses)})()
    )
    with pytest.raises(SessionUnproven, match="source_changed"):
        backend._clean_build()
    assert backend.child is None


def test_cancellation_failure_stays_error_and_preserves_receipt(tmp_path):
    backend, controls, payloads, calls = make(tmp_path)
    backend.verify_home = lambda **kw: (_ for _ in ()).throw(RuntimeError("position_unknown"))
    backend.cancel = lambda: (_ for _ in ()).throw(RuntimeError("cleanup_failed"))
    session = BetaSession(SessionSettings(), controls, backend, backend.output, sha="a" * 40)
    result = session.run()
    assert result["cleanup_unconfirmed"]
    assert result["state"] == "ERROR" and not result["success"]
    assert "cancellation_unconfirmed" in result["reason"]
    assert json.loads((backend.output / "result.json").read_text())["cleanup_unconfirmed"]
