# Inventory release receipt readiness

## Status

The Inventory V3 release receipt is **not issued**.

`src/mining_automation/perception/inventory/inventory_release_receipt.py` is a
deny-first source boundary. Every required gate is currently open, source-owned
issuance is false, no granted record is packaged, and the approved record digest
is absent. The no-argument loader therefore always raises
`InventoryReleaseReceiptUnavailable` in the current source configuration.

This work does not authorize a live Inventory Protocol V2 campaign, approve a
campaign, publish a production detector/profile, activate `WorldState`, or grant
controller/input authority.

## Required gates

A later reviewed source change may package the one immutable singleton only after
all of these independently established conditions close:

1. exact Protocol V2 live authorization from a direct child of frozen L2;
2. passive seven-stage campaign execution at that exact authorization head;
3. an exact finalized campaign package;
4. independent reviewer truth and reviewed-package acceptance;
5. terminal one-shot evaluator PASS with retry disabled;
6. source approval;
7. separate production-identity approval;
8. exact production binding; and
9. the independent resource-perception release receipt.

Closing one gate, setting an issuance boolean, supplying a record, or supplying a
digest is insufficient. All gates, the canonical record, its exact digest, and
the issuance switch must change together in one reviewed source diff followed by
a fresh process import.

## Bound record

The future record binds:

- frozen Protocol V2 L2 commit and lock digest;
- frozen C3 and C4 preparation heads;
- exact live authorization commit and capture execution head;
- authorization, campaign, dataset, and session IDs repeated across every stage;
- campaign, review, evaluator, source-approval, production-identity, and
  production-binding roots;
- the Inventory V3 capacity of 28 and publication floor of `0.8`;
- `UNKNOWN` wrong-tab and row-obstruction semantics with
  `occupied_slots=None` and reason preservation;
- a separately issued resource-perception release root; and
- a final non-activating lead decision that cross-binds every stage root.

Each stage is canonically rooted. The final decision embeds the exact stage-root
map, preventing a valid stage from a foreign or replayed campaign from being
silently substituted. Operator, reviewer, and source approver identities must be
pairwise distinct. Authorization, source approval, production-identity approval,
and production binding must be separate source commits.

## Receipt capability

`InventoryReleaseReceipt` is a sealed immutable tuple subtype with no public
constructor and no serialization path. Its public data is release-lineage
metadata only. It exposes no inventory state, occupied-slot count, regions,
targets, `WorldState`, controller, banking, click, or input capability.

The public loader takes no arguments. A future consumer must additionally prove
object identity with the import-time source-owned singleton by calling
`require_source_owned_inventory_release_receipt`. A copied, reconstructed,
duck-typed, or second tuple instance is rejected even when its visible values
match.

## Current authority

- live Inventory capture: **false**
- source approval: **false**
- production release: **false**
- Inventory release receipt present: **false**
- resource-perception release satisfied: **false**
- `WorldState` authority: **false**
- controller authority: **false**
- input authority: **false**

**LIVE INVENTORY CAMPAIGN NOT AUTHORIZED.**
