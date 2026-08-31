# Inventory positive classifier V2

## Status and boundary

Inventory positive classifier V2 is a deterministic, offline-only candidate
for the reviewed live inventory profile. It is not selected by
`inventory_detector_from_profile`, it cannot activate the candidate profile,
and every report states `activation_allowed=false`. V1 remains fully
replayable as `inventory-baseline@1.0.0` with its original configuration and
safety expectations.

The V2 factory is deliberately separate:

```python
inventory_positive_detector_v2_from_profile(profile, empty_reference)
```

It has no caller-supplied policy or publication-threshold arguments. V2 uses
detector identity `inventory-positive-v2@2.0.0`, classifier identity
`reference-distributed-core-v2@2.0.0`, and a separately hashed configuration.
The reviewed profile remains non-activating until the next provenance/factory
release gate receives lead approval.

## Root cause

V1's binary classification is not the defect. On the first reviewed campaign,
true occupied slots already score from `0.427024` through `0.446401`, safely
above the unchanged occupied boundary of `0.22`. Empty slots score exactly
zero. V1 then maps an occupied score's distance from `0.22` toward a
theoretical score of `1.0` into confidence. Ordinary item sprites therefore
receive only `0.632708` through `0.645129`, below the detector's independent
and unchanged `0.8` publication floor.

The canonical report includes, for all 28 slots in every clean
empty/partial/full case:

- expected slot state derived from the reviewed count and the frozen row-major
  prefix policy (the reviewer did not provide independent per-slot labels);
- raw score and changed-pixel fraction;
- weighted changed-fraction and mean-color components;
- normalized mean RGB L1 delta;
- V1 state, confidence, publication margin, and decision; and
- V2 spatial support, state, confidence, publication margin, and decision.

## Frozen V2 model

V2 preserves all existing geometry, reference pixels, obstruction guards,
pixel threshold `24`, raw score weights `0.7/0.3`, empty boundary `0.08`,
occupied boundary `0.22`, uncertainty band, and detector publication floor
`0.8`.

For an occupied raw decision only, V2 divides the existing 24-by-24 inset slot
core into nine fixed, non-overlapping 8-by-8 cells. A cell has support when at
least one pixel's maximum RGB-channel delta is at least `24`. Occupied
confidence uses the existing occupied confidence curve with the supported-cell
fraction as its evidence value. Every coarse row and column must also contain
support. This means:

- 6/9 cells -> `0.786324786`, so the detector remains `UNKNOWN`;
- 7/9 cells -> `0.857549858`, above the unchanged publication floor;
- 8/9 cells -> `0.928774929`; and
- 9/9 cells -> `1.0`.

The original raw occupied decision is still mandatory. Distributed weak pixels
inside the raw uncertainty band remain uncertain, and a localized strong patch
with insufficient spatial support remains below the publication floor.

The first campaign also freezes three conservative presentation guards for the
constrained mining inventory: no `D>=24` changed pixels outside authoritative
slots, no `D>=61` changed pixel in any slot's 4-pixel ownership perimeter, and
prefix-only occupied slots. Selection, quantity text, hover/non-prefix layouts,
and sprite spill outside slot ownership therefore fail closed as unsupported
presentations. These guards add `UNKNOWN` paths; they do not relax any existing
obstruction or occupancy threshold.

## Calibration and retrospective-validation discipline

The model-development split is separate from the review record's original
`validation_split` field; reviewer truth is not rewritten.

- Calibration campaign: exact source session
  `20260830T183057.424897Z-inventory-session`, the first eight cases.
- Retrospective-validation campaign (labelled `held-out` in the immutable
  reviewer record): exact source session
  `20260830T222938.820219Z-inventory-session`, the final eight cases.

The formal evaluator chronology freezes the algorithm and constants from the
first campaign before running the second-campaign evaluator. This proves that
the reported failure was preserved without changing the frozen model after the
formal evaluation. It does **not** prove that the second batch was unseen by the
implementer: the complete 16-case fixture had already been committed and was
available during model development. The second campaign is therefore described
as frozen retrospective validation, not an independently acquired or blinded
held-out set.

The calibration-only evidence SHA-256 is:

```text
91d12e3a30824da09f98b766bde8659460121d463e3df7ae8755f617d8abf2c9
```

It binds the first campaign's sanitized region hashes, independent reviewer
truth, profile/reference identity, and algorithm constants. It excludes all
second-campaign frame bytes; deterministic tests reproduce the digest after
deleting or corrupting every second-campaign artifact. That establishes input
separation for the calibration reader, not lack of human or development-time
visibility into the second campaign. Model commit
`620dcde6a476b5f458f6736e990f4d4e578791c4` was created before the formal
second-campaign evaluator ran, and the CLI rejects any later change to the
frozen classifier, calibration reader, or factory files.

## Frozen retrospective-validation result: failed safely

The frozen candidate passes all eight calibration expectations. Its spatial
feature also gives every clean second-campaign occupied slot at least
`0.857549858`, above the unchanged `0.8` floor. The frozen candidate
nevertheless fails the required clean retrospective-validation counts because
the first-batch perimeter guard rejects four legitimate `D>=61` pixels in slot
1 of both varied-art cases. The detector therefore returns `UNKNOWN` rather
than bypassing the guard.

The frozen retrospective-validation results are:

| Reviewed case | V2 result | Weakest slot confidence |
|---|---:|---:|
| clean empty | `0` | `1.0` |
| clean five-item partial | `UNKNOWN` (perimeter guard) | `0.0` aggregate; feature min `0.928774929` |
| clean mixed-art full | `UNKNOWN` (perimeter guard) | `0.0` aggregate; feature min `0.857549858` |
| wrong tab | `UNKNOWN` | `0.0` aggregate |
| obstruction | `UNKNOWN` | `0.0` aggregate |

The passive reviewed hover/selected/quantity-text examples remain intentionally
unsupported `UNKNOWN` with zero aggregate confidence. Synthetic weak,
ambiguous, malformed-geometry, no-row-gutter, spill, and obstruction cases also
remain fail closed. No second-campaign-driven threshold or guard change was
made or is permitted for this frozen candidate; it remains non-activating and
the diversity batch is now a consumed retrospective-validation set.

## One-command offline report

From an exact clean head:

```powershell
python tools/evaluate_inventory_positive_v2.py `
  --fixture tests/fixtures/perception/inventory-live-candidate-safety-bb0d0e3f7ff1c73b `
  --output diagnostics/inventory-positive-v2/<RUN> `
  --expected-head <EXACT_40_CHARACTER_HEAD>
```

The output directory is created exclusively and contains canonical JSON plus a
SHA-256 sidecar. A dirty tracked worktree, wrong Git head, changed calibration,
fixture-integrity failure, incorrect count, or unsafe negative produces a
non-zero result. Only a report produced through this CLI has the documented
clean-head and exact-head checks. Direct library calls, in-memory evaluation
objects, test assertions, and a copied JSON file do not by themselves have
verified Git provenance.

## Limitations and next blocker

The reviewed empty regions are byte-identical, so no rendering jitter is yet
proven. Seven-cell support is the observed second-campaign lower edge; one
fewer cell fails closed. More importantly, a perimeter rule derived from the
first batch's single clean art family does not generalize to ordinary varied
item art. The second batch is consumed by this frozen retrospective result and
cannot be relabelled as fresh validation after tuning. A genuinely unseen
release-validation set would require separately controlled evidence. V2 does
not approve new client themes, scales, geometries, tabs, renderers, overlays,
activation authority, or production release.
