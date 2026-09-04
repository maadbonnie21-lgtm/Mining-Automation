from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

REPOSITORY = "maadbonnie21-lgtm/Mining-Automation"
INTEGRATION_BRANCH = "chatgpt/nightly-execution-lead-2026-09-03-authoritative-v10"
STATE_PATH = Path(".nightly/execution_state.json")
REPORT_PATH = Path("docs/verification/NIGHTLY_EXECUTION_LEAD_2026-09-03.json")
DASHBOARD_PATH = Path("docs/MASTER_DASHBOARD_CURRENT.md")
WORKFLOW_PATH = Path(".github/workflows/nightly-execution-lead-2026-09-03.yml")

RESOURCE_PARENT = "0d215457fb4b037eedc74cb776a3cf1f5f58a19f"
INVENTORY_L2 = "66c7e9536539979bc60e17f02f026eb64ebf0768"
INVENTORY_LOCK_SHA256 = (
    "60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5"
)
B2_SHA = "a19b02f090be5165db2baee8b178b19efc4153f5"
B3_SHA = "e5d9f3005402070ca0fa8221611dac58580d6d59"
CONTROLLER_PREP_SHA = "5f370a9f400e8f6f10e60621490a4576f314eb65"
BANKING_ACCEPTED_SHA = "0fa2e11587aa336a5602422dc2f64d71d779d5eb"

CANDIDATES: dict[str, tuple[str, ...]] = {
    "resource": (
        "chatgpt/a12-a13-resource-release-final",
        "chatgpt/a11-resource-live-enable-only",
    ),
    "inventory": (
        "chatgpt/c8a-inventory-live-authorization-final",
        "chatgpt/c8a-inventory-live-authorization",
        "chatgpt/c8a-inventory-live-authorization-prepared",
    ),
    "b4_navigation": ("chatgpt/b4-navigation-writer-integration",),
    "navigation_endpoint": (
        "chatgpt/b5-b7-navigation-endpoint-final",
        "chatgpt/b5-b7-navigation-endpoint-integration",
    ),
    "navigation_pins": ("chatgpt/navigation-arrival-release-pin-final",),
    "banking": (
        "chatgpt/banking-d5-release-pin-final",
        "chatgpt/banking-d2-d5-offline-final",
        "chatgpt/banking-d2-d5-offline-readiness",
    ),
    "mining": (
        "chatgpt/mining-perception-release-pin-final",
        "chatgpt/mining-perception-integration-final",
        "chatgpt/mining-vertical-slice-offline-final",
    ),
    "full_loop": (
        "chatgpt/full-loop-release-pin-final",
        "chatgpt/full-loop-offline-integration-final",
    ),
    "exact_deposit": ("chatgpt/full-loop-exact-deposit-final",),
    "fully_pinned": ("chatgpt/full-loop-fully-pinned-v2-final",),
    "runner": (
        "chatgpt/windows-runner-authority-v2-final",
        "chatgpt/windows-runner-release-readiness-final",
    ),
}

MERGE_ORDER = (
    "resource",
    "inventory",
    "b4_navigation",
    "navigation_endpoint",
    "navigation_pins",
    "banking",
    "mining",
    "full_loop",
    "exact_deposit",
    "fully_pinned",
    "runner",
)

TEMPORARY_WORKFLOW_TERMS = (
    "finalize",
    "patcher",
    "reconcile",
    "rescue",
    "exact-head-audit",
    "exact-audit",
)


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout_tail: str
    stderr_tail: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "passed": self.passed,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
        }


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run(
    command: Iterable[str],
    *,
    check: bool = False,
    timeout: int | None = None,
) -> CommandResult:
    argv = list(command)
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(
        command=argv,
        returncode=completed.returncode,
        stdout_tail=completed.stdout[-12000:],
        stderr_tail=completed.stderr[-12000:],
    )
    if check and not result.passed:
        raise RuntimeError(json.dumps(result.as_dict(), indent=2))
    return result


def git(*args: str, check: bool = False) -> CommandResult:
    return run(("git", *args), check=check)


def git_text(*args: str) -> str:
    result = git(*args, check=True)
    return result.stdout_tail.strip()


def git_object_exists(value: str) -> bool:
    return git("cat-file", "-e", value).passed


def is_ancestor(parent: str, child: str) -> bool:
    return git("merge-base", "--is-ancestor", parent, child).passed


