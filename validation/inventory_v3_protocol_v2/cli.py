"""Fixed command surface for Inventory V3 independent-validation Protocol V2."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import cast

from .protocol import (
    InventoryV3ProtocolV2Error,
    ProtocolV2Paths,
    build_live_authorization_proposal,
    evaluate_locked_protocol_v2,
    finalize_acquisition,
    preflight_source_metadata,
    prepare_approval_request,
    prepare_reviewer_intake,
    publish_reviewed_package,
    record_reviewer_submission,
    run_passive_capture_protocol_v2,
    verify_live_authorization,
    verify_protocol_v2_repository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Non-activating Inventory V3 Protocol V2 capture/finalization/review "
            "coordinator. No command grants live authorization or approval."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def command(name: str) -> argparse.ArgumentParser:
        child = subparsers.add_parser(name)
        child.add_argument("--expected-head", required=True)
        return child

    command("preflight")
    proposal = command("authorization-proposal")
    proposal.add_argument("--opaque-receipt-id", required=True)
    capture = command("capture")
    capture.add_argument("--operator", required=True)
    capture.add_argument("--runelite-build", required=True)
    capture.add_argument("--client-mode", required=True)
    capture.add_argument("--theme", required=True)
    capture.add_argument("--renderer", required=True)
    command("finalize")
    command("prepare-review")
    review = command("record-review")
    review.add_argument("--reviewer", required=True)
    command("publish-review")
    command("evaluate")
    approval = command("approval-request")
    approval.add_argument("--proposed-approver", required=True)
    approval.add_argument("--proposed-approved-at-utc", required=True)
    return parser


def _prompt_choice(label: str, values: tuple[str, ...]) -> str:
    while True:
        value = input(f"{label} ({'/'.join(values)}): ").strip()
        if value in values:
            return value
        print("Unsupported value; enter one of the displayed exact values.")


def _prompt_bool(label: str) -> bool:
    return _prompt_choice(label, ("false", "true")) == "true"


def _interactive_truth_provider(
    reviewer: str,
    template: Mapping[str, object],
    *,
    evidence_root: Path | None = None,
) -> Mapping[str, object]:
    raw_cases = template.get("cases")
    if not isinstance(raw_cases, list):
        raise InventoryV3ProtocolV2Error("review template cases are unavailable")
    cases: list[dict[str, object]] = []
    for index, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, Mapping):
            raise InventoryV3ProtocolV2Error("review template case is invalid")
        review_case_id = raw.get("review_case_id")
        full = raw.get("full_frame")
        region = raw.get("frame_region")
        if (
            not isinstance(review_case_id, str)
            or not isinstance(full, Mapping)
            or not isinstance(region, Mapping)
        ):
            raise InventoryV3ProtocolV2Error("review template case binding is invalid")
        full_path = _review_evidence_path(full, evidence_root=evidence_root)
        region_path = _review_evidence_path(region, evidence_root=evidence_root)
        print(f"\nBlind review case {index}: {review_case_id}")
        print(f"  Full-frame evidence: {full_path}")
        print(f"  Inventory-region evidence: {region_path}")
        decision = _prompt_choice("Decision", ("approved", "rejected"))
        visibility = _prompt_choice(
            "Visibility",
            ("inventory-visible", "wrong-tab-visible", "inventory-obstructed"),
        )
        while True:
            occupied_text = input("Occupied slots (0..28, or blank for UNKNOWN): ").strip()
            if not occupied_text:
                occupied: int | None = None
                break
            try:
                occupied = int(occupied_text)
            except ValueError:
                print("Enter an integer from 0 through 28, or blank.")
                continue
            if 0 <= occupied <= 28:
                break
            print("Enter an integer from 0 through 28, or blank.")
        truth = {
            "decision": decision,
            "drag_visible": _prompt_bool("Drag visible"),
            "hover_visible": _prompt_bool("Hover visible"),
            "occupied_slots": occupied,
            "ordinary_iron_only": _prompt_bool("Ordinary iron only"),
            "quantity_text_visible": _prompt_bool("Quantity text visible"),
            "review_note": input("Review note (blank for none): ").strip() or None,
            "selected_item_visible": _prompt_bool("Selected item visible"),
            "visibility": visibility,
        }
        cases.append({"review_case_id": review_case_id, "truth": truth})
    from datetime import UTC, datetime

    return {
        "cases": cases,
        "reviewed_at_utc": datetime.now(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "reviewer": reviewer,
    }


def _review_evidence_path(
    reference: Mapping[str, object],
    *,
    evidence_root: Path | None,
) -> Path:
    relative = reference.get("path")
    if not isinstance(relative, str) or not relative:
        raise InventoryV3ProtocolV2Error("review evidence path is unavailable")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts or "\\" in relative:
        raise InventoryV3ProtocolV2Error("review evidence path escapes its package")
    if evidence_root is None:
        return Path(*pure.parts)
    root = evidence_root.resolve(strict=True)
    candidate = root.joinpath(*pure.parts).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise InventoryV3ProtocolV2Error("review evidence path escapes its package") from exc
    if not candidate.is_file():
        raise InventoryV3ProtocolV2Error("review evidence path is not a regular file")
    return candidate


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parents[2]
    expected_head = cast(str, arguments.expected_head)
    try:
        if arguments.command == "preflight":
            protocol = verify_protocol_v2_repository(repository_root, expected_head=expected_head)
            authorization = verify_live_authorization(protocol)
            source = preflight_source_metadata(protocol, authorization)
            result: object = {
                "activation_allowed": False,
                "authorization_id": authorization.authorization_id,
                "capture_count": len(source.capture_reports),
                "protocol_lock_git_commit_sha": protocol.lock_commit_sha,
                "protocol_lock_sha256": protocol.lock_sha256,
                "source_campaign_root": str(source.paths.source_campaign_root),
                "status": "preflight-passed-no-sensitive-pixels-opened",
            }
        elif arguments.command == "authorization-proposal":
            result = build_live_authorization_proposal(
                repository_root,
                expected_lock_head=expected_head,
                opaque_receipt_id=cast(str, arguments.opaque_receipt_id),
            )
        elif arguments.command == "capture":
            result = str(
                run_passive_capture_protocol_v2(
                    repository_root,
                    expected_head=expected_head,
                    operator=cast(str, arguments.operator),
                    runelite_build=cast(str, arguments.runelite_build),
                    client_mode=cast(str, arguments.client_mode),
                    theme=cast(str, arguments.theme),
                    renderer=cast(str, arguments.renderer),
                )
            )
        elif arguments.command == "finalize":
            result = finalize_acquisition(repository_root, expected_head=expected_head)
            result = asdict(result)
        elif arguments.command == "prepare-review":
            result = prepare_reviewer_intake(repository_root, expected_head=expected_head)
            result = asdict(result)
        elif arguments.command == "record-review":
            reviewer = cast(str, arguments.reviewer)
            review_protocol = verify_protocol_v2_repository(
                repository_root,
                expected_head=expected_head,
            )
            review_authorization = verify_live_authorization(review_protocol)
            review_paths = ProtocolV2Paths.for_authorization(
                review_protocol.repository_root,
                review_authorization.authorization_id,
                review_protocol.lock_sha256,
            )
            evidence_root = review_paths.review_intake_root / "package"
            result = str(
                record_reviewer_submission(
                    repository_root,
                    expected_head=expected_head,
                    reviewer=reviewer,
                    truth_provider=lambda template: _interactive_truth_provider(
                        reviewer,
                        template,
                        evidence_root=evidence_root,
                    ),
                )
            )
        elif arguments.command == "publish-review":
            result = publish_reviewed_package(repository_root, expected_head=expected_head)
            result = asdict(result)
        elif arguments.command == "evaluate":
            result = evaluate_locked_protocol_v2(repository_root, expected_head=expected_head)
            result = asdict(result)
        elif arguments.command == "approval-request":
            result = prepare_approval_request(
                repository_root,
                expected_head=expected_head,
                proposed_approver=cast(str, arguments.proposed_approver),
                proposed_approved_at_utc=cast(str, arguments.proposed_approved_at_utc),
            )
        else:  # pragma: no cover - argparse owns the command vocabulary
            raise AssertionError("unreachable command")
    except KeyboardInterrupt:
        print(
            "Protocol V2 operation interrupted; owned evidence remains retained.", file=sys.stderr
        )
        return 130
    except (InventoryV3ProtocolV2Error, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Inventory V3 Protocol V2 rejected: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, default=str, ensure_ascii=True, indent=2, sort_keys=True))
    print("activation_allowed=false")
    print("LIVE INVENTORY CAMPAIGN NOT YET AUTHORIZED unless exact source registries say otherwise")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
