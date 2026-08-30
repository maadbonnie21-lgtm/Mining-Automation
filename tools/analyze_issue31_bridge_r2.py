"""Build the canonical read-only Issue #31 R2 bridge evidence package.

This tool inventories exact saved camera receipts and recomputes robust world
registration between the only evidence-backed bridge candidates.  It never
imports an input adapter, executes camera input, or promotes a diagnostic fit
to production scene authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    load_replay_dataset,
    materialize_gzip_replay_dataset,
)
from mining_automation.validation.camera_bridge_planner import (
    FROZEN_ENDPOINT_FAMILY_ID,
    FROZEN_ENDPOINT_OBJECTIVE_ID,
    BridgePlannerDisposition,
    CameraBridgePlannerEvidence,
    MeasuredEndpointEvidence,
    plan_camera_bridge,
)
from mining_automation.validation.camera_report import (
    CameraReportProvenance,
    write_camera_validation_report,
)
from mining_automation.validation.robust_registration import (
    RobustRegistrationEngine,
    robust_registration_algorithm_settings,
    robust_registration_environment,
)
from mining_automation.validation.robust_view_graph import (
    ROBUST_VIEW_GRAPH_ID,
    ROBUST_VIEW_GRAPH_VERSION,
    GraphEdgeEvidence,
    ReadOnlyViewGraph,
    ViewNodeSpec,
    ViewRole,
    build_read_only_view_graph,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUPPORTED_MANIFEST = Path(
    "tests/fixtures/perception/varrock-east-iron-v1/manifest.json"
)
_CURRENT_FRAME = Path(
    "diagnostics/issue31-camera-system-id-v2/frames/"
    "issue31-system-id-eaaad585-20260829-182319-"
    "system-id-horizontal-return-post.raw"
)
_NORTH_REPORT = Path(
    "diagnostics/issue31-camera-reacquisition-v2/reports/"
    "issue31-north-20260829-153603.camera.json"
)
_NORTH_FRAME = Path(
    "diagnostics/issue31-camera-reacquisition-v2/frames/"
    "issue31-north-20260829-153603-v2-post.raw"
)
_NORTH_REPORT_SHA256: Final[str] = (
    "65c873ed19da8aa86a14feb66bd70b37c96f7827609190d1e0b050a2fdfea44a"
)
_NORTH_FRAME_SHA256: Final[str] = (
    "c1cb6fe144600ce153b1ceb2e90d6e375d42babea1eda6a08120efbc7ed2a4cd"
)
_NORTH_REPORT_HEAD: Final[str] = "4abc5dbb00dee6daa167b79e45fb8f3551b90880"
_RESET_REPORT_HEAD: Final[str] = "fa9975da946da8b5272d81b540f99acd3fca63b4"
_R2_PLAN_ID: Final[str] = "issue31-read-only-bridge-analysis-r2"
_R2_PLAN_VERSION: Final[str] = "1.1.0"
_MISSING_LINK: Final[str] = (
    "one receipt-proven, readiness-safe camera transition from the current "
    "cycle-verified component to any exact reviewed production-supported "
    "anchor, with robust inliers distributed across north_west, north_east, "
    "and south_west at both endpoints"
)
_R2_CONCLUSION: Final[str] = "no safe endpoint evidence"
_R2_MISSING_EVIDENCE: Final[str] = (
    "one additional exact receipt-bound north-up-p610-y043-reset endpoint "
    "whose post frame earns cycle-verified all-three-zone edges to both "
    "existing family endpoints and at least one common frozen reviewed "
    "supported anchor"
)
_FULL_GIT_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class FrozenEndpointSpec:
    label: str
    report_path: Path
    report_sha256: str
    frame_path: Path
    frame_sha256: str
    plan_name: str


_RESET_SPECS: Final[tuple[FrozenEndpointSpec, ...]] = (
    FrozenEndpointSpec(
        label="reset:repeat-1",
        report_path=Path(
            "diagnostics/issue31-camera-reacquisition/reports/"
            "issue31-dev-fa9975da-p610-y043-reset1.camera.json"
        ),
        report_sha256=(
            "1925996eb4f431f44a71abc6a33d5198707fc6173f0c81ec91ee4b350241547f"
        ),
        frame_path=Path(
            "diagnostics/issue31-camera-reacquisition/frames/"
            "issue31-dev-fa9975da-p610-y043-reset1-"
            "initial-normalization-candidate-01.raw"
        ),
        frame_sha256=(
            "e07973034c261a836549ccbac535fdc8401aa084e478dffd8cb27b79d5b431c6"
        ),
        plan_name="issue31-dev-north-up-p610-y043-reset",
    ),
    FrozenEndpointSpec(
        label="reset:repeat-3",
        report_path=Path(
            "diagnostics/issue31-camera-reacquisition/reports/"
            "issue31-dev-fa9975da-p610-y043-reset3.camera.json"
        ),
        report_sha256=(
            "a9a75ac611789b9f4d900261c63ad03210764b6db34d55ac883d700546de1dc5"
        ),
        frame_path=Path(
            "diagnostics/issue31-camera-reacquisition/frames/"
            "issue31-dev-fa9975da-p610-y043-reset3-"
            "initial-normalization-candidate-01.raw"
        ),
        frame_sha256=(
            "0e21d3506705e2771dee188c8db9657705d910edc6ecb4a26ed607e0d50ffeee"
        ),
        plan_name="issue31-dev-north-up-p610-y043-reset-repeat",
    ),
)


@dataclass(frozen=True, slots=True)
class NamedFrame:
    label: str
    frame: Frame
    path: str
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "height": self.frame.height,
            "label": self.label,
            "path": self.path,
            "pixel_format": self.frame.pixel_format.value,
            "raw_sha256": self.sha256,
            "width": self.frame.width,
        }


@dataclass(frozen=True, slots=True)
class VerifiedReport:
    path: str
    sha256: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class VerifiedEndpoint:
    named_frame: NamedFrame
    plan: dict[str, object]
    receipt: dict[str, object]
    report: VerifiedReport

    def as_dict(self) -> dict[str, object]:
        provenance = _mapping(self.report.payload.get("provenance"), "provenance")
        return {
            "frame": self.named_frame.as_dict(),
            "plan": self.plan,
            "receipt": self.receipt,
            "report_path": self.report.path,
            "report_provenance": provenance,
            "report_sha256": self.report.sha256,
        }


@dataclass(frozen=True, slots=True)
class R1Evidence:
    report: VerifiedReport
    provenance: dict[str, object]
    authority: dict[str, object]
    result: dict[str, object]
    current_sha256: str
    reviewed_manifest_sha256: str
    supported_items: tuple[tuple[str, str], ...]
    negative_corpus: dict[str, object]


@dataclass(frozen=True, slots=True)
class BridgeCorpus:
    r1: R1Evidence
    current: NamedFrame
    north: VerifiedEndpoint
    resets: tuple[VerifiedEndpoint, ...]
    anchors: tuple[NamedFrame, ...]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute the canonical read-only R2 bridge registrations and "
            "determine whether any fixed missing experiment is safely supported."
        )
    )
    parser.add_argument(
        "--expected-head",
        required=True,
        help="exact clean 40-character Git head required for canonical evidence",
    )
    parser.add_argument(
        "--r1-report",
        required=True,
        type=Path,
        help="canonical exact-head R1 JSON with its adjacent .sha256 sidecar",
    )
    parser.add_argument(
        "--r1-sha256",
        required=True,
        help="reviewed SHA-256 of the canonical exact-head R1 report",
    )
    parser.add_argument(
        "--report",
        required=True,
        type=Path,
        help="new R2 JSON path; a .sha256 sidecar is written exclusively",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    expected_head = str(args.expected_head)
    if _FULL_GIT_SHA.fullmatch(expected_head) is None:
        raise SystemExit("--expected-head must be a full lowercase 40-character SHA")
    r1_path = cast(Path, args.r1_report)
    r1_sha256 = str(args.r1_sha256)
    if re.fullmatch(r"[0-9a-f]{64}", r1_sha256) is None:
        raise SystemExit("--r1-sha256 must be a lowercase SHA-256 digest")
    report_path = cast(Path, args.report)
    head_before, clean_before = _git_state(_REPO_ROOT)
    if head_before != expected_head:
        raise SystemExit(
            f"expected exact head {expected_head}, found {head_before}; no report written"
        )
    if not clean_before:
        raise SystemExit("tracked/untracked worktree is not clean; no report written")

    source_r1 = r1_path if r1_path.is_absolute() else _REPO_ROOT / r1_path
    corpus = load_bridge_corpus(
        _REPO_ROOT,
        source_r1,
        expected_head=expected_head,
        expected_r1_sha256=r1_sha256,
    )
    graph = build_bridge_graph(
        corpus,
        registration_engine=RobustRegistrationEngine(),
    )
    endpoint_evidence = build_endpoint_evidence(corpus, graph)
    planner = plan_camera_bridge(graph, endpoint_evidence)
    pairwise = analyze_bridge_pairs(corpus, graph)
    head_after_analysis, clean_after_analysis = _git_state(_REPO_ROOT)
    if head_after_analysis != head_before or not clean_after_analysis:
        raise SystemExit("Git provenance changed during analysis; no report written")

    evidence = build_report_evidence(
        corpus,
        graph,
        planner,
        pairwise,
        head_before=head_before,
        clean_before=clean_before,
        head_after=head_after_analysis,
        clean_after=clean_after_analysis,
    )
    provenance = CameraReportProvenance(
        git_head_sha=head_before,
        detector_id=_string_field(corpus.r1.provenance, "detector_id"),
        detector_version=_string_field(corpus.r1.provenance, "detector_version"),
        profile_id=_string_field(corpus.r1.provenance, "profile_id"),
        plan_id=_R2_PLAN_ID,
        plan_version=_R2_PLAN_VERSION,
        command_argv=tuple(sys.argv if argv is None else [sys.argv[0], *argv]),
        tracked_worktree_clean=True,
    )
    destination = report_path if report_path.is_absolute() else _REPO_ROOT / report_path
    written = write_camera_validation_report(destination, evidence, provenance)
    head_after_write, clean_after_write = _git_state(_REPO_ROOT)
    if head_after_write != head_before or not clean_after_write:
        written.digest_path.unlink(missing_ok=True)
        written.report_path.unlink(missing_ok=True)
        raise SystemExit("Git provenance changed during publication; report retracted")

    result = _mapping(evidence["result"], "R2 result")
    print(f"Conclusion: {_string_field(result, 'conclusion')}")
    print(f"Planner disposition: {planner.disposition.value}")
    print(f"Pairwise registrations: {len(pairwise)}")
    print("Live input authorized: no")
    print(f"Report: {written.report_path}")
    print(f"Report SHA-256: {written.sha256}")
    return (
        0
        if planner.disposition
        in {
            BridgePlannerDisposition.BRIDGE_EVIDENCE_AVAILABLE,
            BridgePlannerDisposition.MISSING_EXPERIMENT,
        }
        else 1
    )


def load_bridge_corpus(
    repo_root: Path,
    r1_report_path: Path,
    *,
    expected_head: str,
    expected_r1_sha256: str,
) -> BridgeCorpus:
    """Load and authenticate every fixed saved artifact used by R2."""

    r1 = _load_r1_evidence(
        repo_root,
        r1_report_path,
        expected_head=expected_head,
        expected_sha256=expected_r1_sha256,
    )
    current = _load_named_raw(
        repo_root,
        label="current:system-id-return-post",
        relative_path=_CURRENT_FRAME,
        expected_sha256=r1.current_sha256,
        frame_id=1,
    )
    north = _load_north_endpoint(repo_root, frame_id=2)
    resets = tuple(
        _load_reset_endpoint(repo_root, spec, frame_id=index)
        for index, spec in enumerate(_RESET_SPECS, start=3)
    )
    anchors = _load_supported_anchors(repo_root, r1, first_frame_id=5)
    return BridgeCorpus(
        r1=r1,
        current=current,
        north=north,
        resets=resets,
        anchors=anchors,
    )


def build_bridge_graph(
    corpus: BridgeCorpus,
    *,
    registration_engine: RobustRegistrationEngine,
) -> ReadOnlyViewGraph:
    """Build R2's quarantined-safe graph once from exact authenticated pixels."""

    specs = (
        ViewNodeSpec(
            corpus.current.label,
            corpus.current.frame,
            ViewRole.SYSTEM_IDENTIFICATION,
        ),
        ViewNodeSpec(
            corpus.north.named_frame.label,
            corpus.north.named_frame.frame,
            ViewRole.OTHER_UNSUPPORTED,
        ),
        *(
            ViewNodeSpec(
                reset.named_frame.label,
                reset.named_frame.frame,
                ViewRole.OTHER_UNSUPPORTED,
            )
            for reset in corpus.resets
        ),
        *(
            ViewNodeSpec(
                anchor.label,
                anchor.frame,
                ViewRole.REVIEWED_SUPPORTED,
            )
            for anchor in corpus.anchors
        ),
    )
    graph = build_read_only_view_graph(
        specs,
        current_sha256=corpus.north.named_frame.sha256,
        reviewed_manifest_sha256=corpus.r1.reviewed_manifest_sha256,
        reviewed_anchor_sha256s=frozenset(
            anchor.sha256 for anchor in corpus.anchors
        ),
        action_transitions=(),
        registration_engine=registration_engine,
    )
    if graph.negative_nodes or graph.false_edge_count or graph.false_path_count:
        raise ValueError("R2 safe graph unexpectedly contains negative-role evidence")
    if graph.action_transitions or graph.action_path_to_supported is not None:
        raise ValueError("R2 pre-capture graph unexpectedly contains an action path")
    return graph


