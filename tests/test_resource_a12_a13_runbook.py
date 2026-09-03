from __future__ import annotations

from pathlib import Path
import json


_ROOT = Path(__file__).resolve().parents[1]
_RUNBOOK = _ROOT / "validation" / "resource_a12_a13_runbook.json"
_A11 = "01d29ab1c2d8a1849d21c37c9284391cb97119b6"


def test_a12_a13_runbook_preserves_frozen_resource_invariants() -> None:
    data = json.loads(_RUNBOOK.read_text(encoding="utf-8"))
    assert data["a11_exact_authorized_source_sha"] == _A11
    assert data["capture_requires_explicit_tyler_authorization"] is True
    assert data["activation_allowed"] is False
    assert data["input_authority"] is False
    assert data["automatic_retry_count"] == 0
    assert data["automatic_camera_control"] is False
    assert data["automatic_camera_recovery"] is False
    assert data["campaign_case_count"] == 15
    assert data["frame_contract"] == {
        "height": 1078,
        "pixel_format": "bgra8888",
        "reported_dpi": 96,
        "width": 1005,
    }
    assert data["resource_invariants"] == {
        "all_three_zones_required": True,
        "landmark_count": 6,
        "minimum_landmark_quorum": 5,
        "threshold": 0.12,
        "unknown_or_unsupported_result": "zero_targets_and_stop",
    }
    assert data["synthetic_evidence_is_real_release_evidence"] is False


def test_runbook_uses_only_existing_campaign_cli_commands_in_order() -> None:
    data = json.loads(_RUNBOOK.read_text(encoding="utf-8"))
    commands = "\n".join(
        data["a12_operator_sequence"] + data["a13_independent_review_sequence"]
    )
    for command in (
        " start ",
        " status ",
        " capture-next ",
        " seal ",
        " prepare-review ",
        " review-template ",
        " review ",
        " release ",
        " export-review ",
        " verify-export ",
        " prepare-followup ",
        " verify-followup ",
        " prepare-release-decision-readiness ",
        " verify-release-review-packet ",
    ):
        assert command in commands
    assert commands.index(" start ") < commands.index(" capture-next ")
    assert commands.index(" capture-next ") < commands.index(" seal ")
    assert commands.index(" seal ") < commands.index(" prepare-review ")
    assert commands.index(" prepare-review ") < commands.index(" release ")
    assert commands.index(" release ") < commands.index(" export-review ")
    assert commands.index(" export-review ") < commands.index(" verify-export ")
    assert commands.index(" verify-export ") < commands.index(" prepare-followup ")
    assert commands.index(" prepare-followup ") < commands.index(" verify-followup ")
    assert commands.index(" verify-followup ") < commands.index(
        " prepare-release-decision-readiness "
    )
    assert "--allow-input" not in commands
    assert "--camera" not in commands
    assert "--retry" not in commands


def test_hard_stop_list_covers_release_identity_and_uncertainty() -> None:
    data = json.loads(_RUNBOOK.read_text(encoding="utf-8"))
    stops = "\n".join(data["hard_stops"]).lower()
    for term in (
        "git sha",
        "geometry",
        "dpi",
        "unknown",
        "out-of-order",
        "capture failure",
        "identity overlap",
        "root mismatch",
        "camera",
        "input",
        "automatic retry",
    ):
        assert term in stops
