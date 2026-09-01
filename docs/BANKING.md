# Banking foundation (constrained v1)

Milestone: **9 — verified banking** (foundation only; see `docs/MASTER_SPECIFICATION.md`)

This is the offline, non-input architectural foundation for verified banking:
typed contracts, a platform-independent bank perception seam, and a
deterministic workflow state machine. It does not interact with RuneLite,
open a real bank, deposit or withdraw anything, implement `WorldState`
(Issue #14), activate `MiningController`, or implement navigation. See
`mining_automation.banking` for the implementation and
`tests/test_banking_*.py` for the adversarial and replay coverage.

Everything in `mining_automation.banking.testing` is synthetic architecture-test
scaffolding. None of it was captured from a real OSRS client and none of it
may be described as real evidence in a PR, issue, or report.

## Why a separate package, not another `WorldState`

`mining_automation.contracts.InventoryState` and `FrameRef` are reused as-is.
Banking adds its own tri-state bank-interface reading, checkpoint/profile
identity, evidence provenance, and a fail-closed `BankingBlocker` vocabulary,
but it does not define a second world model. A future orchestrator composes
banking authority from three independently-produced, independently-approved
inputs -- navigation arrival, bank perception, and inventory perception -- and
no one of those subsystems may manufacture authority for another:

* navigation arrival != bank open
* bank open != deposit success
* deposit attempt != empty inventory

## State transition matrix

```text
AWAITING_CHECKPOINT_ARRIVAL
    --CheckpointArrivalEvidence-->            ARRIVED_AT_BANK_CHECKPOINT

ARRIVED_AT_BANK_CHECKPOINT
    --BankObservationEvidence(OPEN)-->        BANK_OPEN_VERIFIED
    --BankObservationEvidence(CLOSED)-->      BANK_CLOSED_VERIFIED
    --BankObservationEvidence(UNKNOWN)-->     (unchanged, blocked)

BANK_CLOSED_VERIFIED
    --OpenBankAttempted-->                    BANK_OPEN_ATTEMPT_PENDING

BANK_OPEN_ATTEMPT_PENDING
    --BankObservationEvidence(OPEN)-->        BANK_OPEN_VERIFIED
    --BankObservationEvidence(CLOSED)-->      BANK_CLOSED_VERIFIED
    --BankObservationEvidence(UNKNOWN)-->     (unchanged, blocked)

BANK_OPEN_VERIFIED
    --PreDepositInventoryObservationEvidence(known non-empty)-->
                                               DEPOSIT_READY_VERIFIED

DEPOSIT_READY_VERIFIED
    --DepositAttempted-->                     DEPOSIT_ATTEMPT_PENDING

DEPOSIT_ATTEMPT_PENDING
    --PostDepositInventoryObservationEvidence(known empty)-->
                                               BANKING_COMPLETE
    --PostDepositInventoryObservationEvidence(known non-empty)-->
                                               (unchanged, blocked)

BANKING_COMPLETE is terminal; start a new context for the next visit.
```

The central invariant, enforced structurally rather than by convention: **an
attempted input action never proves its own success.** `OpenBankAttempted`
and `DepositAttempted` carry no evidence and can only move the workflow into a
`*_PENDING` state; only a following fresh, jointly-verified observation can
advance past it. `CheckpointArrivalEvidence` can only ever produce
`ARRIVED_AT_BANK_CHECKPOINT` -- it is structurally incapable of producing a
bank-open or inventory result.

## Bank perception seam

`mining_automation.banking.perception` defines the `BankDetector` protocol and
two pure evaluators (`evaluate_bank_observation`,
`evaluate_inventory_observation`) that turn raw evidence into a trustworthy
reading. Required semantics:

| Input                                              | Result                                    |
| --------------------------------------------------- | ------------------------------------------ |
| definite OPEN / CLOSED reading, clean evidence      | passes through untouched                   |
| ambiguous/unsupported reading, clean evidence        | `UNKNOWN`, no blocker (genuine uncertainty) |
| wrong capture geometry                               | `UNKNOWN`, `BANK_GEOMETRY_UNSUPPORTED`      |
| wrong profile/version                                | rejected, `BANK_PROFILE_MISMATCH`           |
| stale evidence (older than `MAX_BANKING_EVIDENCE_AGE_S`) | rejected, `*_EVIDENCE_STALE`            |
| mixed-frame provenance (independent source disagrees) | rejected, `EVIDENCE_PROVENANCE_MISMATCH`  |
| missing detector delivery                            | rejected, `*_OBSERVATION_MISSING` / `*_EVIDENCE_MISSING` -- zero banking authority |

A rejected `BankPerceptionResult`/`InventoryPerceptionResult` always forces
its state back to `UNKNOWN` (bank) or an unknown occupied-slot count
(inventory) -- there is no path from a rejected reading to a value that looks
like a legitimate `OPEN` or `EMPTY`.

## Adversarial / fail-closed matrix

Every row below is a test in `tests/test_banking_workflow.py`,
`tests/test_banking_perception.py`, or `tests/test_banking_replay.py`. Every
unsafe case yields zero banking-complete authority (the workflow state does
not advance and `blockers` is non-empty).

- arrival evidence missing
- arrival evidence stale
- arrival wrong checkpoint identity
- bank observation missing
- bank state UNKNOWN (does not advance)
- bank wrong profile/version
- bank wrong frame geometry
- bank/inventory mixed-frame provenance
- bank target found but UI still CLOSED after an open attempt
- open-bank attempt without a fresh following verification (repeated attempt)
- deposit requested with inventory UNKNOWN
- deposit requested with stale inventory
- deposit attempted and inventory remains non-empty
- deposit attempted and resulting inventory UNKNOWN
- duplicate conflicting bank observations (duplicate *agreeing* observations
  are collapsed, not rejected)
- duplicate conflicting inventory observations
- unsupported/mismatched capture geometry
- evidence ordering regression (frame does not advance)
- route arrival assumption substituted for a bank observation
- stale pre-deposit inventory reused as if it were a fresh post-deposit reading
- deposit attempted without any inventory verification
- unexpected event for the current state (every state)
- inventory already empty at bank-open (does not grant deposit readiness)

## Replay scenarios

`tests/test_banking_replay.py` replays fixed, named event sequences end to
end from a fresh context and asserts each reaches its expected final state
and blockers, and that replaying the same scenario twice is byte-identical:

- happy path: CLOSED -> open attempted -> OPEN verified -> deposit attempted
  -> EMPTY verified -> `BANKING_COMPLETE`
- still CLOSED after an open attempt
- still FULL after a deposit attempt
- UNKNOWN inventory after a deposit attempt
- stale evidence after arrival
- frame geometry mismatch
- evidence ordering regression (observation replays the arrival frame)
- profile-version change

None of these scenarios may be described as real OSRS evidence; they are
built exclusively from `mining_automation.banking.testing` synthetic fixtures.

## Future real-evidence specification (not yet collected)

This section defines the minimum real evidence a future, reviewed detector
must supply before any of it is used for live banking. Nothing described here
has been collected, and this foundation does not depend on it existing yet.

| Case                                             | Minimum evidence required |
| ------------------------------------------------- | -------------------------- |
| Varrock East bank arrival                          | A same-cycle `FrameRef` at the fixed checkpoint pose, captured after route navigation reports arrival, tagged with the `BankCheckpointIdentity` for Varrock East. |
| Valid bank target / interface presentation         | A frame in which the bank booth/chest geometry matches an approved `BankProfileIdentity` (exact `frame_width`/`frame_height` and `schema_version`) for the current client window. |
| CLOSED state                                       | A frame with no bank interface panel visible, at the approved profile geometry, with detector confidence at or above its published floor. |
| OPEN interface                                     | A frame with the bank interface panel fully rendered (not mid-transition/animating), at the approved profile geometry, confidence at or above its published floor. |
| Non-empty / full inventory while OPEN              | An inventory reading bound to the *same* evidence cycle as the OPEN reading (matching `BankEvidenceProvenance`), with a known `occupied_slots` count and confidence at or above `INVENTORY_PUBLICATION_CONFIDENCE_FLOOR`. |
| Empty inventory after deposit                      | A fresh post-deposit inventory reading (new `BankEvidenceProvenance`, strictly advancing frame id, age within `MAX_BANKING_EVIDENCE_AGE_S`) with `occupied_slots == 0` and confidence at or above the floor. |
| Obstruction / ambiguous presentation               | A frame where the detector cannot confidently classify OPEN vs. CLOSED (partial occlusion, mid-animation, overlapping UI) -- must resolve to `UNKNOWN` with no blocker, never guessed as OPEN or CLOSED. |
| Wrong-location negative                            | A frame captured away from any approved `BankCheckpointIdentity` -- must be rejected via `CHECKPOINT_IDENTITY_MISMATCH`/`BANK_GEOMETRY_UNSUPPORTED`, never silently treated as UNKNOWN-and-ignored. |

Collecting this evidence, training/validating a real detector against it, and
wiring a live `BankDetector` implementation are all out of scope for this
foundation and must go through the same review process as any other
production perception component before being trusted for live banking.

## Architecture boundary

A future banking orchestrator may only grant end-to-end authority
("banking complete, safe to resume mining") when it holds all three of:

1. approved navigation arrival evidence (owned by fixed-route/checkpoint
   navigation, not this package),
2. an accepted `BankPerceptionResult` at `OPEN` for the expected checkpoint
   and profile, and
3. an accepted inventory reading (via `evaluate_inventory_observation`)
   showing `occupied_slots == 0`, bound to the same evidence cycle as (2).

No subsystem may synthesize any of the other two. This package supplies (2)
and the inventory half of (3) as pure, typed, non-input building blocks; it
does not itself perform (1) and does not decide when to resume mining.
