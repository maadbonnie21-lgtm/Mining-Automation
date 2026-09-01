# ADR 0002: Deny-only navigation production-binding decision

- Status: Proposed
- Date: 2026-09-01

## Context

The fixed-route foundation has separate synthetic evidence and causal-replay contracts, but a
future release review needs one deterministic place to show exactly which mine-to-bank and
bank-to-mine identities are bound and which release claims remain absent. A conforming synthetic
package, an approved synthetic review, or a completed offline route session must not become a
shortcut to production support, live input, endpoint state, WorldState, or controller activation.

This decision covers read-only, offline release-decision readiness. It does not approve route
geometry, collect real-client evidence, execute a route, open the bank, detect a mining view, or
authorize input.

## Decision

### Exact two-direction intake

`navigation.release_decision.evaluate_navigation_release_readiness` accepts exactly one named
`mine_to_bank` slot and one named `bank_to_mine` slot. Each slot supplies separate durable
acquisition/review roots, caller-owned durable and causal pins, and one factory-issued offline
route-session result. The evaluator snapshots every caller graph, evaluates each path-like value once, rejects
overlapping roots, and performs strict interleaved double intake:

```text
mine_to_bank -> bank_to_mine -> mine_to_bank -> bank_to_mine
```

Both reads of each direction must reconstruct the same package, reviewer truth, report, external
pins, and physical root/tree identities. Each strict intake rejects changes during its own
verification, and repeated snapshots reject changes observed between the two snapshots of a
direction. This produces a detached read-only decision, not an atomic live lock across four roots;
future use requires a fresh evaluation. All four transaction roots must be physically distinct.

The returned direction keeps exact detached strict-intake and offline-result snapshots as
non-authoritative internal anchors. Public projections are separately reconstructed and checked
against those anchors, including reviewer truth, physical tree identities, endpoint report,
post-attempt provenance, result digest, and fixed matrix rows. Exact contract types are required at
every serialized boundary so a subtype cannot override serialization to inject authority.

The direction lineages must have different campaign, capture-session, route-plan, package,
acquisition, review, and finalization identities. Opaque route IDs and version text may be shared
because direction is part of the typed route identity. A return plan is never inferred by reversing
the outbound plan.

The two plans must share exact logical mine and bank endpoint contracts in opposite roles. This
coherence does not claim that either location is production-supported.

### Bound identities

For each direction the decision retains:

- the full typed route identity/version, endpoints, ordered checkpoints, and adjacent steps;
- the route-plan digest;
- detector and profile identities, including profile content digest;
- frame geometry and pixel format;
- capture build, configuration, environment, and support-envelope digests;
- capture source and session;
- campaign, package, acquisition-head/journal/finalization lineage;
- acquisition physical root identity and stable tree-identity digest;
- review ID, reviewer ID, independent-review/plan/journal/finalization lineage;
- review physical root identity and stable tree-identity digest;
- every source-loaded reviewer case decision and reviewed match; and
- caller-owned pins for the exact route digest, route session, attempt source, and offline policy;
  and
- a detached offline post-attempt causal graph for those exact pins.

Storage paths are diagnostic storage slots, not evidence identities. Capture configuration,
environment, and support-envelope SHA-256 values prove exact equality only; they do not explain or
attest their opaque contents.

### Post-attempt causality boundary

The offline causal binding records each completed step proposal, exact attempt/source/session,
receipt preparation and post-attempt boundaries, receipt-recording time, departure-frame
provenance, and the fresh expected-next-checkpoint frame. The terminal step requires explicit fresh
arrival evidence after its receipt. Missing, stopped, incomplete, stale, or non-post-receipt chains
remain a deterministic `not_satisfied` matrix result.

The offline policy pins exact `max_frame_age_s`, `minimum_confidence`, and
`max_attempt_receipt_age_s` values and records `support_attested=false`. It is only an exact
architecture-test binding. No production policy or support authority is inferred from it, so
production navigation-policy attestation remains `not_satisfied`.

Every current attempt receipt is synthetic, permanently non-authoritative, and does not prove
movement. The passive durable checkpoint package and offline session result are two separately
bound architecture graphs; B1 does not claim they were produced by one real traversal.
`real_post_attempt_causality_satisfied` therefore remains false even when the offline graph
conforms.

### Supported-host assumptions are requirements, not observations

The decision records these fixed future requirements:

```yaml
required_target_platform: win32
required_host_threat_model: trusted_single_windows_user_host_v1
required_namespace_contract: trusted_non_hostile_dedicated_parent_namespace_v1
```

The existing durable manifests do not attest those facts. An opaque environment digest cannot be
relabelled as Windows/namespace proof, so `supported_host_namespace_attested=false`. The frozen
pathname writer also retains `DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE=false`. Both are explicit
release blockers.

### Closed deterministic matrix

Matrix rows are source-owned enums emitted in fixed direction and requirement order. A row may be
`bound_offline` or `not_satisfied`; it is never caller-supplied `PASS`, and every row has
`release_authority=false`. A structurally valid reviewer rejection or stopped causal result returns
an auditable denial. Malformed, rebound, cross-direction, mutated, or changing graphs are integrity
errors and produce no decision.

The top-level decision is mechanically fixed:

```yaml
writer_future_real_evidence_eligible: false
supported_host_namespace_attested: false
real_release_role_satisfied: false
release_eligible: false
live_navigation_enabled: false
world_state_activation_allowed: false
controller_activation_allowed: false
activation_allowed: false
input_authority: false
```

Route arrival remains narrow. Mine-to-bank arrival does not prove the bank interface is open;
bank-to-mine arrival does not prove a supported mining view. Both downstream handoffs remain
ineligible.

## Consequences

Release review can compare one canonical JSON document and digest while preserving the exact
reason every real-release claim is denied. Direction packages cannot cross-satisfy, synthetic
success cannot acquire authority, and negative reviewer or causal outcomes remain visible rather
than being relabelled or skipped.

The evaluator performs repeated full read-only intake, so it favors coherence and auditability over
speed. It deliberately does not define generalized pathfinding, a map engine, live navigation, a
production registry, WorldState, controller plumbing, input, banking behavior, repeated traversal
thresholds, fault campaigns, or endurance campaigns. Those require separately owned milestones.

`NO LIVE NAVIGATION / NO WORLDSTATE / NO CONTROLLER ACTIVATION`