def build_endpoint_evidence(
    corpus: BridgeCorpus,
    graph: ReadOnlyViewGraph,
) -> tuple[MeasuredEndpointEvidence, ...]:
    """Bind frozen reset receipts to exact graph nodes without trusting claims."""

    node_by_sha = {node.sha256: node for node in graph.nodes}
    reset_edge = _graph_edge(
        graph,
        corpus.resets[0].named_frame.sha256,
        corpus.resets[1].named_frame.sha256,
    )
    registration = reset_edge.registration
    selected = None if registration is None else registration.selected_model
    matched_zones = (
        ()
        if selected is None
        else tuple(
            sorted(
                {
                    zone
                    for zone, count in (
                        *selected.source_zone_inliers,
                        *selected.target_zone_inliers,
                    )
                    if count > 0
                },
                key=str,
            )
        )
    )
    results: list[MeasuredEndpointEvidence] = []
    for reset in corpus.resets:
        node = node_by_sha[reset.named_frame.sha256]
        production = node.production
        results.append(
            MeasuredEndpointEvidence(
                evidence_id=f"endpoint:{reset.named_frame.label}",
                family_id=FROZEN_ENDPOINT_FAMILY_ID,
                experiment_id=FROZEN_ENDPOINT_OBJECTIVE_ID,
                source_sha256=graph.current_sha256,
                target_sha256=reset.named_frame.sha256,
                receipt_report_sha256=reset.report.sha256,
                receipt_verified=True,
                readiness_safe=node.registration_eligible,
                target_roles=node.roles,
                production_passed=production.passed,
                production_matched_landmarks=production.matched_landmark_count,
                production_matched_zones=production.matched_zones,
                registration_accepted=(
                    registration is not None and registration.accepted
                ),
                registration_cycle_verified=reset_edge.verified(graph.policy),
                registration_matched_zones=matched_zones,
                mutual_matches=(
                    0
                    if registration is None
                    else registration.correspondence.balanced_matches
                ),
                inliers=0 if selected is None else selected.inliers,
                inlier_ratio=0.0 if selected is None else selected.inlier_ratio,
                median_residual_px=(
                    None if selected is None else selected.median_residual_px
                ),
                p90_residual_px=(
                    None if selected is None else selected.p90_residual_px
                ),
                cycle_p90_px=None if selected is None else selected.cycle_p90_px,
            )
        )
    return tuple(results)


