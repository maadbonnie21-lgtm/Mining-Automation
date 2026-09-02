# Fixed-route checkpoint foundation

## Scope

This package is an offline architecture foundation for constrained-v1 fixed-route navigation. It
does not contain a supported Varrock route, route geometry, client coordinates, camera behavior,
banking behavior, or an input executor. The committed replay manifests use conspicuously synthetic
identifiers and are accepted by the loader only when marked
`synthetic_navigation_architecture_test_only`.

The two directions are independent contracts:

- `mine_to_bank`: a mine origin and bank destination;
- `bank_to_mine`: a bank origin and mine destination.

There is intentionally no route-reversal helper. Real evidence for one direction cannot establish
the other direction.

`NO LIVE NAVIGATION / NO WORLDSTATE / NO CONTROLLER ACTIVATION`

## Contracts

`RouteIdentity` binds an opaque route ID and version to an explicit `RouteDirection`.
`RoutePlan` contains two typed endpoints, an ordered tuple of `Checkpoint` values, and one
`RouteStep` for each adjacent checkpoint pair. Construction rejects wrong-direction endpoints,
duplicate identities, missing endpoints, incorrect checkpoint roles, gaps, branches, reversals,
skips, and disconnected steps. Plans have no coordinates or action targets.

`CheckpointObservation` binds a match result to all of the following:

- exact route ID, version, and direction;
- detector ID and version;
- frame-source and capture-session identity;
- declared frame geometry;
- a positive `FrameRef` with capture time;
- an explicit `UNKNOWN`, `MATCHED`, or `AMBIGUOUS` result;
- unique candidate checkpoint IDs and finite confidence.

Cardinality is structural: unknown observations have no candidates, matched observations have
exactly one, and ambiguous observations have at least two. The navigation reducer never converts
unknown or ambiguous evidence into a match.

`RouteProgress` is route-local workflow state. It does not extend or mutate `WorldState`. It tracks
the current checkpoint only while fresh evidence is active, the exact expected-next checkpoint,
the last accepted frame provenance for ordering, and a capture-time boundary for the next proof.

## Deterministic transitions

The pure reducer reads no clock. Every transition receives an explicit monotonic evaluation time.

1. `start_route` enters `AWAITING_CHECKPOINT`, expects the departure checkpoint, and sets the first
   evidence boundary to the run start time.
2. A fresh exact observation of the expected non-terminal checkpoint enters `READY_FOR_STEP`, with
   current and expected-next IDs plus the active observation.
3. `prepare_step` rechecks that observation's freshness, creates one immutable
   `OfflineStepProposal`, then clears current-location and active evidence. Progress enters
   `AWAITING_ATTEMPT_RECEIPT`; preparation alone cannot authorize another observation.
4. `record_step_attempt_receipt` accepts only the exact pending route/step/attempt identity from
   the configured synthetic receipt source. The receipt must bind the proposal's preparation
   boundary, be strictly later than preparation, not be future or stale at evaluation, and be new.
   The reducer retains the complete proposal-plus-receipt record for every step.
5. Only after a receipt is accepted does progress return to `AWAITING_CHECKPOINT`. The next proof
   must use a higher ordered frame captured strictly after the receipt boundary. A queued
   pre-attempt frame cannot advance the route.
6. A fresh exact observation of the explicit terminal arrival checkpoint enters `ARRIVED` and
   stores `ArrivalEvidence`. Merely exhausting steps cannot complete a route.
7. Any runtime rejection enters absorbing `STOPPED`, clears active location evidence, and emits no
   proposal. `ARRIVED` is also absorbing.

`OfflineStepProposal` is data only. Its `live_input_enabled` field is a non-init
`Literal[False]`. It has no coordinates, interaction region, mouse/keyboard payload, executor, or
controller hook. Nothing in the navigation package can send physical input.

