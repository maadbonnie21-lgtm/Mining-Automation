# Constrained-v1 perception release-gate audit

Status: **not release eligible; detector/trust/replay audit complete, but the
release-evidence boundary and C1/C2 gates remain open**

This ledger supersedes the historical blocker lists in PRs #10 and #12. It
audits the newest accepted lineages without merging or modifying the frozen
inventory or deny-only branches.

## Exact audited inputs

| Input | Exact identity | Role |
| --- | --- | --- |
| Inventory V3 candidate | `5975532b472a74d93f010e04ca44b2efa2a3ffd7` | Frozen development candidate |
| Inventory protocol/readiness | `32764bfd82afb46d4e99292bab7d162be536e2d7` | Frozen protocol lock and independent-validation path |
| Inventory protocol source P | `b3b141e0d9ca15d729eaa98c795f6c855bff68cf` | Parent whose direct lock-only child is the readiness head |
| Resource assembly/trust | `225ea7525ee21b5161f584b4daaad90551d65b31` | Base of this audit; accepted resource production lineage |
| Deny-only integration reference | `d8fd03d8087732a2f3314da4a0c25edb5d134f55` | Frozen, nonactivating reference only |

The resource detector is
`profiled-resource:varrock-east-iron-v1@2.1.0`, profile schema v3, location
`varrock-east-mine`. The packaged profile SHA-256 is
`317bd4f7d3e239874317bb9379a92d2541abac194039b82f4b0c02cc99844989`
(Git blob `2259232823f0887acda93991d8e98eb75af3af03`). The reviewed replay manifest
SHA-256 is
`f86f68822ecc5a7a9ab678763fe04c8e2a60e5661010c447c6f0f76b4ddaa305`
(Git blob `0448a713927e5148b02804b6f7c32fc83afb41de`).

## Product envelope

Constrained v1 supports only the exact proven fixed Varrock East view at
`1005x1078` BGRA8888. Automatic camera normalization, camera hunting, and
arbitrary reacquisition are retired from the v1 critical path. A scene the
production detector cannot validate remains UNCERTAIN, exposes zero targets,
and requires STOP. Diagnostic registration never changes that verdict.

Reported DPI `96` is the required **candidate** value for the constrained
resource envelope, pending fresh source-owned capture and final review. It is
not packaged-profile identity and is not already proven by the stored replay
fixtures. Missing or non-96 DPI evidence may be retained as a failure, but it
cannot satisfy the supported-envelope gates.

`ProductionResourceTrustResult.accepted` means that a complete ensemble passed
the resource identity and shape checks. It is not scene or action authority.
An identity-valid all-UNCERTAIN ensemble retains four explicit unknown states
but has zero actionable targets. The frozen PR #38 reference additionally
demonstrates that any resource uncertainty denies downstream authority; it is
not activated by this audit.

The machine-readable resource record sets `release_eligible=false`. The open B
resource release-evidence boundary is separate from C1 fresh empirical evidence
and C2 evidence-contingent source/review gates. Closing every C1 capture/result
does not close C2 and cannot self-promote either perception lineage.

## PR #10 inventory ledger

### A. Already closed

