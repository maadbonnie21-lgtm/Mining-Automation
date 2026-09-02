# Resource release to first-mining-slice transition

Status: **preparatory only; resource and inventory release gates remain open**

This document fixes the minimum source-owned transition after genuine resource
C1/C2 closure. It does not issue a receipt, approve evidence, implement the
positive same-cycle assembly, activate Issue #14, or grant input authority.
The constrained-v1 product remains limited to the exact reviewed Varrock East
view. Unsupported or uncertain evidence means zero targets and STOP.

## Current boundary

The source-owned resource receipt module is intentionally unissued. Its only
future issuance state is:

- `_OPEN_RESOURCE_RELEASE_GATES`;
- `_RECEIPT_ISSUANCE_ALLOWED`;
- `_SOURCE_OWNED_RELEASE_RECORD`; and
- `_APPROVED_RECEIPT_RECORD_SHA256`.

No caller may supply a record, digest, gate state, path, JSON document, approval
flag, or reconstructed receipt. A4 remains the executable atomic-denial oracle;
it must not be changed into a positive bypass. A7 remains a design for a later
perception-only positive carrier, not an implementation.

The current resource identity remains frozen:

- detector `profiled-resource:varrock-east-iron-v1@2.1.0`;
- profile `varrock-east-iron-v1`, schema 3, location `varrock-east-mine`;
- resource order: north-west, south-west, center, north-east;
- frame `1005x1078` BGRA8888 at reported DPI 96;
- campaign configuration
  `resource-release-campaign:varrock-east-iron-v1@1.1.0`;
- landmark threshold `0.12`, quorum 5 of 6, all three macro zones; and
- unsupported/UNKNOWN policy `zero_targets_and_stop`.

## Required closure inputs

No transition source change may begin until independent review supplies all of
these exact retained roots:

1. source-owned C1 session, completion seal, review package, release summary,
   and follow-up roots for all 15 observations;
2. explicit independent reviewer truth, separate from operator staging labels;
3. permanent replay-adoption roots for every retained real failure;
4. final supported-envelope and renderer review roots;
5. a final resource release decision and lead approval root with no unresolved
   condition; and
6. the exact source commit/tree/blob bindings for the detector, profile,
   reviewed dataset, and adopted replay fixtures.

C1 completion cannot close C2. A proposed replay package is not adoption. A
review decision is not a source receipt. Failed or incomplete evidence remains
retained evidence and requires a fresh source-owned session rather than root
rebinding or a same-session retry.

## Two-commit resource issuance protocol

The canonical release record cannot truthfully bind the commit that embeds the
record itself. Use exactly two reviewed source commits:

### 1. Freeze the release source lineage

Create one clean source-binding commit after C1/C2 review. It adopts every
approved replay fixture and freezes the final detector, profile, dataset,
envelope, and external review roots. Run the complete focused suite, Ruff,
strict Linux mypy, full pytest/CI, and the 36-frame real drift proof on that
exact commit.

That frozen predecessor is the record's source commit. Record its exact commit,
tree, detector/profile/reviewed-dataset Git blob IDs, and derived binding root.
Do not place the receipt grant in this commit.

### 2. Issue the nominal receipt in a narrow direct child

The issuance commit must be one non-merge direct child of the frozen source
commit. Its production change is limited to the four source-owned receipt
constants:

- set the complete gate tuple to empty;
- set issuance allowed to literal `True`;
- embed the strict canonical granted release record; and
- embed the independently reviewed SHA-256 of those exact canonical bytes.

The embedded record must bind the frozen predecessor, not claim to bind the
issuance commit. A fresh process import must construct exactly one sealed
module-owned singleton. The no-argument loader and exact-identity `require`
accessor remain the only access path. The issuance child must add or update
only exact-record, direct-parent, Git reachability, singleton, rebinding, and
non-authority tests plus the corresponding release note.

If any record field, root, digest, parent, reachability proof, or source file is
wrong, import must expose no receipt. Do not repair a failed issuance by
accepting caller data or editing retained roots in place; create a newly
reviewed narrow child or revert the unissued change.

## Inventory dependency

The resource lane must not invent an inventory receipt, detector identity,
profile, configuration, validation root, or approval. The future positive join
requires the C-owned source receipt and unchanged production evaluator. Until
that exact singleton exists and is independently validated, the combined
perception result remains atomic denial.

