from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from validation.inventory_v3_protocol_v2 import cli, launcher
from validation.inventory_v3_protocol_v2.protocol import InventoryV3ProtocolV2Error

_ROOT = Path(__file__).resolve().parents[1]
_LAUNCHER = Path(launcher.__file__).resolve(strict=True)
_HEAD = "a" * 40
_DIRECT_IMPORT_TEST_MODULES = (
    "test_inventory_v3_protocol_v2_bridge.py",
    "test_inventory_v3_protocol_v2_cli.py",
    "test_inventory_v3_protocol_v2_lock_shadows.py",
    "test_inventory_v3_protocol_v2_package_tree.py",
    "test_inventory_v3_protocol_v2_privacy.py",
    "test_inventory_v3_protocol_v2_protocol.py",
    "test_inventory_v3_protocol_v2_transactions.py",
)

_EXPECTED_REQUIRED_ARGUMENTS = {
    "approval-request": {
        "expected_head",
        "proposed_approved_at_utc",
        "proposed_approver",
    },
    "authorization-proposal": {"expected_head", "opaque_receipt_id"},
    "capture": {
        "client_mode",
        "expected_head",
        "operator",
        "renderer",
        "runelite_build",
        "theme",
    },
    "evaluate": {"expected_head"},
    "finalize": {"expected_head"},
    "preflight": {"expected_head"},
    "prepare-review": {"expected_head"},
    "publish-review": {"expected_head"},
    "record-review": {"expected_head", "reviewer"},
}


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    parser = cli.build_parser()
    subparser_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return dict(subparser_action.choices)


def test_cli_command_surface_matches_the_nonactivating_runbook() -> None:
    subcommands = _subcommands()

    assert set(subcommands) == set(_EXPECTED_REQUIRED_ARGUMENTS)
    assert not {
        "activate",
        "approve",
        "apply-approval",
        "authorize",
        "commit-authorization",
        "promote",
        "write-authorization",
    }.intersection(subcommands)

    help_text = cli.build_parser().format_help()
    assert "No command grants live authorization or approval" in help_text
    for command in _EXPECTED_REQUIRED_ARGUMENTS:
        assert command in help_text


def test_each_runbook_command_has_only_its_fixed_required_arguments() -> None:
    subcommands = _subcommands()

    for command, expected in _EXPECTED_REQUIRED_ARGUMENTS.items():
        parser = subcommands[command]
        actual = {
            action.dest
            for action in parser._actions  # type: ignore[attr-defined]
            if action.required
        }
        assert actual == expected
        destinations = {
            action.dest
            for action in parser._actions  # type: ignore[attr-defined]
        }
        assert "output" not in destinations
        assert "case" not in destinations
        assert "stage" not in destinations
        assert "retry" not in destinations


@pytest.mark.parametrize(
    "command",
    tuple(_EXPECTED_REQUIRED_ARGUMENTS),
)
def test_every_command_rejects_missing_required_arguments(command: str) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args([command])

    assert raised.value.code == 2


