from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

import mining_automation.perception.inventory.positive_v3_independent_validation as validation
from mining_automation.perception.inventory.positive_classifier_v3 import (
    InventoryPositiveV3DevelopmentAnalyzer,
)
from mining_automation.perception.inventory.positive_v3_independent_validation import (
    INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA,
    INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256,
    InventoryPositiveV3IndependentValidationError,
    build_inventory_positive_v3_validation_readiness_report,
    evaluate_frozen_v3_independent_validation,
    frozen_v3_model_binding,
    independent_validation_preregistration,
)

_ROOT = Path(__file__).resolve().parent.parent
_EVALUATOR_HEAD = subprocess.run(
    ("git", "-C", str(_ROOT), "rev-parse", "HEAD"),
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
_SESSION_ID = "20990101T000000.000000Z-synthetic-contract-test-session"
_REGION_BYTES = 158 * 248 * 4
_FULL_FRAME_BYTES = 1005 * 1078 * 4


def _synthetic_protocol_lock() -> validation._ValidationProtocolLockBinding:
    return validation._ValidationProtocolLockBinding(
        lock_git_commit_sha="b" * 40,
        lock_git_committed_at_utc="2098-12-31T23:59:00Z",
        lock_git_blob="c" * 40,
        lock_sha256="d" * 64,
        protocol_source_commit_sha="a" * 40,
        locked_git_blobs=(),
        approved_passive_capture=validation._ApprovedPassiveCaptureBinding(
            build_sha="a" * 40,
            capture_configuration_id=(
                "inventory-positive-v3-independent-passive-natural-fill-v1"
            ),
            source_git_blobs=(),
        ),
        evaluator_head_sha=_EVALUATOR_HEAD,
        repository_root=_ROOT,
    )


def load_independent_validation_dataset(
    package_directory: Path,
    *,
    expected_preregistration_sha256: str = (
        INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256
    ),
    protocol_lock: validation._ValidationProtocolLockBinding | None = None,
) -> validation.IndependentValidationDataset:
    return validation._load_independent_validation_dataset(
        package_directory,
        expected_preregistration_sha256=expected_preregistration_sha256,
        protocol_lock=protocol_lock or _synthetic_protocol_lock(),
    )


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_document(path: Path, value: object) -> tuple[bytes, str]:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n",
        encoding="ascii",
        newline="\n",
    )
    return payload, digest


def _synthetic_region(index: int) -> bytes:
    """Return unmistakably synthetic bytes that are never validation evidence."""
    return bytes((offset * 17 + index * 29) % 256 for offset in range(_REGION_BYTES))


def _full_frame_from_region(region: bytes) -> bytes:
    frame = bytearray(_FULL_FRAME_BYTES)
    source_stride = 158 * 4
    destination_stride = 1005 * 4
    for row in range(248):
        source_start = row * source_stride
        destination_start = (569 + row) * destination_stride + 567 * 4
        frame[destination_start : destination_start + source_stride] = region[
            source_start : source_start + source_stride
        ]
    return bytes(frame)


def _development_conformance_payloads() -> tuple[bytes, ...]:
    fixture = (
        _ROOT
        / "tests"
        / "fixtures"
        / "perception"
        / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
        / "frames"
    )
    empty = validation._read_bytes(
        fixture / "001-20260830T183116.108869Z-empty-reference.region.bgra",
        "development empty payload",
    )
    full = validation._read_bytes(
        fixture / "004-20260830T184604.267640Z-full.region.bgra",
        "development full payload",
    )
    wrong_tab = validation._read_bytes(
        fixture / "005-20260830T184613.513325Z-wrong-tab.region.bgra",
        "development wrong-tab payload",
    )
    obstructed = validation._read_bytes(
        fixture / "006-20260830T184628.891977Z-obstructed.region.bgra",
        "development obstructed payload",
    )
    profile = validation._supported_profile()
    slots = profile.layout.all_slot_regions(profile.region)
    stride = profile.region.width * 4

    def occupied_prefix(count: int) -> bytes:
        result = bytearray(empty)
        for slot in slots[:count]:
            relative_x = slot.x - profile.region.x
            relative_y = slot.y - profile.region.y
            for row in range(slot.height):
                start = (relative_y + row) * stride + relative_x * 4
                length = slot.width * 4
                result[start : start + length] = full[start : start + length]
        return bytes(result)

    return (
        empty,
        occupied_prefix(1),
        occupied_prefix(5),
        occupied_prefix(27),
        full,
        wrong_tab,
        obstructed,
    )


PackageMutator = Callable[
    [dict[str, object], dict[str, object], dict[str, object]],
    None,
]


