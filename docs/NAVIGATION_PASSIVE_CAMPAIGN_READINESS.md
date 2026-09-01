# Navigation passive campaign readiness

## Scope and authority

This milestone is a display-free architecture rehearsal. It adds no RuneLite capture run, route
geometry, coordinates, route executor, camera behavior, bank adapter, mining adapter, or physical
input. Every fixture and contract remains synthetic and nonactivating.

`ZERO LIVE ROUTE EVIDENCE / NO LIVE INPUT`

`NO LIVE NAVIGATION / NO WORLDSTATE / NO CONTROLLER ACTIVATION`

The supported direction names remain two separate contracts: `mine_to_bank` and `bank_to_mine`.
A passive session owns exactly one explicit direction and exact route-plan version. There is no
reverse-plan helper and no inference from an opposite-direction campaign.

## Preregistered provenance

Evidence schema v2 extends `RouteEvidenceCampaignPlan` with prerequisites that were previously
only case-local:

- exact passive capture build ID, version, and content SHA-256;
- required frame width and height; and
- required pixel format.

The existing plan pins remain mandatory: exact route and version, detector and checkpoint-profile
identity, capture source and session, capture configuration, environment manifest, support
envelope, operator, case sequence, and direction. Capture build is repeated in every owned case
and detector report. Geometry and pixel format are checked against every exact `FrameRef`. The
caller-owned load expectation pins all of these fields independently.

Changing any build, source/session, route version, geometry, pixel format, configuration,
environment, or support-envelope field changes the canonical package graph. The strict loader
rejects old v1 manifests and foreign or recomputed graphs that differ from the caller's exact v2
expectation.

## Passive acquisition lifecycle

`navigation.passive_campaign.PassiveCampaignSequencer` owns one mutable append-only head around
the accepted route-evidence types. Returned progress values are inspection-only snapshots; no
transition accepts a caller-supplied snapshot. Copying, deep-copying, and pickling the sequencer
are disabled, so a retained pre-failure or pre-finalization snapshot cannot fork that lineage:

```text
READY_FOR_REQUEST
  -> AWAITING_CAPTURE
  -> READY_FOR_REQUEST ...
  -> COMPLETE
  -> FINALIZED

any failure/interruption/timeout -> STOPPED (absorbing)
```

The source contract is intentionally one attempt per preregistered case in v1:

1. Construction binds an already-preregistered campaign plan, one capture-only source, one guarded
   checkpoint detector, and one trusted side-effect-free monotonic clock. The source's snapshotted
   identity contains the guarded checkpoint source plus the capture build, configuration,
   environment, and support-envelope digests. Construction rejects any mismatched detector,
   profile, source/session, geometry, pixel format, route checkpoint order, or fixed-false authority.
   The clock contract itself has fixed-false navigation and input authority.
2. `request_capture` derives the next case, ordinal, campaign digest, and capture session from the
   sequencer head. The operator supplies only its exact identity, a globally unused request ID in
   that lineage, and an acknowledgement time.
3. `capture` invokes the capture-only source exactly once. The source returns the request ID,
   capture ID, UTC time, exact immutable `Frame`, and its own full identity. The sequencer snapshots
   the issued request, source, frame, detector contract, and detector result; revalidates them after
   callbacks; rehashes the bytes; and runs `run_checkpoint_detector` internally. The bound clock is
   sampled before capture, after the source, and after detector/provenance checks. A blocked source
   or detector cannot be recorded with an earlier caller-asserted time. Callers cannot inject a
   prebuilt `CheckpointEvidence` or relabel detector output as source-owned output.
4. The detector result is retained whether it is `MATCHED`, `UNKNOWN`, or `AMBIGUOUS`. It cannot
   control inclusion, retry, checkpoint truth, review truth, or stage advancement.
5. Every successful capture is appended in exact plan order. Capture IDs, request IDs, frame
   identity, monotonic time, UTC time, and payload digest must remain unique and strictly ordered.
6. Every case embeds a fixed-false `RouteEvidenceAcquisitionBinding`: request/capture IDs, exact
   source-identity digest, acknowledgement/expiry/frame/record times, and the prior owned-case
   digest. The first link starts at the campaign-plan digest; every later link commits to the full
   prior owned case, including its exact frame and detector-report artifacts; the finalized package
   publishes the final owned-case digest as the exact chain head.
7. `finalize` re-snapshots and rehashes every retained request, owned case, raw frame, and report,
   then emits the existing `FinalizedRouteEvidencePackage` and exact artifact bytes only when every
   case completed once and the session never failed. Its strictly later monotonic finalization time
   is derived from the bound clock, not asserted by the caller, and is part of the canonical package.