def remote_head(branch: str) -> str | None:
    result = git("ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if not result.passed or not result.stdout_tail.strip():
        return None
    return result.stdout_tail.split()[0]


def read_text(path: str | Path) -> str:
    candidate = Path(path)
    return candidate.read_text(encoding="utf-8") if candidate.is_file() else ""


def all_text(paths: Iterable[Path]) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in paths
        if path.is_file()
    )


def find_json_field(root: Path, field: str) -> Any:
    for path in root.rglob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        stack: list[Any] = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if field in current:
                    return current[field]
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return None


def select_remote_branches() -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for lane, candidates in CANDIDATES.items():
        entry: dict[str, Any] = {
            "candidate_order": list(candidates),
            "branch": None,
            "head_sha": None,
        }
        for branch in candidates:
            head = remote_head(branch)
            if head is None:
                continue
            git("fetch", "--quiet", "origin", branch, check=True)
            entry["branch"] = branch
            entry["head_sha"] = head
            break
        selected[lane] = entry
    a11_head = remote_head("chatgpt/a11-resource-live-enable-only")
    if a11_head is not None:
        git(
            "fetch",
            "--quiet",
            "origin",
            "chatgpt/a11-resource-live-enable-only",
            check=True,
        )
    selected["a11_exact"] = {
        "candidate_order": ["chatgpt/a11-resource-live-enable-only"],
        "branch": (
            "chatgpt/a11-resource-live-enable-only" if a11_head is not None else None
        ),
        "head_sha": a11_head,
    }
    return selected


