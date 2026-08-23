#!/usr/bin/env python3
"""Inspect and edit resource ground-truth drafts after visual review."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mining_automation.perception.resource import ResourceVisualState  # noqa: E402
from mining_automation.perception.resource_fixtures import (  # noqa: E402
    ResourceFixtureAnnotation,
    add_resource_annotation,
    load_resource_fixture_draft,
    mark_resource_fixture_reviewed,
    save_resource_fixture_draft,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show", help="print one draft")
    show.add_argument("draft", type=Path)

    add = subparsers.add_parser("add", help="add or deliberately replace one annotation")
    add.add_argument("draft", type=Path)
    add.add_argument("--resource-id", required=True)
    add.add_argument("--ore-label", default="iron")
    add.add_argument(
        "--state",
        required=True,
        choices=[state.value for state in ResourceVisualState],
    )
    add.add_argument("--region", required=True, nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    add.add_argument("--confidence-min", type=float, default=0.0)
    add.add_argument("--confidence-max", type=float, default=1.0)
    add.add_argument("--notes", default="")
    add.add_argument("--replace", action="store_true")

    review = subparsers.add_parser("review", help="mark a fully annotated draft reviewed")
    review.add_argument("draft", type=Path)
    return parser


def _show(path: Path) -> int:
    load_resource_fixture_draft(path)
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "show":
            return _show(args.draft)
        draft = load_resource_fixture_draft(args.draft)
        if args.command == "add":
            annotation = ResourceFixtureAnnotation(
                resource_id=args.resource_id,
                ore_label=args.ore_label,
                state=ResourceVisualState(args.state),
                region=(args.region[0], args.region[1], args.region[2], args.region[3]),
                confidence_min=args.confidence_min,
                confidence_max=args.confidence_max,
                notes=args.notes,
            )
            draft = add_resource_annotation(
                draft,
                annotation,
                replace_existing=args.replace,
            )
        elif args.command == "review":
            draft = mark_resource_fixture_reviewed(draft)
        else:  # pragma: no cover - argparse enforces the command set
            raise AssertionError(args.command)
        save_resource_fixture_draft(draft, args.draft)
    except (OSError, TypeError, ValueError) as exc:
        print(f"Resource annotation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Updated {args.draft} ({draft.review_status.value}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
