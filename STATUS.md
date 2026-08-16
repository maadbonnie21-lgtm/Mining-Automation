# Project Status

| Person | Working on | Status | Next step |
|---|---|---|---|
| ChatGPT | Architecture, PR review, integration | ACTIVE | Publish/review Claude Issue #5 patch; review Codex Issue #6 when ready |
| Claude 1 | Issue #5 — Windows RuneLite capture backend | CODE COMPLETE / PENDING PUBLISH | Upload patch into GitHub PR, then run real Windows/RuneLite validation |
| Codex | Issue #6 — perception replay/regression harness | IN PROGRESS | Implement Issue #6 and open PR |

## Current milestone

M2 — Real capture + perception infrastructure.

## Completed

- Codex PR #4 merged into `main`.
- Claude PR #2 merged into `main` as `bd9d5f8`.
- Capture foundation and shared-contract hardening are complete.
- Claude Issue #5 implementation is locally complete: 69 new tests, 312 total, Ruff/mypy/pytest pass in both working tree and clean-clone patch apply.

## Pending

- Claude Issue #5 patch must be published to GitHub and reviewed in CI.
- Real Windows/RuneLite validation is still required before Issue #5 is production-complete.
- Codex Issue #6 is in progress.

## Blockers

- Claude sandbox cannot push to GitHub; patch handoff required.
