# Project Status

| Person | Working on | Status | Next step |
|---|---|---|---|
| ChatGPT | Architecture, PR review, integration | ACTIVE | Run PR #8 real RuneLite validation; review Codex Issue #9 when ready |
| Claude 1 | Issue #5 / PR #8 — Windows RuneLite capture backend | CODE COMPLETE | Await real-machine RuneLite validation |
| Codex | Issue #9 — inventory perception + InventoryState | ASSIGNED | Implement Issue #9 and open PR |

## Current milestone

M3 — First production perception.

## Completed

- Capture foundation and shared-contract hardening are merged.
- Perception replay/regression infrastructure is merged.
- ChatGPT completed Claude's outstanding PR #8 review fixes.
- PR #8 is one clean commit on current `main` and is mergeable.
- PR #8 Linux CI: Ruff pass, mypy pass, 513 tests passed, 1 skipped.
- PR #8 Windows smoke: 11 tests passed using the real Win32 DLL boundary.

## Pending

- Run `tools/windows_capture_check.py` with RuneLite open on the owner's Windows machine.
- Confirm real pixels, minimize/restore, resize/move, and DPI behavior.
- Merge PR #8 only after that real-machine gate passes.
- Codex Issue #9 will build the first inventory perception path and shared `InventoryState` adapter.

## Blocker

- Only the real RuneLite capture validation remains for PR #8.