| ID | Closed fact | Evidence |
| --- | --- | --- |
| INV-A1 | The platform-neutral 4x7 layout, 28-slot ownership, reviewed profile/reference seam, strict adapter, obstruction guards, replay path, and UNKNOWN/0.0 behavior exist. | PR #10 foundation and its regression suite |
| INV-A2 | The old V1/V2 clean partial/full publication defect is superseded. Frozen V3 returns exact empty, partial, and full counts on the 16-case development corpus without lowering the `0.8` publication floor; unsupported presentations remain UNKNOWN. | Frozen candidate `5975532b...`; development report SHA-256 `f42a4e9bbd9bcbf591c81c79f9dce3ef42c15e810ff381d6673ff1c6afaaa0b2` |
| INV-A3 | V3 model, constants, prototype set, configuration, and development-only evidence role are frozen. Development cases cannot become independent evidence or training input. | Configuration `inventory-positive-v3-development-4ee2d01517447655700bd0d49637b3f4221edcc950a07c270e0717c52999e72d`; model artifact SHA-256 `0722f1922c88ec011099fe83980b44f2b77637e2aa49a58d87245ff208cdf469` |
| INV-A4 | The independent campaign protocol, passive capture binding, contamination firewall, immutable report path, and P-then-L history are accepted and locked. | Protocol lock SHA-256 `64ab45f8b0294f733c4517ad46ebb01e722f3fbf3d14d52feb79649b5a3649f1`; preregistration SHA-256 `47db5a775095b7828e1c10d19949519002d5c7540eaf8d3c18e0eb3154bd9130`; readiness report SHA-256 `8e10911aa7752b8ebc35695d64cd8272fd877ab343c6cfd2361d6d16f11abe61` |
| INV-A5 | Inventory UNKNOWN and an unvalidated V3 result are proven incapable of granting mining/banking authority in the accepted readiness and deny-only contract tests. | PRs #37 and #38, both frozen and nonactivating |

### B. Offline blockers closed by this audit

There is no remaining executable inventory defect that can safely be changed
offline without violating the frozen PR #37 protocol. This audit closes the
documentation gap by defining the exact post-campaign decision below and by
reusing the accepted evaluator. It does not add a validator, approval, live
authorization, production export, or pixel.

PR #37 must retain ordinary history. Squash, rebase, or cherry-pick changes the
P/L Git identities and invalidates the current protocol lock.

### C1. Fresh empirical evidence required

1. A separately reviewed source-owned live authorization is a prerequisite. No
   inventory campaign may run before it exists.
2. One irrevocable passive seven-case campaign is required in source order:
   `empty`, `early-partial`, `mid-partial`, `near-full`, `full`, `wrong-tab`,
   `row-obstruction`. Every owned capture remains in the campaign.
3. Reviewer truth must be written after package finalization by a reviewer
   distinct from the operator.
4. The frozen V3 candidate must agree exactly with every independently
   reviewed case under the locked evaluator. A miss is a validation failure,
   not permission to tune V3 against the campaign.

### C2. Evidence-contingent source/review gates

1. A source-owned approval-registry entry must bind the exact package,
   campaign, session, completion seal, reviewer truth, and report hashes.
   Its approver must be distinct from both operator and reviewer.
2. After a nonactivating independent PASS, a separate reviewed production
   change must bind the approved detector/profile/configuration/reference path.
   The frozen candidate is deliberately not a production export today.

The inventory release gate stays open until every C1 authorization, campaign,
reviewer-truth, and conformance requirement and every C2 approval and
production-binding gate closes.

## Exact inventory post-campaign decision

Use the existing locked evaluator; do not create a second validator:

```powershell
python -I -S tools/inventory_v3_independent_validation.py evaluate --dataset <finalized-independent-package-directory> --output diagnostics/inventory-positive-v3-independent-results/<exact-head> --expected-head <exact-clean-head>
```

The release decision is FAIL unless all of the following are true:

- report schema is
  `inventory-positive-v3-independent-validation-report-v2` and evaluator is
  `inventory-positive-v3-independent-validation@2.0.0`;
- the exact candidate, protocol lock, preregistration, model, prototype,
  profile, reference, capture configuration, and source Git blobs match the
  identities above;
- the seven cases are complete and source ordered, with reviewer counts
  `empty=0`, `0<early<mid<near<28`, and `full=28`;
- all five positive results have the exact label/count and confidence at least
  `0.8`; wrong-tab and obstruction are UNKNOWN/null at `0.0`;
- every case passes, with no failure reason;
- candidate identity and analyzer state are identical before and after the
  run, all contamination/training/export guards pass, and every package,
  session, capture, completion-seal, and reviewer-truth hash is immutable;