def analyze_bridge_pairs(
    corpus: BridgeCorpus,
    graph: ReadOnlyViewGraph,
) -> tuple[dict[str, object], ...]:
    """Return the fixed R2 matrix by projecting the already-built safe graph."""

    reset_one, reset_three = corpus.resets
    requests: list[tuple[str, NamedFrame, NamedFrame]] = [
        ("current_to_north", corpus.current, corpus.north.named_frame),
    ]
    requests.extend(
        ("north_to_anchor", corpus.north.named_frame, anchor)
        for anchor in corpus.anchors
    )
    requests.extend(
        (
            ("current_to_reset", corpus.current, reset_one.named_frame),
            ("current_to_reset", corpus.current, reset_three.named_frame),
            ("reset_repeat_to_repeat", reset_one.named_frame, reset_three.named_frame),
        )
    )
    requests.extend(
        ("reset_to_anchor", reset.named_frame, anchor)
        for reset in corpus.resets
        for anchor in corpus.anchors
    )
    results: list[dict[str, object]] = []
    for relationship, source, target in requests:
        edge = _graph_edge(graph, source.sha256, target.sha256)
        results.append(
            {
                "edge": edge.as_dict(graph.policy),
                "pair_id": f"{source.label}--{target.label}",
                "relationship": relationship,
                "source": source.as_dict(),
                "target": target.as_dict(),
            }
        )
    return tuple(results)


