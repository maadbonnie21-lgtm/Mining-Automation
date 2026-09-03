# Inventory V3 release-receipt preparation (corrected C5)

## Status

**PREPARATION ONLY — NO INVENTORY RELEASE RECEIPT EXISTS.**

This corrected C5 is a fresh sibling of rejected PR #66. Its single preparation
commit must be a direct child of frozen C4
`74e2becd41af6b63b230ff11b07536d5da61aa80`.

Rejected C5 head
`2aad6ff304d8af20ea360e43cfcd56a54910814e` remains audit evidence and is not
corrected ancestry. Deleting the rejected runtime file in a descendant would not
repair the frozen-history violation because the repository verifiers inspect
history.

## What this preparation does

The canonical, SHA-256-bound contract at
`validation/inventory_v3_release_receipt_preparation/receipt-contract.json`
records the future receipt constraints without adding executable production
source. It preserves:

- Inventory capacity 28 and publication floor 0.8.
- Wrong-tab and row-obstruction outcomes as `UNKNOWN`.
- `UNKNOWN` as non-actionable and non-bank-transition-authorizing.
- All nine release gates as `OPEN`.
- Receipt issuance, runtime packaging, WorldState, controller, input, activation,
  and bank-transition authority as false.
- The frozen v1/V2 protocol lineage and frozen C4 identity.
- Independent resource-perception release as a required gate.
- A one-shot terminal PASS contract with no retry and pairwise-separated
  operator/reviewer/source-approver roles.

This preparation contains no Python runtime module, no loader, no constructor,
no live capture, no authorization-registry mutation, and no receipt issuance
surface.

## Why PR #66 was rejected

PR #66 added
`src/mining_automation/perception/inventory/inventory_release_receipt.py` after
the frozen Inventory protocol source. The legacy passive-capture, frozen v1
protocol-lock, and Protocol V2 repository verifiers correctly reject executable
history added after their approved source commits. The failure was an ancestry
and placement violation, not an Inventory detector failure.

The corrected C5 therefore adds only `validation/`, `docs/`, and `tests/`
content. It does not weaken or edit any frozen verifier.

## Proof required for this C5

`tests/test_inventory_v3_release_receipt_preparation.py` verifies that:

1. The preparation contract is canonical JSON and matches its exact SHA-256
   sidecar.
2. Every current release gate remains open and every authority bit remains
   fail-closed.
3. Inventory's 28-slot, 0.8 publication-floor, and `UNKNOWN` invariants are
   unchanged.
4. No `mining_automation.*` inventory receipt module exists and this preparation
   cannot issue or grant a runtime receipt.
5. The legacy passive-capture repository verifier still passes.
6. The frozen v1 protocol-lock repository verifier still passes.
7. The Protocol V2 repository verifier still passes.
8. The preparation was introduced by exactly one clean commit whose sole parent
   is frozen C4 and whose changed paths are exactly this contract, its sidecar,
   this document, and the focused test.

## What must happen before any runtime Inventory receipt

A later source-owned runtime receipt is a separate gate, not part of C5. Before
that source action is even eligible, the project must close all of these gates:

1. Live Protocol V2 authorization.
2. Live Protocol V2 campaign execution.
3. Finalized campaign package.
4. Independent reviewer truth.
5. Terminal conformance PASS.
6. Source approval.
7. Production identity approval.
8. Production binding.
9. Released resource perception.

Only after those gates close may a separately reviewed source commit introduce
the runtime receipt. That future receipt must be an immutable source-owned
singleton, caller input must not be able to mint or select it, and it must carry
no direct action, WorldState, controller, or input authority.

Synthetic fixtures and preparation evidence are never real-client release
evidence.
