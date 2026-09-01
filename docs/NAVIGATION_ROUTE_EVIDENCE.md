# Navigation route evidence contract

## Authority boundary

`navigation.route_evidence` is a display-free architecture contract for one immutable,
direction-specific checkpoint evidence package. It has no capture backend, route executor,
controller hook, coordinates, geometry, mouse/keyboard capability, production registry, or live
evidence role.

The only role defined by this branch is
`synthetic_route_evidence_architecture_test_only`. A structurally passing package proves that the
offline intake/review mechanics work. It does not prove a Varrock checkpoint, a real traversal, a
supported location, or release eligibility. Every verification report hard-codes:

- `real_release_role_satisfied=false`;
- `live_navigation_enabled=false`;
- `activation_allowed=false`; and
- `input_authority=false`.

There is no role-conversion helper. A future real-client campaign will require a separately
reviewed, source-owned campaign-plan digest and authorization boundary. Caller text cannot promote
a synthetic package.

`NO LIVE NAVIGATION / NO WORLDSTATE / NO CONTROLLER ACTIVATION`

## Directional identity

Each campaign owns one complete `RoutePlan`. The canonical route-plan digest includes its opaque
route ID, version, explicit `mine_to_bank` or `bank_to_mine` direction, endpoints, ordered
checkpoints, and ordered steps.

The two directions therefore require different plans, campaign IDs, plan hashes, cases, finalized
package hashes, and independent reviews. An opposite-direction review, a reversed checkpoint
sequence, or an artifact from another route cannot be rebound to a package. This contract provides
no reverse-route operation.

No real route plan or route geometry is present in this branch. Tests use conspicuous synthetic
identifiers only.

## Immutable digest chain

The evidence chain is deliberately acyclic:

```text
RoutePlan
   -> RouteEvidenceCampaignPlan
      -> OwnedRouteEvidenceCase records
         -> FinalizedRouteEvidencePackage
            -> RouteEvidenceReview
               -> RouteEvidenceVerificationReport
```

### `RouteEvidenceCampaignPlan`

The preregistered plan is finalized before any owned frame. It binds:

- campaign ID and exact directional `RoutePlan`;
- route-plan SHA-256;
- checkpoint detector ID/version;
- checkpoint profile ID/version/content SHA-256;
- passive capture build ID/version/content SHA-256;
- capture-source and capture-session identities;
- exact required frame width, height, and pixel format;
- exact capture-configuration SHA-256;
- capture-environment manifest SHA-256;
- declared support-envelope SHA-256;
- operator identity, explicitly in a staging-not-truth role;
- creation time; and
- the exact ordered case IDs, ordinals, roles, and checkpoint IDs.

Case ordinals are contiguous and case IDs are unique. Every route checkpoint needs at least one
positive case. The first case must be a positive departure. All positive/arrival cases form an
exact nondecreasing subsequence of the route's checkpoint order and collectively cover every
checkpoint; an early arrival, reordered positive, or skipped checkpoint is invalid. Exactly one
explicit `route_arrival` case must name the terminal checkpoint and be the final campaign case.

### `OwnedRouteEvidenceCase`

Each owned case binds the exact campaign-plan and route-plan hashes plus:

- campaign, route, direction, case, capture, and sequence identity;
- detector, profile, and environment identity;
- exact capture source, session, configuration, and support-envelope identity;
- an `operator-intent-unverified` record whose `operator_intent_is_reviewer_truth` field is
  immutable false;
- capture UTC time and exact `FrameRef`;
- pixel format;
- owned frame path, byte length, and SHA-256; and
- owned detector-report path, byte length, and SHA-256.

Frames and reports use distinct safe relative POSIX paths. The frame length must agree with its
`FrameRef` geometry and pixel format.

Every case repeats the campaign's detector, profile, capture source, capture session, capture
build, configuration, environment, and support-envelope bindings. Finalization requires exact
equality, including the campaign-required geometry and pixel format;
mixing a case from another session or a stale configuration cannot be hidden behind otherwise
valid frame/report hashes.

