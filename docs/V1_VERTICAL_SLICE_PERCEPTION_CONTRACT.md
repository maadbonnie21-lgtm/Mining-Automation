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
  captured_monotonic_s: float
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
- exact approved detector/profile/configuration/reference identities;
- the expected supported location;
- a production-supported `SceneProof`;
- exactly the four expected resource IDs, each present once;
- no interaction region on depleted or uncertain resources;
- known inventory occupancy with production publication confidence; and
- a configured freshness limit checked against a monotonic clock.

If any requirement fails, `ready` is false, every exposed target collection is
empty, and `blocking_reasons` is non-empty. Consumers may not reconstruct a
target from raw observation evidence.

## Action and transition separation

Future integration must use distinct types:

```text
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

An input receipt proves only that an action was attempted. Mining success,
arrival, bank opening, deposit, and return all require a later fresh production
observation.

## Vertical-slice phase preconditions

1. **Mine:** fresh ready supported-mine snapshot, known non-full inventory, and
   at least one definitive available iron region.
2. **Mine to bank:** known full inventory plus the reviewed fixed-route start
   checkpoint.
3. **Deposit:** supported bank checkpoint and independently proven bank-open
   state; success requires a later known inventory count of zero.
4. **Bank to mine:** verified empty inventory and the reviewed fixed-route
   return checkpoint.
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
