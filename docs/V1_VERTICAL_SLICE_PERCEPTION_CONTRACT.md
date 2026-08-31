# Constrained v1 perception boundary

## Status

This document freezes the typed boundary needed by the first supported-view
mining vertical slice. It is a design-and-test contract only. It does not
activate `WorldState`, the controller, navigation, banking, or input.

Implementation remains blocked until both production perception release gates
close:

- the approved inventory detector must publish known empty/partial/full state
  on reviewed real evidence; and
- the approved Varrock East resource detector must be integrated with its
  production scene gate and strict observation adapter.

The current controller is not safe to activate: `InventoryState(None)` is not
full, so the scaffold can continue toward a resource action when inventory
truth is unknown.

Navigation and banking are independently blocked as well. A documented route,
checkpoint, or detector proposal is not activation authority for movement,
bank opening, or deposit input. Those phases still require their own reviewed
production proofs and fail-closed integration before any input path is enabled.

## Proposed immutable contracts

`PerceptionIdentity` binds a production observation to all authority-bearing
configuration:

```text
PerceptionIdentity
  detector_id: str
  detector_version: str
  profile_id: str
  configuration_id: str
  fixture_or_reference_sha256: str
```

The supported capture path must also have one exact approved environment
identity rather than a set of runtime assertions assembled by a caller:

```text
CaptureEnvironmentIdentity
  capture_build_sha: str
  capture_configuration_id: str
  runelite_build: str
  physical_frame_size: tuple[int, int]
  pixel_format: str
  windows_dpi: int
  windows_scaling_percent: int
  client_mode: str
  theme: str
  renderer: str
  window_class: str
```

The concrete approved values are not defined by this document. Production
activation would require an immutable, repository-owned support record that
binds this identity, both perception identities, the supported location, and
the reviewed evidence hashes under one activation ID. Runtime flags, a local
profile, a report that merely contains matching strings, or successful detector
construction cannot create that authority. No such activation record is
approved by this design document.

`SceneProof` carries production scene evidence only:

```text
SceneProof
  supported: bool
  matched_landmarks: int
  required_landmarks: int
  matched_macro_zones: tuple[str, ...]
  required_macro_zones: tuple[str, ...]
  reason: str | None
```

For the Varrock East v1 profile, a supported proof requires at least 5/6
frozen world landmarks and every required macro zone. Diagnostic registration,
candidate-rock pixels, fixed UI, and masked pixels cannot populate this type.

`V1PerceptionSnapshot` is the only proposed handoff to later state/controller
work:

```text
V1PerceptionSnapshot
  frame: FrameRef
  capture_environment: CaptureEnvironmentIdentity
  activation_id: str
  location_id: str
  scene: SceneProof
  resource_identity: PerceptionIdentity
  inventory_identity: PerceptionIdentity
  resources: tuple[ResourceState, ...]
  inventory: InventoryState
  ready: bool
  blocking_reasons: tuple[str, ...]
```

The factory, rather than consumers, owns validation. It must require:

- one exact fresh `FrameRef` shared by scene, every resource, and inventory;
- freshness computed from `FrameRef.captured_monotonic_s` (the snapshot must
  not carry a second timestamp that can disagree with the frame);
- an exact match to the repo-owned activation record and its approved capture
  environment identity;
- exact approved detector/profile/configuration/reference identities;
- the expected supported location;
- a production-supported `SceneProof`;
- exactly the four expected resource IDs, each present once;
- no interaction region on depleted or uncertain resources;
- known inventory occupancy with production publication confidence; and
- a configured freshness limit checked against a monotonic clock.

If any requirement fails, `ready` is false, `resources` is empty, every exposed
target collection is empty, and `blocking_reasons` is non-empty. Raw diagnostic
observations may be retained outside this authority-bearing snapshot, but
consumers may not reconstruct a resource or target from them.

## Action and transition separation

Future integration must use distinct types:

```text
InteractionIntent
  intent_id: str
  kind: str
  source_frame: FrameRef
  activation_id: str
  expires_at_monotonic_s: float
  target: object

AttemptedActionEvidence
  action_id: str
  kind: str
  source_frame: FrameRef
  attempted_at_monotonic_s: float
  input_receipt: object

VerifiedTransition
  action_id: str
  before_frame: FrameRef
  after_frame: FrameRef
  outcome: verified-success | verified-failure | timed-out
  evidence: object
```

An interaction intent may be created only from a ready snapshot. Dispatch must
reject it if its `source_frame` is no longer the current authoritative frame,
its activation identity no longer matches, or the monotonic clock has reached
its expiry. A later capture, navigation transition, or readiness loss cannot
extend or silently rebase an intent.

An input receipt proves only that an action was attempted. Mining success,
arrival, bank opening, deposit, and return all require a later fresh production
observation.

## Vertical-slice phase preconditions

1. **Mine:** fresh ready supported-mine snapshot, known non-full inventory, and
   at least one definitive available iron region.
2. **Mine to bank:** known full inventory plus the reviewed fixed-route start
   checkpoint and a future independently approved navigation activation. This
   is currently blocked.
3. **Deposit:** supported bank checkpoint, future independently approved bank
   activation, and independently proven bank-open state; success requires a
   later known inventory count of zero. This is currently blocked.
4. **Bank to mine:** verified empty inventory, the reviewed fixed-route return
   checkpoint, and a future independently approved navigation activation. This
   is currently blocked.
5. **Repeat:** a new fresh ready supported-mine snapshot. Prior scene/resource
   truth cannot be carried across navigation.

Any unknown, stale, mixed-frame, wrong-identity, incomplete, or unsupported
input produces no interaction intent and enters an explicit reacquire/stop
path.

## Required deterministic tests before activation

- unknown inventory exposes zero mining targets;
- a full inventory exposes zero mining targets;
- unsupported scene, stale evidence, mixed frame IDs, wrong identities, or an
  incomplete/duplicate resource ensemble exposes zero targets;
- uncertain and depleted resources never expose interaction regions;
- an available resource region is exposed only from a ready snapshot;
- attempted input never implies a successful transition;
- deposit verification requires bank-open proof and a later known zero-slot
  inventory;
- route completion alone cannot prove deposit or supported mine reacquisition;
- the next mining cycle requires a fresh supported-mine snapshot; and
- replay of every real perception failure remains fail closed.

These tests belong with the future pure snapshot factory. Controller/state
tests must not be enabled by weakening either perception release gate.
