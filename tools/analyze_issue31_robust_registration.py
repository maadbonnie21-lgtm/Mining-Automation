"""Build the canonical read-only robust-registration R1 view graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception import (
    load_replay_dataset,
    materialize_gzip_replay_dataset,
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
    ActionTransition,
    ReadOnlyViewGraph,
    ViewNodeSpec,
    ViewRole,
    build_read_only_view_graph,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUPPORTED_MANIFEST = Path(
    "tests/fixtures/perception/varrock-east-iron-v1/manifest.json"
)
_DRIFT_DIR = Path("diagnostics/issue18-drift-v3")
_SYSTEM_ID_FRAMES = Path("diagnostics/issue31-camera-system-id-v2/frames")
_SYSTEM_ID_REPORT = Path(
    "diagnostics/issue31-camera-system-id-v2/reports/"
    "issue31-system-id-eaaad585-20260829-182319.camera.json"
)
_COMPLETE_SYSTEM_ID_TOKEN = "20260829-182319"
_RISKY_SYSTEM_ID_FILENAMES: tuple[str, ...] = (
    "issue31-system-id-eaaad585-20260829-182047-"
    "system-id-horizontal-baseline-01.raw",
    "issue31-system-id-eaaad585-20260829-182047-"
    "system-id-horizontal-baseline-02.raw",
)
_CURRENT_SUFFIX = "system-id-horizontal-return-post.raw"
_EXTRA_VIEWS: tuple[tuple[str, ViewRole, Path], ...] = (
    (
        "other:one-detent-preflight",
        ViewRole.OTHER_UNSUPPORTED,
        Path(
            "diagnostics/varrock-east-iron/frames/"
            "issue31-one-detent-preflight-368edfb8.raw"
        ),
    ),
    (
        "other:purported-restored",
        ViewRole.OTHER_UNSUPPORTED,
        Path(
            "diagnostics/varrock-east-iron/frames/"
            "reacquire-restored-20260818.raw"
        ),
    ),
    (
        "other:fresh-supported-view-2",
        ViewRole.OTHER_UNSUPPORTED,
        Path(
            "diagnostics/varrock-east-iron/frames/"
            "issue18-fresh-supported-view-2.raw"
        ),
    ),
)
_DISCONNECTED_VIEWS: tuple[tuple[str, ViewRole, Path], ...] = (
    (
        "disconnected:a",
        ViewRole.DISCONNECTED,
        Path(
            "diagnostics/issue31-camera-reacquisition/frames/"
            "issue31-clean-b3922bd-step1000-dx0-dy-52-z-15-a-"
            "initial-normalization-candidate-01.raw"
        ),
    ),
    (
        "disconnected:b",
        ViewRole.DISCONNECTED,
        Path(
            "diagnostics/issue31-camera-reacquisition/frames/"
            "issue31-clean-b3922bd-step1000-dx0-dy-52-z-15-b-"
            "initial-normalization-candidate-01.raw"
        ),
    ),
    (
        "disconnected:c",
        ViewRole.DISCONNECTED,
        Path(
            "diagnostics/varrock-east-iron/frames/"
            "issue31-disconnected-exact-geometry.raw"
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class CorpusEvidence:
    specs: tuple[ViewNodeSpec, ...]
    reviewed_anchor_sha256s: frozenset[str]
    reviewed_manifest_sha256: str
    current_sha256: str
    action_transitions: tuple[ActionTransition, ...]
    system_id_report_sha256: str
    groups: dict[str, dict[str, object]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only, production-independent robust-registration R1 "
            "view graph from the frozen Issue #31 corpus."
        )
    )
    parser.add_argument(
        "--expected-head",
        required=True,
        help="exact clean 40-character Git head required for canonical evidence",
    )
    parser.add_argument(
        "--report",
        required=True,
        type=Path,
        help="new canonical JSON path; a .sha256 sidecar is written exclusively",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    expected_head = str(args.expected_head)
    report_path = cast(Path, args.report)
    head_before, clean_before = _git_state(_REPO_ROOT)
    if head_before != expected_head:
        raise SystemExit(
            f"expected exact head {expected_head}, found {head_before}; no report written"
        )
    if not clean_before:
        raise SystemExit("tracked/untracked worktree is not clean; no report written")

    corpus = load_canonical_corpus(_REPO_ROOT)
    graph = build_read_only_view_graph(
        corpus.specs,
        current_sha256=corpus.current_sha256,
        reviewed_manifest_sha256=corpus.reviewed_manifest_sha256,
        reviewed_anchor_sha256s=corpus.reviewed_anchor_sha256s,
        action_transitions=corpus.action_transitions,
        registration_engine=RobustRegistrationEngine(),
    )
    drift_gate = _drift_gate(graph)
    head_after_analysis, clean_after_analysis = _git_state(_REPO_ROOT)
    if head_after_analysis != head_before or not clean_after_analysis:
        raise SystemExit("Git provenance changed during analysis; no report written")
    evidence = build_report_evidence(
        graph,
        corpus,
        drift_gate,
        head_before=head_before,
        clean_before=clean_before,
        head_after=head_after_analysis,
        clean_after=clean_after_analysis,
    )
    current = next(node for node in graph.nodes if node.current)
    provenance = CameraReportProvenance(
        git_head_sha=head_before,
        detector_id=current.production.detector_id,
        detector_version=current.production.detector_version,
        profile_id=current.production.profile_id,
        plan_id=ROBUST_VIEW_GRAPH_ID,
        plan_version=ROBUST_VIEW_GRAPH_VERSION,
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

    print(f"Conclusion: {graph.conclusion}")
    print(
        "Graph: "
        f"{len(graph.nodes)} nodes, {len(graph.edges)} pairs, "
        f"{sum(edge.registration_accepted for edge in graph.edges)} accepted, "
        f"{sum(edge.verified(graph.policy) for edge in graph.edges)} cycle-verified"
    )
    print(
        "Drift safety: "
        f"{drift_gate['uncertain_frames']}/{drift_gate['expected_frames']} "
        "UNCERTAIN, "
        f"{drift_gate['false_definitive_target_count']} false definitive targets"
    )
    print(
        "Negative-corpus failures: "
        f"{graph.false_edge_count} accepted edges, "
        f"{graph.false_path_count} supported paths, "
        f"{graph.negative_failure_count} aggregate"
    )
    print(f"Report: {written.report_path}")
    print(f"Report SHA-256: {written.sha256}")
    return (
        0
        if drift_gate["passed"] is True and graph.negative_failure_count == 0
        else 1
    )


def load_canonical_corpus(repo_root: Path) -> CorpusEvidence:
    """Load the fixed R1 corpus and verify action evidence by exact SHA."""

    manifest_path = repo_root / _SUPPORTED_MANIFEST
    reviewed_manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    specs: list[ViewNodeSpec] = []
    group_items: dict[str, list[tuple[str, str]]] = {}
    with tempfile.TemporaryDirectory(prefix="issue31-r1-fixtures-") as temporary:
        materialized = materialize_gzip_replay_dataset(
            manifest_path, Path(temporary)
        )
        dataset = load_replay_dataset(materialized)
        anchors: set[str] = set()
        supported_items: list[tuple[str, str]] = []
        for sample in dataset:
            digest = hashlib.sha256(sample.frame.payload).hexdigest()
            label = f"supported:{sample.case.case_id}"
            specs.append(
                ViewNodeSpec(label, sample.frame, ViewRole.REVIEWED_SUPPORTED)
            )
            anchors.add(digest)
            supported_items.append((label, digest))
    if len(anchors) != 5:
        raise ValueError("canonical reviewed manifest must contain five unique frames")
    group_items["reviewed_supported"] = supported_items

    next_frame_id = len(specs) + 1
    drift_paths = sorted((repo_root / _DRIFT_DIR).glob("northwest-full-cycle-*.raw"))
    if len(drift_paths) != 36:
        raise ValueError(f"canonical drift corpus must contain 36 frames, got {len(drift_paths)}")
    drift_specs, next_frame_id = _raw_specs(
        repo_root, drift_paths, "drift", ViewRole.REAL_DRIFT, next_frame_id
    )
    specs.extend(drift_specs)
    group_items["real_drift"] = _spec_digests(drift_specs)

    system_paths = sorted(
        path
        for path in (repo_root / _SYSTEM_ID_FRAMES).glob("*.raw")
        if _COMPLETE_SYSTEM_ID_TOKEN in path.name
    )
    if len(system_paths) != 8:
        raise ValueError(f"complete A/B/A corpus must contain 8 frames, got {len(system_paths)}")
    system_specs, next_frame_id = _raw_specs(
        repo_root,
        system_paths,
        "system-id",
        ViewRole.SYSTEM_IDENTIFICATION,
        next_frame_id,
    )
    specs.extend(system_specs)
    group_items["system_identification"] = _spec_digests(system_specs)

    risky_paths = tuple(
        repo_root / _SYSTEM_ID_FRAMES / filename
        for filename in _RISKY_SYSTEM_ID_FILENAMES
    )
    if len(risky_paths) != 2 or len(set(risky_paths)) != 2:
        raise ValueError("risky no-input corpus must name two unique fixed frames")
    missing_risky = tuple(path for path in risky_paths if not path.is_file())
    if missing_risky:
        missing = ", ".join(path.name for path in missing_risky)
        raise ValueError(f"fixed risky no-input corpus frame missing: {missing}")
    risky_specs, next_frame_id = _raw_specs(
        repo_root,
        risky_paths,
        "risky",
        ViewRole.RISKY_STATE_CHANGE,
        next_frame_id,
    )
    specs.extend(risky_specs)
    group_items["risky_state_change"] = _spec_digests(risky_specs)

    for label, role, relative_path in (*_EXTRA_VIEWS, *_DISCONNECTED_VIEWS):
        frame = _read_raw_frame(repo_root / relative_path, frame_id=next_frame_id)
        next_frame_id += 1
        specs.append(ViewNodeSpec(label, frame, role))
        group_items.setdefault(role.value, []).append(
            (label, hashlib.sha256(frame.payload).hexdigest())
        )

    current_spec = next(
        spec for spec in system_specs if spec.label.endswith(_CURRENT_SUFFIX)
    )
    current_sha = hashlib.sha256(current_spec.frame.payload).hexdigest()
    report_sha, transitions = _load_action_transitions(repo_root, tuple(specs))
    groups = {
        group: {
            "aggregate_sha256": _aggregate_group_digest(items),
            "count": len(items),
            "items": [
                {"label": label, "raw_sha256": digest}
                for label, digest in sorted(items)
            ],
        }
        for group, items in sorted(group_items.items())
    }
    return CorpusEvidence(
        specs=tuple(specs),
        reviewed_anchor_sha256s=frozenset(anchors),
        reviewed_manifest_sha256=reviewed_manifest_sha,
        current_sha256=current_sha,
        action_transitions=transitions,
        system_id_report_sha256=report_sha,
        groups=groups,
    )


def build_report_evidence(
    graph: ReadOnlyViewGraph,
    corpus: CorpusEvidence,
    drift_gate: dict[str, object],
    *,
    head_before: str,
    clean_before: bool,
    head_after: str,
    clean_after: bool,
) -> dict[str, object]:
    selected_families: dict[str, int] = {}
    rejected_dispositions: dict[str, int] = {}
    for edge in graph.edges:
        registration = edge.registration
        if registration is None:
            rejected_dispositions["pre_registration_veto"] = (
                rejected_dispositions.get("pre_registration_veto", 0) + 1
            )
        elif registration.accepted and registration.selected_family is not None:
            family = registration.selected_family.value
            selected_families[family] = selected_families.get(family, 0) + 1
        else:
            disposition = registration.disposition.value
            rejected_dispositions[disposition] = (
                rejected_dispositions.get(disposition, 0) + 1
            )
    return {
        "authority": {
            "development_validation_only": True,
            "diagnostic_registration_can_override_production": False,
            "new_live_camera_input_performed": False,
            "production_detector_remains_sole_scene_authority": True,
            "registration_can_authorize_camera_input": False,
            "registration_can_expose_resources": False,
            "registration_can_validate_scene": False,
        },
        "backend": {
            "algorithm": robust_registration_algorithm_settings(),
            "environment": robust_registration_environment(),
        },
        "corpus": {
            "groups": corpus.groups,
            "reviewed_manifest_path": _SUPPORTED_MANIFEST.as_posix(),
            "reviewed_manifest_sha256": corpus.reviewed_manifest_sha256,
            "system_id_report_path": _SYSTEM_ID_REPORT.as_posix(),
            "system_id_report_sha256": corpus.system_id_report_sha256,
        },
        "drift_safety": drift_gate,
        "git": {
            "after": {
                "head_sha": head_after,
                "worktree_clean": clean_after,
            },
            "before": {
                "head_sha": head_before,
                "worktree_clean": clean_before,
            },
            "exact_head_stable": head_before == head_after,
        },
        "model_selection_summary": {
            "accepted_selected_families": selected_families,
            "rejected_dispositions": rejected_dispositions,
            "smallest_adequate_model_order": [
                "translation",
                "similarity",
                "affine",
                "homography",
            ],
        },
        "result": {
            "conclusion": graph.conclusion,
            "false_edge_count": graph.false_edge_count,
            "false_path_count": graph.false_path_count,
            "missing_link": graph.missing_link,
            "negative_failure_count": graph.negative_failure_count,
            "offline_controller_path_available": (
                graph.offline_controller_path_available
            ),
        },
        "view_graph": graph.as_dict(),
    }


def _load_action_transitions(
    repo_root: Path, specs: tuple[ViewNodeSpec, ...]
) -> tuple[str, tuple[ActionTransition, ...]]:
    report_path = repo_root / _SYSTEM_ID_REPORT
    report_bytes = report_path.read_bytes()
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    sidecar = report_path.with_name(f"{report_path.name}.sha256")
    expected = sidecar.read_text(encoding="ascii").strip()
    if expected != report_sha:
        raise ValueError("system-identification report SHA-256 sidecar mismatch")
    payload = _load_json_bytes(report_bytes)
    evidence = _mapping(payload.get("evidence"), "evidence")
    horizontal = _mapping(evidence.get("horizontal"), "evidence.horizontal")
    if horizontal.get("complete") is not True:
        raise ValueError("system-identification horizontal sequence is incomplete")
    pointer = _mapping(evidence.get("pointer_mapping"), "evidence.pointer_mapping")
    steps = _sequence(pointer.get("steps"), "pointer_mapping.steps")
    if len(steps) != 2 or any(
        _mapping(step, "pointer step").get("complete_receipt") is not True
        or _mapping(step, "pointer step").get("middle_release_acknowledged")
        is not True
        for step in steps
    ):
        raise ValueError("system-identification pointer receipts are incomplete")
    digest_to_spec = {
        hashlib.sha256(spec.frame.payload).hexdigest(): spec for spec in specs
    }
    transitions: list[ActionTransition] = []
    for step_name, action_id in (
        ("positive_step", "horizontal-positive-fixed-4px"),
        ("return_step", "horizontal-return-fixed-4px"),
    ):
        step = _mapping(horizontal.get(step_name), f"horizontal.{step_name}")
        if step.get("input_state") != "complete" or step.get("terminal_reason") != "complete":
            raise ValueError(f"{step_name} is not a complete input transition")
        receipt = _mapping(step.get("receipt"), f"{step_name}.receipt")
        receipt_actions = _sequence(receipt.get("actions"), f"{step_name}.receipt.actions")
        if len(receipt_actions) != 1:
            raise ValueError(f"{step_name} must contain exactly one bounded action")
        receipts = _sequence(
            _mapping(receipt_actions[0], "receipt action").get("input_receipts"),
            "input_receipts",
        )
        if len(receipts) != 3 or any(
            _mapping(item, "input receipt").get("complete") is not True
            for item in receipts
        ):
            raise ValueError(f"{step_name} has an incomplete low-level receipt")
        commit = _mapping(
            _mapping(step.get("commit"), f"{step_name}.commit").get("artifact"),
            f"{step_name}.commit.artifact",
        )
        post = _mapping(
            _mapping(
                _mapping(step.get("post"), f"{step_name}.post").get("frame"),
                f"{step_name}.post.frame",
            ).get("artifact"),
            f"{step_name}.post.frame.artifact",
        )
        source_sha = _digest_field(commit, "raw_sha256")
        target_sha = _digest_field(post, "raw_sha256")
        if source_sha not in digest_to_spec or target_sha not in digest_to_spec:
            raise ValueError(f"{step_name} report endpoints do not match exact corpus pixels")
        transitions.append(
            ActionTransition(
                action_id=action_id,
                source_sha256=source_sha,
                target_sha256=target_sha,
                evidence_report_sha256=report_sha,
                receipt_verified=True,
            )
        )
    return report_sha, tuple(transitions)


def _drift_gate(graph: ReadOnlyViewGraph) -> dict[str, object]:
    drift_nodes = [node for node in graph.nodes if ViewRole.REAL_DRIFT in node.roles]
    uncertain_frames = sum(
        not node.production.scene_validated
        and not node.production.definitive_target_ids
        and all(
            resource.state.value == "uncertain"
            for resource in node.production.resource_states
        )
        for node in drift_nodes
    )
    false_targets = sum(
        len(node.production.definitive_target_ids) for node in drift_nodes
    )
    detector_ids = sorted(
        {
            f"{node.production.detector_id}@{node.production.detector_version}"
            for node in drift_nodes
        }
    )
    return {
        "detector_ids": detector_ids,
        "expected_frames": 36,
        "false_definitive_target_count": false_targets,
        "passed": len(drift_nodes) == 36
        and uncertain_frames == 36
        and false_targets == 0,
        "uncertain_frames": uncertain_frames,
    }


def _raw_specs(
    repo_root: Path,
    paths: list[Path],
    label_prefix: str,
    role: ViewRole,
    next_frame_id: int,
) -> tuple[list[ViewNodeSpec], int]:
    specs: list[ViewNodeSpec] = []
    for path in paths:
        frame = _read_raw_frame(path, frame_id=next_frame_id)
        next_frame_id += 1
        relative = path.relative_to(repo_root).as_posix()
        specs.append(ViewNodeSpec(f"{label_prefix}:{relative}", frame, role))
    return specs, next_frame_id


def _read_raw_frame(path: Path, *, frame_id: int) -> Frame:
    payload = path.read_bytes()
    return Frame.from_raw(
        RawFrame(
            payload=payload,
            width=1005,
            height=1078,
            pixel_format=PixelFormat.BGRA8888,
        ),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def _spec_digests(specs: list[ViewNodeSpec]) -> list[tuple[str, str]]:
    return [
        (spec.label, hashlib.sha256(spec.frame.payload).hexdigest()) for spec in specs
    ]


def _aggregate_group_digest(items: list[tuple[str, str]]) -> str:
    canonical = "".join(f"{label}\0{digest}\n" for label, digest in sorted(items))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _load_json_bytes(payload: bytes) -> dict[str, object]:
    parsed = json.loads(
        payload,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonstandard_number,
    )
    return _mapping(parsed, "report")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number: {value}")


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be a JSON object")
    return cast(dict[str, object], value)


def _sequence(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    return value


def _digest_field(value: dict[str, object], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or len(raw) != 64 or any(
        character not in "0123456789abcdef" for character in raw
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return raw


if __name__ == "__main__":  # pragma: no cover - exercised through main tests
    raise SystemExit(main())