def _graph_edge(
    graph: ReadOnlyViewGraph,
    first_sha256: str,
    second_sha256: str,
) -> GraphEdgeEvidence:
    edge_id = ":".join(sorted((first_sha256, second_sha256)))
    return next(edge for edge in graph.edges if edge.edge_id == edge_id)


def build_report_evidence(
    corpus: BridgeCorpus,
    graph: ReadOnlyViewGraph,
    planner: CameraBridgePlannerEvidence,
    pairwise: tuple[dict[str, object], ...],
    *,
    head_before: str,
    clean_before: bool,
    head_after: str,
    clean_after: bool,
) -> dict[str, object]:
    """Build JSON-only R2 evidence directly from the safe graph and planner."""

    authority = {
        "diagnostic_registration_can_override_production": False,
        "live_camera_input_authorized": False,
        "live_camera_input_performed": False,
        "registration_can_authorize_camera_input": False,
        "registration_can_expose_resources": False,
        "registration_can_validate_scene": False,
    }
    if any(authority.values()):  # defensive assertion for future edits
        raise AssertionError("R2 authority must remain entirely non-authorizing")
    return {
        "authority": authority,
        "backend": {
            "algorithm": robust_registration_algorithm_settings(),
            "environment": robust_registration_environment(),
        },
        "corpus": {
            "current": corpus.current.as_dict(),
            "north": corpus.north.as_dict(),
            "repeated_reset_endpoints": [item.as_dict() for item in corpus.resets],
            "reviewed_supported_anchors": [
                anchor.as_dict() for anchor in corpus.anchors
            ],
        },
        "git": {
            "after": {"head_sha": head_after, "worktree_clean": clean_after},
            "before": {"head_sha": head_before, "worktree_clean": clean_before},
            "exact_head_stable": head_before == head_after,
        },
        "pairwise_registrations": list(pairwise),
        "bridge_planner": planner.as_dict(),
        "production_scene_authority": "unchanged production detector only",
        "r1_source": {
            "authority": corpus.r1.authority,
            "current_sha256": corpus.r1.current_sha256,
            "negative_corpus": corpus.r1.negative_corpus,
            "provenance": corpus.r1.provenance,
            "report_path": corpus.r1.report.path,
            "report_sha256": corpus.r1.report.sha256,
            "result": corpus.r1.result,
            "reviewed_manifest_sha256": corpus.r1.reviewed_manifest_sha256,
        },
        "safe_view_graph": graph.as_dict(),
        "result": _planner_result(planner),
    }


def _planner_result(planner: CameraBridgePlannerEvidence) -> dict[str, object]:
    missing = planner.missing_experiment
    if planner.disposition is BridgePlannerDisposition.BRIDGE_EVIDENCE_AVAILABLE:
        conclusion = "offline controller path available"
    elif missing is not None:
        conclusion = (
            "first missing closed-loop experiment: "
            f"{missing.key.value.upper()} key hold for "
            f"{missing.duration_s:.3f} seconds"
        )
    else:
        conclusion = _R2_CONCLUSION
    return {
        "conclusion": conclusion,
        "live_input_authorized": False,
        "reacquisition_success_claimed": False,
        "selected_experiment_id": (
            None if missing is None else missing.experiment_id
        ),
        "smallest_additional_evidence": (
            _R2_MISSING_EVIDENCE
            if planner.disposition
            is BridgePlannerDisposition.NO_SAFE_ENDPOINT_EVIDENCE
            else None
        ),
    }


