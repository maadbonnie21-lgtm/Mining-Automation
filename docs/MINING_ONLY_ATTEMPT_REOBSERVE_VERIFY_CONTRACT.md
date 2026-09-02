# Mining-only attempt, reobserve, and verify contract

Status: **contract and replay-matrix preparation only; no runtime or input authority**

This document fixes the smallest future mining-only loop boundary after both
perception releases and the positive same-cycle assembly exist. It does not
implement that assembly, issue an attempt receipt, call an interaction backend,
activate `WorldState` or the controller, or authorize a click.

The loop remains limited to the exact reviewed Varrock East view. Unsupported,
stale, mixed, unreleased, or UNKNOWN perception means zero proposed targets and
STOP. There is no camera recovery, navigation, banking, respawn wait, retry, or
fallback in this mining-only boundary.

## Required input

One iteration consumes exactly one immutable, source-owned, released
same-cycle perception snapshot from the future A7 positive assembly. The
snapshot must bind:

- one internally owned frame and exact payload SHA-256;
- one exact capture/source session and cycle;
- frame ID, capture time, geometry, pixel format, and capture configuration;
- the exact resource and inventory receipt IDs and canonical record digests;
- exact current detector/profile/schema/location/build/environment identities;
- production-supported resource scene evidence with every resource definitive;
- a known 28-slot inventory observation at or above its released publication
  floor; and
- observational available-iron regions equal to the packaged profile regions.

The loop never accepts loose observations, caller frames, caller digests,
caller-selected regions, approval flags, thresholds, reconstructed receipts,
or diagnostic scene evidence.

The future source constants are fixed at
`MAX_PRE_ATTEMPT_SNAPSHOT_AGE_S = 1.0` and
`MAX_MINING_ATTEMPTS_PER_LOOP = 28`. Neither value is a caller parameter. A
source-owned monotonic decision time must prove an inclusive snapshot age in
`[0.0, 1.0]` seconds before a proposal is published. Immediately before the
one event, the separately reviewed interaction boundary must record a
source-owned arm time and recheck the same inclusive age bound, exact proposal,
receipt roots, capture session, cycle, build, and environment. If either check
is stale, future-dated, non-finite, or changed, STOP with no event; do not
recapture or retry inside the iteration.

## Pre-attempt decision

The pure proposal step is deterministic:

1. If inventory is full, return `COMPLETE` with no proposal.
2. If inventory is UNKNOWN, the supported view is not proven, any resource is
   UNKNOWN, either receipt is missing or stale, or provenance is mixed, return
   `STOP` with zero targets.
3. If no profiled iron is AVAILABLE, return `STOP`; this boundary does not wait
   for respawn or recapture.
4. Otherwise choose exactly the first AVAILABLE resource in
   `VARROCK_EAST_IRON_RESOURCE_IDS` packaged order and copy only its exact
   packaged candidate region into one proposal.

The proposal is perception/workflow data, not an `ActionIntent`. It has no
click, input, controller, execution-authority, or execution-method field. Its
existence grants none. Confidence ranking, caller choice, nearest-target
heuristics, alternative regions, and multiple simultaneous proposals are
forbidden.

## Attempt receipt boundary

A separately reviewed interaction boundary may later consume the proposal and
return one opaque, module-owned `MiningAttemptReceipt`. The receipt proves only
that exactly one bounded event was **attempted**. It does not prove that the
rock was clicked, mined, depleted, or that inventory changed.

The receipt must bind:

- loop ID and exact attempt ordinal;
- the prior verified-chain digest;
- pre-attempt frame reference, payload SHA-256, cycle ID, pixel format,
  capture-configuration ID, and capture-session identity;
- both perception receipt IDs and record digests;
- selected resource ID and exact packaged region;
- executor ID/version and exact event count `1`;
- source-owned decision/arm times and event ordinals for proposal, attempt
  start, and attempt completion; and
- a canonical receipt digest.

It must be nominal, immutable, non-serializable as authority, and single-use.
Missing, forged, reconstructed, equal-but-foreign, subclassed, replayed,
duplicate, wrong-loop, wrong-ordinal, wrong-target, wrong-region, wrong-root,
or multi-event receipts cause STOP. The future workflow may not accept a
caller mapping or parse a receipt from JSON.

## Mandatory post-attempt observation

After one valid attempt receipt, the source-owned workflow captures exactly one
post-attempt frame and reruns the complete released same-cycle resource and
inventory assembly. A queued second frame remains untouched on every result.

“Strictly newer” means all of the following:

- the same capture/source session;
- `post.frame_id > pre.frame_id`;
- a distinct post cycle ID with a strictly greater source-owned cycle ordinal;
- source-owned event order proves
  `pre observation < attempt receipt < post capture`; and
- the post capture time is nondecreasing from the pre capture and is not before
  attempt completion.

Consecutive monotonic timestamps may be equal, so timestamp equality is not a
freshness failure when frame ID and event order advance. A different payload
digest is not required: a genuinely fresh unchanged frame may be byte-identical.
Such a frame is fresh evidence, but it proves no progress and therefore causes
STOP.

The post snapshot must independently satisfy every release, supported-view,
same-frame, same-cycle, identity, environment, confidence, and definitive-state
rule. Its resource/inventory receipt IDs and record digests must exactly equal
the roots in the attempt receipt. Its detector, profile, schema, location,
capture configuration, source build, backend, renderer, window identity, DPI,
geometry, and pixel format must exactly equal the frozen pre/attempt lineage;
only the new frame/cycle/time and observed resource/inventory state may differ.
The prior snapshot or diagnostic evidence cannot fill a missing post field.