- `detector_conformance_passed=true`;
- a source-owned exact approval is present, `validation_passed=true`, and
  `validation_status=independent-validation-passed-nonactivating`; and
- the canonical report has its matching adjacent SHA-256 sidecar while
  `activation_allowed=false`, `promotion_allowed=false`, and every action
  authority remains false.

Before the source-owned approval exists, detector conformance may be reported
but the release decision remains FAIL/approval-required. A validation region
that happens to be byte-identical to development pixels must be disclosed; it
is not by itself proof of contamination or independence. Durable capture
provenance is the deciding evidence.

## PR #12 resource ledger

### A. Already closed

| ID | Closed fact | Evidence |
| --- | --- | --- |
| RES-A1 | Production scene authority remains frozen-coordinate structural evidence: 5 of 6 world landmarks across all 3 macro zones. Candidates and fixed UI cannot establish scene identity. | Schema-v3 profile and Issue #18/#22 regressions |
| RES-A2 | All five reviewed fixtures pass exact production states, including all-node available, the south-west available/depleted/respawn cycle, and mixed state. Cardinal 2px replay jitter preserves every exact state; tested 3px/4px shifts fail closed. | Reviewed dataset `varrock-east-iron-v1` and production replay tests |
| RES-A3 | Unsupported scenes, scene drift, geometry mismatch, and candidate uncertainty preserve UNKNOWN/UNCERTAIN and never invent a target for the uncertain resource. | Resource detector and adapter regressions |
| RES-A4 | Detector/profile/schema/location/resource IDs, exact current `FrameRef`, candidate regions, completeness, duplication, and mixed/stale evidence are checked fail closed. | `test_resource_production_trust.py` |
| RES-A5 | The source-owned production path captures once, constructs the packaged detector internally, runs the guarded contract, and binds trust to that exact frame. No detector, observation, identity, frame-token, or policy injection is exposed. | `capture_detect_trust_varrock_east_iron()` and pipeline tests |
| RES-A6 | Gzip replay materialization verifies reviewed payload hashes, uses one immutable manifest snapshot, writes the manifest last, and preserves a winning concurrent writer. | Accepted resource head `225ea752...` |
| RES-A7 | The complete real drift corpus is fail closed: 36/36 UNCERTAIN and zero false definitive targets. | Report SHA-256 `50cadb524bd4e54dd2bcbfe80fd9f4a9b7bb27cb5b041433ca0a151828c1b788` |

### B. Offline blockers

#### B1. Closed by this audit

1. The structured resource record incorrectly named detector `1.0.0`; it now
   binds `2.1.0`, profile schema v3, exact geometry/pixel format, candidate
   identities/regions, and the zero-target/STOP camera policy. A regression
   prevents this record from drifting away from the packaged profile.
2. The old release boundary still required client-restart camera
   reacquisition. It now describes the exact-view-only v1 policy and removes
   automatic reacquisition as a gate.
3. A source-owned startup regression now queues an unsupported frame followed
   by a supported one and proves the operation captures exactly once, returns
   four UNKNOWN resource states, exposes zero actionable targets, and performs
   no retry or camera fallback.

No further defect is proven inside the accepted resource-base production
detector, runtime trust, or reviewed-fixture replay-materialization path. This
statement does **not** close the release-evidence boundary or the subsequent
C1/C2 gates below. Thresholds, 5/6 quorum, three-zone policy, candidate policy,
and scene authority remain unchanged.

#### B2. Release-evidence boundary still open

The generic development capture/annotation path cannot make future real
captures count as release evidence. A separate passive, source-owned boundary
must bind the exact repository and capture build, detector/profile/schema/
location, geometry and DPI requirement, capture configuration and fixed case
vocabulary; finalize immutable raw/report/package hashes before independent
review; and reject caller labels, replacement, duplicates, foreign/stale/
partial evidence, and concurrent collisions. Until that boundary is reviewed,
resource C1 evidence collection is not authorized.

### C1. Fresh empirical evidence required