def _load_r1_evidence(
    repo_root: Path,
    report_path: Path,
    *,
    expected_head: str,
    expected_sha256: str,
) -> R1Evidence:
    verified = _load_verified_report(
        repo_root,
        report_path,
        expected_sha256=expected_sha256,
    )
    payload = verified.payload
    if payload.get("schema_version") != 2:
        raise ValueError("R1 report schema_version must be 2")
    provenance = _mapping(payload.get("provenance"), "R1 provenance")
    expected_provenance = {
        "git_head_sha": expected_head,
        "plan_id": ROBUST_VIEW_GRAPH_ID,
        "plan_version": ROBUST_VIEW_GRAPH_VERSION,
        "tracked_worktree_clean": True,
    }
    for field, expected in expected_provenance.items():
        if provenance.get(field) != expected:
            raise ValueError(f"R1 provenance {field} does not match canonical R2 input")
    for field in ("detector_id", "detector_version", "profile_id"):
        _string_field(provenance, field)

    evidence = _mapping(payload.get("evidence"), "R1 evidence")
    authority = _mapping(evidence.get("authority"), "R1 authority")
    required_authority = {
        "diagnostic_registration_can_override_production": False,
        "new_live_camera_input_performed": False,
        "production_detector_remains_sole_scene_authority": True,
        "registration_can_authorize_camera_input": False,
        "registration_can_expose_resources": False,
        "registration_can_validate_scene": False,
    }
    if any(authority.get(key) is not value for key, value in required_authority.items()):
        raise ValueError("R1 authority boundary is not canonical")
    result = _mapping(evidence.get("result"), "R1 result")
    expected_result = {
        "conclusion": f"missing graph link: {_MISSING_LINK}",
        "false_edge_count": 19,
        "false_path_count": 0,
        "missing_link": _MISSING_LINK,
        "negative_failure_count": 19,
        "offline_controller_path_available": False,
    }
    if result != expected_result:
        raise ValueError("R1 result is not the frozen fail-closed missing-link result")
    git = _mapping(evidence.get("git"), "R1 git")
    before = _mapping(git.get("before"), "R1 git.before")
    after = _mapping(git.get("after"), "R1 git.after")
    if (
        before != {"head_sha": expected_head, "worktree_clean": True}
        or after != {"head_sha": expected_head, "worktree_clean": True}
        or git.get("exact_head_stable") is not True
    ):
        raise ValueError("R1 report does not prove stable clean exact-head provenance")

    corpus = _mapping(evidence.get("corpus"), "R1 corpus")
    if corpus.get("reviewed_manifest_path") != _SUPPORTED_MANIFEST.as_posix():
        raise ValueError("R1 reviewed manifest path is not canonical")
    manifest_sha = _digest_field(corpus, "reviewed_manifest_sha256")
    groups = _mapping(corpus.get("groups"), "R1 corpus.groups")
    supported = _mapping(groups.get("reviewed_supported"), "reviewed_supported")
    if supported.get("count") != 5:
        raise ValueError("R1 must contain exactly five reviewed supported anchors")
    supported_items = tuple(
        sorted(
            (
                _string_field(_mapping(item, "supported item"), "label"),
                _digest_field(_mapping(item, "supported item"), "raw_sha256"),
            )
            for item in _sequence(supported.get("items"), "supported items")
        )
    )
    if len(supported_items) != 5 or len(set(supported_items)) != 5:
        raise ValueError("R1 reviewed supported anchors must be five unique items")

    graph = _mapping(evidence.get("view_graph"), "R1 view_graph")
    if (
        graph.get("graph_id") != ROBUST_VIEW_GRAPH_ID
        or graph.get("graph_version") != ROBUST_VIEW_GRAPH_VERSION
        or graph.get("reviewed_manifest_sha256") != manifest_sha
    ):
        raise ValueError("R1 view graph identity does not match its provenance")
    current_sha = _digest_field(graph, "current_sha256")
    current_nodes = [
        _mapping(node, "R1 node")
        for node in _sequence(graph.get("nodes"), "R1 nodes")
        if _mapping(node, "R1 node").get("current") is True
    ]
    if len(current_nodes) != 1 or current_nodes[0].get("sha256") != current_sha:
        raise ValueError("R1 must identify exactly one SHA-bound current node")
    expected_current_label = f"system-id:{_CURRENT_FRAME.as_posix()}"
    if current_nodes[0].get("labels") != [expected_current_label]:
        raise ValueError("R1 current node is not the frozen system-id return frame")
    negative = _mapping(graph.get("negative_corpus"), "R1 negative_corpus")
    if negative.get("policy_roles") != ["disconnected", "risky_state_change"]:
        raise ValueError("R1 negative-role policy is not canonical")
    accepted_edge_ids = _digest_pair_ids(
        negative.get("accepted_pairwise_edge_ids"),
        "R1 negative accepted edges",
    )
    verified_edge_ids = _digest_pair_ids(
        negative.get("cycle_verified_edge_ids"),
        "R1 negative verified edges",
    )
    negative_nodes = tuple(
        _mapping(item, "R1 negative node")
        for item in _sequence(negative.get("nodes"), "R1 negative nodes")
    )
    if (
        negative.get("accepted_pairwise_edge_count") != 19
        or negative.get("cycle_verified_edge_count") != 19
        or negative.get("supported_path_count") != 0
        or negative.get("aggregate_failure_count") != 19
        or len(accepted_edge_ids) != 19
        or verified_edge_ids != accepted_edge_ids
        or len(negative_nodes) != 5
    ):
        raise ValueError("R1 negative-corpus quarantine evidence is not canonical")
    negative_sha256s: set[str] = set()
    observed_roles: list[str] = []
    for node in negative_nodes:
        digest = _digest_field(node, "sha256")
        roles = _sequence(node.get("roles"), "R1 negative node roles")
        if roles not in (["disconnected"], ["risky_state_change"]):
            raise ValueError("R1 negative node retained a non-policy role")
        if (
            node.get("has_verified_path_to_supported") is not False
            or node.get("verified_path_to_supported") is not None
        ):
            raise ValueError("R1 negative node retained a supported-anchor path")
        negative_sha256s.add(digest)
        observed_roles.extend(cast(list[str], roles))
    if (
        len(negative_sha256s) != 5
        or observed_roles.count("disconnected") != 3
        or observed_roles.count("risky_state_change") != 2
    ):
        raise ValueError("R1 negative-node membership is not canonical")
    components = tuple(
        tuple(_sequence(item, "R1 graph component"))
        for item in _sequence(graph.get("components"), "R1 graph components")
    )
    if any(
        digest in negative_sha256s and len(component) != 1
        for component in components
        for digest in component
    ):
        raise ValueError("R1 negative node escaped quarantine into safe connectivity")
    return R1Evidence(
        report=verified,
        provenance=provenance,
        authority=authority,
        result=result,
        current_sha256=current_sha,
        reviewed_manifest_sha256=manifest_sha,
        supported_items=supported_items,
        negative_corpus=negative,
    )


