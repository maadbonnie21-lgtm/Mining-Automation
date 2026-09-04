# Mining-only 28/28 live runbook — 2026-09-04

## Purpose

Run one uninterrupted real RuneLite mining-only session from any known non-full
Inventory count to exactly 28/28. The command stops on the first uncertainty and
never starts navigation or banking.

## Required client state

- RuneLite is open and logged into the explicitly authorized account.
- The client is in the supported Varrock East iron area used by the preserved
  2026-09-03 proof.
- Client geometry is exactly `1005 x 1078`.
- Windows DPI is exactly `96`.
- Inventory tab is visible and unobstructed.
- RuneLite is visible, unminimized, and foreground.
- The local pose-reference `.bgra` files preserved by the proof run are still at
  the paths referenced by `tools/run_three_rock_continuous_proof.py`.
- The repository checkout is clean.

Do not manually move the camera, resize/minimize the client, switch tabs, hover
Inventory, click rocks, navigate, bank, or interact with the account after the
run starts. A foreground, cursor, geometry, perception, or identity mismatch is a
STOP condition, not a request for manual correction mid-run.

## Read-only plan check

This sends no input:

```powershell
python tools\run_mining_to_full.py
```

## Exact live command

First obtain the exact RuneLite HWND from the existing read-only Windows capture
check. Then, from the clean continuation checkout:

```powershell
$sha = (git rev-parse HEAD).Trim()
python tools\run_mining_to_full.py --live --hwnd <EXACT_RUNELITE_HWND> --authorize-execution-sha $sha --confirm MINE_TO_FULL_28_FAIL_CLOSED
```

The exact Git SHA is checked again inside the command. The command refuses a dirty
checkout, wrong HWND, wrong confirmation token, wrong geometry/DPI, hidden or
minimized RuneLite, wrong foreground, wrong interaction text, tooltip-contaminated
Inventory, Resource UNKNOWN, stale evidence, ambiguous Inventory delta, and any
second click inside an attempt.

## Expected terminal outcomes

### Success

- `success: true`
- `phase: complete`
- `stop_reason: inventory_full`
- `end_inventory: 28`
- `verified_ores == click_count == 28 - start_inventory`
- no 29th click
- navigation not started

### Fail-closed stop

- `success: false`
- one exact stop reason
- no automatic retry for the failed attempt
- all prior event evidence retained in the run directory

## Evidence to return

Return the complete `result.json` and the terminal console output. Raw `.bgra`
frames remain local and should not be committed. A curated successful JSON/PNG/
Markdown receipt may be reviewed and committed separately.