Resource receipt issuance should precede final inventory receipt issuance when
the inventory protocol binds the resource receipt projection. The inventory
release must never be inferred from a resource grant.

## Future same-cycle assembly change

Only after both nominal receipt singletons exist may a separate reviewed source
change add the A7 positive perception carrier. That implementation must be a
new module; it must not mutate A4's deny-only semantics.

The future source-owned sequence is fixed:

1. load and require both exact receipt singletons before capture;
2. capture one owned frame exactly once;
3. freeze the bytes and compute one payload SHA-256;
4. create one source-cycle provenance value that includes frame reference,
   digest, pixel format, capture configuration, and capture-session identity;
5. pass the identical `Frame` object to both unchanged production evaluators;
6. require exact supported-view resource evidence and known inventory evidence;
7. snapshot both validated projections once; and
8. publish only perception data and packaged available-iron regions.

The existing `PerceptionCycleProvenance` lacks pixel format and capture-session
identity. Do not weaken or retrofit the frozen A4 type. The future positive
carrier must use a stronger internal owned-cycle provenance value.

The generic frame alone does not attest live DPI, window ownership, renderer,
or the full reviewed capture environment. Those values must come from the
source-owned capture boundary and exact release receipts; caller strings cannot
fill the gap.

Any missing, stale, mixed, forged, wrong-frame, wrong-cycle, wrong-lineage,
wrong-environment, unreleased, unsupported, or UNKNOWN input produces the full
A4-style atomic denial: four UNKNOWN resources, UNKNOWN inventory, no regions,
zero targets, and STOP. There is no retry, recapture, camera recovery, or
diagnostic promotion.

## Issue #14 handoff boundary

Issue #14 may later consume one typed perception acquisition snapshot for one
owned frame reference. It must not consume loose observations, caller-selected
regions, or generic `WorldState` fields as trust roots.

The future perception snapshot is still non-authorizing. It contains no target
selection, action intent, click authority, navigation, banking, controller
state, or execution method. Issue #14 must separately define its controller
gate, freshness rules, attempt receipts, post-action observation, and input
authorization. Receipt existence and a positive perception snapshot alone
grant zero input authority.

## Required transition regressions

Before any positive integration is release-eligible, deterministic tests must
prove:

- every resource gate and every retained root is exact and independently
  bound;
- the issuance commit is a single-parent direct child of the frozen source
  commit and the bound source objects are reachable;
- canonical record bytes and the embedded digest match exactly;
- no singleton exists on any partial, malformed, proposed, stale, mixed, or
  rebound configuration;
- equal-but-foreign, reconstructed, subclassed, or monkey-patched receipts are
  rejected by exact identity;
- both receipt singletons are loaded before the only capture;
- one immutable payload, one digest, one cycle, and the identical `Frame` reach
  both evaluators;
- a queued second frame remains untouched after every failure;
- resource and inventory results match frame, cycle, source, configuration,
  environment, release, and receipt roots exactly;
- supported all-depleted and mixed-resource cases expose only observational
  packaged regions, while any UNKNOWN or unsupported case exposes none;
- buffer reuse, mutation, TOCTOU replacement, concurrent calls, and prior-cycle
  replay cannot alter or mix a published snapshot;
- all 36 real drift frames remain UNKNOWN/UNCERTAIN with zero targets; and
- the positive carrier has no `WorldState`, controller, route, action, click,
  input, retry, camera-recovery, or execution surface.

## Dependency order after real evidence closes

1. Finish and independently review real resource C1.
2. Adopt every retained failure into permanent replay fixtures and close C2.
3. Freeze the final resource source-binding commit and rerun exact-head gates.
4. Issue the nominal resource receipt in its narrow direct child.
5. Complete the independently owned inventory release and nominal receipt.
6. Implement and review the new positive same-cycle perception carrier.
7. Provide that typed, non-authorizing snapshot to Issue #14.
8. Open the controller and input gates only in later explicitly authorized
   changes with post-attempt verification.

Until steps 1 through 6 finish, resource and inventory perception cannot feed a
positive mining slice. Until step 8 finishes, no perception result grants input.