def _load_north_endpoint(repo_root: Path, *, frame_id: int) -> VerifiedEndpoint:
    report = _load_verified_report(
        repo_root,
        repo_root / _NORTH_REPORT,
        expected_sha256=_NORTH_REPORT_SHA256,
    )
    _validate_report_provenance(
        report.payload,
        expected_head=_NORTH_REPORT_HEAD,
        plan_id="issue31-world-only-multi-axis-guidance",
        plan_version="2.0.0",
    )
    evidence = _mapping(report.payload.get("evidence"), "north evidence")
    if (
        evidence.get("command") != "north-bootstrap-v2"
        or evidence.get("development_only") is not True
        or evidence.get("terminal_reason") != "bootstrap_executed"
    ):
        raise ValueError("corrected compass-north report command is not canonical")
    plan = {
        "actions": [{"kind": "compass_click", "x": 608, "y": 49}],
        "name": "issue31-v2-01-heading-north",
    }
    if _mapping(evidence.get("plan"), "north plan") != plan:
        raise ValueError("corrected compass-north plan mismatch")
    receipt = _mapping(evidence.get("receipt"), "north receipt")
    _validate_complete_receipt(receipt, plan)
    input_evidence = _mapping(evidence.get("input"), "north input")
    if (
        input_evidence.get("attempted") is not True
        or input_evidence.get("completed") is not True
        or input_evidence.get("state") != "complete"
    ):
        raise ValueError("corrected compass-north input receipt is incomplete")
    post = _mapping(
        _mapping(evidence.get("frames"), "north frames").get("post"),
        "north frames.post",
    )
    artifact = _mapping(post.get("artifact"), "north post artifact")
    _validate_artifact(
        artifact,
        expected_sha256=_NORTH_FRAME_SHA256,
        expected_raw_reference="frames/issue31-north-20260829-153603-v2-post.raw",
    )
    _validate_fail_closed_production(post.get("production"), "north post production")
    frame = _load_named_raw(
        repo_root,
        label="north:corrected-compass-post",
        relative_path=_NORTH_FRAME,
        expected_sha256=_NORTH_FRAME_SHA256,
        frame_id=frame_id,
    )
    return VerifiedEndpoint(frame, plan, receipt, report)


