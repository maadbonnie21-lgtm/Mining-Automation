# Resource release fault/endurance packaging

Status: **offline integrity verification only; no release or runtime authority**

This development-only checkpoint repeatedly verifies one already-produced
resource release evidence chain. It does not capture RuneLite, inspect private
full frames, alter reviewer truth, prepare replay fixtures, approve the client
envelope, issue a release receipt, or activate the constrained-v1 stack. The
frozen A5 receipt loader is exercised only as a deny-only readiness check.

The implementation is
`mining_automation.perception.resource_release_endurance`. It is intentionally
not re-exported from the package-level perception API and has no CLI or live
capture path.

## Independently retained expectation

`ResourceReleaseChainExpectation` must be constructed from values retained
outside the artifacts being checked:

- exact campaign session ID;
- exact clean repository HEAD recorded by that session;
- review-package manifest SHA-256;
- canonical follow-up SHA-256;
- conditional replay-proposal manifest SHA-256, or `None` only when proposals
  were not required;
- release-decision packet SHA-256.

The expectation cannot be derived or rebound by the report writer. The
proposal directory and proposal root are present or absent together. A stale
session, foreign head, mixed package/follow-up/proposal/decision chain, or
caller-rebound root fails before any report is published.

## Fixed endurance verification

The writer performs exactly three sequential rounds. There is no round-count,
retry, fallback, approval, envelope, policy, detector, profile, or authority
argument. Every round invokes the existing public verifiers in the same order:

1. privacy-safe review package;
2. canonical follow-up inputs;
3. replay-promotion proposal directory when the rooted chain requires it;
4. proposal-only release-decision packet; and
5. frozen A5 source-owned receipt loader, which must produce its exact
   all-source-gates-open unavailability result.

The receipt-readiness projection records a literal null loader result with
receipt and activation authority false. Any loader return (including `None`),
foreign exception, exception subclass, or changed unavailability reason aborts
the round. A6 never constructs, copies, deserializes, or publishes a receipt.

The rounds must return byte-rooted, identical projections. The writer also
checks the follow-up's exact session ID and clean HEAD, package and release
summary roots, case counts, retained-failure partition, environment facts,
renderer identity, unresolved external inputs, and deny-only authority. A
failure stops the sequence immediately. A later round is never used as a retry
for an earlier failure.

The deterministic report contains only IDs, digests, counts, failure case IDs,
environment facts, and literal policy values. It contains no paths, pixels,
observations, interaction regions, targets, inventory state, or action intent.
Its status is always:

`INTEGRITY_ONLY_NO_AUTHORITY`

The report and adjacent SHA-256 sidecar are exclusively created. Existing
files are never overwritten. Ownership-safe cleanup removes only an incomplete
file proven to have been created by this invocation; a sidecar collision or
concurrent winner is preserved exactly. Verification requires an independently
retained report SHA-256, reruns the same fixed three-round public chain,
rechecks the report/root after those rounds, and requires the stored report to
equal the newly computed deterministic projection exactly.

## Fault and drift semantics

Environment facts are evidence, not approval. The report preserves:

- required and observed DPI values;
- required frame and observed client geometries;
- observed window classes and consistency;
- observed capture backends and evidence origins;
- the strict renderer-identity snapshot and unresolved external inputs;
- exact-frame, exact-DPI, and source-ownership results;
- retained source-owned and non-release failure partitions; and
- replay-proposal and unresolved-decision counts.

A valid rooted chain may therefore report a DPI, geometry, or window mismatch
while remaining integrity-valid and entirely powerless. A malformed verifier
projection, internally invalid renderer projection, changed artifact, invalid
replay proposal, inconsistent partition, changed receipt readiness, or
stale/mixed root is an integrity failure and produces no complete endurance
report. A rooted foreign-backend observation remains visible evidence, not an
approval. No fault changes detector thresholds, the 5-of-6 landmark quorum,
all-three-zone policy, scene authority, or UNKNOWN behavior.

## Fresh-session recovery

Every successful report freezes the failure policy:

- retain the failed chain unchanged;
- do not retry or mutate the same session;
- do not rebind expected roots;
- do not use a fallback artifact or approval override;
- start a fresh source-owned campaign session with a new session ID; and
- bind that new session to its own exact clean HEAD.

Failed publication never completes an artifact in place or treats it as
evidence. An incomplete output owned by the failing invocation is removed;
foreign files and sidecars are never removed. A new session/output is required.
This conservative rule keeps faults reproducible and prevents integrity repair
from silently changing the evidence under review.

These rules are enforced inside each invocation: exactly three rounds run,
the first failure stops all later verifier calls, and no complete report is
published. A6 intentionally does not create a mutable machine-global attempt
registry or alter the retained source chain. It therefore cannot police a
separately initiated external invocation; callers remain required to retain a
failed chain and begin a fresh source-owned session. No invocation gains
authority regardless of success or failure.

## Authority boundary

The report hard-codes every authority field false, including approval,
release eligibility, replay promotion, activation, `WorldState`, controller,
mining, banking, navigation, input, and click authority. Passing three rounds
means only that the same independently rooted offline chain remained intact
during repeated verification.

This work does not modify the live resource gate, A3 proposal-only semantics,
A4 same-cycle denial, A5 receipt gates, the production detector, or any
controller/input path. Live resource evidence collection remains separately
authorization-gated.
