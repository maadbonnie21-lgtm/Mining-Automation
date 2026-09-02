# Constrained-v1 perception authority contract

Status: **offline, deny-only integration contract**

This document defines the perception boundary required before the first
constrained mining-to-bank vertical slice can be connected to `WorldState`, a
controller, navigation, banking, or input. It does not activate any of those
systems.

The implementation is
`mining_automation.perception.constrained_v1_authority`. It is intentionally not
re-exported from the package-level perception API and has no dependency on the
controller, interaction, navigation, banking, or application layers.

## Current authority state

The public deny-only contract is schema version 1.
`CONSTRAINED_V1_ACTIVATION_ALLOWED` is source-owned and fixed to `False`.
The source-owned resource and inventory approval registries are empty. Every
snapshot therefore contains all three mandatory blockers:

- `activation_disabled`
- `resource_release_approval_missing`
- `inventory_release_approval_missing`

The current snapshot type is structurally incapable of carrying:

- resource observations;
- an inventory count;
- actionable target identifiers;
- mining authority;
- banking authority;
- navigation authority; or
- click authority.

Its resource and inventory booleans are named `*_shape_valid`. They are
diagnostic results only: they mean that supplied values conformed to the
offline contract shape. They are not trust receipts, approval decisions, or
action capabilities.

Changing a caller argument cannot enable this boundary. There are no caller
parameters for detector thresholds, resource IDs, confidence policy, freshness
policy, approval, activation, or target lists.

## Exact-cycle provenance

Both perception sides must carry the same immutable
`PerceptionCycleProvenance`:

- exact `FrameRef`, including frame ID, monotonic capture time, and geometry;
- non-empty cycle ID;
- lowercase SHA-256 identity for the exact frame bytes; and
- non-empty capture-configuration identity.

The boundary compares the complete provenance value, not only a timestamp or
frame number. A difference in frame, cycle ID, digest, or capture configuration
is a mixed-cycle failure. Evidence older than the source-owned one-second
diagnostic freshness bound, evidence timestamped after evaluation, and invalid
evaluation clocks fail closed.

This module cannot itself prove that a caller-computed digest came from owned
capture bytes. That proof belongs in a future source-owned assembly function.
The current schema may not be converted into an activating schema merely by
populating its public dataclasses.

## Resource evidence

Resource input is the accepted `ProductionResourceTrustResult` from the
existing Varrock East iron trust boundary, wrapped with exact-cycle provenance
and explicit identity. The contract rechecks:

- detector `profiled-resource:varrock-east-iron-v1`;
- detector version `2.1.0`;
- profile `varrock-east-iron-v1`;
- resource profile schema v3;
- location `varrock-east-mine`;
- the exact four canonical resource IDs in packaged order;
- exact packaged frame geometry;
- exact packaged candidate regions for available resources;
- no interaction region for depleted resources;
- no uncertain resource;
- exact receipt/current-cycle frame equality; and
- the production trust success reason.

Rejected, incomplete, duplicate, extra, wrong-type, stale, mixed-frame,
uncertain, or malformed-region resource evidence exposes zero resources and
zero targets. Existing detector thresholds, the 5-of-6 scene-landmark quorum,
the all-three-macro-zone requirement, scene authority, and candidate policy are
unchanged.

The wrapper remains nominal evidence. Because the existing trust-result value
is publicly constructible and does not retain raw detector/profile identity,
future live integration must create this wrapper inside a source-owned
capture/detect/trust assembly rather than accepting one from application or
configuration input.

## Inventory evidence

Inventory is represented only as a future authority contract. It carries:

- detector, detector-version, profile, schema, and configuration identity;
- exact-cycle provenance;
- a typed `InventoryState`;
- an optional independent-validation protocol identity; and
- the matching independent-validation report SHA-256.

Protocol identity and report digest are atomic: both are present or both are
absent. Caller-provided strings do not constitute approval. No inventory
identity/report tuple is in the source-owned approval registry.

The offline shape gate requires:

- known occupancy (`occupied_slots` is not `None`);
- capacity exactly 28;
- confidence at or above the unchanged 0.8 publication floor;
- exact current-cycle provenance; and
- an independent-validation protocol/report pair.

The frozen V3 development/readiness lineage is not treated as production
inventory truth. It remains `activation_allowed=false` and
`independent-campaign-required`. Its development cases must not be relabeled as
independent evidence.

An unknown inventory remains `InventoryState(None)` at this boundary. It is
never interpreted as “not full,” so it cannot authorize continued mining.
Likewise, a known partial or full count with an unapproved identity cannot
authorize mining or banking.

## Deny-first matrix

| Evidence condition | Result |
| --- | --- |
| Inventory missing, unknown, wrong layout, below 0.8, unvalidated, unapproved, stale, or wrong provenance | No mining, banking, navigation, or click authority |
| Resource receipt missing, rejected, wrong identity/location/schema, incomplete, uncertain, stale, mixed, or malformed | No resource states, targets, or click authority |
| Resource and inventory values come from different cycles, frame hashes, or capture configurations | Explicit mixed-provenance blocker; no authority |
| Both sides satisfy offline shape checks | Still no authority: activation and both release approvals remain blocked |
| Caller claims validation or supplies canonical-looking values | Still no authority: caller claims cannot modify source-owned blockers |

## Requirements for a future activating boundary

A later reviewed change must introduce a separate source-owned assembly and a
new authority schema. It must not mutate this deny-only checkpoint in place.
At minimum it must:

1. bind an exact accepted resource release record to detector/profile/schema,
   packaged artifacts, negative replay evidence, and the source-owned capture
   cycle;
2. bind an independently reviewed inventory release record to the frozen model
   artifacts, protocol lock, independent dataset/campaign, reviewer truth,
   report digest, candidate head, and approval identity;
3. compute frame hashes from owned frame bytes rather than accepting unverified
   caller claims;
4. prove resource and inventory observations come from the same fresh owned
   cycle and supported view;
5. preserve UNKNOWN without a fallback to “not full” or “actionable”;
6. expose only packaged available-resource regions after every gate passes;
7. remain separate from input execution and require post-action observation;
   and
8. receive lead review before any `WorldState`, controller, banking,
   navigation, or interaction wiring is added.

Until those requirements close, the constrained-v1 vertical slice remains a
contract and regression-test artifact, not a runnable automation path.