def test_success_output_is_explicitly_nonactivating(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proposal = {
        "activation_allowed": False,
        "authorization_applied": False,
        "source_registry_modified": False,
        "status": "proposal-only-source-action-required",
    }
    monkeypatch.setattr(
        cli, "build_live_authorization_proposal", lambda *_args, **_kwargs: proposal
    )

    result = cli.main(
        [
            "authorization-proposal",
            "--expected-head",
            _HEAD,
            "--opaque-receipt-id",
            "11111111-1111-4111-8111-111111111111",
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    decoded = json.loads(stdout[: stdout.index("\nactivation_allowed=false")])
    assert decoded == proposal
    assert "activation_allowed=false" in stdout
    assert "LIVE INVENTORY CAMPAIGN NOT YET AUTHORIZED" in stdout
    assert "activation_allowed=true" not in stdout


def test_preflight_output_reports_no_sensitive_pixel_opening(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    protocol = SimpleNamespace(
        lock_commit_sha="b" * 40,
        lock_sha256="c" * 64,
    )
    authorization = SimpleNamespace(authorization_id="d" * 64)
    source = SimpleNamespace(
        capture_reports=tuple({"sequence_index": index} for index in range(1, 8)),
        paths=SimpleNamespace(source_campaign_root=Path("opaque-source-root")),
    )
    monkeypatch.setattr(cli, "verify_protocol_v2_repository", lambda *_args, **_kwargs: protocol)
    monkeypatch.setattr(cli, "verify_live_authorization", lambda _protocol: authorization)
    monkeypatch.setattr(cli, "preflight_source_metadata", lambda *_args: source)

    assert cli.main(["preflight", "--expected-head", _HEAD]) == 0

    stdout = capsys.readouterr().out
    decoded = json.loads(stdout[: stdout.index("\nactivation_allowed=false")])
    assert decoded["activation_allowed"] is False
    assert decoded["capture_count"] == 7
    assert decoded["status"] == "preflight-passed-no-sensitive-pixels-opened"


def test_interactive_review_provider_keeps_stage_and_operator_labels_blinded(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers: Iterator[str] = iter(
        (
            "approved",
            "inventory-visible",
            "7",
            "false",
            "false",
            "true",
            "false",
            "",
            "false",
        )
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    hidden_stage = "FORBIDDEN-STAGE-empty"
    hidden_operator = "FORBIDDEN-OPERATOR-Tyler"
    template = {
        "cases": [
            {
                "frame_region": {"path": "evidence/opaque-region-01.bgra"},
                "full_frame": {"path": "evidence/opaque-full-01.bgra"},
                "operator_identity": hidden_operator,
                "operator_stage_label": hidden_stage,
                "planned_stage_id": hidden_stage,
                "review_case_id": "blind-case-4fdb8a7c",
            }
        ]
    }

    result = cli._interactive_truth_provider("independent-reviewer", template)

    assert result["reviewer"] == "independent-reviewer"
    assert result["cases"] == [
        {
            "review_case_id": "blind-case-4fdb8a7c",
            "truth": {
                "decision": "approved",
                "drag_visible": False,
                "hover_visible": False,
                "occupied_slots": 7,
                "ordinary_iron_only": True,
                "quantity_text_visible": False,
                "review_note": None,
                "selected_item_visible": False,
                "visibility": "inventory-visible",
            },
        }
    ]
    assert isinstance(result["reviewed_at_utc"], str)
    stdout = capsys.readouterr().out
    assert "blind-case-4fdb8a7c" in stdout
    assert "opaque-full-01.bgra" in stdout
    assert "opaque-region-01.bgra" in stdout
    assert hidden_stage not in stdout
    assert hidden_operator not in stdout
    assert "empty" not in stdout
    assert "operator" not in stdout.lower()


def test_interactive_review_provider_displays_absolute_evidence_root_paths_blinded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence_root = tmp_path / "review-evidence"
    full_path = evidence_root / "cases" / "001" / "full-frame.bgra"
    region_path = evidence_root / "cases" / "001" / "inventory-region.bgra"
    full_path.parent.mkdir(parents=True)
    full_path.write_bytes(b"synthetic full frame")
    region_path.write_bytes(b"synthetic region")
    hidden_stage = "FORBIDDEN-STAGE-near-full"
    hidden_operator = "FORBIDDEN-OPERATOR-source-owner"
    answers: Iterator[str] = iter(
        (
            "approved",
            "inventory-visible",
            "27",
            "false",
            "false",
            "true",
            "false",
            "",
            "false",
        )
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    template = {
        "cases": [
            {
                "frame_region": {"path": "cases/001/inventory-region.bgra"},
                "full_frame": {"path": "cases/001/full-frame.bgra"},
                "operator_identity": hidden_operator,
                "operator_stage_label": hidden_stage,
                "planned_stage_id": hidden_stage,
                "review_case_id": "blind-case-absolute-a1",
            }
        ]
    }

    result = cli._interactive_truth_provider(
        "independent-reviewer",
        template,
        evidence_root=evidence_root,
    )

    assert result["reviewer"] == "independent-reviewer"
    stdout = capsys.readouterr().out
    assert f"Full-frame evidence: {full_path.resolve(strict=True)}" in stdout
    assert f"Inventory-region evidence: {region_path.resolve(strict=True)}" in stdout
    assert hidden_stage not in stdout
    assert hidden_operator not in stdout
    assert "near-full" not in stdout
    assert "source-owner" not in stdout


def test_interactive_review_provider_preserves_unknown_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: Iterator[str] = iter(
        (
            "approved",
            "wrong-tab-visible",
            "",
            "false",
            "false",
            "false",
            "false",
            "not a stage label",
            "false",
        )
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = cli._interactive_truth_provider(
        "independent-reviewer",
        {
            "cases": [
                {
                    "frame_region": {"path": "evidence/opaque-region.bgra"},
                    "full_frame": {"path": "evidence/opaque-full.bgra"},
                    "review_case_id": "blind-case-a1",
                }
            ]
        },
    )

    truth = result["cases"][0]["truth"]  # type: ignore[index]
    assert truth["visibility"] == "wrong-tab-visible"
    assert truth["occupied_slots"] is None


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_message"),
    (
        (
            InventoryV3ProtocolV2Error("synthetic integrity rejection"),
            1,
            "Inventory V3 Protocol V2 rejected: synthetic integrity rejection",
        ),
        (
            KeyboardInterrupt(),
            130,
            "operation interrupted; owned evidence remains retained",
        ),
    ),
)
def test_cli_failure_exit_codes_are_stable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: BaseException,
    expected_code: int,
    expected_message: str,
) -> None:
    def reject(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(cli, "build_live_authorization_proposal", reject)

    result = cli.main(
        [
            "authorization-proposal",
            "--expected-head",
            _HEAD,
            "--opaque-receipt-id",
            "11111111-1111-4111-8111-111111111111",
        ]
    )

    assert result == expected_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert expected_message in captured.err


@pytest.mark.parametrize("module_name", _DIRECT_IMPORT_TEST_MODULES)
def test_v2_test_surface_bootstraps_without_ambient_repository_path(
    tmp_path: Path,
    module_name: str,
) -> None:
    module_path = _ROOT / "tests" / module_name
    clean_import = (
        "import runpy,sys;"
        "from pathlib import Path;"
        "root=Path(sys.argv[1]).resolve();"
        "assert str(root) not in sys.path;"
        "runpy.run_path(sys.argv[2],run_name='__protocol_v2_clean_import__');"
        "assert str(root) in sys.path;"
        "package=Path(sys.modules['validation.inventory_v3_protocol_v2'].__file__).resolve();"
        "assert package.is_relative_to(root)"
    )

    completed = subprocess.run(
        (sys.executable, "-I", "-c", clean_import, str(_ROOT), str(module_path)),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""


@pytest.mark.parametrize(
    "python_flags",
    ((), ("-I",), ("-S",)),
    ids=("neither", "missing-no-site", "missing-isolation"),
)
def test_launcher_rejects_execution_without_both_isolation_guards(
    python_flags: tuple[str, ...],
) -> None:
    completed = subprocess.run(
        (sys.executable, *python_flags, str(_LAUNCHER), "--help"),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires direct Python -I -S execution" in completed.stderr


def test_launcher_static_preimport_boundary_precedes_cli_import() -> None:
    source = _LAUNCHER.read_text(encoding="utf-8")

    assert "sys.flags.isolated" in source
    assert "sys.flags.no_site" in source
    assert "sys.version_info < (3, 12)" in source
    assert "_is_direct(launcher)" in source
    assert '"status", "--porcelain=v1"' in source
    assert '"replace", "-l"' in source
    assert '"rev-parse", "--is-shallow-repository"' in source
    assert '"rev-parse", "--git-path", "info/grafts"' in source
    assert "locked_git_blobs" in source
    assert source.index("sys.version_info < (3, 12)") < source.index("_verify_preimport(launcher)")
    assert source.index("_verify_preimport(launcher)") < source.index(
        "from validation.inventory_v3_protocol_v2.cli import main as protocol_main"
    )


def test_cli_and_launcher_expose_no_authorization_or_approval_registry_writer() -> None:
    forbidden_path_fragments = {
        "approval-registry.json",
        "live-campaign-authorizations.json",
    }
    forbidden_write_calls = {
        "_canonical_write",
        "_write_canonical",
        "open",
        "unlink",
        "write_bytes",
        "write_text",
    }

    for module_path in (_LAUNCHER, Path(cli.__file__).resolve(strict=True)):
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        string_literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        call_names = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
        }

        path_fragments = forbidden_path_fragments
        if module_path == _LAUNCHER:
            # The read-only bootstrap must name the empty authorization files in
            # the exact P2 changed-path allowlist, but it still exposes no writer.
            path_fragments = {"approval-registry.json"}
        assert not any(
            fragment in literal for fragment in path_fragments for literal in string_literals
        )
        assert not forbidden_write_calls.intersection(call_names)
