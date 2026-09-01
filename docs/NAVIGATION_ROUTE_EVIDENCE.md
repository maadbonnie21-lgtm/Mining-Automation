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
(
    json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    + b"\n"
)
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

## Durable acquisition and review transactions

`navigation.durable_route_evidence` is the navigation-specific, offline-only writer around the
accepted passive campaign sequencer. It is intentionally not exported by the navigation package
root or downstream integration boundary.

### Writer ownership boundary

The current writer has the explicit contract
`trusted_non_hostile_dedicated_parent_namespace_v1` and reports
`DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE=false`. Acquisition and review parents must be
caller-controlled dedicated namespaces in which no hostile actor can rename or replace an owned
directory during a write. This is a required precondition, not an implementation convenience.

Files are created with exclusive pathname opens. Static links/reparse points and path replacement
observed by the pre/post identity checks fail closed, but the writer does not hold a directory
handle across the last check and create. A hostile actor could replace a parent in that interval;
the path open may create bytes in the replacement namespace before the post-write identity check
detects the swap. Detection latches the transaction `STOPPED` and returns no writer receipt.

The terminal manifests bind the physical `transaction_root_identity` captured from the
writer-owned root. A swap during the terminal open can therefore leave terminal-named bytes in a
complete replacement clone, but strict intake recomputes the current root identity and rejects the
clone before content verification. A recreated/copy root is audit bytes, not the same finalized
transaction. The writer does not undo the misdirected write and does not claim hostile-namespace
confinement. Future real release-evidence acquisition requires a separately reviewed
handle-relative, no-follow writer boundary.

Successful acquisition/review receipts also carry required external
`acquisition_physical_identity_sha256` and `review_physical_identity_sha256` pins. Each digest is
computed only after the terminal manifest and exact-tree check and covers the root plus every
expected directory/file—including the terminal file—using stable mode, device, inode/file ID,
link-count, file-attribute, and reparse-tag fields. Volatile timestamps and content metadata are
excluded; content/size remain independently digest-bound. Strict intake recomputes the complete
physical digest before content authority is returned, so an exact-byte file replacement or cloned
child directory is still rejected. Receipt construction occurs after this pin succeeds and before
owned handles close; a terminal-open/pin/close failure returns no receipt.

The review plan persists the acquisition physical-identity binding under
`fixed-route-durable-review-plan-v2`; this is an incompatible wire-shape change from the frozen v1
offline contract, rather than a new required field hidden behind the old schema label.

### Handle-anchored Windows writer

`navigation.handle_anchored_route_evidence` is that separate writer boundary for the supported
Windows platform. It does not change or upgrade the pathname writer. Its machine-readable
contract is `windows_nt_handle_relative_no_follow_fresh_directory_v1` and its machine-readable
process-integrity prerequisite is true. `HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE`
remains false pending lead review of that explicit boundary; platform support alone cannot flip a
release-facing gate. Every requested parent/root must still pass a native per-transaction
capability check. The reviewed
storage envelope is a fixed local NTFS drive only. Mapped/network drives, UNC paths, non-NTFS
filesystems, reparse-point ancestry, and hosts missing the required parent queries fail before the
transaction root is created. A capability failure discoverable only after the root's atomic
creation can retain that empty owned root, but occurs before sequencer/source access or evidence
bytes. The constant identifies an implementation that may be attempted; it is never a claim that
an arbitrary Windows path is eligible.

The implementation verifies fixed-drive type and the NTFS filesystem before any root mutation,
opens the drive root once, and traverses every existing parent component with relative
`NtCreateFile` open-reparse-point calls. It then uses the held final parent/directory handles as
`RootDirectory` with `FILE_CREATE` for the fresh transaction root, every child directory, and every
immutable file. Directory handles use asynchronous metadata/child-create access; file handles are
synchronous and write-through. Every retained handle receives
`HANDLE_FLAG_PROTECT_FROM_CLOSE` before it enters the owned ledger, and that protection is
rechecked before native identity use. An ordinary competing `CloseHandle` therefore cannot turn a
validated integer into a same-value foreign capability before the next relative create. All
handles remain writer-owned until STOP or finalization. A file is
written, flushed, rewound, and read back through a duplicate of the exact created handle. Before
every later create and before a receipt, the writer reopens each owned name relative to its
retained parent handle and compares stable native file identity, regular-file link count, size,
type, reparse state, and the public-path identity used by strict intake. There is no fallback from
an invalid native handle to a saved pathname.

Parent/root replacement before the root's atomic create cannot redirect later evidence: the new
root is created beneath the held original parent, public-path revalidation fails, the empty owned
prefix is retained, and no source/sequencer call or receipt occurs. Once the plan file exists, its
retained non-delete-sharing handle also prevents parent/root rename during frame, case-record,
package, review, and terminal-manifest creation. A replacement namespace never receives writer
bytes. Hard-link aliases, reparse identities, handle invalidation, short writes, foreign tree
entries, and unexpected native create outcomes fail closed. A same-principal hard-link installed
after exclusive create can observe bytes before the post-write link-count check; detection still
forces STOP/no receipt, and the alias survives because this is an integrity boundary rather than a
confidentiality or foreign-delete boundary. Cleanup revalidates type/physical identity before
closing each numeric handle, requires the close-protection bit still to be present, and only then
clears it for the controlled close. An unprotected stale value—even one reopened on the same owned
file—is reported and not closed. The contract does not defend against arbitrary code execution in
the writer process that deliberately clears handle protection or mutates private ledger state;
such code already possesses the native authority being protected. The writer has no
delete/rollback API, so foreign bytes and partial owned prefixes survive.

