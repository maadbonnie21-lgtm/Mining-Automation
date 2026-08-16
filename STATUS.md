# Project Status

| Person | Working on | Status | Next step |
|---|---|---|---|
| ChatGPT | Issue #11 — Varrock East iron-rock perception + real fixture capture | ACTIVE | Build recorder/annotation path, collect real frames, implement and test detector |
| Claude 1 | Issue #5 / PR #8 — Windows RuneLite capture backend | COMPLETE / MERGED | Await next assigned issue when available |
| Codex | Issue #9 — inventory perception + InventoryState | IN PROGRESS | Implement Issue #9 and open PR |

## Current milestone

M3 — First production perception.

## Completed

- Capture foundation and shared-contract hardening are merged.
- Perception replay/regression infrastructure is merged.
- Windows RuneLite capture backend merged through PR #8 as `1c7c770`.
- Linux CI, Windows smoke, and real-machine RuneLite capture validation passed for the tested envelope.
- Real capture correctly handled live pixels, move/resize, minimize/restore, and frame identity.

## Active

- ChatGPT Issue #11: first real iron-rock detector for Varrock East Mine, including real fixture capture and replay regression data.
- Codex Issue #9: 28-slot inventory perception and clean `InventoryState` adapter.

## Next integration target

Combine resource and inventory observations into world state so the application can reliably know:
- which supported iron rocks are available/depleted/uncertain
- whether inventory is empty/partial/full/uncertain

No clicking, navigation, or banking will be added until both perception paths pass their release tests.

## Blocker

- None.
