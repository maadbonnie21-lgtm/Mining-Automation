# Project Status

| Person | Working on | Status | Next step |
|---|---|---|---|
| ChatGPT | Issue #11 / Draft PR #12 — Varrock East iron perception | REAL-FRAME CALIBRATION | Collect reviewed available/depleted/obstructed frames and calibrate detector profile |
| Claude 1 | Issue #5 / PR #8 — Windows RuneLite capture backend | COMPLETE / MERGED | Await next assigned issue when available |
| Codex | Issue #9 — inventory perception + InventoryState | IN PROGRESS | Implement Issue #9 and open PR |

## Current milestone

M3 — First production perception.

## Completed

- Capture foundation and shared-contract hardening are merged.
- Perception replay/regression infrastructure is merged.
- Windows RuneLite capture backend merged and passed Linux CI, Windows smoke, and live RuneLite validation.
- Draft PR #12 now contains the first resource detector, shared `ResourceState` adapter, real-frame recorder, annotation/review workflow, replay-manifest builder, documentation, and synthetic regression tests.
- Draft PR #12 is one clean commit and passes Ruff, strict mypy, and the full suite: 525 passed, 1 skipped.

## Active

- ChatGPT Issue #11: collect reviewed Varrock East frames, calibrate available/depleted/uncertain iron signatures, and complete real-frame replay evaluation.
- Codex Issue #9: 28-slot inventory perception and clean `InventoryState` adapter.

## Next integration target

Combine resource and inventory observations into world state so the application can reliably know:
- which supported iron rocks are available/depleted/uncertain
- whether inventory is empty/partial/full/uncertain

No clicking, navigation, or banking will be added until both perception paths pass their release tests.

## Blocker

- Issue #11 now needs deliberate real RuneLite fixture collection at Varrock East Mine.