8. Independent reviewer truth remains the existing `RouteEvidenceReview`, created after
   finalization and bound to the exact finalized-package SHA-256. The acquisition module has no
   review constructor or review-file writer. The contract enforces distinct normalized operator and
   reviewer identifiers plus strict post-finalization chronology; stronger human or cryptographic
   identity attestation remains an external prerequisite for any future real campaign.

The request's fixed-false fields make the operator boundary explicit:

- `operator_acknowledgement_is_reviewer_truth=false`;
- `checkpoint_truth_asserted=false`;
- `navigation_automation_enabled=false`;
- `camera_automation_enabled=false`;
- `mouse_input_enabled=false`; and
- `keyboard_input_enabled=false`.

## Failure and ownership matrix

| Condition | Passive session result |
| --- | --- |
| Wrong operator or second acknowledgement while capture is pending | `STOPPED` |
| Capture without a source-issued next-case request | `STOPPED` |
| Frame captured before or exactly at acknowledgement | `STOPPED` |
| Frame delivered after the fixed request timeout | `STOPPED` |
| Future frame or regressing event time | `STOPPED` |
| Foreign detector/profile/source/session or frame provenance | `STOPPED` |
| Issued request, source result, frame, detector result, or metadata changed during capture | `STOPPED` |
| Recursive capture/finalization/restart or bound adapter replacement | `STOPPED` or restart rejected |
| Mixed geometry or pixel format | `STOPPED` |
| Repeated capture ID, frame identity, or exact payload | `STOPPED` |
| Capture failure, timeout, or interruption | `STOPPED`, partial audit retained |
| Attempt to request another case after failure | terminal no-change |
| Malformed, partial, failed, backdated, or repeated finalization | latched stop or finalization error |
| Caller mutates a previously returned progress/finalization object | detached snapshot only; head unchanged |
| Same campaign ID or capture-session ID used for recovery | restart rejected |
| Opposite direction used for recovery | restart rejected |
| New externally preregistered campaign and capture session | explicit same-direction restart |

There is no overwrite, drop, replacement, retry, or adopt-existing-artifact operation within a
sequencer lineage. The finalizer generates deterministic owned paths and seals the exact bytes
retained by the session. Every public progress, retired-session, and finalization value is a deep
detached contract snapshot, so `object.__setattr__` against a caller-owned prior result cannot
rewrite the private acquisition head. A restart retains the stopped session and any exact pending
request as detached audit history while beginning with no carried request or capture authority.
Cross-process campaign uniqueness remains an external preregistration and
intake responsibility; this synthetic module does not claim a global durable lock.
A future filesystem acquisition tool must use exclusive creation, retain partial audits outside a
sealed review bundle, and write the finalized manifest last. This branch deliberately does not
implement that live tool or claim hostile concurrent filesystem acquisition is production-ready.

## Execution-readiness rehearsal

`navigation.offline_route_session.OfflineRouteSessionSequencer` adds an outer synthetic
route-session identity while retaining PR #43's `SyntheticStepAttemptReceipt` authority. It owns
one mutable head and delegates checkpoint, step-preparation, and receipt transitions to that
reducer.

The wrapper adds only:

- an explicit route-session ID and direction;
- deterministic caller-recorded checkpoint/step/attempt timeout events;
- deterministic interruption;
- detection of mid-session outer-session replacement; and
- an explicit restart contract requiring a globally new route-session ID and fresh
  checkpoint-source and attempt-source session IDs while preserving the exact route plan/version,
  navigation policy, checkpoint detector/profile/frame-source semantics, and attempt-source
  semantics.

It remains input-disabled and has no executor. Timeout, interruption, and session replacement call
the core `stop_route` transition, clearing actionable inner evidence and making both layers
absorbing. Recovery preserves a full lineage of used route, checkpoint-source, attempt-source, and
attempt IDs, preventing A-B-A reuse. Every caller-supplied session, observation, and receipt is
reconstructed as an exact owned contract graph before evaluation. Every public progress, result,
transition, and restart value is a deep detached snapshot, including fixed-false authority fields.
Route identities and ordered IDs require exact built-in strings and integers; all session and
causality times require exact built-in finite floats. Primitive-subclass comparison operators,
post-construction mutation, and caller result mutation therefore cannot rewrite chronology,
identity, confidence, input authority, or the private head. Public evaluation, interruption,
timeout, progress inspection, and restart calls are serialized around the complete evaluate-and-
commit window, so concurrent calls cannot fork a head or overwrite a committed STOP. A
caller-recorded timeout is only a deterministic fail-stop event: this harness does not read a
clock or independently prove that a real deadline elapsed. It never retries automatically.

