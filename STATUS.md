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

- Inventory: one separately authorized, independently reviewed seven-case
  campaign, exact evaluator PASS, source-owned approval, and a later reviewed
  production binding.
- Resource: real depletion/respawn cycles for north-west, center, and
  north-east; real obstruction; unsupported-location and neighboring
  copper/tin/terrain negatives; and final exact-view startup/envelope review.

See `docs/V1_PERCEPTION_RELEASE_GATE_AUDIT.md` for the exact closed/offline/
fresh-evidence ledger and immutable evidence identities.

## Next integration target

Issue #14 remains blocked. Once both perception release gates genuinely close,
the next implementation is atomic same-frame resource/inventory acquisition
into canonical `WorldState`. No controller, clicking, navigation, or banking
activation is authorized before that gate.