Preparation time is not proof that a physical action occurred. `SyntheticStepAttemptReceipt` is a
future integration contract whose authority, movement-success, and live-input fields are immutable
false. It records only that a named synthetic attempt event occurred. This branch has no input path.

## Fail-closed matrix

| Condition | Result |
| --- | --- |
| Plan, source, session, or policy context changes mid-run | `STOPPED / CONTEXT_MISMATCH` |
| Wrong route ID | `STOPPED / ROUTE_ID_MISMATCH` |
| Route version changes | `STOPPED / ROUTE_VERSION_MISMATCH` |
| Wrong/reversed direction | `STOPPED / DIRECTION_MISMATCH` |
| Detector, source, session, or geometry provenance differs | `STOPPED / PROVENANCE_MISMATCH` |
| Frame capture time is in the future | `STOPPED / INVALID_FRAME_TIME` |
| Frame does not postdate route-start or accepted receipt boundary | `STOPPED / EVIDENCE_NOT_AFTER_BOUNDARY` |
| Frame age exceeds policy | `STOPPED / STALE_FRAME` |
| Frame ID repeats | `STOPPED / REPEATED_FRAME` |
| Frame ID or capture time regresses | `STOPPED / OUT_OF_ORDER_FRAME` |
| Transition evaluation time regresses | `STOPPED / OUT_OF_ORDER_EVALUATION` |
| Checkpoint is unknown | `STOPPED / UNKNOWN_CHECKPOINT` |
| Checkpoint is ambiguous | `STOPPED / AMBIGUOUS_CHECKPOINT` |
| Confidence is below policy | `STOPPED / LOW_CONFIDENCE` |
| A later checkpoint appears | `STOPPED / SKIPPED_CHECKPOINT` |
| A prior checkpoint appears | `STOPPED / OUT_OF_ORDER_CHECKPOINT` |
| A foreign checkpoint appears | `STOPPED / UNEXPECTED_CHECKPOINT` |
| Another observation arrives before active evidence is consumed | `STOPPED / STEP_EVIDENCE_NOT_CONSUMED` |
| Step preparation is requested without active evidence | `STOPPED / STEP_NOT_READY` |
| Observation or second preparation arrives while a receipt is pending | `STOPPED / ATTEMPT_RECEIPT_REQUIRED` |
| Receipt arrives when no proposal is pending | `STOPPED / ATTEMPT_RECEIPT_NOT_EXPECTED` |
| Receipt source, route, step, attempt, or preparation binding differs | `STOPPED` with the exact mismatch reason |
| Attempt ID or receipt identity is reused | `STOPPED / DUPLICATE_ATTEMPT_ID` or `DUPLICATE_ATTEMPT_RECEIPT` |
| Receipt is future, pre-preparation, delayed, or evaluated out of order | `STOPPED` with the exact temporal reason |
| Replay ends before its declared terminal state | replay failure; never implicit arrival |

Failure precedence is deterministic: route and source provenance are checked before checkpoint
content; impossible future time and frame ordering are checked before age and checkpoint content.

## Replay harness

`navigation.replay` loads strict UTF-8 JSON. It rejects duplicate keys, non-standard JSON numbers,
unknown or missing fields, wrong types, unsupported schema versions, non-synthetic fixture roles,
malformed linear plans, and decreasing event times. Explicit frame IDs and capture timestamps are
preserved; the loader does not normalize adversarial sequences.

Replay events are an observation, an offline step preparation, or a synthetic attempt receipt.
Each event declares the exact expected outcome, phase, current checkpoint, expected-next
checkpoint, failure reason, proposal identity, and receipt identity/boundaries as applicable. The
harness produces an immutable trace and deterministic text/JSON reports. A case must declare an
expected terminal phase of `ARRIVED` or `STOPPED`, so an incomplete route cannot pass as complete.

Run the committed architecture fixtures without a display:

```powershell
python tools/evaluate_navigation.py --manifest tests/fixtures/navigation/synthetic_mine_to_bank.json
python tools/evaluate_navigation.py --manifest tests/fixtures/navigation/synthetic_bank_to_mine.json
```

