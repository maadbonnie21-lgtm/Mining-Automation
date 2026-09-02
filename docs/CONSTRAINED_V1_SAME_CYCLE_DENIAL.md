# Constrained-v1 same-cycle denial preparation

Status: **offline, structurally non-activating integration preparation**

This checkpoint joins two frozen, accepted offline lineages without changing
either one:

- resource release-decision readiness from A3; and
- the deny-first constrained-v1 authority contract from PR #38.

The join does not approve either perception path. The A3 source record remains
`PROPOSED_NOT_GRANTED`, and PR #38 keeps its empty source-owned approval
registries and literal activation gate set to false.

## Atomic denial projection

`prepare_constrained_v1_same_cycle_denial()` delegates identity, freshness,
shape, and same-cycle checks to the frozen PR #38 contract. It then publishes
only a sealed denial projection:

- the exact four canonical Varrock East iron resource IDs, in packaged order;
- every resource state UNKNOWN (`available=None`);
- confidence `0.0` and no interaction region for every resource;
- inventory occupancy UNKNOWN with capacity 28 and confidence `0.0`;
- no actionable target IDs;
- no resource or inventory release binding;
- activation disabled; and
- mining, banking, navigation, and click authority all false.

Even when both input shapes are diagnostically valid, the output remains the
same atomic denial. The result never retains the supplied evidence, detector
observations, production trust receipt, or inventory publication. Therefore an
accepted-looking nested value cannot be read around the denial projection.

## Same-cycle and fail-closed behavior

The frozen authority contract continues to reject:

- missing, duck-typed, rejected, incomplete, uncertain, or wrong-lineage
  resource evidence;
- missing, unknown, below-floor, wrong-layout, or unvalidated inventory
  evidence; every nominal inventory identity remains unapproved;
- stale, future, or invalid timestamps;
- disagreement in frame ID, capture timestamp, geometry, frame SHA-256, cycle
  ID, or capture-configuration identity; and
- mixed resource and inventory provenance.

Every such result exposes four UNKNOWN resources rather than an empty tuple,
so downstream preparation has an explicit canonical denial shape. UNKNOWN
inventory is never interpreted as “not full.” No detector threshold, 5-of-6
landmark quorum, all-three-zone requirement, scene authority, inventory
publication floor, or production perception behavior changes here.

## Deliberately absent release seam

There is no runtime argument for a release record, digest, approval flag,
activation switch, threshold, policy, target list, or resource list. An A3
decision-shaped mapping is merely a wrong-typed resource input and remains
denied. The A3 candidate head is not hard-coded as an approval, and no
inventory approval receipt is invented.

This module is intentionally not re-exported from the package-level perception
API. It has no dependency on `WorldState`, the controller, navigation, banking,
interaction, input, or the application layer. A future activating boundary
must use a new reviewed schema, compute provenance from owned frame bytes, and
consume separately approved source-owned resource and inventory release
records. This checkpoint cannot be mutated into that boundary by caller data.