Every v2 case and detector report also contains the same canonical acquisition binding. It binds
the exact campaign/source identity, request/capture/operator identities, fixed-false input and truth
claims, and strict acknowledgement/expiry/frame/record chronology. The first binding starts at the
campaign-plan digest; each later binding names the prior full owned-case digest, thereby committing
to that case's exact frame and detector-report artifacts. The package declares the final owned-case
digest as its chain head and a strictly later monotonic finalization time; the external load
expectation pins that head.

### `SyntheticRouteEvidenceDetectorReport`

Every detector-report artifact is a typed canonical record, not an opaque blob. It repeats the
campaign/plan/case/capture/sequence identities; directional route and route-plan digest;
detector/profile identities; capture source, session, configuration, environment, and support
envelope; exact capture build; exact `FrameRef`, pixel format, and frame SHA-256; and the route-free
detector output.

The three authority fields are schema-fixed false:
`detector_output_is_reviewer_truth`, `activation_allowed`, and `input_authority`. The verifier
strictly parses the owned bytes, rejects duplicate JSON keys, requires the exact nested and top
level field sets, reconstructs the typed values, and requires byte-for-byte equality with the
canonical serialization. It then compares every identity and frame binding to the owned case and
campaign. A report cannot be rebound to a different case, frame, session, detector, profile, or
route even if an attacker recomputes every outer artifact/package/review digest.

### `FinalizedRouteEvidencePackage`

The finalized package contains the complete plan and every case in plan order. It binds each case
record digest as well as the nested frame/report hashes. Construction rejects missing or foreign
cases, source-order regression, duplicate capture IDs, duplicate `FrameRef` values, reused artifact
paths (including case-fold aliases), reused case records, duplicate exact frame SHA-256 values,
duplicate exact detector-report SHA-256 values, or any foreign
route/campaign/detector/profile/environment binding. Artifact components containing a colon,
trailing dot/space, or a Windows reserved device name are invalid.

Its canonical digest is the hash to which later reviewer truth is bound. The package does not
include its own hash, avoiding a digest cycle.

Byte-identical frame payloads are rejected, even when capture IDs and `FrameRef` values differ.
This is a deliberate fail-closed policy: evidence cannot silently present one payload as multiple
independent route cases. Byte-identical detector reports are rejected for the same reason.

### `RouteEvidenceReview`

Review happens only after finalization. It binds the exact finalized-package SHA-256, campaign,
route, direction, route-plan digest, reviewer identity, review time, and one truth record for every
case in exact order.

Reviewer truth binds the inspected frame and detector-report hashes. It separately supplies an
approved/rejected decision and an explicit `MATCHED`, `UNKNOWN`, or `AMBIGUOUS` checkpoint result.
The schema requires portable operator/reviewer identifiers that differ after Unicode-aware
normalization. Operator staging and stored detector output cannot structurally substitute for a
review record. The data model cannot attest that two labels represent different humans or prove the
reviewer's process; stronger human or cryptographic identity independence remains an external
release prerequisite.

### `RouteEvidenceVerificationReport`

Verification snapshots immutable artifact bytes, rejects a missing or foreign artifact set,
checks every size/hash, and reads the mapping again to detect replacement during verification.
Review coverage and order must exactly equal the preregistered case set.

The programmatic verifier also reconstructs the package, review, and caller expectation as exact
owned contract graphs before reading artifacts. Malformed container subclasses, mutated
fixed-authority fields, or a graph that changes during intake are integrity failures. Verification
uses only those owned graphs, and the returned report and endpoint carry separate route-identity
snapshots, so later caller mutation cannot rewrite a latched result or splice its direction from its
bound package digest.

Verification also requires a caller-supplied `RouteEvidenceLoadExpectation`. This authority pin
names the exact finalized-package SHA-256, campaign, route, direction, route-plan SHA-256,
detector/profile, capture source/session/configuration/environment, and support envelope expected
by the caller. It also pins the acquisition-chain head, capture build, and required geometry/pixel format. It is
intentionally external to the package graph. Therefore a uniformly stale but
internally self-consistent package/report/review graph cannot authorize itself by recomputing all
of its own hashes. Omitting the expectation is a type/API error; any mismatch is an integrity
failure.