The tool exits `0` when every event and declared terminal phase match, `1` for a deterministic
expectation mismatch, and `2` for malformed input or report setup errors. Detached text and JSON
reports retain the synthetic fixture role, exact route ID/version/direction, and an explicit
live-navigation-disabled marker.

These fixtures test contracts and reducer behavior only. They are not evidence that any real mine,
bank, checkpoint, route, client configuration, or traversal is supported.

`navigation.offline_route_session` adds a single-head outer rehearsal identity. Explicit timeout,
interruption, or session replacement emits a real STOP in the underlying reducer, and recovery
requires a globally unused route-session ID plus fresh checkpoint-source and attempt-source
session IDs in that lineage. Recovery preserves the exact route plan/version, policy,
detector/profile/frame-source semantics, and attempt-source semantics. The sequencer owns exact
reconstructed ingress, returns deep detached snapshots, and serializes every complete public
transition so concurrent callers cannot fork the head or overwrite STOP. It has no automatic retry
or executor. See
[`NAVIGATION_PASSIVE_CAMPAIGN_READINESS.md`](NAVIGATION_PASSIVE_CAMPAIGN_READINESS.md).

## Two-direction synthetic rehearsal

`navigation.round_trip_rehearsal` composes one exact B1 `mine_to_bank` result and one exact B1
`bank_to_mine` result into a terminal, display-free report. Each result must reproduce its named
direction binding and retained source result exactly; swapping directions, route versions,
checkpoint plans, source/session provenance, or result lineages is an integrity error and produces
no report. Both named results are validated before either leg outcome is evaluated. When the
outbound leg stops, the return leg is omitted from the reported evaluation order, but its identity
and binding cannot be ignored or cross-slotted.

The caller must provide a `SyntheticRoundTripTimelineExpectation` that pins the exact B1 decision
digest plus both route-session IDs and both session-result digests. The two directions require
distinct route sessions. The contract asserts only one caller-owned synthetic numeric timeline;
`real_monotonic_clock_attested`, release authority, activation authority, and input authority are
all fixed false. Numeric ordering in this rehearsal is therefore not a claim about a real host
clock or real route capture.

Two independent `ARRIVED` results are not sufficient. The return leg's departure frame must be
captured strictly after the outbound arrival event was accepted. This prevents a queued bank
departure frame from being relabeled as evidence of an ordered handoff. The direction-specific
arrival and departure checkpoint IDs remain separate contracts; only their shared typed bank
endpoint is required to agree.

Durable evidence conformance, independent reviewer approval, and durable endpoint-arrival proof
are evaluated before synthetic session causality. Their failure emits the direction-specific
`*_EVIDENCE_NOT_APPROVED` reason even when the same session also stopped. An accepted but stopped
or incomplete outbound result emits `MINE_TO_BANK_NOT_ARRIVED`. A nonfresh return departure emits
`BANK_TO_MINE_DEPARTURE_NOT_FRESH`; a fresh stopped or incomplete return emits
`BANK_TO_MINE_NOT_ARRIVED`. It retains a proven bank handoff only when the exact return result has a
fresh completed departure attempt; without such an attempt, the handoff remains absent. Reports
retain the inner session and navigation failure reasons without relabeling them as success.

The decision, direction bindings, results, timeline, legs, handoff, and mine arrival retain strict
source anchors. Construction and serialization rebuild those anchors, so internal-token
replacement or post-construction mutation cannot create a different digest, checkpoint, session,
stop reason, chronology, or authority claim. Legs, handoff, and arrival are evaluator-owned nested
projections; only their enclosing report establishes the shared timeline, and no projection grants
standalone authority. The evaluator has no retry, restart, resume, controller, world-state, or input
method; retry count and every authority field remain fixed at zero/false.

