# Project Status

| Person | Working on | Status | Next step |
|---|---|---|---|
| ChatGPT | Architecture, PR review, integration | ACTIVE | Re-review Claude PR #8 after fixes; review Codex Issue #6 when ready |
| Claude 1 | Issue #5 / PR #8 — Windows RuneLite capture backend | CHANGES REQUIRED | Fix Windows-only GDI blockers in PR #8, rerun gates, then lead re-review |
| Codex | Issue #6 — perception replay/regression harness | IN PROGRESS | Implement Issue #6 and open PR |

## Current milestone

M2 — Real capture + perception infrastructure.

## Completed

- Codex PR #4 merged into `main`.
- Claude PR #2 merged into `main` as `bd9d5f8`.
- Capture foundation and shared-contract hardening are complete.
- Claude Issue #5 patch is published as PR #8.
- PR #8 GitHub CI passes Ruff, mypy, and pytest.

## Pending

- Claude must fix the Windows-only GDI correctness/resource-lifecycle findings from lead review on PR #8.
- After the fixes pass CI and re-review, run real Windows/RuneLite validation on the owner's machine.
- Codex Issue #6 is in progress.

## Blockers

- PR #8 must not merge until the Windows GDI review findings are fixed and re-reviewed.