For this synthetic contract:

- checkpoint-positive and route-arrival cases pass only with approved reviewer truth matching the
  exact intended checkpoint;
- checkpoint-negative cases pass only with approved `UNKNOWN` or `AMBIGUOUS` truth;
- rejected cases remain failures; and
- foreign checkpoint candidates are integrity errors.

Stored detector output is never promoted to reviewer truth. It is compared with the separately
recorded review solely as a conformance check; disagreement is retained as a conformance failure.

A conformance PASS is synthetic architecture evidence only.

## Canonical JSON and SHA-256

All schema identities use sorted, compact ASCII JSON with non-finite numbers forbidden and exactly
one final LF:

```python
json.dumps(
    value,
    allow_nan=False,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
).encode("ascii") + b"\n"
```

SHA-256 values are exactly 64 lowercase hexadecimal characters over the stored bytes. Future
on-disk production records will need a separately authorized release format. In this synthetic
loader, binary frame and report ownership remains bound in both the case record and finalized
package; no unpinned sidecar can supply authority.

The detector-report parser already rejects duplicate JSON keys, unknown/missing schema fields, and
noncanonical bytes. It also bounds report bytes and normalizes non-finite, unrepresentable, and
overly recursive input into an integrity failure.

## Read-only filesystem loader

`navigation.route_evidence_loader` loads exactly `finalized-package.json`,
`independent-review.json`, and the artifact paths owned by that package. It never writes. The
caller must provide `RouteEvidenceFilesystemExpectation`, which extends the package authority pins
with the exact independent-review SHA-256 and reviewer identity. Neither expectation is derived
from the loaded graph.

Before returning, the loader:

- enforces bounded, exact-schema, duplicate-key-free canonical ASCII JSON and typed canonical
  round trips for both manifests;
- recomputes nested route-plan, campaign-plan, case-record, package, review, frame, and detector
  report bindings;
- rejects unsafe, escaping, case-fold/Unicode-aliasing, reserved, or fixed-manifest-colliding paths;
- rejects missing or foreign files/directories, symlinks, reparse points, and hard-link aliases,
  including a link to content outside the evidence tree;
- validates the exact tree, file identities, sizes, bytes, and hashes before and after verification;
  and
- calls the same pinned synthetic verifier, whose output remains nonactivating.

The standard-library implementation cannot make a hostile concurrent Windows directory namespace
swap fully atomic on filesystems that report no stable directory identity. It compensates with
component reparse checks, resolved containment, path/open-handle identity checks, complete final
rereads, and a second exact-tree comparison. A future production loader would require an approved
native handle strategy before treating hostile concurrent intake as supported.

## Fail-closed matrix

| Condition | Result |
| --- | --- |
| Arrival-first, reordered/skipped positive, or nonterminal arrival case | plan construction failure |
| Missing, extra, duplicated, or reordered case | integrity failure |
| Missing or foreign artifact path | integrity failure |
| Missing/foreign filesystem entry or fixed-manifest path collision | integrity failure |
| Symlink, reparse point, hard-link alias, or root escape | integrity failure |
| Manifest/file/tree replacement during verification | integrity failure |
| Frame/report size or digest replacement | integrity failure |
| Reused capture ID, `FrameRef`, path/case-fold alias, or case record | package construction failure |
| Duplicate exact frame or detector-report payload SHA-256 | package construction failure |
| Colon, reserved Windows device, or trailing dot/space path component | artifact construction failure |
| Foreign campaign, route, version, direction, detector, profile, or environment | integrity failure |
| Stale/mixed capture source, session, configuration, or support envelope | package construction failure |
| Missing or mismatched caller load expectation | API/integrity failure |
| Uniformly stale but internally recomputed package graph | integrity failure |
| Duplicate-key, noncanonical, missing/unknown-field detector report | integrity failure |
| Detector report rebound to another case/frame/source/configuration | integrity failure |
| Detector output disagrees with independent reviewer truth | conformance failure |
| Opposite-direction review or reversed route plan | integrity failure |
| Review bound to another package hash | integrity failure |
| Reviewer equals operator | integrity failure |
| Review does not follow package finalization | integrity failure |
| Missing, foreign, duplicated, or reordered reviewer truth | integrity failure |
| Operator label used as truth | structurally unavailable |
| Positive checkpoint reviewed UNKNOWN/AMBIGUOUS/wrong | conformance failure |
| Negative checkpoint reviewed MATCHED | conformance failure |
| Reviewer rejects a case | retained conformance failure |
| Synthetic package asks for real release authority | always false |

