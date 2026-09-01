"""Command-line workflow for the passive Varrock East resource release campaign."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Final

from ..capture import CaptureError
from .resource_release_campaign import (
    CAMPAIGN_PLAN,
    LIVE_RESOURCE_CAMPAIGN_AUTHORIZED,
    CampaignError,
    _capture_next_windows_case,
    create_campaign,
    evaluate_release,
    export_review_package,
    load_campaign_status,
    load_review_decision,
    prepare_case_review,
    read_repository_provenance,
    record_case_review,
    review_template_for_case,
    seal_campaign,
    verify_review_package,
    write_release_summary,
)

__all__ = ["build_parser", "main"]

_DEFAULT_CAMPAIGN_ROOT: Final[Path] = Path("diagnostics/resource-release-campaigns")


def build_parser() -> argparse.ArgumentParser:
    """Build the fixed-policy campaign command parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="create one uniquely owned session")
    start.add_argument("--operator-id", required=True)

    status = subparsers.add_parser("status", help="verify and show resumable state")
    status.add_argument("--session", type=Path, required=True)

    capture = subparsers.add_parser(
        "capture-next",
        help="capture/evaluate the one fixed next case (source gate currently disabled)",
    )
    capture.add_argument("--session", type=Path, required=True)
    capture.add_argument(
        "--confirm-staged-case",
        required=True,
        help="exact next case ID shown by status; acknowledgment, not a selector",
    )

    seal = subparsers.add_parser("seal", help="seal a complete 15-case capture set")
    seal.add_argument("--session", type=Path, required=True)

    prepare = subparsers.add_parser(
        "prepare-review",
        help="create deterministic sanitized artifacts before reviewer truth",
    )
    prepare.add_argument("--session", type=Path, required=True)
    prepare.add_argument(
        "--case-id", choices=tuple(case.case_id for case in CAMPAIGN_PLAN), required=True
    )

    template = subparsers.add_parser(
        "review-template",
        help="write a blank reviewer decision; operator labels are not copied",
    )
    template.add_argument("--session", type=Path, required=True)
    template.add_argument(
        "--case-id", choices=tuple(case.case_id for case in CAMPAIGN_PLAN), required=True
    )
    template.add_argument("--output", type=Path, required=True)

    review = subparsers.add_parser(
        "review", help="bind independent reviewer truth and privacy-safe artifacts"
    )
    review.add_argument("--session", type=Path, required=True)
    review.add_argument("--decision", type=Path, required=True)

    release = subparsers.add_parser(
        "release", help="write exact CLOSED/STILL_OPEN PR #39 blocker ledger"
    )
    release.add_argument("--session", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)

    export = subparsers.add_parser(
        "export-review",
        help="export the complete privacy-reviewed package with manifest last",
    )
    export.add_argument("--session", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser(
        "verify-export", help="rehash/replay a privacy-safe review package"
    )
    verify.add_argument("--package", type=Path, required=True)
    return parser


def _status_json(session: Path) -> dict[str, object]:
    status = load_campaign_status(session)
    return {
        "session_id": status.session_id,
        "captured_case_ids": list(status.captured_case_ids),
        "captured_count": len(status.captured_case_ids),
        "total_count": len(CAMPAIGN_PLAN),
        "next_case_no_frame_failures": status.next_case_no_frame_failures,
        "sealed": status.sealed,
        "reviewed_case_ids": list(status.reviewed_case_ids),
        "prepared_case_ids": list(status.prepared_case_ids),
        "next_case": (
            None
            if status.next_case is None
            else {
                "ordinal": status.next_case.ordinal,
                "case_id": status.next_case.case_id,
                "operator_prompt_unverified": status.next_case.operator_prompt,
                "operator_prompt_is_reviewer_truth": False,
            }
        ),
        "live_resource_campaign_authorized": LIVE_RESOURCE_CAMPAIGN_AUTHORIZED,
        "input_authority": False,
    }


def _capture(
    session: Path, repository_root: Path, *, confirmed_case_id: str
) -> dict[str, object]:
    if not LIVE_RESOURCE_CAMPAIGN_AUTHORIZED:
        raise CampaignError(
            "LIVE RESOURCE CAMPAIGN NOT YET AUTHORIZED; no backend was opened"
        )
    repository = read_repository_provenance(repository_root)
    status = load_campaign_status(session, repository=repository)
    if status.next_case is None:
        raise CampaignError("campaign has no remaining capture case")
    if confirmed_case_id != status.next_case.case_id:
        raise CampaignError(
            "staged-case acknowledgment does not match the one fixed next case"
        )
    return _capture_next_windows_case(
        session,
        repository_root=repository_root,
        repository=repository,
        expected_case_id=confirmed_case_id,
    )


def _private_campaign_root(repository_root: Path) -> Path:
    repository = repository_root.resolve()
    root = (repository / _DEFAULT_CAMPAIGN_ROOT).resolve()
    try:
        root.relative_to(repository)
    except ValueError as exc:  # pragma: no cover - source-owned constant
        raise CampaignError("private campaign root escaped the repository") from exc
    probe = root / ".privacy-ignore-probe"
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", str(probe)],
        cwd=repository,
        check=False,
    )
    if ignored.returncode != 0:
        raise CampaignError("source-owned private campaign root is not Git-ignored")
    return root