def _load_reset_endpoint(
    repo_root: Path,
    spec: FrozenEndpointSpec,
    *,
    frame_id: int,
) -> VerifiedEndpoint:
    report = _load_verified_report(
        repo_root,
        repo_root / spec.report_path,
        expected_sha256=spec.report_sha256,
    )
    _validate_report_provenance(
        report.payload,
        expected_head=_RESET_REPORT_HEAD,
        plan_id=spec.plan_name,
        plan_version="0.0.0",
    )
    evidence = _mapping(report.payload.get("evidence"), "reset evidence")
    plan = _reset_plan(spec.plan_name)
    strategy = _mapping(evidence.get("normalization_strategy"), "reset strategy")
    if (
        strategy.get("id") != spec.plan_name
        or strategy.get("version") != "0.0.0"
        or strategy.get("diagnostic_registration_used") is not False
        or strategy.get("selection_authority")
        != "unchanged_production_camera_evaluation"
        or strategy.get("candidates")
        != [{"actions": plan["actions"], "index_1_based": 1, "name": spec.plan_name}]
    ):
        raise ValueError(f"{spec.label} frozen normalization plan mismatch")
    initial = _mapping(evidence.get("initial_normalization"), "initial normalization")
    attempts = _sequence(initial.get("attempts"), "initial normalization attempts")
    if len(attempts) != 1:
        raise ValueError(f"{spec.label} must contain exactly one frozen attempt")
    attempt = _mapping(attempts[0], "reset attempt")
    if (
        attempt.get("identity") != spec.plan_name
        or attempt.get("index_1_based") != 1
        or attempt.get("counts_as_confirmation") is not False
        or attempt.get("production_gate_passed") is not False
        or _mapping(attempt.get("plan"), "reset attempt plan") != plan
    ):
        raise ValueError(f"{spec.label} attempt is not the frozen rejected endpoint")
    receipt = _mapping(attempt.get("receipt"), "reset receipt")
    _validate_complete_receipt(receipt, plan)
    candidate = _mapping(attempt.get("candidate_frame"), "reset candidate frame")
    artifact = _mapping(candidate.get("artifact"), "reset artifact")
    _validate_artifact(
        artifact,
        expected_sha256=spec.frame_sha256,
        expected_raw_reference=f"frames/{spec.frame_path.name}",
    )
    _validate_fail_closed_production(
        candidate.get("production"), f"{spec.label} production"
    )
    frame = _load_named_raw(
        repo_root,
        label=spec.label,
        relative_path=spec.frame_path,
        expected_sha256=spec.frame_sha256,
        frame_id=frame_id,
    )
    return VerifiedEndpoint(frame, plan, receipt, report)


def _load_supported_anchors(
    repo_root: Path,
    r1: R1Evidence,
    *,
    first_frame_id: int,
) -> tuple[NamedFrame, ...]:
    manifest_path = repo_root / _SUPPORTED_MANIFEST
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if manifest_sha != r1.reviewed_manifest_sha256:
        raise ValueError("reviewed supported manifest SHA-256 differs from R1")
    anchors: list[NamedFrame] = []
    with tempfile.TemporaryDirectory(prefix="issue31-r2-fixtures-") as temporary:
        materialized = materialize_gzip_replay_dataset(
            manifest_path, Path(temporary)
        )
        dataset = load_replay_dataset(materialized)
        for index, sample in enumerate(dataset, start=first_frame_id):
            digest = hashlib.sha256(sample.frame.payload).hexdigest()
            anchors.append(
                NamedFrame(
                    label=f"supported:{sample.case.case_id}",
                    frame=Frame.from_raw(
                        RawFrame(
                            payload=sample.frame.payload,
                            width=sample.frame.width,
                            height=sample.frame.height,
                            pixel_format=sample.frame.pixel_format,
                        ),
                        frame_id=index,
                        captured_monotonic_s=float(index),
                    ),
                    path=(
                        f"{_SUPPORTED_MANIFEST.as_posix()}#"
                        f"{sample.case.case_id}"
                    ),
                    sha256=digest,
                )
            )
    anchors.sort(key=lambda item: item.label)
    actual = tuple((item.label, item.sha256) for item in anchors)
    if actual != r1.supported_items:
        raise ValueError("materialized reviewed anchors differ from exact R1 corpus")
    return tuple(anchors)


def _load_verified_report(
    repo_root: Path,
    report_path: Path,
    *,
    expected_sha256: str | None,
) -> VerifiedReport:
    payload_bytes = report_path.read_bytes()
    actual_sha = hashlib.sha256(payload_bytes).hexdigest()
    if expected_sha256 is not None and actual_sha != expected_sha256:
        raise ValueError(f"report SHA-256 mismatch: {report_path}")
    sidecar = report_path.with_name(f"{report_path.name}.sha256")
    if sidecar.read_bytes() != f"{actual_sha}\n".encode("ascii"):
        raise ValueError(f"report SHA-256 sidecar mismatch: {report_path}")
    parsed = json.loads(
        payload_bytes,
        object_pairs_hook=_reject_duplicate_keys,
        parse_float=_finite_json_float,
        parse_constant=_reject_nonstandard_number,
    )
    report = _mapping(parsed, "report")
    return VerifiedReport(
        path=_display_path(repo_root, report_path),
        sha256=actual_sha,
        payload=report,
    )


def _validate_report_provenance(
    payload: dict[str, object],
    *,
    expected_head: str,
    plan_id: str,
    plan_version: str,
) -> None:
    if payload.get("schema_version") != 2:
        raise ValueError("private report schema_version must be 2")
    provenance = _mapping(payload.get("provenance"), "private provenance")
    expected = {
        "detector_id": "profiled-resource:varrock-east-iron-v1",
        "detector_version": "2.1.0",
        "git_head_sha": expected_head,
        "plan_id": plan_id,
        "plan_version": plan_version,
        "profile_id": "varrock-east-iron-v1",
        "tracked_worktree_clean": True,
    }
    if any(provenance.get(field) != value for field, value in expected.items()):
        raise ValueError("private report provenance mismatch")