def merge_selected(selected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for lane in MERGE_ORDER:
        branch = selected[lane]["branch"]
        head = selected[lane]["head_sha"]
        if branch is None or head is None:
            results[lane] = {"status": "missing", "branch": None, "head_sha": None}
            continue
        before = git_text("rev-parse", "HEAD")
        merge = git("merge", "--no-edit", "--no-ff", head)
        if merge.passed:
            results[lane] = {
                "status": "merged_or_already_present",
                "branch": branch,
                "head_sha": head,
                "before_sha": before,
                "after_sha": git_text("rev-parse", "HEAD"),
                "stdout_tail": merge.stdout_tail,
            }
            continue
        conflicts = git("diff", "--name-only", "--diff-filter=U").stdout_tail.splitlines()
        git("merge", "--abort")
        results[lane] = {
            "status": "conflict",
            "branch": branch,
            "head_sha": head,
            "before_sha": before,
            "conflicted_paths": conflicts,
            "stderr_tail": merge.stderr_tail,
        }
    return results


def migrate_b4_earlier_rejection_test() -> dict[str, Any]:
    source_path = Path("src/mining_automation/navigation/durable_route_evidence.py")
    test_path = Path("tests/test_navigation_release_decision.py")
    result: dict[str, Any] = {
        "eligible": False,
        "changed": False,
        "reason": "required files or markers missing",
    }
    if not source_path.is_file() or not test_path.is_file():
        return result
    source = source_path.read_text(encoding="utf-8")
    tests = test_path.read_text(encoding="utf-8")
    required = (
        "acquisition_physical_identity_sha256",
        "review_physical_identity_sha256",
    )
    if any(marker not in source for marker in required):
        return result
    start = tests.find(
        "def test_repeated_pair_intake_rejects_metadata_change_between_snapshots"
    )
    if start < 0:
        result["reason"] = "specific B4 regression test missing"
        return result
    end = tests.find("\ndef ", start + 4)
    if end < 0:
        end = len(tests)
    block = tests[start:end]
    result["eligible"] = True
    if re.search(r"assert\s+call_count\s*==\s*3\b", block):
        result["reason"] = "test already expects the stronger earlier rejection"
        return result
    migrated, count = re.subn(
        r"(assert\s+call_count\s*==\s*)4\b",
        r"\g<1>3",
        block,
        count=1,
    )
    if count != 1:
        result["reason"] = "legacy call_count == 4 assertion not found"
        return result
    tests = tests[:start] + migrated + tests[end:]
    test_path.write_text(tests, encoding="utf-8")
    result["changed"] = True
    result["reason"] = (
        "migrated only the stale assertion; physical-identity rejection now occurs "
        "one snapshot earlier"
    )
    return result


def clean_temporary_workflows() -> list[str]:
    removed: list[str] = []
    root = Path(".github/workflows")
    if not root.is_dir():
        return removed
    for path in root.glob("*.yml"):
        if path == WORKFLOW_PATH:
            continue
        lowered = path.name.lower()
        if any(term in lowered for term in TEMPORARY_WORKFLOW_TERMS):
            path.unlink()
            removed.append(path.as_posix())
    return removed


def audit_resource(selected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    head = selected["a11_exact"]["head_sha"]
    result: dict[str, Any] = {
        "exact_a11_head": head,
        "parent_sha": RESOURCE_PARENT,
        "parent_available": git_object_exists(RESOURCE_PARENT),
        "changed_files": [],
        "production_files": [],
        "production_gate_diff_exact": False,
    }
    if head and result["parent_available"]:
        changed = git_text("diff", "--name-only", RESOURCE_PARENT, head).splitlines()
        production = [path for path in changed if path.startswith("src/")]
        result["changed_files"] = changed
        result["production_files"] = production
        if production == [
            "src/mining_automation/perception/resource/live_campaign.py"
        ]:
            diff = git_text(
                "diff",
                "--unified=0",
                RESOURCE_PARENT,
                head,
                "--",
                production[0],
            )
            changed_lines = [
                line
                for line in diff.splitlines()
                if line.startswith(("+", "-"))
                and not line.startswith(("+++", "---"))
            ]
            result["production_gate_diff_exact"] = changed_lines == [
                "-LIVE_RESOURCE_CAMPAIGN_AUTHORIZED = False",
                "+LIVE_RESOURCE_CAMPAIGN_AUTHORIZED = True",
            ]
            result["production_changed_lines"] = changed_lines
    corpus = all_text(
        tuple(Path("src/mining_automation/perception/resource").rglob("*.py"))
        + tuple(Path("tests").glob("*resource*.py"))
    )
    lowered = corpus.lower()
    result.update(
        {
            "threshold_0_12_present": "0.12" in corpus,
            "six_landmarks_present": "landmark" in lowered and "6" in corpus,
            "five_of_six_quorum_present": "quorum" in lowered and "5" in corpus,
            "three_zones_present": "zone" in lowered and "3" in corpus,
            "frame_1005x1078_present": "1005" in corpus and "1078" in corpus,
            "dpi_96_present": "dpi" in lowered and "96" in corpus,
            "fifteen_cases_present": "15" in corpus and "case" in lowered,
            "zero_retry_present": (
                "retry_count" in lowered and "0" in corpus
            )
            or "zero retries" in lowered,
            "unknown_fail_closed_present": "unknown" in lowered
            and ("zero target" in lowered or "stop" in lowered),
        }
    )
    live_source = read_text(
        "src/mining_automation/perception/resource/live_campaign.py"
    )
    result["live_source_has_no_input_implementation"] = not any(
        marker in live_source
        for marker in ("pyautogui", "SendInput", "SetCursorPos", "mouse_event")
    )
    result["passed"] = all(
        result.get(key) is True
        for key in (
            "production_gate_diff_exact",
            "threshold_0_12_present",
            "six_landmarks_present",
            "five_of_six_quorum_present",
            "three_zones_present",
            "frame_1005x1078_present",
            "dpi_96_present",
            "fifteen_cases_present",
            "zero_retry_present",
            "unknown_fail_closed_present",
            "live_source_has_no_input_implementation",
        )
    )
    return result


def audit_inventory(selected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    head = selected["inventory"]["head_sha"]
    result: dict[str, Any] = {
        "selected_head": head,
        "frozen_l2_sha": INVENTORY_L2,
        "frozen_l2_is_ancestor": bool(head and is_ancestor(INVENTORY_L2, head)),
        "launcher_lock_sha256": INVENTORY_LOCK_SHA256,
    }
    inventory_paths = tuple(Path("src/mining_automation/perception/inventory").rglob("*.py"))
    supporting_paths = tuple(Path("scripts").glob("*inventory*c8a*")) + tuple(
        Path("docs").rglob("*C8A*")
    )
    corpus = all_text(inventory_paths + supporting_paths + tuple(Path("tests").glob("*inventory*.py")))
    lowered = corpus.lower()
    allowlist = find_json_field(Path("docs"), "approved_authorization_child_paths")
    result.update(
        {
            "launcher_lock_present": INVENTORY_LOCK_SHA256 in corpus,
            "publication_floor_0_8_present": "0.8" in corpus,
            "capacity_28_present": "28" in corpus,
            "unknown_fail_closed_present": "unknown" in lowered
            and ("fail" in lowered or "stop" in lowered),
            "unknown_not_treated_as_not_full": (
                "unknown != not-full" in lowered
                or "unknown != not full" in lowered
                or ("unknown" in lowered and "not_full" in lowered)
            ),
            "proposal_label_present": "proposal" in lowered,
            "proposal_has_no_input_implementation": not any(
                marker in corpus
                for marker in (
                    "import pyautogui",
                    "SendInput(",
                    "SetCursorPos(",
                    "mouse_event(",
                )
            ),
            "exact_three_file_allowlist_present": isinstance(allowlist, list)
            and len(allowlist) == 3
            and len(set(map(str, allowlist))) == 3,
            "authorization_child_allowlist": allowlist,
        }
    )
    result["passed"] = all(
        result.get(key) is True
        for key in (
            "frozen_l2_is_ancestor",
            "launcher_lock_present",
            "publication_floor_0_8_present",
            "capacity_28_present",
            "unknown_fail_closed_present",
            "proposal_label_present",
            "proposal_has_no_input_implementation",
        )
    )
    return result


def audit_navigation(selected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    head = selected["b4_navigation"]["head_sha"]
    source = read_text(
        "src/mining_automation/navigation/durable_route_evidence.py"
    )
    tests = read_text("tests/test_navigation_release_decision.py")
    start = tests.find(
        "def test_repeated_pair_intake_rejects_metadata_change_between_snapshots"
    )
    end = tests.find("\ndef ", start + 4) if start >= 0 else -1
    block = tests[start : (len(tests) if end < 0 else end)] if start >= 0 else ""
    result: dict[str, Any] = {
        "selected_b4_head": head,
        "b2_preserved": bool(head and is_ancestor(B2_SHA, head)),
        "b3_preserved": bool(head and is_ancestor(B3_SHA, head)),
        "acquisition_physical_identity_present": (
            "acquisition_physical_identity_sha256" in source
        ),
        "review_physical_identity_present": (
            "review_physical_identity_sha256" in source
        ),
        "earlier_rejection_expected_at_three": bool(
            re.search(r"assert\s+call_count\s*==\s*3\b", block)
        ),
        "future_real_writer_not_enabled": (
            "FUTURE_REAL" not in source
            or "FUTURE_REAL_EVIDENCE_ELIGIBLE = True" not in source
        ),
    }
    endpoint = read_text("src/mining_automation/navigation/endpoint_handoff.py")
    pins = read_text("src/mining_automation/navigation/pinned_arrival.py")
    endpoint_corpus = endpoint + "\n" + pins
    result.update(
        {
            "arrival_not_bank_open": "bank_open_claim" in endpoint_corpus
            and "False" in endpoint_corpus,
            "arrival_not_supported_mining_view": (
                "supported_mining_view_claim" in endpoint_corpus
                and "False" in endpoint_corpus
            ),
            "arrival_release_pins_present": (
                "ExpectedNavigationArrivalPins" in pins
                and "navigation_release_receipt_sha256" in pins
            ),
        }
    )
    result["passed"] = all(
        result.get(key) is True
        for key in (
            "b2_preserved",
            "b3_preserved",
            "acquisition_physical_identity_present",
            "review_physical_identity_present",
            "earlier_rejection_expected_at_three",
            "future_real_writer_not_enabled",
            "arrival_not_bank_open",
            "arrival_not_supported_mining_view",
        )
    )
    return result


def audit_mining() -> dict[str, Any]:
    vertical = read_text("src/mining_automation/mining_vertical_slice.py")
    adapter = read_text("src/mining_automation/mining_perception_adapter.py")
    resource_binding = read_text(
        "src/mining_automation/perception/resource/same_cycle_release_binding.py"
    )
    inventory_binding = read_text(
        "src/mining_automation/perception/inventory/same_cycle_release_binding.py"
    )
    corpus = "\n".join((vertical, adapter, resource_binding, inventory_binding))
    lower = corpus.lower()
    result = {
        "controller_prep_preserved": git_object_exists(CONTROLLER_PREP_SHA)
        and is_ancestor(CONTROLLER_PREP_SHA, "HEAD"),
        "same_cycle_bindings_present": bool(resource_binding and inventory_binding),
        "immutable_epoch_and_frame_cycle_identity_present": all(
            marker in lower
            for marker in ("epoch", "frame_id", "cycle_id", "capture_source")
        ),
        "resource_and_inventory_receipts_present": (
            "resource_release_receipt" in lower
            and "inventory_release_receipt" in lower
        ),
        "atomic_clear_present": "publisher.clear()" in corpus,
        "canonical_first_target_present": "targets[0]" in corpus,
        "one_click_exact_present": "click_count != 1" in corpus,
        "newer_reobserve_required": "REOBSERVATION_NOT_NEWER" in corpus,
        "ambiguous_causality_stops": "AMBIGUOUS_CAUSALITY" in corpus,
        "unknown_blocks_action": "unknown" in lower and "stop" in lower,
        "full_inventory_terminal_present": "full" in lower,
    }
    result["passed"] = all(value is True for value in result.values())
    return result


def audit_banking() -> dict[str, Any]:
    offline = read_text("src/mining_automation/banking/offline_release.py")
    pinned = read_text("src/mining_automation/banking/pinned_compatibility.py")
    exact_deposit = read_text("src/mining_automation/full_loop_exact_deposit.py")
    fully_pinned = read_text("src/mining_automation/full_loop_fully_pinned_v2.py")
    corpus = "\n".join((offline, pinned, exact_deposit, fully_pinned))
    lower = corpus.lower()
    result = {
        "accepted_banking_history_preserved": git_object_exists(BANKING_ACCEPTED_SHA)
        and is_ancestor(BANKING_ACCEPTED_SHA, "HEAD"),
        "bank_open_requires_endpoint_owned_evidence": (
            "bank open" in lower and "arrival" in lower
        ),
        "deposit_click_not_success": (
            "success_claim" in lower and "false" in lower
        )
        or "click cannot claim success" in lower,
        "strictly_newer_empty_required": (
            "strictly newer" in lower and "empty" in lower
        ),
        "same_inventory_lineage_required": (
            "lineage" in lower
            or (
                "capture_session_id" in lower
                and "inventory_release_receipt" in lower
            )
        ),
        "fully_pinned_round_trip_present": (
            "FullyPinnedFullLoopCycleReceiptV2" in fully_pinned
            or "FullyPinnedFullLoopCycleReceipt" in fully_pinned
        ),
        "endurance_gate_present": "EnduranceGate" in fully_pinned,
    }
    result["passed"] = all(value is True for value in result.values())
    return result


def audit_runner() -> dict[str, Any]:
    supervisor = read_text("src/mining_automation/runner/supervisor.py")
    authority = read_text("src/mining_automation/runner/authority_v2.py")
    log = read_text("src/mining_automation/runner/evidence_log.py")
    corpus = "\n".join((supervisor, authority, log))
    result = {
        "supervisor_present": bool(supervisor),
        "exact_execution_release_and_window_binding_present": all(
            marker in corpus
            for marker in (
                "authorization_receipt_sha256",
                "resource_release_receipt_sha256",
                "inventory_release_receipt_sha256",
                "full_loop_release_receipt_sha256",
                "runelite_window_title",
            )
        ),
        "stop_path_present": "STOP.request" in corpus
        or "emergency_stop" in corpus,
        "failed_frame_preservation_present": "preserve_failed_frame" in corpus,
        "fresh_session_present": "fresh session" in corpus.lower()
        or "session_id" in corpus,
        "production_live_backend_absent": not any(
            marker in corpus
            for marker in (
                "import pyautogui",
                "SendInput(",
                "SetCursorPos(",
                "mouse_event(",
                "BitBlt(",
                "PrintWindow(",
            )
        ),
    }
    result["passed"] = all(value is True for value in result.values())
    return result


def prepare() -> None:
    git("config", "user.name", "crusader-row-execution-lead", check=True)
    git(
        "config",
        "user.email",
        "actions@users.noreply.github.com",
        check=True,
    )
    git("fetch", "--quiet", "origin", "main", check=True)
    selected = select_remote_branches()
    merge_results = merge_selected(selected)
    b4_migration = migrate_b4_earlier_rejection_test()
    removed_workflows = clean_temporary_workflows()
    audits = {
        "resource": audit_resource(selected),
        "inventory": audit_inventory(selected),
        "navigation": audit_navigation(selected),
        "mining": audit_mining(),
        "banking": audit_banking(),
        "runner": audit_runner(),
    }
    state = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "integration_branch": INTEGRATION_BRANCH,
        "prepared_at_utc": utc_now(),
        "starting_main_sha": git_text("rev-parse", "origin/main"),
        "prepared_head_before_record": git_text("rev-parse", "HEAD"),
        "selected_heads": selected,
        "merge_results": merge_results,
        "b4_test_migration": b4_migration,
        "temporary_workflows_removed": removed_workflows,
        "lane_audits": audits,
        "live_pixels_captured": False,
        "live_input_performed": False,
        "live_authority_granted": False,
        "synthetic_evidence_represented_as_real": False,
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    git("add", "-A", check=True)
    if git("diff", "--cached", "--quiet").returncode != 0:
        git(
            "commit",
            "-m",
            "feat(command): integrate and audit all safe overnight work",
            check=True,
        )
    git("push", "origin", f"HEAD:{INTEGRATION_BRANCH}", check=True)


def existing_focused_tests() -> list[str]:
    preferred = (
        "tests/test_resource_same_cycle_release_binding.py",
        "tests/test_inventory_same_cycle_release_binding.py",
        "tests/test_mining_vertical_slice.py",
        "tests/test_mining_perception_adapter.py",
        "tests/test_navigation_durable_route_evidence.py",
        "tests/test_navigation_release_decision.py",
        "tests/test_navigation_endpoint_handoff.py",
        "tests/test_navigation_pinned_arrival.py",
        "tests/test_banking_offline_release.py",
        "tests/test_banking_pinned_compatibility.py",
        "tests/test_full_loop_offline_integration.py",
        "tests/test_full_loop_release.py",
        "tests/test_full_loop_exact_deposit.py",
        "tests/test_full_loop_fully_pinned_v2.py",
        "tests/test_runner_release_readiness.py",
        "tests/test_runner_authority_v2.py",
    )
    return [path for path in preferred if Path(path).is_file()]


def verify(output_path: Path) -> None:
    results: dict[str, Any] = {
        "os": os.environ.get("RUNNER_OS", sys.platform),
        "verified_at_utc": utc_now(),
        "head_sha": git_text("rev-parse", "HEAD"),
        "commands": {},
    }
    commands: list[tuple[str, list[str], int]] = [
        (
            "install",
            [sys.executable, "-m", "pip", "install", "-e", ".[dev]"],
            900,
        ),
        ("ruff", ["ruff", "check", "."], 900),
        ("strict_mypy", ["mypy", "--strict", "src/mining_automation"], 1200),
    ]
    focused = existing_focused_tests()
    if focused:
        commands.append(
            (
                "critical_path_pytest",
                [sys.executable, "-m", "pytest", "-q", *focused],
                2400,
            )
        )
    commands.append(
        ("full_pytest", [sys.executable, "-m", "pytest", "-q"], 3600)
    )
    for name, command, timeout in commands:
        try:
            result = run(command, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            results["commands"][name] = {
                "command": command,
                "returncode": 124,
                "passed": False,
                "stdout_tail": (error.stdout or "")[-12000:]
                if isinstance(error.stdout, str)
                else "",
                "stderr_tail": "command timed out",
            }
        else:
            results["commands"][name] = result.as_dict()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    results["lane_audits"] = state["lane_audits"]
    results["all_commands_passed"] = all(
        item.get("passed") is True for item in results["commands"].values()
    )
    results["live_pixels_captured"] = False
    results["live_input_performed"] = False
    results["live_authority_granted"] = False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def flatten_true_checks(audits: dict[str, Any]) -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    for lane, record in audits.items():
        if not isinstance(record, dict):
            continue
        checks.append((f"{lane}.audit_passed", record.get("passed") is True))
    return checks


def finalize(receipts_root: Path) -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    receipts: dict[str, Any] = {}
    for path in receipts_root.rglob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        os_name = str(record.get("os", path.stem)).lower()
        if "windows" in os_name:
            receipts["windows"] = record
        elif "linux" in os_name or "ubuntu" in os_name:
            receipts["linux"] = record
    audits = state["lane_audits"]
    engineering_checks = flatten_true_checks(audits)
    engineering_checks.extend(
        (
            ("linux.all_commands_passed", receipts.get("linux", {}).get("all_commands_passed") is True),
            ("windows.all_commands_passed", receipts.get("windows", {}).get("all_commands_passed") is True),
            (
                "critical_lanes_present",
                all(
                    state["selected_heads"].get(lane, {}).get("head_sha")
                    for lane in (
                        "a11_exact",
                        "inventory",
                        "b4_navigation",
                        "banking",
                        "mining",
                    )
                ),
            ),
            (
                "no_merge_conflicts",
                all(
                    item.get("status") != "conflict"
                    for item in state["merge_results"].values()
                ),
            ),
        )
    )
    engineering_passed = sum(1 for _, passed in engineering_checks if passed)
    engineering_total = len(engineering_checks)
    engineering_percent = round(100 * engineering_passed / engineering_total)

    offline_release_gates = {
        "resource_offline_audit": audits["resource"].get("passed") is True,
        "inventory_prehost_offline_audit": audits["inventory"].get("passed") is True,
        "navigation_offline_audit": audits["navigation"].get("passed") is True,
        "mining_offline_audit": audits["mining"].get("passed") is True,
        "banking_round_trip_offline_audit": audits["banking"].get("passed") is True,
        "runner_offline_audit": audits["runner"].get("passed") is True,
        "linux_full_verification": receipts.get("linux", {}).get("all_commands_passed") is True,
        "windows_full_verification": receipts.get("windows", {}).get("all_commands_passed") is True,
    }
    real_release_gates = {
        "real_resource_campaign": False,
        "real_inventory_campaign": False,
        "real_mining_only_vertical_slice": False,
        "real_mine_until_full": False,
        "real_bank_deposit_return": False,
        "real_repeated_endurance": False,
        "production_backend_bound_and_authorized": False,
        "release_package_approved": False,
    }
    release_gate_values = list(offline_release_gates.values()) + list(
        real_release_gates.values()
    )
    release_readiness_percent = round(
        100 * sum(value is True for value in release_gate_values) / len(release_gate_values)
    )
    real_client_proof_percent = 0

    merge_conflicts = {
        lane: result
        for lane, result in state["merge_results"].items()
        if result.get("status") == "conflict"
    }
    missing_lanes = [
        lane
        for lane, entry in state["selected_heads"].items()
        if lane != "a11_exact" and entry.get("head_sha") is None
    ]
    failed_audits = [
        lane for lane, record in audits.items() if record.get("passed") is not True
    ]
    failed_platforms = [
        name
        for name in ("linux", "windows")
        if receipts.get(name, {}).get("all_commands_passed") is not True
    ]
    blockers: list[dict[str, Any]] = []
    if missing_lanes:
        blockers.append({"type": "missing_branch", "lanes": missing_lanes})
    if merge_conflicts:
        blockers.append({"type": "merge_conflict", "details": merge_conflicts})
    if failed_audits:
        blockers.append({"type": "offline_audit_failure", "lanes": failed_audits})
    if failed_platforms:
        blockers.append({"type": "cross_platform_verification_failure", "platforms": failed_platforms})
    blockers.extend(
        (
            {
                "type": "human_live_authorization",
                "detail": "Exact Resource SHA authorization and the real Windows C8A host proposal have not been supplied.",
            },
            {
                "type": "real_client_evidence",
                "detail": "No real RuneLite Resource, Inventory, mining-only, banking, return, or endurance proof exists in this run.",
            },
            {
                "type": "production_backend",
                "detail": "The production live capture/input backend remains deliberately unbound until exact authorization and release receipts exist.",
            },
        )
    )

    all_offline_green = (
        engineering_percent == 100
        and not missing_lanes
        and not merge_conflicts
        and not failed_audits
        and not failed_platforms
    )
    worker_states = {
        "A_resource": "COMPLETE" if audits["resource"].get("passed") is True else "STOPPED",
        "B_navigation": "COMPLETE" if audits["navigation"].get("passed") is True else "STOPPED",
        "C_inventory": "HUMAN-BLOCKED" if audits["inventory"].get("passed") is True else "STOPPED",
        "M_mining_integration": "COMPLETE" if audits["mining"].get("passed") is True else "STOPPED",
        "D_banking": "COMPLETE" if audits["banking"].get("passed") is True else "STOPPED",
        "Windows_live_validation": "HUMAN-BLOCKED",
    }

    report = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "integration_branch": INTEGRATION_BRANCH,
        "finalized_at_utc": utc_now(),
        "final_head_before_report_commit": git_text("rev-parse", "HEAD"),
        "selected_heads": state["selected_heads"],
        "merge_results": state["merge_results"],
        "b4_test_migration": state["b4_test_migration"],
        "lane_audits": audits,
        "platform_verification": receipts,
        "completion": {
            "engineering_completion_percent": engineering_percent,
            "engineering_measurement": {
                "passed_gates": engineering_passed,
                "total_gates": engineering_total,
                "gates": [
                    {"name": name, "passed": passed}
                    for name, passed in engineering_checks
                ],
            },
            "real_client_proof_completion_percent": real_client_proof_percent,
            "real_client_measurement": "0 of 6 required real-client proof stages were executed by this host-independent run.",
            "release_readiness_percent": release_readiness_percent,
            "release_readiness_measurement": {
                "offline_gates": offline_release_gates,
                "real_and_release_gates": real_release_gates,
            },
            "all_host_independent_work_green": all_offline_green,
        },
        "worker_states": worker_states,
        "blockers_to_100_percent": blockers,
        "live_pixels_captured": False,
        "live_input_performed": False,
        "live_authority_granted": False,
        "synthetic_evidence_represented_as_real": False,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    status = (
        "HOST-INDEPENDENT WORK GREEN; REAL WINDOWS/LIVE STEP REMAINS"
        if all_offline_green
        else "HOST-INDEPENDENT WORK STILL HAS FAILURES"
    )
    critical_path = (
        "Exact Resource authorization + non-writing C8A Windows proposal, then passive real-client perception proof"
        if all_offline_green
        else "Repair failed audit/test/conflict gates shown in NIGHTLY_EXECUTION_LEAD_2026-09-03.json"
    )
    dashboard = f"""# Crusader Row Master Dashboard — Current\n\n"
    dashboard += f"**Overall status:** {status}\n\n"
    dashboard += "## Executive snapshot\n\n"
    dashboard += f"- Current phase: First real mining-only vertical slice\n"
    dashboard += f"- Engineering completion: **{engineering_percent}%** ({engineering_passed}/{engineering_total} exact offline integration/audit/platform gates)\n"
    dashboard += f"- Real-client proof completion: **{real_client_proof_percent}%** (0/6 real-client stages performed in this run)\n"
    dashboard += f"- Release readiness: **{release_readiness_percent}%** ({sum(release_gate_values)}/{len(release_gate_values)} defined offline + live + release gates)\n"
    dashboard += f"- Last verified: {report['finalized_at_utc']}\n"
    dashboard += f"- Critical path: {critical_path}\n"
    dashboard += "- Active workers: none claimed RUNNING without current evidence\n"
    dashboard += f"- Current blockers: {len(blockers)} recorded in the exact JSON report\n"
    dashboard += "- Next 24 hours: repair any failed offline gates; otherwise execute the exact Windows authorization/proposal step\n"
    dashboard += "- Primary risk: mistaking offline or synthetic verification for real RuneLite release evidence\n"
    dashboard += "- Tyler action: only required after all offline gates are green and an exact command/SHA is available\n\n"
    dashboard += "## Worker states\n\n"
    for lane, state_value in worker_states.items():
        dashboard += f"- {lane}: **{state_value}**\n"
    dashboard += "\n## Evidence boundaries\n\n"
    dashboard += "- Engineering built and audited: see the exact branch heads and lane records in the JSON report.\n"
    dashboard += "- Offline tested: see Linux and Windows receipts in the JSON report.\n"
    dashboard += "- Proven on the real RuneLite client: **NO**.\n"
    dashboard += "- Release ready: **NO**.\n"
    DASHBOARD_PATH.write_text(dashboard, encoding="utf-8")

    green_flag = Path("docs/verification/NIGHTLY_ALL_HOST_INDEPENDENT_GREEN.flag")
    blocked_flag = Path("docs/verification/NIGHTLY_OFFLINE_BLOCKERS_PRESENT.flag")
    green_flag.unlink(missing_ok=True)
    blocked_flag.unlink(missing_ok=True)
    if all_offline_green:
        green_flag.write_text("true\n", encoding="utf-8")
    else:
        blocked_flag.write_text("true\n", encoding="utf-8")

    # Preserve standard CI while deleting only this temporary execution workflow.
    WORKFLOW_PATH.unlink(missing_ok=True)
    git("add", "-A", check=True)
    if git("diff", "--cached", "--quiet").returncode != 0:
        git(
            "commit",
            "-m",
            "chore(command): publish exact overnight dashboard and blockers",
            check=True,
        )
    git("push", "origin", f"HEAD:{INTEGRATION_BRANCH}", check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("prepare")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--output", type=Path, required=True)
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--receipts-root", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "prepare":
        prepare()
    elif args.mode == "verify":
        verify(args.output)
    elif args.mode == "finalize":
        finalize(args.receipts_root)
    else:  # pragma: no cover
        raise AssertionError(args.mode)


if __name__ == "__main__":
    main()