The reviewed user-mode `NtCreateFile` / `OBJECT_ATTRIBUTES` surface exposes no supported flag that
atomically returns these file handles already protected. The native call's output remains an
unpublished local until protection is set; the boundary therefore requires process integrity and
does not claim confinement against code running inside the process that intercepts the native call
itself. This is distinct from the ordinary external filesystem rename/replacement/concurrency
model exercised here and is why the future-real eligibility constant stays false until lead
review.

Linux and other platforms are deliberately unsupported by this fresh-directory writer and fail
before reserving the requested root or consulting the capture source. `mkdirat()` returns no
directory handle, leaving an uncloseable `mkdirat -> openat` replacement/adoption interval. Random
names, a marker file, `openat2`, or post-open inode checks cannot prove that the opened directory
is the one this invocation created. The implementation does not label that weaker sequence as
handle-anchored eligibility.

This constant describes writer infrastructure only. Packages emitted today retain the exact
synthetic architecture-test evidence role and all live-navigation, activation, and input fields
false. A later source-owned campaign export must explicitly bind use of this writer before any
future real-evidence decision; a synthetic verification report cannot self-promote by referring
to the platform constant. The physical-identity pins prove object continuity for strict intake;
they do not by themselves prove that the handle-anchored factory, rather than the ineligible
pathname factory, produced a package. That writer/reviewer binding remains a separate required
campaign-export contract.

One acquisition transaction exclusively reserves a previously absent root, constructs and owns
one private passive sequencer, and writes an append-only prefix:

```text
campaign-plan.json
audit/<ordinal>-<case>-request.json
cases/<ordinal>-<case>/frame.bin
cases/<ordinal>-<case>/detector-report.json
audit/<ordinal>-<case>-owned.json
...
finalized-package.json                 # exact typed package payload
acquisition-finalization.json          # successful durable commit marker, last
```

Every request record is durable before its capture-only source invocation. For each successful
capture, the exact frame bytes are persisted first, the exact detector report second, and the
hash-chained owned-case record last. The exact package payload is then exclusively created and a
strict exact-tree preflight runs. `acquisition-finalization.json` binds the ordered case-record
digests, acquisition head, journal head, and exact package digest and is the last write. No
subsequent filesystem mutation or write follows a successful terminal-manifest write; consumers
independently reload both files through the read-only verifier.

A persistence or passive-contract failure latches the transaction `STOPPED`. The successfully
written prefix remains in place, optionally with `acquisition-stop.json`, and cannot be reviewed.
No code path deletes it, adopts it, replaces it, resumes it, retries it in place, or silently starts
a new source session. An existing root or immutable path is a collision even if its bytes happen
to match.

Independent review uses a separate, freshly reserved root and has no source, detector, capture,
navigation, or input method:

```text
review-plan.json
truth/<ordinal>-<case>.json
...
independent-review.json                # exact typed reviewer-truth payload
review-finalization.json               # successful durable commit marker, last
```

Review can begin only after strict acquisition intake against caller-owned pins. Its plan binds the
exact acquisition finalization, package, route/direction, operator, independent reviewer, ordered
case artifact hashes, and start time. Truth records must follow exact case order and strict UTC
chronology and form their own hash chain. Finalization re-intakes the acquisition before writing,
then exclusively creates the independent-review payload. `review-finalization.json` binds its exact
bytes and is the last write. Operator acknowledgement and detector output remain zero review
authority. A mutation after review invalidates the pair and requires a fresh review id; accepted
truth is never rewritten.

`DurableAcquisitionFilesystemExpectation` and
`DurableRouteEvidenceFilesystemExpectation` are caller-owned authority pins. They bind the normal
route/source/build/configuration/environment identities plus acquisition journal/finalization,
complete physical-tree identity, review plan/journal/finalization, independent-review digest,
reviewer, and review id. The loader checks both roots independently, calls the existing synthetic
verifier, then repeats cross-root intake checks. A verified report still keeps activation and input
false.

Mine-to-bank and bank-to-mine require different campaign, source-session, acquisition-root,
review-root, package, review, and external-expectation lineages. No reverse, relabel, or shared-root
operation exists.

## Fail-closed matrix

| Condition | Result |
| --- | --- |
| Arrival-first, reordered/skipped positive, or nonterminal arrival case | plan construction failure |
| Missing, extra, duplicated, or reordered case | integrity failure |
| Missing or foreign artifact path | integrity failure |
| Missing/foreign filesystem entry or fixed-manifest path collision | integrity failure |
| Symlink, reparse point, hard-link alias, or root escape | integrity failure |
| Manifest/file/tree replacement during verification | integrity failure |
| Exact-byte file or cloned child-directory replacement after finalization | physical-identity integrity failure |
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
| Existing acquisition/review root or immutable file at check/open | collision; existing bytes untouched |
| Hostile parent swap between identity check and pathname open | unsupported namespace; possible replacement-namespace write, then `STOPPED`; no receipt or verification |
| Root clone swapped during terminal-manifest open | terminal-named bytes may exist, but bound physical root identity differs; strict intake rejects |
| Partial prefix, stop record, or missing terminal manifest | non-reviewable integrity failure |
| Foreign sentinel inserted before finalization | finalization failure; prefix retained |
| Stale request/case/review journal predecessor | integrity failure |
| Acquisition finalization/package mismatch | integrity failure |
| Review before acquisition finalization | integrity failure before review-root creation |
| Review truth repeated, skipped, reordered, or rebound | absorbing review STOP |
| Review id, plan, journal, finalization, or package pin drift | integrity failure |
| Post-review acquisition or review mutation | integrity failure; fresh review required |
| Opposite-direction durable package/review reuse | identity/integrity failure |

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
5. Finalize only the exact complete case set, writing the package payload and then the durable
   acquisition terminal manifest last.
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
