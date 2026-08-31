# Inventory positive classifier V3 development gate

## Status

Inventory V3 is an offline, non-activating development candidate. It is not a
production detector, is not exported by the inventory package, and cannot be
adapted into `InventoryState`. Its only purpose is to evaluate whether a
strictly source-owned positive-appearance model can count the existing
privacy-safe inventory corpus while preserving fail-closed behavior.

Every V3 report must state:

- `validation_status=independent-campaign-required`;
- `activation_allowed=false`;
- all 16 current real cases are development/regression evidence;
- there are no independent validation cases; and
- generalization is unproven.

No RuneLite interaction or new evidence collection is part of this gate.

## V2 root cause

V2 correctly showed that distributed 24x24 core support can raise legitimate
occupied-slot confidence above the unchanged `0.8` publication floor. Its
internal four-pixel perimeter guard was invalid, however. The rejected second
campaign differs from the empty reference at these slot-local positions in
slot 1:

| x | y | Reference RGB | Candidate RGB | Max delta |
|---:|---:|---|---|---:|
| 28 | 10 | `(62, 53, 41)` | `(1, 1, 2)` | 61 |
| 29 | 12 | `(62, 53, 41)` | `(1, 1, 2)` | 61 |
| 29 | 14 | `(62, 53, 41)` | `(1, 1, 2)` | 61 |
| 28 | 26 | `(62, 53, 41)` | `(1, 1, 2)` | 61 |

All four coordinates are inside the authoritative 32x32 slot and outside the
24x24 core. Their complete slot bytes recur across distinct panels whose
independent review places slot 1 inside the occupied row-major prefix. They
are stable owned slot content consistent with item artwork, not defensible
evidence that those owned pixels are an obstruction. The prefix annotation
does not establish that any whole presentation is legitimate. Nonmatching
presentation cases remain ambiguous; four pixels alone are not used to infer
a UI state.

The canonical forensic tool records the exact pixels, per-case RGB values,
slot hashes, recurrence cohorts, corpus identity, Git head, and report digest.

## V3 model

V3 uses a frozen exact-appearance allowlist rather than another shape
heuristic:

1. The unchanged V1 classifier and its empty/occupied thresholds remain the
   binary gate. V3's configuration identity also binds the complete V1 policy
   and the inherited `0.7` changed-fraction / `0.3` mean-color-delta score
   weights, so a behavioral dependency change cannot reuse the same identity.
2. Each authoritative 32x32 slot is converted to canonical semantic RGB,
   covering all 1,024 owned pixels.
3. A slot that exactly matches its corresponding reviewed empty-reference slot
   is EMPTY.
4. A V1-OCCUPIED slot whose complete RGB digest exactly matches a source-owned
   clean-positive prototype is OCCUPIED with publication confidence.
5. Every other appearance is UNCERTAIN. A single changed owned pixel,
   including a perimeter or sub-threshold pixel, therefore prevents a known
   count.
6. Any uncertain slot makes the complete inventory result UNKNOWN.
7. Existing non-slot and row-gutter obstruction checks remain authoritative.
   Row-major prefix shape is never scene, presentation, or activation evidence.
   Because the development corpus supplies counts rather than independent
   per-slot truth, an otherwise exact gapped ensemble returns explicit UNKNOWN;
   it is never silently truncated or miscounted.

The model has no caller-supplied thresholds, prototypes, profile, or reference.
Its identity binds the exact dataset, manifest, reviewed profile, semantic-RGB
reference, source case identities and hashes, every prototype occurrence, and
all algorithm constants.

## Development evidence and limits

Only the four reviewed clean-visible positive cases supply model prototypes.
Wrong-tab, obstruction, selection, hover, quantity-text, and operator action
labels do not supply positive prototypes.

The clean sources contain 62 occupied slot instances and 46 unique exact RGB
prototypes. Sixteen prototypes recur across separate clean cases, covering
32/62 instances; ten recur across both sessions, covering 20/62 instances.
Leave-one-case exact coverage is:

- first partial: 1/1;
- first full: 11/28;
- second partial: 5/5; and
- second full: 15/28.

The partial cases therefore have useful cross-frame recurrence, while the full
cases require substantial self-fit. A 16/16 development result is not
validation and must not be presented as evidence of generalization.

Release still requires a new independently controlled real-client campaign
evaluated after the V3 model is frozen. Until that happens, V3 cannot become an
approved factory or controller input.

## Safety and activation boundary

V3 deliberately does not implement the production detector, slot-classifier,
observation, or inventory-state adapter contracts. The package-level V1 and
frozen V2 exports remain unchanged, and the application/controller have no V3
dependency.

The current controller is also not ready for activation: an unknown inventory
can fall through toward mining, and banking steps are not yet guarded by the
future typed perception snapshot. The behavioral safety tests belong with the
source-owned `V1PerceptionSnapshot` authority described in
`V1_VERTICAL_SLICE_PERCEPTION_CONTRACT.md`; this development gate adds only
an honest non-activation proof.

## Required regression matrix

The V3 development evaluator must keep all of these fail closed:

- wrong tab and external obstruction;
- selected, hover, drag, and quantity-text presentations while unsupported;
- wrong profile, reference, geometry, dataset, manifest, or model identity;
- one-pixel, perimeter-only, sub-threshold, and known-core/altered-perimeter
  changes;
- an exact known binary change mask rendered with different RGB values;
- all-slot perimeter corruption;
- checkerboard, lattice, cross, diagonal, edge, ring, stripe, blob/tendril,
  neighbor-bleed, distributed-noise, and highlight-like patterns; and
- any non-prefix or partially known slot ensemble, with a dedicated regression
  proving that the former returns explicit UNKNOWN rather than a prefix count.

V1 and frozen V2 identities and replay outputs must remain unchanged.
