"""Exact-clean-head CLI for frozen Inventory V3 validation readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .positive_v3_independent_validation import (
    INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA,
    InventoryPositiveV3IndependentValidationError,
    build_inventory_positive_v3_validation_readiness_report,
    evaluate_frozen_v3_independent_validation,
    frozen_v3_model_binding,
    independent_validation_preregistration,
)


@dataclass(slots=True)
class _OwnedArtifact:
    """Filesystem identity of one path exclusively created by this invocation."""

    path: Path
    device: int
    inode: int
    expected_sha256: str
    handle: BinaryIO
    modified_ns: int
    size_bytes: int
    complete: bool = False

    @classmethod
    def from_open_file(
        cls,
        path: Path,
        handle: BinaryIO,
        payload: bytes,
    ) -> _OwnedArtifact:
        stat = os.fstat(handle.fileno())
        return cls(
            path=path,
            device=stat.st_dev,
            inode=stat.st_ino,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            handle=handle,
            modified_ns=stat.st_mtime_ns,
            size_bytes=len(payload),
        )

    def mark_complete(self) -> None:
        stat = os.fstat(self.handle.fileno())
        self.modified_ns = stat.st_mtime_ns
        self.complete = True

    def current_path_matches_recorded_artifact(self) -> bool:
        if not self.current_path_matches_recorded_identity():
            return False
        try:
            payload_sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        except OSError:
            return False
        return payload_sha256 == self.expected_sha256

    def current_path_matches_recorded_identity(self) -> bool:
        try:
            stat = self.path.stat(follow_symlinks=False)
        except (OSError, ValueError):
            return False
        return (
            stat.st_dev == self.device
            and stat.st_ino == self.inode
            and stat.st_mtime_ns == self.modified_ns
            and stat.st_size == self.size_bytes
        )

    def still_owns_path(self) -> bool:
        try:
            held = os.fstat(self.handle.fileno())
            stat = self.path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            return False
        identity_matches = (
            held.st_dev == self.device
            and held.st_ino == self.inode
            and stat.st_dev == self.device
            and stat.st_ino == self.inode
        )
        if not identity_matches:
            return False
        if not self.complete:
            self.modified_ns = held.st_mtime_ns
            self.size_bytes = held.st_size
            return identity_matches
        return self.current_path_matches_recorded_artifact()

    def close(self) -> None:
        try:
            self.handle.close()
        except OSError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or evaluate the non-activating independent-validation "
            "pipeline for the exact frozen Inventory V3 candidate."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare",
        help="write canonical readiness/preregistration artifacts without live input",
    )
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--expected-head", required=True)
    evaluate = subparsers.add_parser(
        "evaluate",
        help="evaluate one separately reviewed future validation package",
    )
    evaluate.add_argument("--dataset", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    evaluate.add_argument("--expected-head", required=True)
    return parser


def _git(*arguments: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ("git", *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and not allow_failure:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise InventoryPositiveV3IndependentValidationError(
            f"Git command failed: {detail}"
        )
    return completed


def _verify_clean_head(expected_head: str) -> tuple[str, Path]:
    _require_git_sha(expected_head, "--expected-head")
    actual = _git("rev-parse", "HEAD").stdout.strip()
    if actual != expected_head:
        raise InventoryPositiveV3IndependentValidationError(
            f"Git HEAD mismatch: expected {expected_head}, got {actual}"
        )
    dirty = _git("status", "--porcelain=v1").stdout.strip()
    if dirty:
        raise InventoryPositiveV3IndependentValidationError(
            "worktree changes prevent exact-head independent-validation evidence"
        )
    ancestor = _git(
        "merge-base",
        "--is-ancestor",
        INVENTORY_POSITIVE_V3_FROZEN_HEAD_SHA,
        actual,
        allow_failure=True,
    )
    if ancestor.returncode != 0:
        raise InventoryPositiveV3IndependentValidationError(
            "current evaluator head is not descended from the frozen V3 candidate"
        )
    for path, expected_blob in frozen_v3_model_binding().source_git_blobs:
        actual_blob = _git("rev-parse", f"HEAD:{path}").stdout.strip()
        if actual_blob != expected_blob:
            raise InventoryPositiveV3IndependentValidationError(
                f"frozen V3 transitive source changed: {path}"
            )
    root_text = _git("rev-parse", "--show-toplevel").stdout.strip()
    root = Path(root_text).resolve(strict=True)
    return actual, root


def _canonical_json(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _write_text_and_sidecar(
    output: Path,
    filename: str,
    text: str,
    owned_paths: list[_OwnedArtifact],
) -> tuple[Path, str]:
    path = output / filename
    payload = text.encode("utf-8")
    handle = path.open("xb")
    owned = _OwnedArtifact.from_open_file(path, handle, payload)
    owned_paths.append(owned)
    handle.write(payload)
    handle.flush()
    owned.mark_complete()
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar_payload = f"{digest}  {path.name}\n".encode("ascii")
    sidecar_handle = sidecar.open("xb")
    owned_sidecar = _OwnedArtifact.from_open_file(
        sidecar,
        sidecar_handle,
        sidecar_payload,
    )
    owned_paths.append(owned_sidecar)
    sidecar_handle.write(sidecar_payload)
    sidecar_handle.flush()
    owned_sidecar.mark_complete()
    if path.read_bytes() != payload or sidecar.read_bytes() != sidecar_payload:
        raise InventoryPositiveV3IndependentValidationError(
            f"written artifact failed readback verification: {path.name}"
        )
    return path, digest


def _remove_owned_output(output: Path, owned_paths: list[_OwnedArtifact]) -> None:
    """Roll back only artifacts this invocation created exclusively."""

    for owned in reversed(owned_paths):
        should_unlink = owned.still_owns_path()
        if not should_unlink:
            owned.close()
            continue
        try:
            owned.path.unlink(missing_ok=True)
        except OSError:
            # Windows keeps the path locked while the identity handle is open.
            # Close only after proving ownership, then recheck the full recorded
            # fingerprint before the platform fallback unlink.
            owned.close()
            still_owned = (
                owned.current_path_matches_recorded_artifact()
                if owned.complete
                else owned.current_path_matches_recorded_identity()
            )
            if not still_owned:
                continue
            try:
                owned.path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            owned.close()
    try:
        output.rmdir()
    except OSError:
        pass


def _release_owned_output(owned_paths: list[_OwnedArtifact]) -> None:
    """Release the identity handles after successful materialization."""

    for owned in reversed(owned_paths):
        owned.close()


def _create_output_directory(output: Path) -> None:
    try:
        output.mkdir(exist_ok=False)
    except FileExistsError as error:
        raise InventoryPositiveV3IndependentValidationError(
            f"output already exists: {output}"
        ) from error


def _prepare_templates() -> Mapping[str, Mapping[str, object]]:
    return {
        "approval-registry-entry.template.json": {
            "note": (
                "Template only. A lead-reviewed entry must be committed to the "
                "source-owned approval registry; a dataset-local copy has no authority."
            ),
            "required_bindings": [
                "approval_id",
                "approved_at_utc",
                "approver",
                "campaign_id",
                "campaign_manifest_sha256",
                "dataset_id",
                "operator",
                "package_sha256",
                "reviewer",
                "reviewer_truth_sha256",
                "source_session_report_sha256",
            ],
            "schema": (
                "inventory-positive-v3-independent-validation-approval-entry-template-v1"
            ),
            "template_only": True,
        },
        "campaign-manifest.template.json": {
            "note": (
                "Template only. Populate through the future reviewed passive "
                "campaign workflow and bind its durable source-session report; "
                "this file is not executable evidence."
            ),
            "required_positive_sequence": [
                "empty",
                "early-partial",
                "mid-partial",
                "near-full",
                "full",
            ],
            "required_negative_sequence": ["wrong-tab", "row-obstruction"],
            "required_provenance": [
                "finalized_at_utc",
                "operator",
                "source_session_report",
            ],
            "schema": "inventory-positive-v3-independent-validation-dataset-template-v1",
            "template_only": True,
        },
        "source-capture-report.template.json": {
            "note": (
                "Template only. Every case must bind a canonical durable capture "
                "report that owns the exact inventory-region artifact."
            ),
            "required_bindings": [
                "capture_environment",
                "capture_id",
                "captured_at_utc",
                "inventory_region.path",
                "inventory_region.region",
                "inventory_region.sha256",
                "inventory_region.size_bytes",
                "session_id",
            ],
            "schema": "inventory-positive-v3-independent-source-capture-template-v1",
            "template_only": True,
        },
        "source-session-report.template.json": {
            "note": (
                "Template only. The finalized source session must list every owned "
                "capture report in acquisition order."
            ),
            "required_bindings": [
                "campaign_id",
                "capture_environment",
                "captures",
                "completed_at_utc",
                "operator",
                "session_id",
                "started_at_utc",
            ],
            "schema": "inventory-positive-v3-independent-source-session-template-v1",
            "template_only": True,
        },
        "reviewer-truth.template.json": {
            "note": (
                "Template only. Reviewer truth is created separately after capture "
                "by an identity distinct from the operator, and never populated "
                "from operator stage labels."
            ),
            "schema": "inventory-positive-v3-independent-validation-review-template-v1",
            "template_only": True,
            "truth_source": "independent-human-review",
        },
        "validation-package.template.json": {
            "note": (
                "Template only. The final package binds exact canonical manifest "
                "and reviewer-truth hashes. It remains approval-required until a "
                "separate source-owned registry entry is reviewed and committed."
            ),
            "schema": "inventory-positive-v3-independent-validation-package-template-v1",
            "template_only": True,
        },
    }


def _prepare(output: Path, expected_head: str) -> int:
    head, root = _verify_clean_head(expected_head)
    report = build_inventory_positive_v3_validation_readiness_report(
        root,
        evaluator_git_head_sha=head,
    )
    _verify_clean_head(expected_head)
    output.parent.mkdir(parents=True, exist_ok=True)
    _create_output_directory(output)
    owned_paths: list[_OwnedArtifact] = []
    try:
        prereg_path, prereg_digest = _write_text_and_sidecar(
            output,
            "preregistration.json",
            _canonical_json(independent_validation_preregistration()),
            owned_paths,
        )
        for filename, template in _prepare_templates().items():
            _write_text_and_sidecar(
                output,
                filename,
                _canonical_json(template),
                owned_paths,
            )
        report_path, report_digest = _write_text_and_sidecar(
            output,
            "inventory-positive-v3-validation-readiness-report.json",
            _canonical_json(report),
            owned_paths,
        )
        _verify_clean_head(expected_head)
    except Exception:
        _remove_owned_output(output, owned_paths)
        raise
    else:
        _release_owned_output(owned_paths)
    print("Inventory V3 independent-validation readiness: PASS")
    print("Live validation authorized: false")
    print("Activation allowed: false")
    print(f"Preregistration: {prereg_path}")
    print(f"Preregistration SHA-256: {prereg_digest}")
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {report_digest}")
    return 0


def _evaluate(dataset: Path, output: Path, expected_head: str) -> int:
    head, root = _verify_clean_head(expected_head)
    report = evaluate_frozen_v3_independent_validation(
        dataset,
        repository_root=root,
        evaluator_git_head_sha=head,
    )
    _verify_clean_head(expected_head)
    output.parent.mkdir(parents=True, exist_ok=True)
    _create_output_directory(output)
    owned_paths: list[_OwnedArtifact] = []
    try:
        report_path, report_digest = _write_text_and_sidecar(
            output,
            "inventory-positive-v3-independent-validation-report.json",
            report.to_json(),
            owned_paths,
        )
        _verify_clean_head(expected_head)
    except Exception:
        _remove_owned_output(output, owned_paths)
        raise
    else:
        _release_owned_output(owned_paths)
    status = "PASS" if report.validation_passed else "NOT APPROVED"
    print(f"Inventory V3 independent validation: {status}")
    print(
        "Detector conformance passed: "
        f"{str(report.detector_conformance_passed).lower()}"
    )
    print(f"Source-owned approval present: {str(report.approval is not None).lower()}")
    print(f"Validation status: {report.validation_status}")
    print("Activation allowed: false")
    print(f"Report: {report_path}")
    print(f"Report SHA-256: {report_digest}")
    return 0 if report.validation_passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            return _prepare(args.output, args.expected_head)
        if args.command == "evaluate":
            return _evaluate(args.dataset, args.output, args.expected_head)
        raise InventoryPositiveV3IndependentValidationError(
            f"unsupported command: {args.command}"
        )
    except (
        InventoryPositiveV3IndependentValidationError,
        OSError,
        ValueError,
    ) as exc:
        print(f"inventory V3 independent-validation gate failed: {exc}", file=sys.stderr)
        return 2


def _require_git_sha(value: str, label: str) -> None:
    if len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise InventoryPositiveV3IndependentValidationError(
            f"{label} must be an exact lowercase 40-character Git SHA"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