@pytest.fixture(autouse=True)
def _allow_synthetic_tests_to_use_an_uncommitted_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit packages are negative fixtures, never canonical exact-head evidence."""
    monkeypatch.setattr(
        validation,
        "_verify_repository_state",
        lambda root, _expected_head: root.resolve(strict=True),
    )
    binding = _synthetic_protocol_lock()
    monkeypatch.setattr(
        validation,
        "_current_validation_protocol_lock",
        lambda _root: binding,
    )
    monkeypatch.setattr(
        validation,
        "_verify_capture_execution_authorization",
        lambda *_args, **_kwargs: (
            "2" * 64,
            "2098-12-31T23:59:30Z",
            "2098-12-31T23:59:45Z",
        ),
    )


def _build_synthetic_contract_package(
    tmp_path: Path,
    *,
    mutate: PackageMutator | None = None,
    dataset_id: str | None = None,
    session_id: str = _SESSION_ID,
    first_region_payload: bytes | None = None,
    first_full_frame_payload: bytes | None = None,
    first_full_frame_path: str | None = None,
    region_payloads: Sequence[bytes] | None = None,
    source_started_at_utc: str = "2098-12-31T23:59:59Z",
) -> Path:
    root = tmp_path / "synthetic-contract-package"
    root.mkdir(parents=True)
    protocol_lock = validation._current_validation_protocol_lock(_ROOT)
    specifications = (
        ("empty", 0, "inventory-visible"),
        ("early-partial", 1, "inventory-visible"),
        ("mid-partial", 5, "inventory-visible"),
        ("near-full", 27, "inventory-visible"),
        ("full", 28, "inventory-visible"),
        ("wrong-tab", None, "wrong-tab-visible"),
        ("row-obstruction", None, "inventory-obstructed"),
    )
    host_reservation = {
        "authorization_id": "2" * 64,
        "capture_build_sha": protocol_lock.approved_passive_capture.build_sha,
        "capture_configuration_id": (
            protocol_lock.approved_passive_capture.capture_configuration_id
        ),
        "live_authorization_git_commit_sha": "f" * 40,
        "protocol_lock_git_commit_sha": protocol_lock.lock_git_commit_sha,
        "protocol_lock_sha256": protocol_lock.lock_sha256,
        "repository": "maadbonnie21-lgtm/Mining-Automation",
        "schema": "inventory-positive-v3-independent-host-reservation-v1",
        "status": "reserved-and-irrevocably-consumed",
    }
    environment: dict[str, object] = {
        "capture_build_sha": protocol_lock.approved_passive_capture.build_sha,
        "capture_configuration_id": (
            protocol_lock.approved_passive_capture.capture_configuration_id
        ),
        "capture_execution_head_sha": "c" * 40,
        "client_mode": "synthetic-test",
        "frame": {
            "height": 1078,
            "pixel_format": "bgra8888",
            "profile_id": "candidate-live-inventory-348867800b28a54e",
            "width": 1005,
        },
        "host_reservation_sha256": hashlib.sha256(
            _canonical_bytes(host_reservation)
        ).hexdigest(),
        "live_authorization_id": "2" * 64,
        "live_authorization_git_blob": "1" * 40,
        "live_authorization_git_commit_sha": "f" * 40,
        "renderer": "synthetic-test",
        "protocol_lock_git_commit_sha": protocol_lock.lock_git_commit_sha,
        "python_isolated_mode": True,
        "python_isolated_source_cache": True,
        "python_no_site_mode": True,
        "runelite_build": "not-a-live-campaign",
        "theme": "synthetic-test",
        "window_class": "synthetic-test",
        "window_handle": 3107,
        "windows_dpi": 96,
        "windows_scaling_percent": 100,
        "windows_version": "synthetic-test",
    }
    campaign_id = validation._content_bound_campaign_id(session_id)
    cases: list[dict[str, object]] = []
    truths: list[dict[str, object]] = []
    materialized_regions: list[bytes] = []
    for index, (stage, count, visibility) in enumerate(specifications, start=1):
        if region_payloads is not None:
            payload = region_payloads[index - 1]
        else:
            payload = (
                first_region_payload
                if index == 1 and first_region_payload is not None
                else _synthetic_region(index)
            )
        capture_id = f"20990101T0000{index:02d}.000000Z-{stage}"
        case_id = f"{session_id}/{capture_id}"
        relative_path = f"frames/{index:03d}-{stage}.region.bgra"
        payload_path = root / Path(relative_path)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.write_bytes(payload)
        materialized_regions.append(payload)
        digest = hashlib.sha256(payload).hexdigest()
        cases.append(
            {
                "capture_id": capture_id,
                "captured_at_utc": f"2099-01-01T00:00:{index:02d}Z",
                "case_id": case_id,
                "frame_region": {
                    "path": relative_path,
                    "sha256": digest,
                    "size_bytes": len(payload),
                },
                "operator_label_status": "operator-selected-unverified",
                "operator_stage_label": f"operator-intended-{stage}",
                "planned_stage_id": stage,
                "sequence_index": index,
                "session_id": session_id,
                "source": {},
            }
        )
        truths.append(
            {
                "case_id": case_id,
                "decision": "approved",
                "drag_visible": False,
                "frame_region_sha256": digest,
                "hover_visible": False,
                "occupied_slots": count,
                "ordinary_iron_only": stage in {
                    "empty",
                    "early-partial",
                    "mid-partial",
                    "near-full",
                    "full",
                },
                "quantity_text_visible": False,
                "review_note": "synthetic contract test; not real validation truth",
                "selected_item_visible": False,
                "visibility": visibility,
            }
        )
    manifest: dict[str, object] = {
        "activation_allowed": False,
        "all_owned_captures_included": True,
        "campaign_id": campaign_id,
        "campaign_status": "finalized",
        "candidate_head_sha": INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA,
        "capture_environment": environment,
        "cases": cases,
        "dataset_id": dataset_id or "pending-content-bound-dataset-id",
        "dataset_role": "independent-validation-only",
        "finalized_at_utc": "2099-01-01T00:02:00Z",
        "operator": "synthetic-contract-test-operator",
        "preregistration_sha256": INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256,
        "prior_campaigns": [],
        "prototype_eligible": False,
        "schema": "inventory-positive-v3-independent-validation-dataset-v2",
        "selection_policy": (
            "all-owned-captures-in-source-order-no-drop-no-replacement"
        ),
        "session_id": session_id,
        "source_completion_seal": {
            "path": "source/completion-seal.json",
            "sha256": "0" * 64,
        },
        "source_session_report": {
            "path": "source/session-report.json",
            "sha256": "0" * 64,
        },
        "training_allowed": False,
    }
    review: dict[str, object] = {
        "activation_allowed": False,
        "campaign_id": campaign_id,
        "campaign_manifest_sha256": "0" * 64,
        "cases": truths,
        "dataset_id": dataset_id or "pending-content-bound-dataset-id",
        "reviewed_at_utc": "2099-01-01T01:00:00Z",
        "reviewer": "synthetic-contract-test-reviewer",
        "schema": "inventory-positive-v3-independent-validation-review-v1",
        "truth_source": "independent-human-review",
    }
    package: dict[str, object] = {
        "activation_allowed": False,
        "campaign_manifest": {
            "path": "campaign-manifest.json",
            "sha256": "0" * 64,
        },
        "dataset_role": "independent-validation-only",
        "preregistration_sha256": INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256,
        "prototype_eligible": False,
        "reviewer_truth": {
            "path": "reviewer-truth.json",
            "sha256": "0" * 64,
        },
        "schema": "inventory-positive-v3-independent-validation-package-v2",
        "training_allowed": False,
    }
    if mutate is not None:
        mutate(manifest, review, package)

    source_captures: list[dict[str, object]] = []
    source_owned_attempts: list[dict[str, object]] = []
    raw_cases = manifest["cases"]
    assert isinstance(raw_cases, list)
    source_environment = manifest["capture_environment"]
    assert isinstance(source_environment, dict)
    for position, raw_case in enumerate(raw_cases, start=1):
        assert isinstance(raw_case, dict)
        capture_id = raw_case["capture_id"]
        captured_at_utc = raw_case["captured_at_utc"]
        case_session_id = raw_case["session_id"]
        frame_region = raw_case["frame_region"]
        assert isinstance(capture_id, str)
        assert isinstance(captured_at_utc, str)
        assert isinstance(case_session_id, str)
        assert isinstance(frame_region, dict)
        report_relative = f"source/captures/{position:03d}-capture-report.json"
        full_frame = (
            first_full_frame_payload
            if position == 1 and first_full_frame_payload is not None
            else _full_frame_from_region(
                materialized_regions[position - 1]
            )
        )
        full_frame_relative = (
            first_full_frame_path
            if position == 1 and first_full_frame_path is not None
            else f"source/frames/{position:03d}-full-frame.bgra"
        )
        full_frame_path = root / Path(full_frame_relative)
        if ".." not in Path(full_frame_relative).parts:
            full_frame_path.parent.mkdir(parents=True, exist_ok=True)
            full_frame_path.write_bytes(full_frame)
        owned_report_relative = f"source/owned/{position:03d}-owned-frame.json"
        owned_report = {
            "capture_id": capture_id,
            "captured_at_utc": captured_at_utc,
            "frame": {
                "frame_id": position,
                "height": 1078,
                "path": full_frame_relative,
                "pixel_format": "bgra8888",
                "sha256": hashlib.sha256(full_frame).hexdigest(),
                "size_bytes": len(full_frame),
                "width": 1005,
            },
            "planned_stage_id": raw_case["planned_stage_id"],
            "schema": "inventory-positive-v3-independent-owned-frame-v1",
            "sequence_index": position,
            "session_id": case_session_id,
            "status": "captured-unreviewed",
            "window": {
                "class": "synthetic-test",
                "handle": 3107,
                "windows_dpi": 96,
            },
        }
        _, owned_report_sha = _write_document(
            root / Path(owned_report_relative), owned_report
        )
        capture_report = {
            "activation_allowed": False,
            "capture_policy": {
                "backend_attempts": 1,
                "detector_executed": False,
                "input_automation_allowed": False,
                "pixel_materialization": "fixed-bgra-row-slice-only",
            },
            "capture_environment": source_environment,
            "capture_id": capture_id,
            "captured_at_utc": captured_at_utc,
            "full_frame": {
                "height": 1078,
                "path": full_frame_relative,
                "pixel_format": "bgra8888",
                "sha256": hashlib.sha256(full_frame).hexdigest(),
                "size_bytes": len(full_frame),
                "width": 1005,
            },
            "inventory_region": {
                "path": frame_region["path"],
                "region": list(validation.SUPPORTED_REGION),
                "sha256": frame_region["sha256"],
                "size_bytes": frame_region["size_bytes"],
            },
            "schema": "inventory-positive-v3-independent-source-capture-v2",
            "session_id": case_session_id,
        }
        _, capture_report_sha = _write_document(
            root / Path(report_relative), capture_report
        )
        raw_case["source"] = {
            "capture_report": {
                "path": report_relative,
                "sha256": capture_report_sha,
            }
        }
        source_captures.append(
            {
                "capture_id": capture_id,
                "captured_at_utc": captured_at_utc,
                "capture_report": {
                    "path": report_relative,
                    "sha256": capture_report_sha,
                },
                "planned_stage_id": raw_case["planned_stage_id"],
                "sequence_index": position,
            }
        )
        source_owned_attempts.append(
            {
                "capture_id": capture_id,
                "full_frame_attempt": {
                    "path": full_frame_relative,
                    "sha256": hashlib.sha256(full_frame).hexdigest(),
                    "size_bytes": len(full_frame),
                },
                "owned_frame_report": {
                    "path": owned_report_relative,
                    "sha256": owned_report_sha,
                },
                "planned_stage_id": raw_case["planned_stage_id"],
                "sequence_index": position,
                "status": "owned-frame-finalized",
            }
        )
    source_session = {
        "activation_allowed": False,
        "all_owned_captures_included": True,
        "campaign_id": manifest["campaign_id"],
        "capture_environment": source_environment,
        "captures": source_captures,
        "completed_at_utc": "2099-01-01T00:01:00Z",
        "operator": manifest["operator"],
        "owned_attempts": source_owned_attempts,
        "schema": "inventory-positive-v3-independent-source-session-v2",
        "session_id": manifest["session_id"],
        "started_at_utc": source_started_at_utc,
    }
    _, source_session_sha = _write_document(
        root / "source" / "session-report.json", source_session
    )
    source_session_ref = manifest["source_session_report"]
    assert isinstance(source_session_ref, dict)
    source_session_ref["sha256"] = source_session_sha
    completion_seal = {
        "activation_allowed": False,
        "authorization_id": source_environment["live_authorization_id"],
        "campaign_id": manifest["campaign_id"],
        "capture_count": len(source_captures),
        "capture_execution_head_sha": source_environment[
            "capture_execution_head_sha"
        ],
        "completed_at_utc": source_session["completed_at_utc"],
        "host_reservation_sha256": source_environment[
            "host_reservation_sha256"
        ],
        "live_authorization_git_commit_sha": source_environment[
            "live_authorization_git_commit_sha"
        ],
        "protocol_lock_git_commit_sha": source_environment[
            "protocol_lock_git_commit_sha"
        ],
        "schema": "inventory-positive-v3-independent-source-completion-seal-v1",
        "session_id": manifest["session_id"],
        "source_session_report_sha256": source_session_sha,
        "status": "complete-not-reviewed",
    }
    _, completion_seal_sha = _write_document(
        root / "source" / "completion-seal.json",
        completion_seal,
    )
    completion_seal_ref = manifest["source_completion_seal"]
    assert isinstance(completion_seal_ref, dict)
    completion_seal_ref["sha256"] = completion_seal_sha
    if manifest["dataset_id"] == "pending-content-bound-dataset-id":
        manifest["dataset_id"] = validation._content_bound_dataset_id(manifest)
    review["campaign_id"] = manifest["campaign_id"]
    review["dataset_id"] = manifest["dataset_id"]
    _, manifest_sha = _write_document(root / "campaign-manifest.json", manifest)
    review["campaign_manifest_sha256"] = manifest_sha
    _, review_sha = _write_document(root / "reviewer-truth.json", review)
    campaign_ref = package["campaign_manifest"]
    reviewer_ref = package["reviewer_truth"]
    assert isinstance(campaign_ref, dict)
    assert isinstance(reviewer_ref, dict)
    campaign_ref["sha256"] = manifest_sha
    reviewer_ref["sha256"] = review_sha
    _write_document(root / "validation-package.json", package)
    return root


def _rewrite_document(path: Path, mutate: Callable[[dict[str, object]], None]) -> None:
    decoded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    mutate(decoded)
    _write_document(path, decoded)


def _approval_entry_for(
    dataset: validation.IndependentValidationDataset,
    *,
    registry_sha256: str = "e" * 64,
) -> tuple[dict[str, object], str]:
    pending = validation._ApprovedCampaignBinding(
        approval_id="pending",
        approved_at_utc="2099-01-01T02:00:00Z",
        approver="synthetic-contract-test-approver",
        operator=dataset.operator,
        reviewer=dataset.reviewer,
        campaign_id=dataset.campaign_id,
        dataset_id=dataset.dataset_id,
        package_sha256=dataset.package_sha256,
        campaign_manifest_sha256=dataset.campaign_manifest_sha256,
        reviewer_truth_sha256=dataset.reviewer_truth_sha256,
        source_completion_seal_sha256=(
            dataset.source_completion_seal_sha256
        ),
        source_session_report_sha256=dataset.source_session_report_sha256,
        registry_sha256=registry_sha256,
    )
    approved = replace(
        pending,
        approval_id=validation._content_bound_approval_id(pending),
    )
    return approved.to_dict(), registry_sha256


def test_source_owned_preregistration_matches_frozen_candidate_and_readiness() -> None:
    preregistration_path = (
        _ROOT / "validation" / "inventory-positive-v3" / "preregistration.json"
    )
    payload = preregistration_path.read_bytes()
    assert payload == _canonical_bytes(independent_validation_preregistration())
    assert hashlib.sha256(payload).hexdigest() == (
        INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256
    )
    assert preregistration_path.with_suffix(".sha256").read_text(
        encoding="ascii"
    ) == (
        f"{INVENTORY_POSITIVE_V3_PREREGISTRATION_SHA256}  "
        "preregistration.json\n"
    )

    report = build_inventory_positive_v3_validation_readiness_report(
        _ROOT,
        evaluator_git_head_sha=_EVALUATOR_HEAD,
    )

    binding = frozen_v3_model_binding()
    assert binding.candidate_head_sha == INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA
    assert report["readiness_passed"] is True
    assert report["live_validation_performed"] is False
    assert report["campaign_execution_authorized"] is False
    assert report["activation_allowed"] is False
    assert report["independent_validation_case_count"] == 0
    assert report["approval_registry"] == {
        "approved_campaign_count": 0,
        "path": "validation/inventory-positive-v3/approved-campaigns.json",
        "sha256": "a2bc8cb0fa829fddb9a8fe672d1fa93b835ae57e5d037bba880dfbb96f3fd4ad",
    }


def test_source_owned_approval_registry_is_canonical_empty_and_nonactivating() -> None:
    path = (
        _ROOT
        / "validation"
        / "inventory-positive-v3"
        / "approved-campaigns.json"
    )
    expected = {
        "activation_allowed": False,
        "entries": [],
        "promotion_allowed": False,
        "schema": (
            "inventory-positive-v3-independent-validation-approval-registry-v1"
        ),
    }
    payload = validation._read_bytes(path, "test approval registry")
    assert payload == _canonical_bytes(expected)
    digest = hashlib.sha256(payload).hexdigest()
    assert validation._read_text(
        path.with_suffix(".json.sha256"),
        "test approval registry sidecar",
    ) == f"{digest}  approved-campaigns.json\n"


def test_synthetic_schema_matrix_is_evaluated_but_cannot_claim_validation(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)

    report = evaluate_frozen_v3_independent_validation(
        package,
        repository_root=_ROOT,
        evaluator_git_head_sha=_EVALUATOR_HEAD,
    )

    assert report.validation_passed is False
    assert report.activation_allowed is False
    assert report.validation_status == "approval-required"
    assert len(report.cases) == 7
    assert any(not item.passed for item in report.cases)
    assert all(not item.byte_identical_to_development_payload for item in report.cases)
    assert report.candidate_identity_before == report.candidate_identity_after
    decoded = report.to_dict()
    assert decoded["promotion_allowed"] is False
    assert decoded["contamination_firewall"]["prototypes_added"] == 0
    assert decoded["action_authority"]["click_authority"] is False


def test_failed_synthetic_campaign_is_deterministic_and_never_learns(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)

    first = evaluate_frozen_v3_independent_validation(
        package,
        repository_root=_ROOT,
        evaluator_git_head_sha=_EVALUATOR_HEAD,
    )
    second = evaluate_frozen_v3_independent_validation(
        package,
        repository_root=_ROOT,
        evaluator_git_head_sha=_EVALUATOR_HEAD,
    )

    assert first.validation_passed is False
    assert first.to_json() == second.to_json()
    assert first.candidate_identity_before == first.candidate_identity_after
    assert first.to_dict()["contamination_firewall"] == {
        "candidate_identity_unchanged": True,
        "development_and_validation_dataset_paths_are_separate": True,
        "prototype_learning_allowed": False,
        "prototypes_added": 0,
        "training_allowed": False,
        "validation_case_export_to_model_allowed": False,
    }


def test_missing_source_capture_report_rejects_copied_pixels_before_analysis(
    tmp_path: Path,
) -> None:
    development_region_path = (
        _ROOT
        / "tests"
        / "fixtures"
        / "perception"
        / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
        / "frames"
        / "001-20260830T183116.108869Z-empty-reference.region.bgra"
    )
    development_region = validation._read_bytes(
        development_region_path,
        "frozen development region test input",
    )
    package = _build_synthetic_contract_package(
        tmp_path,
        first_region_payload=development_region,
    )
    (package / "source" / "captures" / "001-capture-report.json").unlink()

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="cannot resolve .*source capture report",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_rebound_source_capture_report_is_rejected_before_analysis(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    _rewrite_document(
        package / "source" / "captures" / "001-capture-report.json",
        lambda report: report.__setitem__("activation_allowed", True),
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="source capture report SHA-256 mismatch",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_missing_source_full_frame_is_rejected_before_analysis(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    (package / "source" / "frames" / "001-full-frame.bgra").unlink()

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="owned full frame|source full frame",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_missing_completion_seal_is_rejected_before_analysis(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    (package / "source" / "completion-seal.json").unlink()

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="cannot resolve source completion seal",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_rebound_completion_seal_is_rejected_before_analysis(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    _rewrite_document(
        package / "source" / "completion-seal.json",
        lambda seal: seal.__setitem__("status", "failed-retained"),
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="completion seal SHA-256 mismatch",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_source_full_frame_wrong_size_is_rejected_before_analysis(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(
        tmp_path,
        first_full_frame_payload=b"not-a-full-frame",
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="full frame cannot be cropped|full-frame size differs",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_source_region_must_be_the_exact_fixed_crop_of_full_frame(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(
        tmp_path,
        first_full_frame_payload=bytes(_FULL_FRAME_BYTES),
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="not the exact fixed full-frame row slice",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_source_full_frame_path_cannot_escape_campaign(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(
        tmp_path,
        first_full_frame_path="../outside-private-frame.bgra",
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="safe relative POSIX path",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_byte_identical_content_bound_region_is_reported_not_rejected(
    tmp_path: Path,
) -> None:
    development_region_path = (
        _ROOT
        / "tests"
        / "fixtures"
        / "perception"
        / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
        / "frames"
        / "001-20260830T183116.108869Z-empty-reference.region.bgra"
    )
    development_region = validation._read_bytes(
        development_region_path,
        "frozen development region test input",
    )
    package = _build_synthetic_contract_package(
        tmp_path,
        first_region_payload=development_region,
    )

    report = evaluate_frozen_v3_independent_validation(
        package,
        repository_root=_ROOT,
        evaluator_git_head_sha=_EVALUATOR_HEAD,
    )

    assert report.validation_passed is False
    assert report.cases[0].byte_identical_to_development_payload is True
    assert report.activation_allowed is False


@pytest.mark.parametrize(
    "package_path",
    [
        _ROOT
        / "tests"
        / "fixtures"
        / "perception"
        / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b",
        _ROOT / "tests" / "fixtures" / "perception",
        _ROOT
        / "tests"
        / "fixtures"
        / "perception"
        / "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
        / "frames",
    ],
    ids=("equal", "ancestor", "inside"),
)
def test_development_fixture_path_overlap_rejected_before_package_load(
    package_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def package_load_is_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("overlapping validation package was opened")

    monkeypatch.setattr(
        validation,
        "_load_independent_validation_dataset",
        package_load_is_forbidden,
    )
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="package path overlaps the development fixture",
    ):
        evaluate_frozen_v3_independent_validation(
            package_path,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest, review, package: manifest.__setitem__(
                "dataset_id", ""
            ),
            "dataset_id is empty",
        ),
        (
            lambda manifest, review, package: manifest.__setitem__(
                "campaign_id", ""
            ),
            "campaign_id is empty",
        ),
        (
            lambda manifest, review, package: manifest.__setitem__(
                "session_id", ""
            ),
            "session_id is empty",
        ),
        (
            lambda manifest, review, package: manifest["cases"][0].__setitem__(  # type: ignore[index,union-attr]
                "case_id", ""
            ),
            "case_id is empty",
        ),
        (
            lambda manifest, review, package: manifest["cases"][0].__setitem__(  # type: ignore[index,union-attr]
                "capture_id", ""
            ),
            "capture_id is empty",
        ),
    ],
)
def test_every_campaign_identity_must_be_nonempty(
    tmp_path: Path,
    mutate: PackageMutator,
    message: str,
) -> None:
    package = _build_synthetic_contract_package(tmp_path, mutate=mutate)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match=message,
    ):
        load_independent_validation_dataset(package)


def test_campaign_and_dataset_ids_are_content_bound(tmp_path: Path) -> None:
    arbitrary_dataset = _build_synthetic_contract_package(
        tmp_path / "dataset",
        dataset_id="arbitrary-independent-dataset",
    )
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="dataset_id is not derived",
    ):
        load_independent_validation_dataset(arbitrary_dataset)

    def replace_campaign(
        manifest: dict[str, object],
        review: dict[str, object],
        package: dict[str, object],
    ) -> None:
        manifest["campaign_id"] = "arbitrary-independent-campaign"

    arbitrary_campaign = _build_synthetic_contract_package(
        tmp_path / "campaign",
        mutate=replace_campaign,
    )
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="campaign_id is not derived",
    ):
        load_independent_validation_dataset(arbitrary_campaign)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("capture_build_sha", "9" * 40, "source-approved passive build"),
        (
            "capture_build_sha",
            INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA,
            "source-approved passive build",
        ),
        ("capture_build_sha", "0" * 40, "source-approved passive build"),
        (
            "capture_configuration_id",
            "attacker-rebound-capture-configuration",
            "source-approved identity",
        ),
        (
            "protocol_lock_git_commit_sha",
            "8" * 40,
            "wrong protocol lock commit",
        ),
        (
            "live_authorization_id",
            "3" * 64,
            "live authorization identity differs",
        ),
        (
            "python_isolated_mode",
            False,
            "not produced through Python isolated mode",
        ),
        (
            "python_isolated_source_cache",
            False,
            "no-site isolated-source launcher",
        ),
        (
            "python_no_site_mode",
            False,
            "no-site isolated-source launcher",
        ),
    ],
)
def test_capture_environment_cannot_rebind_source_owned_provenance(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    def mutate(
        manifest: dict[str, object],
        _review: dict[str, object],
        _package: dict[str, object],
    ) -> None:
        environment = manifest["capture_environment"]
        assert isinstance(environment, dict)
        environment[field] = value

    package = _build_synthetic_contract_package(tmp_path, mutate=mutate)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match=message,
    ):
        load_independent_validation_dataset(package)


def test_session_start_must_be_strictly_after_protocol_lock(
    tmp_path: Path,
) -> None:
    protocol_lock = validation._current_validation_protocol_lock(_ROOT)
    package = _build_synthetic_contract_package(
        tmp_path,
        source_started_at_utc=protocol_lock.lock_git_committed_at_utc,
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="must begin after the protocol lock",
    ):
        load_independent_validation_dataset(package, protocol_lock=protocol_lock)


def test_session_start_must_follow_authorization_and_execution_head(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(
        tmp_path,
        source_started_at_utc="2098-12-31T23:59:40Z",
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="after authorization and execution HEAD",
    ):
        load_independent_validation_dataset(package)


def test_each_source_capture_must_follow_authorization_and_execution_head(
    tmp_path: Path,
) -> None:
    def backdate_first_capture(
        manifest: dict[str, object],
        _review: dict[str, object],
        _package: dict[str, object],
    ) -> None:
        cases = manifest["cases"]
        assert isinstance(cases, list)
        first = cases[0]
        assert isinstance(first, dict)
        first["captured_at_utc"] = "2098-12-31T23:59:45Z"

    package = _build_synthetic_contract_package(
        tmp_path,
        mutate=backdate_first_capture,
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="predates authorization or execution HEAD",
    ):
        load_independent_validation_dataset(package)


@pytest.mark.parametrize("api", ["readiness", "evaluate"])
def test_report_apis_refuse_unverified_exact_head_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api: str,
) -> None:
    def reject_unverified_head(root: Path, expected_head: str) -> Path:
        raise InventoryPositiveV3IndependentValidationError(
            "synthetic unverified exact-head claim"
        )

    monkeypatch.setattr(validation, "_verify_repository_state", reject_unverified_head)
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="unverified exact-head claim",
    ):
        if api == "readiness":
            build_inventory_positive_v3_validation_readiness_report(
                _ROOT,
                evaluator_git_head_sha=_EVALUATOR_HEAD,
            )
        else:
            evaluate_frozen_v3_independent_validation(
                _build_synthetic_contract_package(tmp_path),
                repository_root=_ROOT,
                evaluator_git_head_sha=_EVALUATOR_HEAD,
            )


def test_readiness_report_rechecks_repository_state_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def verify(root: Path, _expected_head: str) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise InventoryPositiveV3IndependentValidationError(
                "repository changed while readiness report was constructed"
            )
        return root.resolve(strict=True)

    monkeypatch.setattr(validation, "_verify_repository_state", verify)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="repository changed while readiness report was constructed",
    ):
        build_inventory_positive_v3_validation_readiness_report(
            _ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )
    assert calls == 2


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest, review, package: manifest.__setitem__(
                "dataset_id", "inventory-live-candidate-safety-bb0d0e3f7ff1c73b"
            ),
            "development dataset",
        ),
        (
            lambda manifest, review, package: manifest.__setitem__(
                "candidate_head_sha", "c" * 40
            ),
            "candidate head",
        ),
        (
            lambda manifest, review, package: manifest.__setitem__(
                "preregistration_sha256", "c" * 64
            ),
            "preregistration hash",
        ),
        (
            lambda manifest, review, package: manifest.__setitem__(
                "model_override", {"publication_floor": 0.1}
            ),
            "model_override",
        ),
        (
            lambda manifest, review, package: manifest[
                "capture_environment"
            ].pop("runelite_build"),  # type: ignore[union-attr]
            "runelite_build",
        ),
        (
            lambda manifest, review, package: manifest.__setitem__(
                "activation_allowed", True
            ),
            "cannot authorize activation",
        ),
        (
            lambda manifest, review, package: manifest.__setitem__(
                "training_allowed", True
            ),
            "cannot allow training",
        ),
        (
            lambda manifest, review, package: manifest.__setitem__(
                "prototype_eligible", True
            ),
            "prototype-eligible",
        ),
    ],
)
def test_manifest_candidate_provenance_and_model_knobs_fail_closed(
    tmp_path: Path,
    mutate: PackageMutator,
    message: str,
) -> None:
    package = _build_synthetic_contract_package(tmp_path, mutate=mutate)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match=message,
    ):
        load_independent_validation_dataset(package)


def test_operator_label_cannot_populate_missing_reviewer_truth(
    tmp_path: Path,
) -> None:
    def mutate(
        manifest: dict[str, object],
        review: dict[str, object],
        package: dict[str, object],
    ) -> None:
        cases = manifest["cases"]
        truths = review["cases"]
        assert isinstance(cases, list)
        assert isinstance(truths, list)
        case = cases[0]
        assert isinstance(case, dict)
        case["operator_stage_label"] = "empty-with-claimed-zero"
        truths.pop(0)

    package = _build_synthetic_contract_package(tmp_path, mutate=mutate)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="operator label cannot substitute",
    ):
        load_independent_validation_dataset(package)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest, review, package: manifest["cases"].reverse(),  # type: ignore[union-attr]
            "sequence indexes",
        ),
        (
            lambda manifest, review, package: manifest["cases"][3].__setitem__(  # type: ignore[index,union-attr]
                "planned_stage_id", "mid-partial"
            ),
            "exactly the seven preregistered stages",
        ),
        (
            lambda manifest, review, package: manifest["cases"][3].__setitem__(  # type: ignore[index,union-attr]
                "planned_stage_id", "unexpected-presentation"
            ),
            "unsupported source-session stage",
        ),
        (
            lambda manifest, review, package: review["cases"][3].__setitem__(  # type: ignore[index,union-attr]
                "occupied_slots", 4
            ),
            "0 < early < mid < near < 28",
        ),
        (
            lambda manifest, review, package: manifest["cases"][0].__setitem__(  # type: ignore[index,union-attr]
                "captured_at_utc", "2026-08-31T05:25:40Z"
            ),
            "pre-protocol-lock",
        ),
        (
            lambda manifest, review, package: manifest["cases"][0][  # type: ignore[index]
                "frame_region"
            ].__setitem__("path", "../foreign.region.bgra"),  # type: ignore[union-attr]
            "safe relative POSIX path",
        ),
    ],
)
def test_sequence_truth_chronology_and_path_integrity_fail_closed(
    tmp_path: Path,
    mutate: PackageMutator,
    message: str,
) -> None:
    package = _build_synthetic_contract_package(tmp_path, mutate=mutate)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match=message,
    ):
        load_independent_validation_dataset(package)


def test_protocol_v1_rejects_prior_campaign_disclosure_or_retry(
    tmp_path: Path,
) -> None:
    def add_prior_campaign(
        manifest: dict[str, object],
        _review: dict[str, object],
        _package: dict[str, object],
    ) -> None:
        prior_campaigns = manifest["prior_campaigns"]
        assert isinstance(prior_campaigns, list)
        prior_campaigns.append(
            {
                "campaign_id": "inventory-positive-v3-campaign-prior",
                "manifest_sha256": "4" * 64,
                "status": "failed",
            }
        )

    package = _build_synthetic_contract_package(
        tmp_path,
        mutate=add_prior_campaign,
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="one irrevocable authorized capture attempt only",
    ):
        load_independent_validation_dataset(package)


def test_development_session_identity_cannot_masquerade_as_validation(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(
        tmp_path,
        session_id="20260830T183057.424897Z-inventory-session",
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="development session",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_validation_package_and_review_hash_rebinding_is_rejected(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    package_path = package / "validation-package.json"
    _rewrite_document(
        package_path,
        lambda decoded: decoded["reviewer_truth"].__setitem__(  # type: ignore[union-attr]
            "sha256", "d" * 64
        ),
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="review truth hash",
    ):
        load_independent_validation_dataset(package)


def test_runtime_model_rebinding_fails_before_validation_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    monkeypatch.setattr(validation, "MODEL_ARTIFACT_SHA256", "f" * 64)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="runtime V3 candidate differs",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_campaign_mutation_during_evaluation_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    target = package / "frames" / "001-empty.region.bgra"
    original_match = validation._matches_expected
    changed = False

    def match_and_mutate(*args: object):
        nonlocal changed
        result = original_match(*args)  # type: ignore[arg-type]
        if not changed:
            changed = True
            target.write_bytes(target.read_bytes() + b"foreign-mutation")
        return result

    monkeypatch.setattr(validation, "_matches_expected", match_and_mutate)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="changed during evaluation",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_preregistration_callers_cannot_mutate_source_owned_contract() -> None:
    first = independent_validation_preregistration()
    candidate = first["candidate"]
    assert isinstance(candidate, dict)
    candidate["configuration_id"] = "attacker-rebind"

    second = independent_validation_preregistration()
    second_candidate = second["candidate"]
    assert isinstance(second_candidate, dict)
    assert second_candidate["configuration_id"] == (
        frozen_v3_model_binding().configuration_id
    )


def test_copied_development_conformance_is_disclosed_but_not_approved(
    tmp_path: Path,
) -> None:
    package = _build_synthetic_contract_package(
        tmp_path,
        region_payloads=_development_conformance_payloads(),
    )

    report = evaluate_frozen_v3_independent_validation(
        package,
        repository_root=_ROOT,
        evaluator_git_head_sha=_EVALUATOR_HEAD,
    )

    assert report.detector_conformance_passed is True
    assert report.validation_passed is False
    assert report.validation_status == "approval-required"
    assert report.approval is None
    assert any(item.byte_identical_to_development_payload for item in report.cases)
    assert report.to_dict()["activation_allowed"] is False
    assert report.to_dict()["promotion_allowed"] is False


def test_operator_cannot_review_own_campaign(tmp_path: Path) -> None:
    def same_person(
        manifest: dict[str, object],
        review: dict[str, object],
        _package: dict[str, object],
    ) -> None:
        review["reviewer"] = manifest["operator"]

    package = _build_synthetic_contract_package(tmp_path, mutate=same_person)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="reviewer must be distinct",
    ):
        load_independent_validation_dataset(package)


def test_missing_or_forged_source_approval_cannot_validate(tmp_path: Path) -> None:
    dataset = load_independent_validation_dataset(
        _build_synthetic_contract_package(tmp_path)
    )
    registry_sha = "e" * 64
    assert (
        validation._parse_approval_registry(
            (), dataset, registry_sha256=registry_sha
        )
        is None
    )
    entry, _ = _approval_entry_for(dataset, registry_sha256=registry_sha)
    approved = validation._parse_approval_registry(
        (entry,), dataset, registry_sha256=registry_sha
    )
    assert approved is not None
    assert approved.approver == "synthetic-contract-test-approver"
    entry["package_sha256"] = "f" * 64
    rebound = validation._ApprovedCampaignBinding(
        approval_id="pending",
        approved_at_utc=str(entry["approved_at_utc"]),
        approver=str(entry["approver"]),
        operator=str(entry["operator"]),
        reviewer=str(entry["reviewer"]),
        campaign_id=str(entry["campaign_id"]),
        dataset_id=str(entry["dataset_id"]),
        package_sha256=str(entry["package_sha256"]),
        campaign_manifest_sha256=str(entry["campaign_manifest_sha256"]),
        reviewer_truth_sha256=str(entry["reviewer_truth_sha256"]),
        source_completion_seal_sha256=str(
            entry["source_completion_seal_sha256"]
        ),
        source_session_report_sha256=str(entry["source_session_report_sha256"]),
        registry_sha256=registry_sha,
    )
    entry["approval_id"] = validation._content_bound_approval_id(rebound)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="forged or rebound",
    ):
        validation._parse_approval_registry(
            (entry,), dataset, registry_sha256=registry_sha
        )


def test_runtime_analyze_replacement_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    monkeypatch.setattr(
        InventoryPositiveV3DevelopmentAnalyzer,
        "analyze",
        lambda _self, _frame: None,
    )

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="class/callable binding changed",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_live_analyzer_state_rebinding_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _build_synthetic_contract_package(tmp_path)
    original_load = validation._load_frozen_candidate

    def load_mutated(root: Path):
        candidate = original_load(root)
        candidate.analyzer._prototype_sources.clear()
        return candidate

    monkeypatch.setattr(validation, "_load_frozen_candidate", load_mutated)
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="state differs from the frozen",
    ):
        evaluate_frozen_v3_independent_validation(
            package,
            repository_root=_ROOT,
            evaluator_git_head_sha=_EVALUATOR_HEAD,
        )


def test_dataclass_replace_cannot_turn_failure_into_serialized_pass(
    tmp_path: Path,
) -> None:
    report = evaluate_frozen_v3_independent_validation(
        _build_synthetic_contract_package(tmp_path),
        repository_root=_ROOT,
        evaluator_git_head_sha=_EVALUATOR_HEAD,
    )
    failed = next(item for item in report.cases if not item.passed)

    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="case result integrity mismatch",
    ):
        replace(failed, passed=True, failure_reason=None)
    with pytest.raises(
        InventoryPositiveV3IndependentValidationError,
        match="verified evaluator",
    ):
        replace(report, cases=report.cases)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest, review, package: manifest.__setitem__(
                "finalized_at_utc", "2099-01-01T00:00:30Z"
            ),
            "finalization must follow session completion",
        ),
        (
            lambda manifest, review, package: review.__setitem__(
                "reviewed_at_utc", "2099-01-01T00:01:30Z"
            ),
            "precede review",
        ),
    ],
)
def test_session_manifest_review_chronology_is_strict(
    tmp_path: Path,
    mutate: PackageMutator,
    message: str,
) -> None:
    package = _build_synthetic_contract_package(tmp_path, mutate=mutate)
    with pytest.raises(InventoryPositiveV3IndependentValidationError, match=message):
        load_independent_validation_dataset(package)
