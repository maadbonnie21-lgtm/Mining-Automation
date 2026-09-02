# First-mining-slice same-cycle assembly design

Status: **design and regression matrix only; no runtime assembly or authority**

This document defines the future perception-only join between released resource
perception and released inventory perception for the constrained Varrock East
v1 slice. It does not implement that join. The resource release receipt is not
issued, the inventory release lineage remains independently owned, and the
existing same-cycle implementation remains the atomic deny-only projection.

The future join may be implemented only after both perception lineages have
source-owned, independently reviewed release receipts. It must be a new
reviewed schema rather than a positive mutation of the deny-only A4 boundary.

## Non-authority boundary

The future result is perception data only. It may describe exact resource
states, inventory occupancy, and observational regions for available iron. It
must not select a target or contain `WorldState`, an action intent, controller
state, route state, mining/banking/navigation policy, click authority, input
authority, or an execution method.

An inventory observation of `full` remains an observation. Deciding to bank is
later controller policy. An available-iron region remains an observation.
Choosing or clicking one is later controller and input policy.

The current implementation status is intentionally stricter: every assembly
attempt is denied by `constrained_v1_same_cycle`, which emits four canonical
UNKNOWN resources, UNKNOWN inventory, no regions, and no downstream authority.

## Future owned-frame sequence

The eventual source-owned implementation must perform one indivisible cycle in
this order:

1. Internally load the exact source-owned resource receipt and the separately
   released inventory receipt. Callers cannot supply, reconstruct, or select a
   receipt.
2. Refuse the cycle before capture unless both values are the exact packaged
   singletons with their exact retained source record digests.
3. Capture exactly one owned frame from the reviewed source. Do not accept a
   caller frame, payload, frame digest, observation, detector, profile, policy,
   region, target ID, approval, or threshold.
4. Freeze the owned payload and compute SHA-256 exactly once over those bytes.
5. Create one internal cycle provenance value binding the frame reference,
   payload digest, pixel format, capture configuration identity, and cycle ID.
6. Run unchanged released resource perception and released inventory
   perception against the identical owned `Frame` object. Neither path may
   capture again or substitute a frame reference.
7. Validate each returned identity and provenance against the internal cycle,
   then snapshot the validated plain values once.
8. Publish a perception-only positive result only when every positive
   requirement below holds. Otherwise publish one atomic denial.

There is no automatic retry, recapture, camera recovery, diagnostic override,
or fallback. A queued second frame must remain untouched after any failure.

## Positive publication requirements

A future positive carrier requires all of the following at once:

- both exact source-owned release receipts are present and current;
- one exact, fresh, internally owned frame and payload digest back both results;
- resource identity matches the released detector, detector version, profile,
  schema, location, and ordered four-resource ensemble;
- unchanged production resource perception validates the exact supported view;
- all four resource states are definitive;
- each available resource region equals its packaged candidate region and each
  depleted resource has no region;
- inventory identity matches the independently released detector, profile,
  configuration, layout, and validation roots;
- inventory occupancy is known, within the 28-slot layout, and meets the frozen
  publication confidence floor; and
- resource and inventory evidence match the internal frame ID, capture time,
  geometry, pixel format, payload SHA-256, capture configuration, and cycle ID.

Diagnostic registration, candidate pixels, fixed UI, and caller assertions
cannot establish the supported resource scene. The production 5-of-6 world
landmark quorum and all-three-zone requirement remain unchanged.

The future positive carrier should contain only:

- exact cycle provenance;
- resource receipt identity and record digest;
- inventory receipt identity and record digest;
- the ordered four resource observations;
- the inventory observation; and
- available-iron observational regions in packaged resource order.

It must not expose `actionable_target_ids`, `click_authorized`,
`mining_authorized`, `to_world_state`, `to_action_intent`, or equivalent
execution-oriented fields.

## Atomic denial

Any failure produces the complete denial shape: four canonical iron resources
with UNKNOWN availability, confidence `0.0`, and no interaction regions;
UNKNOWN inventory with capacity 28 and confidence `0.0`; no observational
available-iron regions; and no downstream authority.

Denial is required for:

- a missing, forged, reconstructed, rebound, stale, or wrong-lineage receipt;
- capture/readiness failure or an unsupported frame;
- stale, future, non-finite, malformed, or mixed provenance;
- a rejected resource trust result, any UNKNOWN resource, or a wrong resource
  identity, order, count, type, state, or region;
- UNKNOWN inventory, wrong tab, obstruction, wrong layout, below-floor
  confidence, invalid count, wrong identity, or unvalidated inventory evidence;
- either evaluator raising or returning a hostile/non-nominal value; or
- any disagreement between the two perception results and the internally owned
  frame/cycle.

The denial is atomic. A valid inventory count cannot leak through a rejected
resource cycle, and valid resource regions cannot leak through rejected
inventory evidence.

## Required regression matrix for the future implementation

| Area | Required deterministic proof |
| --- | --- |
| Receipt gates | Missing, forged, equal-but-not-identical, reconstructed, wrong-lineage, or rebound resource/inventory receipt denies before capture. |
| Capture count | Exactly one capture; a queued second frame is untouched. Capture failure calls neither evaluator and causes no retry. |
| Payload ownership | Reuse or mutation of a backend buffer cannot alter the owned bytes, digest, or either evaluator input. |
| Digest | One SHA-256 computation over the exact owned `bytes`; no caller digest seam. |
| Same object | Both evaluators receive the identical `Frame`. Equal-but-foreign frame refs or payloads fail. |
| Provenance | Wrong frame ID, timestamp, cycle, format, geometry, digest, or capture configuration denies atomically. |
| Freshness | Stale, future, NaN, infinity, bool-as-number, and invalid timing deny atomically. |
| Resource identity | Wrong detector/version/profile/schema/location/order/count/type denies. |
| Resource view | Rejected production trust, any UNKNOWN, wrong region, depleted-with-region, or diagnostic-only evidence denies. |
| Resource positive | Supported all-depleted exposes no regions; supported mixed state exposes only exact available regions in packaged order. |
| Inventory identity | Wrong detector/profile/configuration/layout/validation roots deny. |
| Inventory state | UNKNOWN, wrong tab, obstruction, below `0.8`, invalid count, or non-28-slot evidence denies. |
| Combined positive | Both exact receipts plus one same-frame supported resource result and known inventory yield perception data only. |
| Drift | Every frame in the 36-frame real drift corpus remains atomic denial with zero regions or targets. |
| Retry policy | Unsupported/UNKNOWN/exception performs no recapture, camera recovery, or fallback. |
| Non-authority | No controller, `WorldState`, interaction, navigation, banking, or input dependency; the result cannot select or execute. |
| Concurrency | Concurrent invocations share no cached frame, digest, receipt, evidence, or result. |
| Snapshot/TOCTOU | Mutation or replacement after validation cannot change the immutable projection. |

The adversarial set must also cover caller keyword injection of frames, hashes,
evidence, receipts, approval flags, thresholds, regions, or targets; duck-typed
or tuple-reconstructed receipts; hostile subclasses with explosive equality or
iteration; coordinated forged resource and inventory provenance that agrees
with itself but not the owned frame; loader/evaluator rebinding; duplicate or
reordered resource IDs; region substitution; one evaluator succeeding while
the other fails; replay of a prior cycle; and attempted diagnostic-scene
promotion.

## Reuse and unresolved dependencies

The future implementation should reuse `Frame`, `FrameRef`,
`PerceptionCycleProvenance`, the packaged resource IDs/regions, unchanged
production resource evaluation, and the no-argument source-owned receipt
accessors. A4 remains the executable negative-semantics oracle; its diagnostic
shape-valid flags can never become a positive bypass.

Do not add a second approval registry, activation switch, receipt validator, or
denial abstraction. Do not invent an inventory receipt, inventory production
evaluator identity, or approval root in the resource lane. Those are C-owned
release dependencies.

The generic capture frame does not itself prove live DPI, top-level window
ownership, or renderer identity. Those remain explicit reviewed capture/source
dependencies for the future join; caller strings cannot fill the gap.

Implementation remains blocked until both perception release gates close and a
separate lead-reviewed activation task authorizes a positive assembly schema.
