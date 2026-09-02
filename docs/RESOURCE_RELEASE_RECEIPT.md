# Constrained-v1 resource release receipt readiness

Status: **no receipt issued; all resource release gates remain open**

This checkpoint defines the narrow nominal receipt that a future Issue #14
same-cycle assembly may consume after the real resource campaign and every C2
review/source gate close. It does not approve resource perception, activate
`WorldState`, run a controller, or expose input.

The implementation is
`mining_automation.perception.resource_release_receipt`. It is intentionally
not re-exported from the package-level perception API.

## Current source state

All seven source-owned gates are open:

1. source-owned C1 campaign completion;
2. independent reviewer truth;
3. exact production conformance;
4. permanent replay adoption for every retained failure;
5. final renderer and exact supported-envelope review;
6. exact source Git/tree/blob binding; and
7. final lead/source-release grant.

The source issuance switch is false. There is no packaged granted record, no
approved record digest, and no receipt singleton. The no-argument loader raises
`ResourceReleaseReceiptUnavailable`.

A3 remains the proposal-only release-decision packet. Its record is
`PROPOSED_NOT_GRANTED`; it cannot be passed to this module. A4 remains the only
current same-cycle assembly preparation and always returns four UNKNOWN
resources, UNKNOWN inventory, no targets, and no authority.

## Structural issuance boundary

`ResourceReleaseReceipt` is a sealed, tuple-backed immutable,
non-serializable nominal type with no public constructor. The loader accepts
no path, JSON, digest, approval flag, gate state, identity, threshold, or
policy. A valid receipt can appear only after one reviewed source change
atomically:

- closes every enumerated gate;
- enables the source-owned issuance switch;
- packages one strict canonical granted record;
- embeds the independently reviewed SHA-256 for that exact record; and
- constructs the module-owned singleton during a fresh process import.

The public accessors close over the import-time singleton and its immutable
projection; rebinding module globals cannot replace that capability. A future
consumer must use
`require_source_owned_varrock_east_iron_release_receipt()`; it accepts only
exact object identity with that singleton, not equality, duck typing, a caller
mapping, or a reconstructed object.

## Required future record

The future canonical record must bind all of the following without extra or
missing fields:

- the exact detector ID/version, profile ID/schema, location, and ordered four
  resource IDs;
- a CLOSED source-owned C1 campaign with independent reviewer truth, exact
  production conformance, and the review-package, release-summary,
  completion-seal, and follow-up roots;
- a CLOSED retained-failure partition with no unresolved case and an exact
  permanent-replay-adoption root;
- an independently APPROVED `1005x1078` BGRA8888, reported-DPI-96 envelope,
  including exact window class, `windows-runelite` capture backend, capture
  configuration, renderer identity, no automatic camera recovery, and the
  `zero_targets_and_stop` unsupported-view policy;
- COMPLETE source commit/tree and detector/profile/reviewed-dataset Git blob
  bindings plus their binding root; and
- a GRANTED final source decision with no unresolved condition, exact decision
  and lead-approval roots, `release_eligible=true`, and
  `activation_allowed=false`.

The permanent-replay, approved-envelope, and source-binding roots are
recomputed from their exact component fields during loading. The C1 package
roots and final decision/lead roots remain externally retained content roots;
the canonical record digest binds those exact values rather than pretending
their source artifacts are present in this metadata-only module.

The receipt retains only immutable release-lineage IDs and digests. It carries
no pixels, observations, frame/cycle values, resource states, candidate or
interaction regions, inventory values, targets, `WorldState`, action intent,
or action authority.

## Future Issue #14 boundary

Receipt existence will eventually prove only that the resource perception
lineage was released for the one approved constrained envelope. It will not
prove that a current frame is supported or that a rock is actionable.

A separately reviewed Issue #14 source-owned assembly must still capture owned
bytes once, compute exact same-cycle provenance, run unchanged production
resource and approved inventory perception, verify the current supported view,
preserve UNKNOWN, and require the resource and inventory release receipts.
Only that later work may produce a typed state candidate. Input and controller
activation remain separate later gates.
