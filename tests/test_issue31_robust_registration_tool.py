"""Deterministic contracts for the read-only Issue #31 R1 report tool."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import ResourceVisualState
from mining_automation.validation.robust_registration import (
    ModelFamily,
    RegistrationDisposition,
)
from mining_automation.validation.robust_view_graph import ViewNodeSpec, ViewRole

_TOOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "analyze_issue31_robust_registration.py"
)
_HEAD = "a" * 40
_OTHER_HEAD = "b" * 40


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "analyze_issue31_robust_registration_test",
        _TOOL_PATH,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> ModuleType:
    return _load_tool()


def _frame(marker: int) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=bytes((marker, marker, marker, 255)),
            width=1,
            height=1,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=marker,
        captured_monotonic_s=float(marker),
    )


def _production(
    *,
    scene_validated: bool = False,
    targets: tuple[str, ...] = (),
    state: ResourceVisualState = ResourceVisualState.UNCERTAIN,
) -> SimpleNamespace:
    return SimpleNamespace(
        scene_validated=scene_validated,
        definitive_target_ids=targets,
        resource_states=(SimpleNamespace(state=state),),
        detector_id="profiled-resource",
        detector_version="2.1.0",
        profile_id="varrock-east-iron-v1",
    )


def _drift_node(
    *,
    scene_validated: bool = False,
    targets: tuple[str, ...] = (),
    state: ResourceVisualState = ResourceVisualState.UNCERTAIN,
) -> SimpleNamespace:
    return SimpleNamespace(
        roles=(ViewRole.REAL_DRIFT,),
        current=False,
        production=_production(
            scene_validated=scene_validated,
            targets=targets,
            state=state,
        ),
    )


@dataclass
class _Graph:
    nodes: tuple[object, ...]
    edges: tuple[object, ...] = ()
    conclusion: str = "missing graph link: receipt-proven current-to-anchor edge"
    false_edge_count: int = 0
    missing_link: str | None = "receipt-proven current-to-anchor edge"
    offline_controller_path_available: bool = False
    policy: object = None

    def as_dict(self) -> dict[str, object]:
        return {
            "authority": {
                "can_authorize_camera_input": False,
                "can_validate_scene": False,
            },
            "conclusion": self.conclusion,
            "false_edge_count": self.false_edge_count,
            "reachability": {
                "offline_controller_path_available": (
                    self.offline_controller_path_available
                )
            },
        }


def _graph_with_exact_drift_gate() -> _Graph:
    current = SimpleNamespace(current=True, roles=(), production=_production())
    return _Graph(nodes=(current, *(_drift_node() for _ in range(36))))


def _corpus(tool: ModuleType) -> object:
    return tool.CorpusEvidence(
        specs=(),
        reviewed_anchor_sha256s=frozenset({"1" * 64}),
        reviewed_manifest_sha256="2" * 64,
        current_sha256="3" * 64,
        action_transitions=(),
        system_id_report_sha256="4" * 64,
        groups={"real_drift": {"count": 36}},
    )


@pytest.mark.parametrize(
    ("found_head", "clean", "message"),
    [
        (_OTHER_HEAD, True, "expected exact head"),
        (_HEAD, False, "worktree is not clean"),
    ],
)
def test_main_refuses_noncanonical_start_without_loading_or_publishing(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    found_head: str,
    clean: bool,
    message: str,
) -> None:
    report = tmp_path / "r1.json"
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "_git_state", lambda _root: (found_head, clean))

    def unexpected_load(_root: Path) -> object:
        raise AssertionError("the corpus must not load after provenance refusal")

    monkeypatch.setattr(tool, "load_canonical_corpus", unexpected_load)
    with pytest.raises(SystemExit, match=message):
        tool.main(["--expected-head", _HEAD, "--report", str(report)])
    assert not report.exists()
    assert not report.with_name(f"{report.name}.sha256").exists()


@pytest.mark.parametrize("mutation_call", [2, 3])
def test_main_retracts_or_refuses_report_if_git_provenance_changes(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_call: int,
) -> None:
    report = tmp_path / "r1.json"
    states = [(_HEAD, True), (_HEAD, True), (_HEAD, True)]
    states[mutation_call - 1] = (_OTHER_HEAD, True)
    calls = iter(states)
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "_git_state", lambda _root: next(calls))
    monkeypatch.setattr(tool, "load_canonical_corpus", lambda _root: _corpus(tool))
    monkeypatch.setattr(tool, "RobustRegistrationEngine", object)
    monkeypatch.setattr(
        tool,
        "build_read_only_view_graph",
        lambda *_args, **_kwargs: _graph_with_exact_drift_gate(),
    )
    with pytest.raises(SystemExit, match="Git provenance changed"):
        tool.main(["--expected-head", _HEAD, "--report", str(report)])
    assert not report.exists()
    assert not report.with_name(f"{report.name}.sha256").exists()


def test_main_writes_canonical_report_and_matching_sha256_sidecar(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "r1.json"
    graph = _graph_with_exact_drift_gate()
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "_git_state", lambda _root: (_HEAD, True))
    monkeypatch.setattr(tool, "load_canonical_corpus", lambda _root: _corpus(tool))
    monkeypatch.setattr(tool, "RobustRegistrationEngine", object)
    monkeypatch.setattr(
        tool,
        "build_read_only_view_graph",
        lambda *_args, **_kwargs: graph,
    )
    monkeypatch.setattr(tool, "robust_registration_algorithm_settings", lambda: {})
    monkeypatch.setattr(tool, "robust_registration_environment", lambda: {})

    result = tool.main(
        ["--expected-head", _HEAD, "--report", str(report)]
    )

    report_bytes = report.read_bytes()
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    sidecar = report.with_name(f"{report.name}.sha256")
    assert result == 0
    assert report_bytes.endswith(b"\n")
    assert sidecar.read_text(encoding="ascii") == f"{report_sha}\n"
    payload = json.loads(report_bytes)
    assert payload["provenance"]["git_head_sha"] == _HEAD
    assert payload["provenance"]["tracked_worktree_clean"] is True
    assert payload["evidence"]["drift_safety"] == {
        "detector_ids": ["profiled-resource@2.1.0"],
        "expected_frames": 36,
        "false_definitive_target_count": 0,
        "passed": True,
        "uncertain_frames": 36,
    }
    assert payload["evidence"]["result"] == {
        "conclusion": graph.conclusion,
        "false_edge_count": 0,
        "missing_link": graph.missing_link,
        "offline_controller_path_available": False,
    }
    output = capsys.readouterr().out
    assert "36/36 UNCERTAIN, 0 false definitive targets" in output
    assert f"Report SHA-256: {report_sha}" in output


def test_drift_gate_requires_exactly_36_uncertain_frames_and_zero_targets(
    tool: ModuleType,
) -> None:
    exact = _Graph(nodes=tuple(_drift_node() for _ in range(36)))
    short = _Graph(nodes=tuple(_drift_node() for _ in range(35)))
    definitive = _Graph(
        nodes=(*tuple(_drift_node() for _ in range(35)), _drift_node(targets=("x",)))
    )
    exposed_state = _Graph(
        nodes=(
            *tuple(_drift_node() for _ in range(35)),
            _drift_node(state=ResourceVisualState.AVAILABLE),
        )
    )
    scene_pass = _Graph(
        nodes=(
            *tuple(_drift_node() for _ in range(35)),
            _drift_node(scene_validated=True),
        )
    )

    assert tool._drift_gate(exact)["passed"] is True
    assert tool._drift_gate(short)["passed"] is False
    definitive_result = tool._drift_gate(definitive)
    assert definitive_result["passed"] is False
    assert definitive_result["false_definitive_target_count"] == 1
    assert tool._drift_gate(exposed_state)["passed"] is False
    assert tool._drift_gate(scene_pass)["passed"] is False


def test_action_receipts_bind_exact_corpus_endpoints_and_report_digest(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ViewNodeSpec("source", _frame(1), ViewRole.SYSTEM_IDENTIFICATION)
    middle = ViewNodeSpec("middle", _frame(2), ViewRole.SYSTEM_IDENTIFICATION)
    target = ViewNodeSpec("target", _frame(3), ViewRole.SYSTEM_IDENTIFICATION)
    source_sha = hashlib.sha256(source.frame.payload).hexdigest()
    middle_sha = hashlib.sha256(middle.frame.payload).hexdigest()
    target_sha = hashlib.sha256(target.frame.payload).hexdigest()
    report_path = tmp_path / "system-id.json"
    payload = _action_report(source_sha, middle_sha, target_sha)
    report_bytes = (json.dumps(payload, sort_keys=True) + "\n").encode()
    report_path.write_bytes(report_bytes)
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    report_path.with_name(f"{report_path.name}.sha256").write_text(
        report_sha,
        encoding="ascii",
    )
    monkeypatch.setattr(tool, "_SYSTEM_ID_REPORT", Path(report_path.name))

    found_sha, transitions = tool._load_action_transitions(
        tmp_path,
        (source, middle, target),
    )

    assert found_sha == report_sha
    assert [item.action_id for item in transitions] == [
        "horizontal-positive-fixed-4px",
        "horizontal-return-fixed-4px",
    ]
    assert [
        (item.source_sha256, item.target_sha256) for item in transitions
    ] == [(source_sha, middle_sha), (middle_sha, target_sha)]
    assert all(item.receipt_verified for item in transitions)
    assert all(item.evidence_report_sha256 == report_sha for item in transitions)


def test_canonical_corpus_loader_enforces_fixed_group_membership_and_current(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "supported" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"schema_version": 1}\n', encoding="utf-8")
    drift_root = tmp_path / "drift"
    system_root = tmp_path / "system"
    drift_root.mkdir()
    system_root.mkdir()
    for index in range(36):
        (drift_root / f"northwest-full-cycle-{index:02d}.raw").touch()
    for index in range(7):
        (system_root / f"complete-{index:02d}.raw").touch()
    (system_root / "complete-07-return-post.raw").touch()
    for index in range(2):
        (system_root / f"risky-{index:02d}.raw").touch()
    (tmp_path / "extra.raw").touch()
    (tmp_path / "disconnected.raw").touch()

    reviewed_samples = tuple(
        SimpleNamespace(case=SimpleNamespace(case_id=f"case-{index}"), frame=_frame(index + 1))
        for index in range(5)
    )

    def read_frame(path: Path, *, frame_id: int) -> Frame:
        marker = hashlib.sha256(path.as_posix().encode()).digest()[0]
        return Frame.from_raw(
            RawFrame(
                payload=bytes((marker, frame_id % 256, 0, 255)),
                width=1,
                height=1,
                pixel_format=PixelFormat.BGRA8888,
            ),
            frame_id=frame_id,
            captured_monotonic_s=float(frame_id),
        )

    monkeypatch.setattr(tool, "_SUPPORTED_MANIFEST", Path("supported/manifest.json"))
    monkeypatch.setattr(tool, "_DRIFT_DIR", Path("drift"))
    monkeypatch.setattr(tool, "_SYSTEM_ID_FRAMES", Path("system"))
    monkeypatch.setattr(tool, "_COMPLETE_SYSTEM_ID_TOKEN", "complete-")
    monkeypatch.setattr(tool, "_RISKY_SYSTEM_ID_TOKEN", "risky-")
    monkeypatch.setattr(tool, "_CURRENT_SUFFIX", "return-post.raw")
    monkeypatch.setattr(
        tool,
        "_EXTRA_VIEWS",
        (("other:test", ViewRole.OTHER_UNSUPPORTED, Path("extra.raw")),),
    )
    monkeypatch.setattr(
        tool,
        "_DISCONNECTED_VIEWS",
        (("disconnected:test", ViewRole.DISCONNECTED, Path("disconnected.raw")),),
    )
    monkeypatch.setattr(
        tool,
        "materialize_gzip_replay_dataset",
        lambda _manifest, destination: destination / "manifest.json",
    )
    monkeypatch.setattr(tool, "load_replay_dataset", lambda _path: reviewed_samples)
    monkeypatch.setattr(tool, "_read_raw_frame", read_frame)
    monkeypatch.setattr(
        tool,
        "_load_action_transitions",
        lambda _root, _specs: ("f" * 64, ()),
    )

    corpus = tool.load_canonical_corpus(tmp_path)

    assert len(corpus.specs) == 5 + 36 + 8 + 2 + 1 + 1
    assert len(corpus.reviewed_anchor_sha256s) == 5
    assert corpus.groups["reviewed_supported"]["count"] == 5
    assert corpus.groups["real_drift"]["count"] == 36
    assert corpus.groups["system_identification"]["count"] == 8
    assert corpus.groups["risky_state_change"]["count"] == 2
    current = next(
        spec for spec in corpus.specs if spec.label.endswith("return-post.raw")
    )
    assert corpus.current_sha256 == hashlib.sha256(current.frame.payload).hexdigest()
    assert corpus.system_id_report_sha256 == "f" * 64

    (drift_root / "northwest-full-cycle-35.raw").unlink()
    with pytest.raises(ValueError, match="must contain 36 frames, got 35"):
        tool.load_canonical_corpus(tmp_path)


def _action_report(
    source_sha: str,
    middle_sha: str,
    target_sha: str,
) -> dict[str, object]:
    def step(before: str, after: str) -> dict[str, object]:
        return {
            "commit": {"artifact": {"raw_sha256": before}},
            "input_state": "complete",
            "post": {"frame": {"artifact": {"raw_sha256": after}}},
            "receipt": {
                "actions": [
                    {
                        "input_receipts": [
                            {"complete": True},
                            {"complete": True},
                            {"complete": True},
                        ]
                    }
                ]
            },
            "terminal_reason": "complete",
        }

    return {
        "evidence": {
            "horizontal": {
                "complete": True,
                "positive_step": step(source_sha, middle_sha),
                "return_step": step(middle_sha, target_sha),
            },
            "pointer_mapping": {
                "steps": [
                    {
                        "complete_receipt": True,
                        "middle_release_acknowledged": True,
                    },
                    {
                        "complete_receipt": True,
                        "middle_release_acknowledged": True,
                    },
                ]
            },
        }
    }


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_report_parser_rejects_nonfinite_json_numbers(
    tool: ModuleType,
    constant: str,
) -> None:
    with pytest.raises(ValueError, match="non-standard JSON number"):
        tool._load_json_bytes(f'{{"value": {constant}}}'.encode())


def test_report_parser_rejects_duplicate_keys_at_any_depth(tool: ModuleType) -> None:
    with pytest.raises(ValueError, match="duplicate JSON key: complete"):
        tool._load_json_bytes(
            b'{"evidence": {"complete": true, "complete": false}}'
        )


def test_build_report_evidence_summarizes_models_rejections_and_authority(
    tool: ModuleType,
) -> None:
    accepted = SimpleNamespace(
        accepted=True,
        selected_family=ModelFamily.SIMILARITY,
        disposition=RegistrationDisposition.ACCEPTED,
    )
    rejected = SimpleNamespace(
        accepted=False,
        selected_family=None,
        disposition=RegistrationDisposition.GLOBAL_MODEL_INADEQUATE,
    )
    edges = (
        SimpleNamespace(registration=accepted),
        SimpleNamespace(registration=rejected),
        SimpleNamespace(registration=None),
    )
    graph = _Graph(nodes=(), edges=cast(tuple[object, ...], edges))
    drift = {
        "expected_frames": 36,
        "uncertain_frames": 36,
        "false_definitive_target_count": 0,
        "passed": True,
    }

    evidence = tool.build_report_evidence(
        graph,
        _corpus(tool),
        drift,
        head_before=_HEAD,
        clean_before=True,
        head_after=_HEAD,
        clean_after=True,
    )

    assert evidence["authority"] == {
        "development_validation_only": True,
        "diagnostic_registration_can_override_production": False,
        "new_live_camera_input_performed": False,
        "production_detector_remains_sole_scene_authority": True,
        "registration_can_authorize_camera_input": False,
        "registration_can_expose_resources": False,
        "registration_can_validate_scene": False,
    }
    summary = cast(dict[str, object], evidence["model_selection_summary"])
    assert summary["accepted_selected_families"] == {"similarity": 1}
    assert summary["rejected_dispositions"] == {
        "global_model_inadequate": 1,
        "pre_registration_veto": 1,
    }
    assert evidence["result"] == {
        "conclusion": graph.conclusion,
        "false_edge_count": 0,
        "missing_link": graph.missing_link,
        "offline_controller_path_available": False,
    }