## Verification and continuation

The result is `OBSERVED_PROGRESS_CONTINUE` only when the fresh post snapshot
proves at least one of:

- the selected resource changed from AVAILABLE to DEPLETED; or
- known inventory occupancy increased by exactly one.

If both occur, the attempt is still one verified step. An unchanged selected
resource with an exact `+1` inventory count is accepted as observed inventory
progress, not inferred depletion. A selected-resource depletion with unchanged
inventory is accepted only as observed scene progress; it grants no item claim.

STOP on no change, inventory decrease, an occupancy jump greater than one,
UNKNOWN evidence, unsupported view, a stale/mixed post snapshot, invalid target
transition, receipt mismatch, or contradictory provenance. Do not retry the
same attempt or consume another frame in search of success.

If the post inventory count is 28, return `COMPLETE` with no proposal. Otherwise
the exact verified post snapshot and verification-chain digest become the sole
allowed pre-state for the next ordinal. A prior snapshot, merely equal snapshot,
or concurrently produced snapshot cannot be substituted.

The literal source-owned ceiling is 28 attempted receipts per loop. Every
issued receipt consumes its ordinal, including depletion-only progress and an
attempt whose post evidence causes STOP. Ordinals are never reused. Reaching
28 without full inventory causes STOP; the ceiling is not a retry budget and
has no caller override. There is no waiting for depleted rocks to respawn and
no automatic camera, navigation, banking, or recovery transition.

## Replay transcript format

The current single-frame perception replay schema is not a temporal authority
format. Future deterministic tests must use a separate versioned, explicitly
synthetic transcript format for:

1. released pre-snapshot;
2. proposal;
3. opaque attempt receipt projection;
4. released post-snapshot; and
5. verification result and next-chain digest.

Transcript fixtures are test data only. They cannot mint either perception
receipt, an interaction receipt, a positive production snapshot, or runtime
authority. Validation data cannot silently become calibration or approval.

## Required deterministic replay matrix

| Area | Required proof |
| --- | --- |
| Selection | One first-AVAILABLE resource in packaged order; multiple available resources still yield one proposal. |
| Pre-state denial | Full inventory completes without proposal; UNKNOWN inventory, unsupported view, any UNKNOWN resource, malformed region, all depleted, wrong identity, missing receipt, age over the inclusive 1.0-second bound, or stale provenance yields STOP and zero targets. |
| Receipt | Missing, forged, reconstructed, replayed, duplicate, cross-loop, wrong-ordinal, wrong-target, wrong-region, wrong-root, or multi-event receipt yields STOP. |
| Freshness | Stale/future/non-finite decision or arm time, same/lower frame ID, reused/non-advancing cycle ID/ordinal, foreign capture session, older timestamp, out-of-order event, changed detector/profile/config/build/environment lineage, mixed release roots, or stale post evidence yields STOP. |
| Equal timestamp | Higher frame ID plus valid event order and equal monotonic time is fresh. |
| Identical pixels | Fresh byte-identical frame with no state change yields unverified STOP, not a retry. |
| Progress | Target depletion only, exact `+1` inventory only, and both changes verify one attempted step. |
| Contradiction | Unchanged target/count, inventory decrease, jump greater than one, invalid resource transition, or post UNKNOWN/unsupported evidence yields STOP. |
| Completion | Post occupancy 28 completes with no next proposal; otherwise only the exact verified post snapshot can become the next pre-snapshot. |
| Consumption | Failure leaves every queued transcript step after the one post frame unconsumed. |
| Isolation | Prior-chain replay, duplicate receipt, cross-loop substitution, concurrency, buffer mutation, and snapshot TOCTOU cannot mix iterations. |
| Bounds | Literal source-owned ceiling 28, no caller override, and every receipt consumes one non-reusable ordinal; there is no retry, respawn wait, camera recovery, navigation, or fallback. |
| Non-authority | No controller, `WorldState`, live interaction implementation, click authority, input authority, or execution method is imported or exposed. |

The drift corpus remains a required regression: all 36 real unsupported drift
frames must produce atomic same-cycle denial, zero proposals, and zero targets.

## Dependencies and future source scope

The future implementation may reuse `Frame`, `FrameRef`, `PixelFormat`,
`ResourceState`, `InventoryState`, `VARROCK_EAST_IRON_RESOURCE_IDS`, and the
packaged profile regions. It must consume the future released A7 owned-cycle
carrier and exact source-owned resource/inventory receipt accessors.

Do not use the current generic `MiningController`, `WorldState`, `ActionIntent`,
or `ActionResult` as trust roots. They do not carry the required release,
frame, cycle, capture-session, event-order, or receipt provenance. The eventual
pure transition logic belongs in a new workflow-owned module outside
`perception`; the existing deny-only perception contracts remain unchanged.

Runtime implementation remains blocked on:

1. real resource C1/C2 closure and nominal resource receipt issuance;
2. independently validated inventory release and nominal inventory receipt;
3. the reviewed positive A7 same-cycle assembly;
4. Issue #14's typed perception-fusion boundary; and
5. a separately reviewed one-event interaction receipt issuer and input gate.

Until all five exist, this contract grants no positive runtime path and no
input authority.
