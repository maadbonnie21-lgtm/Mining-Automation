# Project Status

| Person | Working on | Status | Next step |
|---|---|---|---|
| ChatGPT | Architecture, PR review, integration | ACTIVE | Review Codex Issue #9 when ready; define next perception milestone |
| Claude 1 | Issue #5 / PR #8 — Windows RuneLite capture backend | COMPLETE / MERGED | Await next assigned issue |
| Codex | Issue #9 — inventory perception + InventoryState | ASSIGNED | Implement Issue #9 and open PR |

## Current milestone

M3 — First production perception.

## Completed

- Capture foundation and shared-contract hardening are merged.
- Perception replay/regression infrastructure is merged.
- Windows RuneLite capture backend merged through PR #8 as `1c7c770`.
- PR #8 Linux CI: Ruff pass, mypy pass, 513 tests passed, 1 skipped.
- PR #8 Windows smoke: 11 tests passed using the real Win32 DLL boundary.
- Real Windows/RuneLite capture passed on `RuneLite - Chief Luma` at DPI 96.
- Initial capture: 5/5 successful; uploaded BMP visually confirmed real game/UI pixels.
- Move/resize capture: 12/12 successful with changing frame dimensions.
- Minimize/restore: typed minimized failures observed, followed by successful recovery without false frame-id advancement.

## Pending

- Codex Issue #9 will build the first inventory perception path and shared `InventoryState` adapter.
- Assign Claude's next perception task when Claude is available.

## Blocker

- None.
