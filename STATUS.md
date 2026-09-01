# Project Status

## Current milestone

M3 — constrained-v1 perception release-gate closure.

The current support target is one exact reviewed Varrock East iron view.
Arbitrary camera reacquisition is not part of the v1 critical path. An
unsupported or uncertain view exposes zero targets and stops.

## Accepted development lineages

- Windows RuneLite capture backend: merged through PR #8.
- Inventory V3 frozen candidate:
  `5975532b472a74d93f010e04ca44b2efa2a3ffd7`.
- Inventory independent-validation protocol/readiness:
  `32764bfd82afb46d4e99292bab7d162be536e2d7`.
- Resource assembly/trust/replay:
  `225ea7525ee21b5161f584b4daaad90551d65b31`.
- Deny-only constrained-v1 reference:
  `d8fd03d8087732a2f3314da4a0c25edb5d134f55`.

These open lineages are accepted development inputs, not production
activation or a supported end-to-end workflow.

## Open perception release gates

- **B — offline resource release-evidence boundary:** the generic development
  capture/annotation tools are not a release authority. A separate passive,
  source-owned immutable campaign and independent-review boundary must be
  implemented and accepted before resource C1 collection.
- **C1 — fresh empirical evidence:** inventory live authorization, the
  seven-case campaign, independent reviewer truth, and exact frozen-evaluator
  conformance; plus resource depletion/respawn cycles for north-west, center,
  and north-east, a real obstruction, unsupported location, neighboring
  copper/tin/terrain negatives, and a fresh exact-view startup positive through
  the accepted release-evidence boundary.
- **C2 — evidence-contingent source/review gates:** source-owned inventory
  approval and a later reviewed production binding; resource failure-to-replay
  promotion, final lead review of the exact client/renderer/profile envelope,
  and the resulting source-owned resource release/promotion record. Reported
  DPI `96` is a required candidate constraint for that envelope; it remains
  pending fresh review and is not packaged-profile identity.

Machine-readable release eligibility remains `false` while either C1 or C2 is
open, and the resource lineage also remains ineligible while B is open.
Completing captures alone cannot close the later review/promotion chain.

See `docs/V1_PERCEPTION_RELEASE_GATE_AUDIT.md` for the exact closed/offline,
C1, and C2 ledgers and immutable evidence identities.

## Next integration target

Issue #14 remains blocked. Once both perception release gates genuinely close,
the next implementation is atomic same-frame resource/inventory acquisition
into canonical `WorldState`. No controller, clicking, navigation, or banking
activation is authorized before that gate.