def _require_private_session(session: Path, repository_root: Path) -> Path:
    root = _private_campaign_root(repository_root)
    resolved = Path(session).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CampaignError("session is outside the source-owned ignored private root") from exc
    return resolved


def _write_new_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None, *, repository_root: Path | None = None) -> int:
    """Run one campaign operation. Live capture stays source-gated off."""

    arguments = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    repository_path = Path.cwd() if repository_root is None else Path(repository_root)
    try:
        if arguments.command == "start":
            repository = read_repository_provenance(repository_path)
            session = create_campaign(
                _private_campaign_root(repository_path),
                operator_id=arguments.operator_id,
                repository=repository,
            )
            _print_json({"session": str(session), **_status_json(session)})
        elif arguments.command == "status":
            session = _require_private_session(arguments.session, repository_path)
            _print_json(_status_json(session))
        elif arguments.command == "capture-next":
            session = _require_private_session(arguments.session, repository_path)
            record = _capture(
                session,
                repository_path,
                confirmed_case_id=arguments.confirm_staged_case,
            )
            _print_json(
                {
                    "case_id": record["case_id"],
                    "capture_count": record["capture_count"],
                    "automatic_retry_count": record["automatic_retry_count"],
                    "production": record["production"],
                    "next": _status_json(session)["next_case"],
                }
            )
        elif arguments.command == "seal":
            session = _require_private_session(arguments.session, repository_path)
            repository = read_repository_provenance(repository_path)
            _print_json(
                seal_campaign(session, repository=repository)
            )
        elif arguments.command == "prepare-review":
            session = _require_private_session(arguments.session, repository_path)
            repository = read_repository_provenance(repository_path)
            _print_json(
                prepare_case_review(
                    session,
                    arguments.case_id,
                    repository=repository,
                )
            )
        elif arguments.command == "review-template":
            session = _require_private_session(arguments.session, repository_path)
            repository = read_repository_provenance(repository_path)
            _write_new_json(
                arguments.output,
                review_template_for_case(
                    session,
                    arguments.case_id,
                    repository=repository,
                ),
            )
            _print_json(
                {
                    "template": str(arguments.output),
                    "case_id": arguments.case_id,
                    "operator_labels_copied": False,
                    "valid_decision_before_independent_completion": False,
                }
            )
        elif arguments.command == "review":
            session = _require_private_session(arguments.session, repository_path)
            repository = read_repository_provenance(repository_path)
            decision = load_review_decision(arguments.decision)
            truth = record_case_review(
                session,
                decision,
                repository=repository,
            )
            _print_json(
                {
                    "case_id": truth["case_id"],
                    "review": truth["review"],
                    "activation_allowed": False,
                }
            )
        elif arguments.command == "release":
            session = _require_private_session(arguments.session, repository_path)
            repository = read_repository_provenance(repository_path)
            report = evaluate_release(session, repository=repository)
            digest = write_release_summary(arguments.output, report)
            _print_json(
                {
                    "report": str(arguments.output),
                    "sha256": digest,
                    "closed_blockers": report["closed_blockers"],
                    "still_open_blockers": report["still_open_blockers"],
                    "release_eligible": report["release_eligible"],
                    "activation_allowed": False,
                }
            )
        elif arguments.command == "export-review":
            session = _require_private_session(arguments.session, repository_path)
            repository = read_repository_provenance(repository_path)
            _print_json(
                export_review_package(
                    session,
                    arguments.output,
                    repository=repository,
                )
            )
        elif arguments.command == "verify-export":
            _print_json(verify_review_package(arguments.package))
        else:  # pragma: no cover - argparse enforces the command set
            raise AssertionError(f"unhandled command {arguments.command!r}")
    except (CampaignError, CaptureError, OSError, ValueError) as exc:
        print(f"RESOURCE CAMPAIGN FAILED: {exc}", file=sys.stderr)
        return 1
    return 0
