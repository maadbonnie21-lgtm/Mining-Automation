# Iron-only banking program - live development result

The standalone banking component is built on navigation baseline b99e021.
Working mining/navigation source and the ROUTE-PROGRAM/LIVE worktrees are unchanged.

## Actual live evidence (2026-09-07)
- Program opened the bank on e28fbfd55fc1d75e3de12c6bf2ebde6ee85a6a5f.
- Opening evidence: outputs/bank-program-20260907-162110/.
- Deposit and X-close passed on bc1d941070e876c9fd8329e13f79561a1f85a5a0.
- Final evidence: outputs/bank-program-20260907-162359/result.json.
- Inventory: 28 verified iron ore before, 28 empty slots after.
- Independently viewed bank stack: 1377 before, 1405 after (increase 28).
- Program clicked the bank X and verified normal inventory tabs and empty inventory.
- Final run: two program-selected clicks (iron deposit and X-close), no human rescue.
- Start/end native window snapshots are identical. No geometry or camera changes.
- The final run began with the bank already open following an earlier program run.
  This is staged live proof, NOT one uninterrupted closed-bank-to-closed-bank pass.
- Combined mining, navigation, deposit and return execution remains separate work.

## Mechanism and fixes
The component identifies the booth visually, verifies bank title plus X, and
identifies the 28 iron sprites specifically in the player-inventory panel.
It checks the bank Quantity-All control is visibly selected before clicking iron.
It never uses deposit-entire-inventory or deposit-equipment controls.
Hover text is diagnostic only: capture sampling varied, and hover overlays covered
both the bank title and inventory cells. Fresh clean observations clear those overlays.
Two newer empty observations are required before X-close; closure is also verified.
Unknown/mixed inventory, stale input, changed window, and unverified UI stop the run.

Entry: tools/run_bank_iron.py --help. No live action runs without explicit arguments.