The accepted bank boundary still records `bank_interface_open_proven=false`, and final mine
arrival still records `supported_mining_view_proven=false`. This rehearsal proves only synthetic
two-direction ordering and provenance composition. It is not route evidence and grants no release
or activation authority.

## Repeated synthetic fault and endurance packaging

`navigation.endurance_rehearsal` folds an exact caller-owned tuple of already-issued synthetic
round-trip reports. Its manifest preregisters every report digest, scenario, cycle number, terminal
state, and explicit recovery link, and requires at least two planned successful cycles. It is a
one-shot deterministic packager, not a route runner, retry loop, clock, or fault injector.

Every named direction in every traversal must retain a globally unique route-session ID,
checkpoint capture-session ID, attempt-source session ID, durable campaign/review identity,
package/review digest, physical evidence-root identity, and completed or pending attempt ID. This
includes the bound `bank_to_mine` result when an outbound STOP prevents that leg from being
evaluated. The route plan/version, detector/profile/build/configuration/environment, support
envelope, navigation policy, and stable source semantics cannot change between traversals.

Traversal terminal times must be strictly ordered. After the first traversal, the next outbound
departure frame must be strictly newer than the preceding traversal's effective terminal event.
A STOP remains in the ordered history and may be followed only by an immediately preregistered
same-cycle recovery traversal with fresh identities and a fresh departure boundary. Recovery is a
new complete B1 synthetic traversal; it does not resume a stopped physical leg, adopt an old
report, or authorize an automatic retry. A completed nested round trip with a nonfresh campaign
departure is still retained as a campaign-boundary STOP and requires the same explicit recovery.

The package retains each round-trip digest, direction order, outer session STOP reason, inner
navigation failure reason, terminal timing, checkpoint/attempt history that the B1 result retains,
and whether each named direction was actually evaluated. A rejected attempt that the underlying
B1 terminal result does not retain cannot be reconstructed or claimed by this layer. Endpoint
review denial remains a terminal failure until a separately bound fresh B1 evidence lineage is
supplied.

The manifest, nested reports, folded history, canonical bytes, and authority fields are
source-revalidated when serialized. History removal/reordering, report splicing, identity reuse,
contract drift, source mutation, or authority mutation therefore produces no package. Real-clock
attestation, real endurance satisfaction, bank-interface proof, supported-mining-view proof,
release eligibility, WorldState/controller activation, live navigation, and input authority all
remain fixed false.

## Endpoint proof boundary

Route arrival proves only a fresh match of that direction's terminal checkpoint.

- Mine arrival does not prove that the supported mining view is visible or usable.
- Bank arrival does not prove that the bank interface is open.
- A prepared step or accepted attempt receipt does not prove movement or checkpoint arrival.

`ArrivalEvidence` exposes both downstream claims as hard-coded false values. Separate future
perception and state evidence must establish them.

## Requirements for future real-route evidence

No item in this section is implemented or claimed by the synthetic fixtures. Each route direction
will require its own reviewed evidence package before production activation:

- a distinct immutable route identity, version, and artifact digest;
- independently defined departure, transit, and arrival checkpoint semantics;
- ordered real-client frames with raw artifact hashes and exact `FrameRef` provenance;
- exact detector/profile, capture source/session, geometry, pixel format, DPI, renderer, client,
  window, and display configuration provenance;
- positive evidence across the declared support envelope and negative evidence for adjacent,
  lookalike, reversed-route, ambiguous, occluded, stale, repeated, and out-of-order cases;
- explicit post-step evidence for every eventual physical step, never dead reckoning;
- repeated complete traversals, fault injection, regression replay, and endurance coverage;
- explicit terminal-arrival evidence plus separate mine-view and bank-interface proof;
- independent review and invalidation whenever checkpoint or route semantics change.

The future evidence review must not infer `bank_to_mine` from a reversed `mine_to_bank` sequence,
relabel mutable artifacts, or promote remembered coordinates into production truth.
