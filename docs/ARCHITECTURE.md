# Architecture

## Architectural principle

The application is a stateful closed-loop system. Pixels are observations, not decisions. User actions occur only through validated workflow logic and are followed by observation and verification.

## Production layers

### `capture`
Acquires frames from the supported game/client surface and attaches timestamps/frame identity. No game decisions live here.

### `perception`
Produces typed observations from frames: resources, availability/depletion, inventory occupancy, landmarks, supported UI/bank state, and confidence/evidence.

### `state`
Fuses observations over time into `WorldState`. Owns confidence, uncertainty, expected observations, and durable workflow facts.

### `controller`
Selects objectives/actions from `WorldState`. It never treats an attempted action as a verified outcome.

### `interaction`
Executes bounded pointer/input actions against validated interaction regions. Interaction implementation is separated from decision policy.

### `navigation`
Maintains route/checkpoint progress, landmark localization, confidence, and reacquisition/recovery decisions.

### `banking`
Owns the bank workflow state machine: localize → interact → verify opened → deposit → verify inventory → exit.

### `scheduling`
Owns active/inactive routine segments, pause/resume semantics, clocks, transitions, and reacquisition requirements.

### `recovery`
Owns explicit recovery policies and escalation. Low-confidence state is never silently converted into progress.

### `diagnostics`
Structured event logging, state snapshots, expected-vs-observed outcomes, failure evidence, replay metadata, and regression-case export.

### `knowledge`
Loads version-controlled structured information about supported ores, mines, banks, landmarks, routes, requirements, and support status.

### `gui`
Professional desktop interface. Talks to application services/state contracts rather than embedding perception or controller logic.

## Core contracts

### Observation
A time-bounded perception result with:
- frame id/timestamp
- observation type
- evidence
- confidence
- source/detector version

### WorldState
Canonical structured estimate containing:
- selected session configuration
- location estimate/confidence
- resources and states
- inventory state
- current workflow/objective
- navigation/bank/UI state
- schedule state
- expected next observation
- recovery state
- timestamps/staleness

### ActionIntent
What the controller wants executed, including target/region, preconditions, timeout, and expected evidence of success/failure.

### ActionResult
Records attempted execution separately from verified outcome.

### RecoveryDecision
Represents retry/reacquire/pause/abort escalation with reason and evidence.

## State machine boundaries

High-level session states should remain explicit, such as:
- `IDLE`
- `ACQUIRING`
- `MINING`
- `NAVIGATING_TO_BANK`
- `BANKING`
- `NAVIGATING_TO_MINE`
- `BREAK`
- `RECOVERING`
- `PAUSED`
- `STOPPING`
- `STOPPED`
- `ERROR`

Sub-workflows may use their own states but must expose deterministic transitions and diagnostic reasons.

## Verification rule

An input event only means `attempted`. Success requires later evidence from perception/state. Timeouts produce a failure/recovery path, not inferred success.

The offline constrained-v1 perception authority boundary is documented in
[`V1_VERTICAL_SLICE_PERCEPTION_CONTRACT.md`](V1_VERTICAL_SLICE_PERCEPTION_CONTRACT.md).
It is a deny-only integration contract and does not activate `WorldState`, the
controller, navigation, banking, or input.

The future perception-only owned-frame join and its required regression matrix
are specified in
[`FIRST_MINING_SLICE_SAME_CYCLE_ASSEMBLY_DESIGN.md`](FIRST_MINING_SLICE_SAME_CYCLE_ASSEMBLY_DESIGN.md).
That design remains non-executable until both perception release lineages are
independently approved; it grants no controller or input authority.

The exact source transition from reviewed resource evidence to a nominal
receipt, then to the future same-cycle join and Issue #14, is frozen in
[`RESOURCE_RELEASE_TO_FIRST_MINING_SLICE_TRANSITION.md`](RESOURCE_RELEASE_TO_FIRST_MINING_SLICE_TRANSITION.md).
It is a two-commit, preparatory release protocol and currently grants no
receipt, positive perception path, controller, or input authority.

The future mining-only temporal boundary after that join is specified in
[`MINING_ONLY_ATTEMPT_REOBSERVE_VERIFY_CONTRACT.md`](MINING_ONLY_ATTEMPT_REOBSERVE_VERIFY_CONTRACT.md).
It separates one proposed resource, one attempted-event receipt, and one fresh
post-attempt perception cycle; it is currently replay-contract preparation only
and grants no interaction or input authority.

## Support envelope

Support is data-driven and explicit. A location cannot be marked production-supported until required detectors, navigation, banking, recovery, integration tests, and real validation satisfy release gates.

## Dependency direction

Preferred dependency direction:

`gui -> application services -> controller/state/scheduler`

`controller -> state contracts + knowledge + action interface`

`perception -> observation contracts`

`state -> observations`

`interaction -> platform/input adapters`

Avoid circular imports and avoid direct GUI-to-detector/action coupling.

## Testing architecture

- pure contracts/state logic: unit tests
- detectors: fixture/dataset tests
- workflows: deterministic state-machine tests
- multi-subsystem flows: integration tests with replay/simulated observations
- fixed real failures: regression tests
- complete validated workflow: acceptance/endurance/fault-injection tests

## Architectural change control

Material changes to boundaries, state ownership, support semantics, or release criteria require an ADR under `docs/adr/` and review before broad implementation.
