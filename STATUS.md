# Project Status

Keep this file simple. It is the owner-facing snapshot of who is doing what.

| Person | Working on | Status | Next step |
|---|---|---|---|
| ChatGPT | Architecture, task assignment, PR review, integration | ACTIVE | Re-review PR #2 after Claude's fix patch is published; assign Codex next task |
| Claude 1 | Issue #1 / PR #2 — capture foundation | CODE FIXES COMPLETE | Publish `capture-review-fixes.patch` onto PR #2, then await lead re-review |
| Codex | Issue #3 — core contracts + tooling hardening | COMPLETE / MERGED | Await next assigned issue |

## Current blockers

- Claude 1's four review fixes are complete and locally verified, but the fix patch is not yet published to PR #2 because Claude's sandbox cannot push.

## Completed

- Codex PR #4 merged into `main` as `94024da`.
- Issue #3 closes via the merged PR.

## Claude 1 verification

- 106 tests passing
- 100% statement coverage across all six capture modules
- Ruff passes repo-wide
- mypy passes
- Scope remains capture package/tests/docs only

## Current milestone

M1 — Foundation and reliable capture.

## Rule

Only one owner per implementation issue. ChatGPT reviews/integrates; Claude and Codex work separate scoped issues in parallel.