| Offline rehearsal condition | Result |
| --- | --- |
| Wrong direction or mid-session route/source replacement | `STOPPED` or restart rejected |
| Route plan/version, policy, detector/profile/frame source, or attempt-source semantics change on restart | restart rejected |
| Reused route, capture, attempt-source session, or attempt ID | restart rejected or `STOPPED` |
| Mutated fixed-false authority field or non-exact primitive ingress | rejected before transition |
| Caller mutates a returned result or nested contract | detached snapshot only; head unchanged |
| Concurrent calls target the same head | serialized; later call sees the committed earlier head |
| Fresh same-contract recovery with all three session IDs globally unused | restart at departure with no carried evidence |

The replay and causality suite proves:

- a frame captured before or exactly at the receipt boundary cannot advance even when delivered
  later;
- missing, stale, and duplicate attempt outcomes stop;
- an interruption after an accepted receipt stops before the next checkpoint;
- a mid-route checkpoint skip stops;
- route-version or source-session replacement stops;
- old-session checkpoint evidence cannot enter a fresh session; and
- fresh recovery begins at departure with zero carried checkpoint or attempt authority;
- public mutations cannot forge arrival or enable input; and
- concurrent observations cannot fork or overwrite the single committed head.

## Endpoint boundary

The new campaign and execution-readiness sequencers are not re-exported by the root navigation
package. The closed `navigation.integration_boundary` exports nothing. Neither a passive finalization, a
synthetic verification report, nor an offline route-session result can enter banking, mining, or
another runtime state subsystem.

- Bank route arrival does not prove that the bank interface is open.
- Mine route arrival does not prove that a supported mining view is visible.
- A step proposal or attempt receipt does not prove movement.
- A synthetic package does not prove a real character location or route release.

## Future real evidence checklist

Nothing below has been collected. No checkpoint name here implies geometry, coordinates, or
production support.

### Mine to bank

- exact mine departure positive evidence;
- positive evidence for every ordered transit checkpoint;
- explicit bank-terminal arrival evidence;
- adjacent-location and visual-lookalike negatives for every checkpoint;
- obstruction, partial visibility, occlusion, and ambiguous outcomes;
- wrong-direction and wrong-location evidence;
- stale, repeated, and out-of-order frame cases;
- skipped/prior/unexpected checkpoint cases;
- missing, delayed, duplicate, and wrong-source attempt receipts;
- repeated complete traversals with exact post-attempt evidence for every step; and
- a preregistered endurance campaign with no continuation through uncertainty.

Bank-terminal arrival must be followed by separate fresh bank-interface evidence before any bank
workflow could become eligible.

### Bank to mine

- exact bank departure positive evidence, separate from bank-interface-open proof;
- positive evidence for every independently defined return transit checkpoint;
- explicit mine-terminal arrival evidence;
- adjacent-location and visual-lookalike negatives for every return checkpoint;
- obstruction, partial visibility, occlusion, and ambiguous outcomes;
- wrong-direction and wrong-location evidence, including mine-to-bank lookalikes;
- stale, repeated, and out-of-order frame cases;
- skipped/prior/unexpected checkpoint cases;
- missing, delayed, duplicate, and wrong-source attempt receipts;
- repeated complete return traversals with exact post-attempt evidence for every step; and
- a separately preregistered return-route endurance campaign.

Mine-terminal arrival must be followed by separate fresh supported-mining-view evidence before any
mining workflow could become eligible. Mine-to-bank evidence can never satisfy bank-to-mine.

## Remaining pre-live requirements

Before any future authorized real campaign, the lead must separately approve the exact real route
plans, detector/profile, support envelope, capture build/configuration, source target identity,
case matrix, filesystem acquisition tool, reviewer workflow, traversal counts, and endurance
criteria. The current `Frame` contract does not cryptographically attest a particular client
window; a future source-owned capture adapter must fail on target replacement and may not recover
or rediscover a different target inside one campaign. The bound monotonic clock is deliberately a
trusted side-effect-free timing dependency: it supplies chronology only and cannot establish source,
checkpoint, review, navigation, or input authority. A future real clock adapter therefore requires
separate implementation review and tests proving that reading it has no callbacks or side effects.
