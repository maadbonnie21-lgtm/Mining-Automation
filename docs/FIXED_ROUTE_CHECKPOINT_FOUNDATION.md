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
   `OfflineStepProposal`, then clears current-location and active evidence. Progress returns to
   `AWAITING_CHECKPOINT` and moves the evidence boundary to the preparation time.
4. In this offline reducer, only a higher ordered frame captured strictly after that preparation
   boundary can prove the next checkpoint. A queued pre-proposal frame cannot advance the route.
5. A fresh exact observation of the explicit terminal arrival checkpoint enters `ARRIVED` and
   stores `ArrivalEvidence`. Merely exhausting steps cannot complete a route.
6. Any runtime rejection enters absorbing `STOPPED`, clears active location evidence, and emits no
   proposal. `ARRIVED` is also absorbing.

`OfflineStepProposal` is data only. Its `live_input_enabled` field is a non-init
`Literal[False]`. It has no coordinates, interaction region, mouse/keyboard payload, executor, or
controller hook. Nothing in the navigation package can send physical input.

Preparation time is not future proof that a physical action occurred. A later live integration
must establish a new boundary at or after each actual input attempt and require the next checkpoint
frame to be captured strictly after that post-attempt boundary. This branch has no attempt event and
no input path.

## Fail-closed matrix

| Condition | Result |
| --- | --- |
| Plan, source, session, or policy context changes mid-run | `STOPPED / CONTEXT_MISMATCH` |
| Wrong route ID | `STOPPED / ROUTE_ID_MISMATCH` |
| Route version changes | `STOPPED / ROUTE_VERSION_MISMATCH` |
| Wrong/reversed direction | `STOPPED / DIRECTION_MISMATCH` |
| Detector, source, session, or geometry provenance differs | `STOPPED / PROVENANCE_MISMATCH` |
| Frame capture time is in the future | `STOPPED / INVALID_FRAME_TIME` |
| Frame does not postdate route-start or prepared-step boundary | `STOPPED / EVIDENCE_NOT_AFTER_BOUNDARY` |
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
| Replay ends before its declared terminal state | replay failure; never implicit arrival |

Failure precedence is deterministic: route and source provenance are checked before checkpoint
content; impossible future time and frame ordering are checked before age and checkpoint content.

## Replay harness

`navigation.replay` loads strict UTF-8 JSON. It rejects duplicate keys, non-standard JSON numbers,
unknown or missing fields, wrong types, unsupported schema versions, non-synthetic fixture roles,
malformed linear plans, and decreasing event times. Explicit frame IDs and capture timestamps are
preserved; the loader does not normalize adversarial sequences.

Replay events are either an observation or an offline step preparation. Each event declares the
exact expected outcome, phase, current checkpoint, expected-next checkpoint, failure reason, and
proposed step. The harness produces an immutable trace and deterministic text/JSON reports. A case
must declare an expected terminal phase of `ARRIVED` or `STOPPED`, so an incomplete route cannot
pass as complete.

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

## Endpoint proof boundary

Route arrival proves only a fresh match of that direction's terminal checkpoint.

- Mine arrival does not prove that the supported mining view is visible or usable.
- Bank arrival does not prove that the bank interface is open.
- A prepared step does not prove movement or checkpoint arrival.

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