## Endpoint proof boundary

`RouteEndpointVerification` can only be created by the package verifier. It exposes one positive
claim: `route_arrival_verified`. That value is true only when the complete synthetic package
conforms and the explicit terminal-arrival case has approved exact checkpoint truth.

The containing verification report additionally requires the endpoint's finalized-package and
reviewer-truth hashes to equal its own hashes exactly; endpoint proof cannot be spliced from a
different verified graph.

It permanently keeps both downstream claims false:

- `supported_mining_view_proven=false`; and
- `bank_interface_open_proven=false`.

Mine arrival therefore cannot start mining. Bank arrival cannot claim that the bank interface is
open and does not enter Claude's banking workflow. Those require separate, fresh downstream
perception evidence.

## Future real-campaign runbook

The display-free append-only acquisition lifecycle, explicit failure/restart behavior, outer
execution-session rehearsal, and direction-specific future evidence checklist are specified in
[`NAVIGATION_PASSIVE_CAMPAIGN_READINESS.md`](NAVIGATION_PASSIVE_CAMPAIGN_READINESS.md). Those
contracts extend this synthetic package format to v2 but grant no live role.

Nothing in this section authorizes a live run. A future direction campaign must use the following
one-way lifecycle:

```text
PREREGISTERED -> CAPTURING -> FINALIZED -> REVIEWED -> VERIFIED
```

1. Commit and independently review one direction-specific route plan, checkpoint semantics,
   detector/profile, capture source, capture configuration, support envelope, environment fields,
   and exact case matrix before pixels.
2. Issue one source-owned campaign identity and exclusively allocate its evidence directory.
3. Capture only the next preregistered case. Retain every owned attempt in source order. An abort
   or failed attempt is retained and ineligible; it is never silently dropped or replaced.
4. Persist the full frame first, detector report second, and owned case record last. No detector
   result controls inclusion, retry, or stage advancement.
5. Finalize only the exact complete case set, writing the package manifest last.
6. Give an independent reviewer the finalized package hash and exact immutable bytes. Reviewer
   truth is a new record and cannot mutate acquisition evidence.
7. Recompute every identity, canonical digest, artifact hash, case expectation, and review binding.
   The result remains nonactivating until a separate lead-owned production gate exists.

### Minimum checkpoint evidence per direction

For every declared departure, transit, and arrival checkpoint, preregistration must include:

- expected-direction positives across the declared support envelope;
- adjacent and route-neighbor negatives;
- visual lookalikes;
- wrong-direction and wrong-version cases;
- partial visibility, occlusion, and ambiguity;
- unsupported geometry/profile/environment cases; and
- explicit UNKNOWN and AMBIGUOUS outcomes that never authorize progress.

No checkpoint name, coordinate, screen region, or environment is production truth until this
future package exists and is approved.

### Later traversal, fault, and endurance evidence

Passive checkpoint packages do not prove movement. After a separately reviewed input boundary
exists, each physical step must bind fresh current-checkpoint evidence, one exact step proposal,
one source-issued attempt receipt, and a strictly post-receipt higher frame proving the expected
next checkpoint.

Each direction then requires preregistered repeated complete traversals plus deterministic faults
for missing/wrong/duplicate/delayed receipts, stale/repeated/out-of-order frames, skipped/prior/
unexpected checkpoints, wrong route/direction/version, ambiguity, timeouts, and interrupted runs.
An endurance campaign must preregister its exact run count or duration before execution and permit
zero continuation through uncertainty. This architecture does not invent those quantitative live
thresholds or claim they have been met.

Mine-to-bank success cannot establish bank-to-mine. Terminal mine arrival still needs supported
mining-view proof, and terminal bank arrival still needs independent bank-interface proof.
