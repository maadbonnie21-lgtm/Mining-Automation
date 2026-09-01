# Banking foundation (constrained v1)

Milestone: **9 — verified banking** (foundation only; see `docs/MASTER_SPECIFICATION.md`)

This is the offline, non-input architectural foundation for verified banking:
typed contracts, a platform-independent bank perception seam, a deterministic
workflow state machine, future attempt-receipt causality contracts, an
immutable future evidence intake/reviewer design, and a type-level
closed boundary reserved for future source-owned navigation and inventory
release receipts. It
does not interact with RuneLite, open a real bank, deposit or withdraw
anything, implement `WorldState` (Issue #14), activate `MiningController`, or
implement navigation. See `mining_automation.banking` for the implementation
and `tests/test_banking_*.py` for the adversarial and replay coverage.

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

BANK_CLOSED_VERIFIED / BANK_OPEN_VERIFIED / DEPOSIT_READY_VERIFIED /
DEPOSIT_ATTEMPT_PENDING
    --fresh BankObservationEvidence(OPEN)-->   BANK_OPEN_VERIFIED
    --fresh BankObservationEvidence(CLOSED)--> BANK_CLOSED_VERIFIED
    --fresh BankObservationEvidence(UNKNOWN)--> ARRIVED_AT_BANK_CHECKPOINT,
                                                blocked

BANKING_COMPLETE is terminal; start a new context for the next visit.
```

The central invariant, enforced structurally rather than by convention: **an
attempted input action never proves its own success.** `OpenBankAttempted`
and `DepositAttempted` must carry a causally valid exact receipt before the
workflow can enter a `*_PENDING` state. The pending state retains that exact
receipt as an exclusive evidence boundary; only a higher ordered observation
captured strictly after the receipt time and no more than
`MAX_ATTEMPT_RECEIPT_AGE_S` later can advance past it. Cached arrival or
bank-open evidence must also remain within `MAX_BANKING_EVIDENCE_AGE_S` when
it is composed with a later bank or inventory observation; a shared
`cycle_id` never makes old evidence durable.
Any newer bank-interface reading in an established OPEN, readiness, or
deposit-pending state revokes that older authority before it is interpreted:
CLOSED transitions to
`BANK_CLOSED_VERIFIED`, UNKNOWN returns to the arrival boundary with
`BANK_STATE_UNKNOWN`, and an invalid or conflicting re-observation likewise
returns to that boundary with its exact blocker. Re-observation while a
deposit attempt is pending abandons that receipt, while keeping its ID spent.
`BANK_OPEN_ATTEMPT_PENDING` is different: its next bank observation is the
required post-attempt outcome evidence, so UNKNOWN/invalid evidence retains
the open-attempt boundary and remains pending.
Once an authority-bearing state has blockers, action/inventory evidence cannot
erase that denial. A fresh accepted bank re-observation must first clear the
fault (and OPEN must then receive fresh non-empty inventory evidence again)
before an attempt can be authorized.
`CheckpointArrivalEvidence` can only ever produce
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
| wrong bank detector ID/version                       | rejected, `BANK_DETECTOR_*_MISMATCH`        |
| detector output digest differs from `sha256(frame.payload)` | runner rejects the detector contract; the observation is never published |
| stale evidence (older than `MAX_BANKING_EVIDENCE_AGE_S`) | rejected, `*_EVIDENCE_STALE`            |
| fresh observation composed with stale cached arrival/OPEN support | rejected, `SUPPORTING_EVIDENCE_STALE` |
| mixed-frame provenance (independent source disagrees) | rejected, `EVIDENCE_PROVENANCE_MISMATCH`  |
| advancing frame from a different evidence `cycle_id`  | rejected, `EVIDENCE_PROVENANCE_MISMATCH`  |
| duck-typed inventory shape instead of an exact banking observation | rejected, `INVENTORY_EVIDENCE_TYPE_INVALID` |
| missing detector delivery                            | rejected, `*_OBSERVATION_MISSING` / `*_EVIDENCE_MISSING` -- zero banking authority |
| obstructed view / ambiguous UI (detector confidently reports UNKNOWN) | accepted, `UNKNOWN`, no blocker -- same as any genuine ambiguous reading |
| confidently-labeled OPEN/CLOSED below `BANK_PUBLICATION_CONFIDENCE_FLOOR` ("false OPEN"/"false CLOSED") | rejected, `BANK_CONFIDENCE_BELOW_FLOOR`, forced to `UNKNOWN` |

A rejected `BankPerceptionResult`/`InventoryPerceptionResult` always forces
its state back to `UNKNOWN` (bank) or an unknown occupied-slot count
(inventory) -- there is no path from a rejected reading to a value that looks
like a legitimate `OPEN` or `EMPTY`. The confidence floor exists specifically
because a label-only check cannot catch a *confidently wrong* label: a
detector that mislabels CLOSED as OPEN has already put the "right" label in
the field, so only its own admitted uncertainty (confidence) can catch it.
This is distinct from -- and rejected differently than -- a detector's own
genuine `UNKNOWN` call, which is accepted with no blocker because it is not a
defect.

## Adversarial / fail-closed matrix

Every row below is a test in `tests/test_banking_workflow.py`,
`tests/test_banking_perception.py`, or `tests/test_banking_replay.py`. Every
unsafe case yields zero banking-complete authority (the workflow state does
not advance and `blockers` is non-empty).

- arrival evidence missing
- forged or post-construction-mutated arrival contract
- arrival evidence stale
- arrival wrong checkpoint identity
- bank observation missing
- bank state UNKNOWN (does not advance)
- bank wrong profile/version
- bank wrong frame geometry
- bank/inventory mixed-frame provenance
- cross-cycle evidence substituted into an advancing frame
- bank target found but UI still CLOSED after an open attempt
- open-bank attempt without a fresh following verification (repeated attempt)
- deposit requested with inventory UNKNOWN
- deposit requested with stale inventory
- deposit attempted and inventory remains non-empty
- deposit attempted and resulting inventory UNKNOWN
- duplicate conflicting bank observations (duplicate *agreeing* observations
  are collapsed, not rejected)
- duplicate conflicting inventory observations
- malformed second/late observations cannot hide inside an apparently
  agreeing duplicate; every member is recursively revalidated before equality
  or collapse
- unsupported/mismatched capture geometry
- evidence ordering regression (frame does not advance)
- route arrival assumption substituted for a bank observation
- stale pre-deposit inventory reused as if it were a fresh post-deposit reading
- deposit attempted without any inventory verification
- unexpected event for the current state (every state)
- inventory already empty at bank-open (does not grant deposit readiness)
- obstructed bank view (confident UNKNOWN, no blocker -- genuine uncertainty)
- ambiguous UI presentation, e.g. mid-transition (same handling as obstruction)
- false OPEN / false CLOSED: a confidently-labeled reading below the
  publication confidence floor is rejected, never treated as the label it claims
- missing, duplicate, wrong-provenance, pre-evidence, or stale
  open-bank/deposit attempt receipts
  (see "Attempt-receipt causality" below) -- every one denies the transition
- a receipt issued against evidence already older than the attempt freshness
  limit -- denied with `ATTEMPT_PRECEDING_EVIDENCE_STALE`
- a higher-frame-id observation captured before or exactly at its accepted
  attempt-receipt boundary -- remains pending with
  `POST_ATTEMPT_EVIDENCE_NOT_FRESH`
- a fresh current observation delivered more than
  `MAX_ATTEMPT_RECEIPT_AGE_S` after its retained receipt -- remains pending
  with `POST_ATTEMPT_EVIDENCE_STALE` (the exact upper boundary is accepted)
- a fresh bank observation composed with stale cached arrival evidence, or a
  fresh inventory observation composed with stale cached OPEN evidence --
  denied with `SUPPORTING_EVIDENCE_STALE`
- a malformed nested pending observation -- rejected by its evidence-type
  blocker before any receipt-boundary field is dereferenced
- detector output whose claimed frame digest does not equal the SHA-256 of the
  exact input payload, or a detector that mutates that payload during a run
- a newer CLOSED bank reading after OPEN, deposit readiness, or a pending
  deposit revokes the older authority; inventory evidence alone cannot restore
  it, and any pending receipt is abandoned but remains spent
- a newer UNKNOWN, invalid, missing, or conflicting bank re-observation after
  OPEN, readiness, or deposit-pending returns to the arrival boundary with a
  blocker; old OPEN authority cannot survive the uncertainty
- a denied `DEPOSIT_READY_VERIFIED` context is `NOT_READY` and cannot consume
  a later deposit receipt until fresh bank and inventory evidence restore it
- interrupted transaction: a stray/replayed event (e.g. an old arrival) while
  a deposit is `DEPOSIT_ATTEMPT_PENDING` is denied, never mistaken for a
  fresh post-deposit reading or a restart
- a retried open-bank attempt after a fault reusing a previously-used receipt
  id is rejected as a duplicate, even when its provenance is otherwise correct

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
- bank closes after OPEN, after deposit readiness, or while deposit is pending;
  each replay proves that older authority is revoked (fault/recovery)
- bank becomes UNKNOWN after OPEN; replay returns to the arrival boundary
- a blocked deposit-ready snapshot cannot consume a later otherwise-valid
  deposit receipt; fresh bank and inventory re-verification is required
- interrupted transaction: a stray arrival event replays mid-deposit (fault/recovery)
- a retried open-bank attempt after a fault reuses a receipt id and is
  rejected as a duplicate, even though its provenance is otherwise valid
  (fault/recovery, ties attempt-receipt causality to replay)
- delayed delivery of a higher-frame-id OPEN observation captured before the
  open attempt -- remains pending
- delayed delivery of a higher-frame-id EMPTY inventory observation captured
  before the deposit attempt -- remains pending
- delayed fresh-current OPEN/EMPTY observations outside the retained receipt
  window -- remain pending with `POST_ATTEMPT_EVIDENCE_STALE`
- fresh inventory paired with an old cached bank-OPEN prerequisite -- remains
  `BANK_OPEN_VERIFIED` with `SUPPORTING_EVIDENCE_STALE`

None of these scenarios may be described as real OSRS evidence; they are
built exclusively from `mining_automation.banking.testing` synthetic fixtures.

## Attempt-receipt causality (future, data-only)

`mining_automation.banking.attempts` defines `OpenBankAttemptReceipt` and
`DepositAttemptReceipt`: typed, data-only records a future orchestrator that
*does* issue a real click could attach to `OpenBankAttempted`/`DepositAttempted`
purely for causality bookkeeping. A missing receipt is denied with
`ATTEMPT_RECEIPT_MISSING`; there is no receipt-less path into either pending
state. These local dataclasses do not prove that an input source issued a
physical action; a future live source must supply its own authenticated
issuance identity/seal.

A receipt proves nothing about the attempted action's outcome. It only lets
`evaluate_attempt_receipt_causality` check that the attempt is not a replay:

| Defect | Result |
| --- | --- |
| `attempt_id` already used earlier in this visit | rejected, `ATTEMPT_RECEIPT_DUPLICATE` |
| bound to evidence other than what the workflow currently holds | rejected, `ATTEMPT_RECEIPT_WRONG_PROVENANCE` |
| issued before its bound preceding evidence | rejected, `ATTEMPT_RECEIPT_PRECEDES_EVIDENCE` |
| bound preceding evidence already stale when issued | rejected, `ATTEMPT_PRECEDING_EVIDENCE_STALE` |
| issued too long before the evaluation time | rejected, `ATTEMPT_RECEIPT_STALE` |
| issued after the evaluation time | rejected, `ATTEMPT_RECEIPT_FROM_FUTURE` |
| outcome capture is not strictly after the retained receipt | rejected, `POST_ATTEMPT_EVIDENCE_NOT_FRESH` |
| outcome capture is more than `MAX_ATTEMPT_RECEIPT_AGE_S` after the retained receipt | rejected, `POST_ATTEMPT_EVIDENCE_STALE` |

A rejected receipt denies the `OpenBankAttempted`/`DepositAttempted`
transition outright -- the workflow stays in `BANK_CLOSED_VERIFIED` or
`DEPOSIT_READY_VERIFIED` respectively. An accepted receipt still only reaches
a `*_PENDING` state and is retained there. Only a strictly post-receipt fresh
`BankObservationEvidence` or inventory observation event captured inside the
fixed receipt window can prove the attempt worked. Equal-boundary timestamps
are not post-attempt evidence; over-window outcomes remain pending and retain
the exact receipt.

`deposit_readiness` is likewise a time-bounded, blocker-aware snapshot. Its
caller must provide the current monotonic evaluation time; stale/future
support or blockers from a denied transition always resolve to `NOT_READY`.
It is not a durable token and cannot authorize physical input.

## Immutable future bank-evidence intake / reviewer design (not yet collected)

`mining_automation.banking.evidence_intake` defines the typed shape a future
real bank-evidence pipeline must implement before any bank fixture is trusted
as release evidence. It collects nothing -- no pixels, real or synthetic.

Two invariants, both enforced by construction:

* **Operator labels are not reviewer truth.** `OperatorIntentLabel` (what the
  capturing operator believes a fixture shows) is embedded, inert, inside a
  `FinalizedBankEvidencePackage`. Only an independently-constructed
  `ReviewerVerdict` -- with its own `reviewed_case`, which may disagree with
  the operator's claim -- can make a package releasable.
* **Reviewer truth is cryptographically bound to a finalized package.**
  `ReviewedBankEvidenceCase` can only be constructed when
  `verdict.bound_package_sha256 == package.package_sha256`; the canonical
  package digest covers the raw hash, manifest hash, checkpoint/profile,
  operator label, identities, and chronology. A mismatch raises at
  construction time. Operator and reviewer identities must differ, and
  operator labeling -> package finalization -> review order is strict
  (equality at either boundary is rejected). Canonical timestamps are exact,
  finite, non-negative floats, with signed zero normalized before hashing.
  Digest-bound strings must contain Unicode scalar values (literal surrogate
  code points are rejected), preventing distinct Python strings from sharing
  one escaped canonical JSON representation. Finalized-package fields and
  reviewer-decision fields establish their own flattened construction-time
  snapshots; the wrapper separately snapshots the verdict/package binding.
  All three are recursively revalidated before digesting, wrapping, or release
  use, so neither a finalized package nor a rejected verdict can be changed
  after construction and then rebound as if it were a new review. Retained
  snapshots must be exact built-in tuples of exact primitive/enum members;
  malformed snapshot objects are rejected before equality, preventing a
  comparison callback from changing reviewer truth during validation.

`validate_release_evidence_case_batch` is the fixed-policy release check over
a proposed batch, covering every case
`docs/BANKING.md`'s real-evidence table above requires
(`REQUIRED_BANK_EVIDENCE_CASES` == every `BankEvidenceCase` member, including
`OBSTRUCTED_AMBIGUOUS` and `WRONG_LOCATION_NEGATIVE`). Its coverage and
freshness policy cannot be overridden by a caller. The configurable
policy engine is underscore-private and absent from both module and package
`__all__`; it is not a supported release-facing API. The expected
checkpoint/profile are still explicit caller inputs until a source-owned
deployment policy exists:

The batch boundary accepts only exact built-in `tuple` and `frozenset`
containers, recursively revalidates the expected checkpoint/profile and every
reviewed package, and rejects container subclasses before iteration. This
prevents a stateful container or post-construction nested mutation from
changing what was checked into a different release decision. Evaluation time
and even the private diagnostic freshness parameter must be exact finite
non-negative floats; integers are rejected before conversion so large values
cannot round across the release freshness boundary.

| Defect | Blocker |
| --- | --- |
| duplicate package ID, canonical package digest, or raw digest in the batch | `DUPLICATE_EVIDENCE_PACKAGE` |
| a rejected verdict included as if it were release evidence | `REJECTED_EVIDENCE_PACKAGE_INCLUDED` |
| package older than `MAX_EVIDENCE_PACKAGE_AGE_S` | `EVIDENCE_PACKAGE_STALE` |
| package foreign to the expected checkpoint/profile | `CHECKPOINT_IDENTITY_MISMATCH` / `BANK_PROFILE_MISMATCH` |
| operator/package/review chronology is in the future | `EVIDENCE_FROM_FUTURE` |
| batch missing coverage of a required *independent* case (everything except `NON_EMPTY_BEFORE_DEPOSIT`/`EMPTY_AFTER_DEPOSIT`) | `MISSING_REQUIRED_EVIDENCE_CASE` |
| `NON_EMPTY_BEFORE_DEPOSIT`/`EMPTY_AFTER_DEPOSIT` coverage claimed with no valid, batch-referencing `DepositResultEvidenceRecord` in `deposit_results` | `DEPOSIT_RESULT_COVERAGE_MISSING` |
| a supplied `DepositResultEvidenceRecord`'s `pre_deposit`/`post_deposit` is not itself present in the batch's `cases` | `DEPOSIT_RESULT_PACKAGE_NOT_IN_BATCH` |

An empty result means the batch is structurally acceptable for the supplied
target, never that any pixel or opaque manifest digest is real or correct --
this function never loads a manifest or inspects pixels.

**`NON_EMPTY_BEFORE_DEPOSIT`/`EMPTY_AFTER_DEPOSIT` coverage cannot be
satisfied by bare presence.** Every other required case is satisfied simply
by an accepted case carrying that label anywhere in the batch. These two are
different: two independently-valid packages from unrelated visits/sessions
carrying these labels used to satisfy "deposit-result coverage" with zero
causal link between them (a real defect, confirmed by reproduction and fixed
-- see `tests/test_banking_evidence_intake.py`'s
`test_validate_release_evidence_case_batch_rejects_unrelated_deposit_pair`).
Both `_validate_evidence_case_batch` and `validate_release_evidence_case_batch`
now accept a `deposit_results: tuple[DepositResultEvidenceRecord, ...]`
parameter; coverage for these two labels is granted only when at least one
entry's `pre_deposit`/`post_deposit` are themselves members of `cases`.
Every supplied record is re-validated (`DepositResultEvidenceRecord.__post_init__`)
before being trusted, so a record forged via `object.__setattr__` after
construction raises rather than silently granting coverage.

### Deposit-result verification packaging

A batch merely containing *some* reviewed `NON_EMPTY_BEFORE_DEPOSIT` case and
*some* reviewed `EMPTY_AFTER_DEPOSIT` case is a materially weaker claim than
"this exact deposit attempt went from non-empty to empty": the two samples
could come from unrelated visits, or a genuinely valid receipt from a
*different* attempt could be substituted in without anything noticing.
`DepositResultEvidenceRecord` (`evidence_intake.py`) makes and validates the
full causal chain:

* `pre_deposit` is a reviewer-accepted `NON_EMPTY_BEFORE_DEPOSIT` case;
* `attempt_receipt` (a `DepositAttemptReceipt`, see "Attempt-receipt
  causality" above) is bound to that *exact* pre-deposit package by content
  hash -- `attempt_receipt.preceding_provenance.frame_sha256` must equal
  `pre_deposit.package.raw_sha256`. This is what rejects a wrong or replayed
  receipt: a receipt issued against different evidence, however valid on its
  own, cannot bind here. The receipt itself carries a retained
  construction-time snapshot and a canonical `receipt_sha256` digest (which
  also enforces exact finite non-negative `float` timestamps, rejecting
  int/NaN/Inf/negative aliases) -- a receipt forged via `object.__setattr__`
  after construction is caught the next time `receipt_sha256` is read;
* the receipt is issued at or after the pre-deposit capture and within
  `MAX_ATTEMPT_RECEIPT_AGE_S` of it;
* `post_deposit_provenance` is bound the same way to the exact `post_deposit`
  package, shares the receipt's `cycle_id` (the same bank visit/session), and
  is captured strictly after the receipt and within that same freshness
  window -- mirroring `workflow._post_attempt_freshness_blocker`'s live rule
  exactly;
* `pre_deposit` and `post_deposit` must additionally share the exact same
  `CaptureEnvironmentIdentity` (`source_session_id`, `capture_build_id`,
  `capture_config_digest`, `environment_id`) -- a shared `cycle_id` alone is
  a caller-chosen label, not proof of the same capture session/build/config/
  environment. `CaptureEnvironmentIdentity` is a required field on
  `FinalizedBankEvidencePackage`, cryptographically bound into its canonical
  `package_sha256` digest (schema `v2`, deliberately non-colliding with `v1`);
* `post_deposit` is a reviewer-accepted `EMPTY_AFTER_DEPOSIT` case, sharing
  the pre-deposit package's checkpoint/profile, backed by distinct
  underlying evidence.

Any violation raises at construction. Cross-visit, cross-session,
cross-environment, wrong receipt, replayed receipt, forged receipt, stale
evidence, swapped ordering, duplicate raw content, int/negative timestamp
aliases, and a missing/wrong-type receipt are all covered by dedicated
regression tests in `tests/test_banking_deposit_result_evidence.py`. Two
prior, weaker designs (first: checking only case labels, checkpoint/profile,
and package-level finalized-timestamp ordering, with no binding to any
specific receipt at all; second: adding the receipt binding but with no
session/build/config/environment identity and no anti-forging on the receipt
itself) were each proven insufficient by a failing regression before being
replaced -- see that test file's module docstring and `tests/test_banking_attempts.py`.

## Closed integration boundary

`mining_automation.banking.integration_boundary` deliberately exports no
protocols or adapters. Codex B's current navigation endpoint export is
explicitly non-authoritative, and Inventory V3 does not yet publish a reviewed
nominal release identity/receipt contract. Duck typing either lane into a
banking workflow event would manufacture authority, so no convenience seam is
available. A future integration change must consume the exact source-owned
nominal contracts after their owning lanes publish and approve them.

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
| Non-empty / full inventory while OPEN              | An inventory reading bound to the *same* evidence cycle as the OPEN reading (matching `cycle_id`, while frame id/time still advance), with a known `occupied_slots` count and confidence at or above `INVENTORY_PUBLICATION_CONFIDENCE_FLOOR`. |
| Empty inventory after deposit                      | A fresh post-deposit inventory reading captured strictly after and no more than `MAX_ATTEMPT_RECEIPT_AGE_S` beyond the accepted deposit-attempt receipt boundary (new `BankEvidenceProvenance`, strictly advancing frame id and capture time, age within `MAX_BANKING_EVIDENCE_AGE_S`) with `occupied_slots == 0` and confidence at or above the floor. |
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
2. an accepted `BankPerceptionResult` at `OPEN` for the expected checkpoint,
   profile, and exact bank detector ID/version, and
3. a future source-owned approved inventory result (no adapter exists yet)
   showing `occupied_slots == 0`, bound
   to the same evidence cycle as (2).

No subsystem may synthesize any of the other two. This package supplies (2)
and banking-local exact inventory-observation evaluation as pure, typed,
non-input building blocks; that local evaluation is not Inventory V3 approval.
It does not itself perform (1) and does not decide when to resume mining.

These contracts remain offline and non-authoritative. Actor IDs are
self-asserted strings, the evidence package has no source-owned attestation
seal or raw-capture clock identity, and monotonic timestamps are comparable
only inside one identified clock domain/boot. The manifest is an opaque digest,
not a schema-validated proof of cross-artifact causality. Python objects also
cannot defend against a hostile caller coordinating `object.__setattr__`
mutation of both a value and its retained snapshot/binding. A live release
boundary must replace those local assumptions with an authenticated
source-owned immutable seal. None is invented here.

The banking-local inventory observation types and detector identity strings
are synthetic contract scaffolding, not an Inventory V3 release receipt. No
adapter is exported, and no live caller may treat local `READY` or
`BANKING_COMPLETE` state as cross-lane authority until the source-owned
navigation and inventory release contracts are approved and integrated.