1. One reviewed real available/depleted/respawn sequence for each unproven
   node: north-west, center, and north-east. The south-west cycle cannot prove
   the other nodes' provisional shared depleted signature.
2. One genuinely captured obstruction over a profiled sample/landmark. The
   existing synthetic mutation of real pixels proves the mechanism, not the
   live presentation.
3. One reviewed real unsupported-location case at the exact client geometry,
   expected to produce four UNCERTAIN states and zero targets.
4. Reviewed real negatives for a neighboring copper rock, a neighboring tin
   rock, and terrain clutter, each proving no false iron target.
5. One fresh current-client positive startup capture already in the exact
   reviewed `1005x1078` BGRA supported view, reporting candidate DPI `96`.

### C2. Evidence-contingent source/review gates

1. Final lead review must approve the exact client, candidate DPI `96`,
   renderer, profile, and supported-view operating envelope. A C1 image that
   merely reports `96` does not perform this review.
2. Every retained failure must be promoted through the privacy-safe permanent
   replay-regression path before its associated release gate can close.
3. A resulting source-owned constrained-v1 resource release/promotion record
   must bind the exact reviewed package, reviewer truth, production results,
   permanent replay promotions, and approved envelope. It remains separate
   from C1 detector results.

Even a complete C1 campaign leaves `release_eligible=false` until every C2
source, review, promotion, and approval gate is explicitly closed.

Any detector/profile-changing head must also rerun the complete 36-frame drift
set and preserve 36/36 UNCERTAIN with zero false definitive targets. None of
the missing cases may be synthesized and described as real.

## Retired stale requirements

- Camera reacquisition, client-restart camera normalization, and Issue #31
  R2.x research are not constrained-v1 perception gates.
- V1/V2 partial/full confidence failures are not current candidate blockers;
  frozen V3 supersedes them without lowering the `0.8` floor.
- Independently captured but byte-identical static inventory pixels are not
  automatically invalid; they require disclosure plus durable independent
  provenance.
- Hover, drag, selected-item, quantity-text, and wide-sprite presentations are
  outside the positive v1 inventory envelope. If encountered they remain
  UNKNOWN; the preregistered release negatives are wrong-tab and row
  obstruction.
- Multi-theme, multi-renderer, multi-DPI, and multiple client-mode support are
  not required for the one exact v1 envelope. A different environment has
  zero authority until separately reviewed.

## Downstream dependency order after both perception gates close

The current `WorldState` and `MiningController` are scaffolding only. In
particular, the current controller can fall through from UNKNOWN inventory,
and no interaction, navigation, banking, route/checkpoint, bank-interface, or
deposit implementation/evidence exists. Nothing in this audit activates them.

The required order is:

1. Integrate the approved perception lineages and add a new source-owned,
   same-cycle activating assembly that computes provenance from owned bytes.
   Do not mutate PR #38's frozen deny-only schema.
2. Implement Issue #14 atomic acquisition `WorldState` fusion: one exact
   frame, explicit delivery, ordering/provenance, wholesale resource
   replacement, preserved uncertainty reasons, and typed per-objective
   readiness. No input.
3. Implement the exact-view closed-loop mining state machine. Select only a
   fresh packaged available target with known non-full inventory; an input is
   only attempted until a fresh observation proves the outcome.
4. Define and validate versioned route/checkpoint/localization contracts and
   real replay evidence. The mine scene detector cannot prove route position.
5. Implement the fixed mine-to-bank route one bounded, freshly verified step
   at a time.
6. Add bank object/interface perception and the verified open/deposit/empty
   workflow.
7. Validate and implement bank-to-mine as its own direction. Arrival must
   already satisfy the exact supported mine view; otherwise STOP.
8. Run repeated complete cycles, fault injection, regression, and endurance
   validation before later scheduler/GUI/package release work.

At every layer, downstream code may narrow perception authority but may never
promote UNKNOWN, stale, mixed, wrong-identity, unsupported-scene, or
unapproved evidence. Attempted input never proves success.