def _validate_complete_receipt(
    receipt: dict[str, object], plan: dict[str, object]
) -> None:
    if _mapping(receipt.get("plan"), "receipt plan") != plan:
        raise ValueError("receipt plan mismatch")
    preflight = _mapping(receipt.get("preflight"), "receipt preflight")
    if preflight != {
        "client_height": 1078,
        "client_width": 1005,
        "focused": True,
        "supported": True,
    }:
        raise ValueError("receipt preflight is incomplete")
    actions = _sequence(receipt.get("actions"), "receipt actions")
    planned_actions = _sequence(plan.get("actions"), "plan actions")
    if len(actions) != len(planned_actions):
        raise ValueError("receipt action count mismatch")
    for index, (raw_receipt, raw_action) in enumerate(
        zip(actions, planned_actions, strict=True)
    ):
        action_receipt = _mapping(raw_receipt, "action receipt")
        action = _mapping(raw_action, "planned action")
        if (
            action_receipt.get("action_index") != index
            or _mapping(action_receipt.get("action"), "received action") != action
        ):
            raise ValueError("receipt action does not match frozen plan")
        actual_low_level = _sequence(
            action_receipt.get("input_receipts"), "low-level receipts"
        )
        if actual_low_level != _expected_low_level_receipts(action):
            raise ValueError("incomplete or unexpected low-level input receipt")


def _expected_low_level_receipts(action: dict[str, object]) -> list[object]:
    kind = action.get("kind")
    if kind == "pause":
        return []
    if kind == "compass_click":
        return [
            {
                "complete": True,
                "completed_events": 2,
                "operation": "compass_click",
                "requested_events": 2,
            }
        ]
    if kind in {"key_hold", "reset_zoom_key"}:
        return [
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
    raise ValueError(f"unsupported frozen action kind: {kind!r}")


def _validate_artifact(
    artifact: dict[str, object],
    *,
    expected_sha256: str,
    expected_raw_reference: str,
) -> None:
    files = _mapping(artifact.get("files"), "artifact files")
    if (
        artifact.get("raw_sha256") != expected_sha256
        or artifact.get("width") != 1005
        or artifact.get("height") != 1078
        or artifact.get("pixel_format") != "bgra8888"
        or files.get("raw") != expected_raw_reference
    ):
        raise ValueError("report artifact does not match the frozen exact frame")


def _validate_fail_closed_production(value: object, context: str) -> None:
    production = _mapping(value, context)
    resources = _sequence(production.get("resources"), f"{context}.resources")
    if (
        production.get("passed") is not False
        or production.get("definitive_target_ids") != []
        or len(resources) != 4
        or any(
            _mapping(resource, "resource").get("state") != "uncertain"
            or _mapping(resource, "resource").get("definitive") is not False
            for resource in resources
        )
    ):
        raise ValueError(f"{context} is not a fail-closed production observation")


def _reset_plan(plan_name: str) -> dict[str, object]:
    return {
        "actions": [
            {"kind": "compass_click", "x": 608, "y": 49},
            {"duration_s": 0.5, "kind": "pause"},
            {"duration_s": 0.043, "key": "right", "kind": "key_hold"},
            {"duration_s": 3.0, "key": "up", "kind": "key_hold"},
            {"duration_s": 0.61, "key": "down", "kind": "key_hold"},
            {"dwell_s": 0.1, "key": "control", "kind": "reset_zoom_key"},
        ],
        "name": plan_name,
    }


def _load_named_raw(
    repo_root: Path,
    *,
    label: str,
    relative_path: Path,
    expected_sha256: str,
    frame_id: int,
) -> NamedFrame:
    path = repo_root / relative_path
    payload = path.read_bytes()
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != expected_sha256:
        raise ValueError(f"exact frame SHA-256 mismatch: {relative_path.as_posix()}")
    frame = Frame.from_raw(
        RawFrame(
            payload=payload,
            width=1005,
            height=1078,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )
    return NamedFrame(label, frame, relative_path.as_posix(), actual_sha)


def _git_state(repo_root: Path) -> tuple[str, bool]:
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return head, not status.strip()


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number: {value}")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _digest_pair_ids(value: object, context: str) -> tuple[str, ...]:
    items = _sequence(value, context)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{context} must contain string edge ids")
    result = tuple(cast(list[str], items))
    if result != tuple(sorted(set(result))):
        raise ValueError(f"{context} must be sorted and unique")
    for edge_id in result:
        parts = edge_id.split(":")
        if len(parts) != 2:
            raise ValueError(f"{context} contains a malformed edge id")
        for digest in parts:
            _digest_field({"digest": digest}, "digest")
    return result


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be a JSON object")
    return cast(dict[str, object], value)


def _sequence(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    return value


def _string_field(value: dict[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-empty string")
    return item


def _digest_field(value: dict[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or len(item) != 64 or any(
        character not in "0123456789abcdef" for character in item
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return item


if __name__ == "__main__":  # pragma: no cover - exercised through main tests
    raise SystemExit(main())
