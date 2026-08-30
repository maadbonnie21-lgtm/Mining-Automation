"""Deterministic contracts for the read-only Issue #31 R2 analysis tool."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame

_TOOL_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "analyze_issue31_bridge_r2.py"
)
_HEAD = "a" * 40
_OTHER_HEAD = "b" * 40


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "analyze_issue31_bridge_r2_test",
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


def _raw_payload(marker: int, *, exact_geometry: bool = False) -> bytes:
    pixel = bytes((marker, marker, marker, 255))
    return pixel * (1005 * 1078 if exact_geometry else 1)


def _frame(marker: int) -> Frame:
    return Frame.from_raw(
        RawFrame(
            payload=_raw_payload(marker),
            width=1,
            height=1,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=marker,
        captured_monotonic_s=float(marker),
    )


def _write_report(path: Path, payload: dict[str, object]) -> str:
    report_bytes = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(report_bytes)
    digest = hashlib.sha256(report_bytes).hexdigest()
    path.with_name(f"{path.name}.sha256").write_text(
        f"{digest}\n", encoding="ascii", newline="\n"
    )
    return digest


def _provenance(
    *, head: str, plan_id: str, plan_version: str
) -> dict[str, object]:
    return {
        "command_argv": ["tool.py"],
        "detector_id": "profiled-resource:varrock-east-iron-v1",
        "detector_version": "2.1.0",
        "git_head_sha": head,
        "plan_id": plan_id,
        "plan_version": plan_version,
        "profile_id": "varrock-east-iron-v1",
        "tracked_worktree_clean": True,
    }


def _fail_closed_production() -> dict[str, object]:
    return {
        "definitive_target_ids": [],
        "passed": False,
        "resources": [
            {
                "definitive": False,
                "resource_id": f"rock-{index}",
                "state": "uncertain",
            }
            for index in range(4)
        ],
    }


def _receipt(plan: dict[str, object]) -> dict[str, object]:
    actions = plan["actions"]
    assert isinstance(actions, list)
    records: list[dict[str, object]] = []
    for index, action_value in enumerate(actions):
        assert isinstance(action_value, dict)
        kind = action_value["kind"]
        if kind == "pause":
            low_level: list[dict[str, object]] = []
        elif kind == "compass_click":
            low_level = [
                {
                    "complete": True,
                    "completed_events": 2,
                    "operation": "compass_click",
                    "requested_events": 2,
                }
            ]
        else:
            low_level = [
                {
                    "complete": True,
                    "completed_events": 1,
                    "operation": "key_down",
                    "requested_events": 1,
                },
                {
                    "complete": True,
                    "completed_events": 1,
                    "operation": "key_up",
                    "requested_events": 1,
                },
            ]
        records.append(
            {
                "action": action_value,
                "action_index": index,
                "input_receipts": low_level,
            }
        )
    return {
        "actions": records,
        "plan": plan,
        "preflight": {
            "client_height": 1078,
            "client_width": 1005,
            "focused": True,
            "supported": True,
        },
    }


def _north_payload(tool: ModuleType, frame_sha: str) -> dict[str, object]:
    plan = {
        "actions": [{"kind": "compass_click", "x": 608, "y": 49}],
        "name": "issue31-v2-01-heading-north",
    }
    return {
        "evidence": {
            "command": "north-bootstrap-v2",
            "development_only": True,
            "frames": {
                "post": {
                    "artifact": {
                        "files": {
                            "raw": (
                                "frames/issue31-north-20260829-153603-"
                                "v2-post.raw"
                            )
                        },
                        "height": 1078,
                        "pixel_format": "bgra8888",
                        "raw_sha256": frame_sha,
                        "width": 1005,
                    },
                    "production": _fail_closed_production(),
                }
            },
            "input": {"attempted": True, "completed": True, "state": "complete"},
            "plan": plan,
            "receipt": _receipt(plan),
            "terminal_reason": "bootstrap_executed",
        },
        "provenance": _provenance(
            head=tool._NORTH_REPORT_HEAD,
            plan_id="issue31-world-only-multi-axis-guidance",
            plan_version="2.0.0",
        ),
        "schema_version": 2,
    }


def _reset_payload(
    tool: ModuleType,
    spec: object,
    frame_sha: str,
) -> dict[str, object]:
    plan = tool._reset_plan(spec.plan_name)
    artifact = {
        "files": {"raw": f"frames/{spec.frame_path.name}"},
        "height": 1078,
        "pixel_format": "bgra8888",
        "raw_sha256": frame_sha,
        "width": 1005,
    }
    return {
        "evidence": {
            "initial_normalization": {
                "attempts": [
                    {
                        "candidate_frame": {
                            "artifact": artifact,
                            "production": _fail_closed_production(),
                        },
                        "counts_as_confirmation": False,
                        "identity": spec.plan_name,
                        "index_1_based": 1,
                        "plan": plan,
                        "production_gate_passed": False,
                        "receipt": _receipt(plan),
                    }
                ]
            },
            "normalization_strategy": {
                "candidates": [
                    {
                        "actions": plan["actions"],
                        "index_1_based": 1,
                        "name": spec.plan_name,
                    }
                ],
                "diagnostic_registration_used": False,
                "id": spec.plan_name,
                "selection_authority": "unchanged_production_camera_evaluation",
                "version": "0.0.0",
            },
        },
        "provenance": _provenance(
            head=tool._RESET_REPORT_HEAD,
            plan_id=spec.plan_name,
            plan_version="0.0.0",
        ),
        "schema_version": 2,
    }


def _r1_payload(tool: ModuleType, *, head: str = _HEAD) -> dict[str, object]:
    current_sha = "1" * 64
    negative_specs = [
        (hashlib.sha256(f"negative-{index}".encode()).hexdigest(), role)
        for index, role in enumerate(
            (
                "disconnected",
                "disconnected",
                "disconnected",
                "risky_state_change",
                "risky_state_change",
            )
        )
    ]
    other_sha256s = [
        hashlib.sha256(f"other-{index}".encode()).hexdigest()
        for index in range(4)
    ]
    negative_edge_ids = sorted(
        {
            ":".join(sorted((negative_sha256, other_sha256)))
            for negative_sha256, _role in negative_specs
            for other_sha256 in other_sha256s
        }
    )[:19]
    supported = [
        {"label": f"supported:case-{index}", "raw_sha256": str(index) * 64}
        for index in range(2, 7)
    ]
    missing = tool._MISSING_LINK
    return {
        "evidence": {
            "authority": {
                "diagnostic_registration_can_override_production": False,
                "new_live_camera_input_performed": False,
                "production_detector_remains_sole_scene_authority": True,
                "registration_can_authorize_camera_input": False,
                "registration_can_expose_resources": False,
                "registration_can_validate_scene": False,
            },
            "corpus": {
                "groups": {
                    "reviewed_supported": {"count": 5, "items": supported}
                },
                "reviewed_manifest_path": tool._SUPPORTED_MANIFEST.as_posix(),
                "reviewed_manifest_sha256": "7" * 64,
            },
            "git": {
                "after": {"head_sha": head, "worktree_clean": True},
                "before": {"head_sha": head, "worktree_clean": True},
                "exact_head_stable": True,
            },
            "result": {
                "conclusion": f"missing graph link: {missing}",
                "false_edge_count": 19,
                "false_path_count": 0,
                "missing_link": missing,
                "negative_failure_count": 19,
                "offline_controller_path_available": False,
            },
            "view_graph": {
                "current_sha256": current_sha,
                "graph_id": tool.ROBUST_VIEW_GRAPH_ID,
                "graph_version": tool.ROBUST_VIEW_GRAPH_VERSION,
                "negative_corpus": {
                    "accepted_pairwise_edge_count": 19,
                    "accepted_pairwise_edge_ids": negative_edge_ids,
                    "aggregate_failure_count": 19,
                    "cycle_verified_edge_count": 19,
                    "cycle_verified_edge_ids": negative_edge_ids,
                    "nodes": [
                        {
                            "has_verified_path_to_supported": False,
                            "roles": [role],
                            "sha256": digest,
                            "verified_path_to_supported": None,
                        }
                        for digest, role in negative_specs
                    ],
                    "policy_roles": ["disconnected", "risky_state_change"],
                    "supported_path_count": 0,
                },
                "components": [
                    [digest] for digest, _role in negative_specs
                ],
                "nodes": [
                    {
                        "current": True,
                        "labels": [f"system-id:{tool._CURRENT_FRAME.as_posix()}"],
                        "sha256": current_sha,
                    }
                ],
                "reviewed_manifest_sha256": "7" * 64,
            },
        },
        "provenance": _provenance(
            head=head,
            plan_id=tool.ROBUST_VIEW_GRAPH_ID,
            plan_version=tool.ROBUST_VIEW_GRAPH_VERSION,
        ),
        "schema_version": 2,
    }


def _named(tool: ModuleType, label: str, marker: int) -> object:
    frame = _frame(marker)
    return tool.NamedFrame(
        label=label,
        frame=frame,
        path=f"{label}.raw",
        sha256=hashlib.sha256(frame.payload).hexdigest(),
    )


def _endpoint(tool: ModuleType, label: str, marker: int) -> object:
    named = _named(tool, label, marker)
    plan = {"actions": [], "name": label}
    report = tool.VerifiedReport(
        path=f"{label}.json",
        sha256=str(marker) * 64,
        payload={"provenance": _provenance(head=_HEAD, plan_id=label, plan_version="1")},
    )
    return tool.VerifiedEndpoint(named, plan, {"actions": [], "plan": plan}, report)


def _corpus(tool: ModuleType) -> object:
    r1_report = tool.VerifiedReport(
        path="r1.json",
        sha256="8" * 64,
        payload={},
    )
    r1 = tool.R1Evidence(
        report=r1_report,
        provenance=_provenance(
            head=_HEAD,
            plan_id=tool.ROBUST_VIEW_GRAPH_ID,
            plan_version=tool.ROBUST_VIEW_GRAPH_VERSION,
        ),
        authority={"registration_can_authorize_camera_input": False},
        result={"conclusion": f"missing graph link: {tool._MISSING_LINK}"},
        current_sha256="1" * 64,
        reviewed_manifest_sha256="2" * 64,
        supported_items=(),
        negative_corpus={"policy_roles": ["disconnected", "risky_state_change"]},
    )
    return tool.BridgeCorpus(
        r1=r1,
        current=_named(tool, "current", 1),
        north=_endpoint(tool, "north", 2),
        resets=(
            _endpoint(tool, "reset-1", 3),
            _endpoint(tool, "reset-3", 4),
        ),
        anchors=tuple(
            _named(tool, f"supported-{index}", index) for index in range(5, 10)
        ),
    )


class _Registration:
    def __init__(self, source_sha: str, target_sha: str) -> None:
        self._source_sha = source_sha
        self._target_sha = target_sha

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": False,
            "source_sha256": self._source_sha,
            "target_sha256": self._target_sha,
        }


class _Engine:
    def analyze(self, source: Frame, target: Frame) -> _Registration:
        return _Registration(
            hashlib.sha256(source.payload).hexdigest(),
            hashlib.sha256(target.payload).hexdigest(),
        )


class _PairEdge:
    def __init__(self, first_sha256: str, second_sha256: str) -> None:
        self.edge_id = ":".join(sorted((first_sha256, second_sha256)))

    def as_dict(self, _policy: object) -> dict[str, object]:
        return {"edge_id": self.edge_id, "verified": False}


def _pair_graph(corpus: object) -> SimpleNamespace:
    resets = corpus.resets
    pairs = [(corpus.current, corpus.north.named_frame)]
    pairs.extend((corpus.north.named_frame, anchor) for anchor in corpus.anchors)
    pairs.extend(
        (
            (corpus.current, resets[0].named_frame),
            (corpus.current, resets[1].named_frame),
            (resets[0].named_frame, resets[1].named_frame),
        )
    )
    pairs.extend(
        (reset.named_frame, anchor)
        for reset in resets
        for anchor in corpus.anchors
    )
    graph = SimpleNamespace(
        edges=tuple(_PairEdge(first.sha256, second.sha256) for first, second in pairs),
        policy=object(),
    )
    graph.as_dict = lambda: {
        "authority": {
            "can_accept": False,
            "can_authorize_camera_input": False,
            "can_expose_resources": False,
            "can_validate_scene": False,
            "diagnostic_registration_can_override_production": False,
        },
        "current_sha256": corpus.north.named_frame.sha256,
    }
    return graph


def _no_safe_planner(tool: ModuleType) -> SimpleNamespace:
    return SimpleNamespace(
        disposition=tool.BridgePlannerDisposition.NO_SAFE_ENDPOINT_EVIDENCE,
        missing_experiment=None,
        as_dict=lambda: {
            "authority": {
                "can_accept": False,
                "can_authorize_camera_input": False,
                "can_expose_resources": False,
                "can_validate_scene": False,
                "diagnostic_registration_can_override_production": False,
            },
            "disposition": "no_safe_endpoint_evidence",
            "missing_experiment": None,
            "ranked_families": [],
        },
    )


def _patch_no_safe_analysis(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = _corpus(tool)
    graph = _pair_graph(corpus)
    planner = _no_safe_planner(tool)
    monkeypatch.setattr(tool, "load_bridge_corpus", lambda *_args, **_kwargs: corpus)
    monkeypatch.setattr(
        tool,
        "build_bridge_graph",
        lambda *_args, **_kwargs: graph,
    )
    monkeypatch.setattr(tool, "build_endpoint_evidence", lambda *_args: ())
    monkeypatch.setattr(tool, "plan_camera_bridge", lambda *_args: planner)


def test_verified_report_rejects_mismatched_sidecar(
    tool: ModuleType, tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    report.write_text("{}\n", encoding="utf-8", newline="\n")
    report.with_name(f"{report.name}.sha256").write_text(
        f"{'0' * 64}\n", encoding="ascii", newline="\n"
    )

    with pytest.raises(ValueError, match="sidecar mismatch"):
        tool._load_verified_report(tmp_path, report, expected_sha256=None)


def test_verified_report_rejects_duplicate_and_nonfinite_json(
    tool: ModuleType, tmp_path: Path
) -> None:
    for name, payload, message in (
        ("duplicate", b'{"value":1,"value":2}\n', "duplicate JSON key"),
        ("nan", b'{"value":NaN}\n', "non-standard JSON number"),
        ("overflow", b'{"value":1e9999}\n', "non-finite JSON number"),
    ):
        report = tmp_path / f"{name}.json"
        report.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        report.with_name(f"{report.name}.sha256").write_text(
            f"{digest}\n", encoding="ascii", newline="\n"
        )
        with pytest.raises(ValueError, match=message):
            tool._load_verified_report(
                tmp_path,
                report,
                expected_sha256=digest,
            )


def test_r1_loader_requires_exact_quarantined_negative_membership(
    tool: ModuleType, tmp_path: Path
) -> None:
    report = tmp_path / "r1.json"
    payload = _r1_payload(tool)
    digest = _write_report(report, payload)

    loaded = tool._load_r1_evidence(
        tmp_path,
        report,
        expected_head=_HEAD,
        expected_sha256=digest,
    )
    assert len(loaded.negative_corpus["nodes"]) == 5

    graph = payload["evidence"]["view_graph"]
    assert isinstance(graph, dict)
    components = graph["components"]
    assert isinstance(components, list)
    assert isinstance(components[0], list)
    components[0].append("f" * 64)
    mutated = tmp_path / "r1-mutated.json"
    mutated_digest = _write_report(mutated, payload)
    with pytest.raises(ValueError, match="escaped quarantine"):
        tool._load_r1_evidence(
            tmp_path,
            mutated,
            expected_head=_HEAD,
            expected_sha256=mutated_digest,
        )


def test_r1_provenance_must_match_requested_exact_head(
    tool: ModuleType, tmp_path: Path
) -> None:
    report = tmp_path / "r1.json"
    digest = _write_report(report, _r1_payload(tool, head=_OTHER_HEAD))

    with pytest.raises(ValueError, match="R1 provenance git_head_sha"):
        tool._load_r1_evidence(
            tmp_path,
            report,
            expected_head=_HEAD,
            expected_sha256=digest,
        )


@pytest.mark.parametrize("mutation", ["plan", "receipt"])
def test_north_endpoint_rejects_wrong_plan_or_incomplete_receipt(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    frame_sha = "c" * 64
    payload = _north_payload(tool, frame_sha)
    evidence = payload["evidence"]
    assert isinstance(evidence, dict)
    if mutation == "plan":
        plan = evidence["plan"]
        assert isinstance(plan, dict)
        plan["name"] = "wrong-plan"
        match = "plan mismatch"
    else:
        receipt = evidence["receipt"]
        assert isinstance(receipt, dict)
        actions = receipt["actions"]
        assert isinstance(actions, list) and isinstance(actions[0], dict)
        low_level = actions[0]["input_receipts"]
        assert isinstance(low_level, list) and isinstance(low_level[0], dict)
        low_level[0]["complete"] = False
        match = "low-level input receipt"
    report = tmp_path / "north.json"
    report_sha = _write_report(report, payload)
    monkeypatch.setattr(tool, "_NORTH_REPORT", Path("north.json"))
    monkeypatch.setattr(tool, "_NORTH_REPORT_SHA256", report_sha)
    monkeypatch.setattr(tool, "_NORTH_FRAME", Path("north.raw"))
    monkeypatch.setattr(tool, "_NORTH_FRAME_SHA256", frame_sha)

    with pytest.raises(ValueError, match=match):
        tool._load_north_endpoint(tmp_path, frame_id=1)


def test_north_endpoint_rejects_frame_digest_different_from_report(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported_frame_sha = "c" * 64
    report = tmp_path / "north.json"
    report_sha = _write_report(report, _north_payload(tool, reported_frame_sha))
    (tmp_path / "north.raw").write_bytes(b"different")
    monkeypatch.setattr(tool, "_NORTH_REPORT", Path("north.json"))
    monkeypatch.setattr(tool, "_NORTH_REPORT_SHA256", report_sha)
    monkeypatch.setattr(tool, "_NORTH_FRAME", Path("north.raw"))
    monkeypatch.setattr(tool, "_NORTH_FRAME_SHA256", reported_frame_sha)

    with pytest.raises(ValueError, match="exact frame SHA-256 mismatch"):
        tool._load_north_endpoint(tmp_path, frame_id=1)


def test_frozen_north_and_reset_reports_bind_complete_receipts_and_exact_frames(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    north_payload = _raw_payload(11, exact_geometry=True)
    north_frame_sha = hashlib.sha256(north_payload).hexdigest()
    (tmp_path / "north.raw").write_bytes(north_payload)
    north_report = tmp_path / "north.json"
    north_report_sha = _write_report(
        north_report, _north_payload(tool, north_frame_sha)
    )
    monkeypatch.setattr(tool, "_NORTH_REPORT", Path("north.json"))
    monkeypatch.setattr(tool, "_NORTH_REPORT_SHA256", north_report_sha)
    monkeypatch.setattr(tool, "_NORTH_FRAME", Path("north.raw"))
    monkeypatch.setattr(tool, "_NORTH_FRAME_SHA256", north_frame_sha)

    template = tool._RESET_SPECS[0]
    reset_path = Path("reset.raw")
    reset_payload = _raw_payload(12, exact_geometry=True)
    reset_frame_sha = hashlib.sha256(reset_payload).hexdigest()
    (tmp_path / reset_path).write_bytes(reset_payload)
    provisional = tool.FrozenEndpointSpec(
        label="reset:test",
        report_path=Path("reset.json"),
        report_sha256="0" * 64,
        frame_path=reset_path,
        frame_sha256=reset_frame_sha,
        plan_name=template.plan_name,
    )
    reset_report = tmp_path / provisional.report_path
    reset_report_sha = _write_report(
        reset_report, _reset_payload(tool, provisional, reset_frame_sha)
    )
    reset_spec = tool.FrozenEndpointSpec(
        label=provisional.label,
        report_path=provisional.report_path,
        report_sha256=reset_report_sha,
        frame_path=provisional.frame_path,
        frame_sha256=provisional.frame_sha256,
        plan_name=provisional.plan_name,
    )

    north = tool._load_north_endpoint(tmp_path, frame_id=1)
    reset = tool._load_reset_endpoint(tmp_path, reset_spec, frame_id=2)

    assert north.named_frame.sha256 == north_frame_sha
    assert reset.named_frame.sha256 == reset_frame_sha
    assert len(reset.receipt["actions"]) == 6


def test_pairwise_matrix_contains_every_required_relationship(
    tool: ModuleType,
) -> None:
    corpus = _corpus(tool)
    pairwise = tool.analyze_bridge_pairs(corpus, _pair_graph(corpus))

    relationships = [item["relationship"] for item in pairwise]
    assert len(pairwise) == 19
    assert relationships.count("current_to_north") == 1
    assert relationships.count("north_to_anchor") == 5
    assert relationships.count("current_to_reset") == 2
    assert relationships.count("reset_repeat_to_repeat") == 1
    assert relationships.count("reset_to_anchor") == 10
    assert all("edge" in item for item in pairwise)


def test_bridge_graph_uses_authenticated_north_origin_and_no_action_edges(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = _corpus(tool)
    captured: dict[str, object] = {}
    expected_graph = SimpleNamespace(
        action_path_to_supported=None,
        action_transitions=(),
        false_edge_count=0,
        false_path_count=0,
        negative_nodes=(),
    )

    def build(specs: object, **kwargs: object) -> object:
        captured["specs"] = specs
        captured.update(kwargs)
        return expected_graph

    monkeypatch.setattr(tool, "build_read_only_view_graph", build)
    graph = tool.build_bridge_graph(corpus, registration_engine=_Engine())

    assert graph is expected_graph
    assert captured["current_sha256"] == corpus.north.named_frame.sha256
    assert captured["action_transitions"] == ()
    assert captured["reviewed_anchor_sha256s"] == frozenset(
        anchor.sha256 for anchor in corpus.anchors
    )
    specs = captured["specs"]
    assert isinstance(specs, tuple)
    roles = [spec.role for spec in specs]
    assert roles.count(tool.ViewRole.SYSTEM_IDENTIFICATION) == 1
    assert roles.count(tool.ViewRole.OTHER_UNSUPPORTED) == 3
    assert roles.count(tool.ViewRole.REVIEWED_SUPPORTED) == 5
    assert not any(
        role in {tool.ViewRole.DISCONNECTED, tool.ViewRole.RISKY_STATE_CHANGE}
        for role in roles
    )


@pytest.mark.parametrize(
    ("found_head", "clean", "message"),
    [
        (_OTHER_HEAD, True, "expected exact head"),
        (_HEAD, False, "worktree is not clean"),
    ],
)
def test_main_refuses_noncanonical_git_start(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    found_head: str,
    clean: bool,
    message: str,
) -> None:
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "_git_state", lambda _root: (found_head, clean))

    with pytest.raises(SystemExit, match=message):
        tool.main(
            [
                "--expected-head",
                _HEAD,
                "--r1-report",
                "r1.json",
                "--r1-sha256",
                "8" * 64,
                "--report",
                "r2.json",
            ]
        )
    assert not (tmp_path / "r2.json").exists()


@pytest.mark.parametrize("mutation_call", [2, 3])
def test_main_requires_stable_git_provenance_and_retracts_publication(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_call: int,
) -> None:
    states = [(_HEAD, True), (_HEAD, True), (_HEAD, True)]
    states[mutation_call - 1] = (_OTHER_HEAD, True)
    calls = iter(states)
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "_git_state", lambda _root: next(calls))
    _patch_no_safe_analysis(tool, monkeypatch)
    monkeypatch.setattr(tool, "RobustRegistrationEngine", _Engine)
    monkeypatch.setattr(tool, "robust_registration_algorithm_settings", dict)
    monkeypatch.setattr(tool, "robust_registration_environment", dict)

    with pytest.raises(SystemExit, match="Git provenance changed"):
        tool.main(
            [
                "--expected-head",
                _HEAD,
                "--r1-report",
                "r1.json",
                "--r1-sha256",
                "8" * 64,
                "--report",
                str(tmp_path / "r2.json"),
            ]
        )
    assert not (tmp_path / "r2.json").exists()
    assert not (tmp_path / "r2.json.sha256").exists()


def test_main_report_is_deterministic_and_entirely_non_authorizing(
    tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "r2.json"
    arguments = [
        "--expected-head",
        _HEAD,
        "--r1-report",
        "r1.json",
        "--r1-sha256",
        "8" * 64,
        "--report",
        str(report),
    ]
    monkeypatch.setattr(tool, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "_git_state", lambda _root: (_HEAD, True))
    _patch_no_safe_analysis(tool, monkeypatch)
    monkeypatch.setattr(tool, "RobustRegistrationEngine", _Engine)
    monkeypatch.setattr(tool, "robust_registration_algorithm_settings", dict)
    monkeypatch.setattr(tool, "robust_registration_environment", dict)

    assert tool.main(arguments) == 1
    first = report.read_bytes()
    first_sha = hashlib.sha256(first).hexdigest()
    assert report.with_name(f"{report.name}.sha256").read_text(
        encoding="ascii"
    ) == f"{first_sha}\n"
    payload = json.loads(first)
    assert len(payload["evidence"]["pairwise_registrations"]) == 19
    assert set(payload["evidence"]["authority"].values()) == {False}
    assert payload["evidence"]["bridge_planner"]["disposition"] == (
        "no_safe_endpoint_evidence"
    )
    assert payload["evidence"]["bridge_planner"]["ranked_families"] == []
    assert payload["evidence"]["result"] == {
        "conclusion": "no safe endpoint evidence",
        "live_input_authorized": False,
        "reacquisition_success_claimed": False,
        "selected_experiment_id": None,
        "smallest_additional_evidence": tool._R2_MISSING_EVIDENCE,
    }

    report.unlink()
    report.with_name(f"{report.name}.sha256").unlink()
    assert tool.main(arguments) == 1
    assert report.read_bytes() == first
