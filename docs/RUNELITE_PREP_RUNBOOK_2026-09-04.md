# RuneLite PREP -> READY runbook — 2026-09-04

## Purpose

Prepare and verify the supported starting client state for a separately authorized
mining-only `0 -> 28` attempt.

PREP is not a miner. It never authorizes or executes mining, navigation, banking,
item movement, Resource release, or Inventory release.

## Evidence-driven correction from the independent audit

The retained 2026-09-03 camera experiments do **not** establish a deterministic
camera-normalization recipe. The recorded zoom/pitch/manual-restoration attempts that
were inspected remained outside the frozen Resource gate. The working mining session
came from the retained successful current-view pose references / software registration,
not from proof that a particular wheel or pitch sequence restores a canonical view.

Therefore, for today's P0 path:

- production PREP sends **zero automatic camera input** by default;
- Tyler sets the supported Varrock East mining view once before PREP and then leaves it
  alone;
- PREP may still use the existing software pose/registration path to *measure* the
  current view;
- if the frozen Resource gate does not pass, PREP returns one precise STOP instead of
  poking the camera;
- the typed camera primitives remain in the repository for focused testing/future
  explicitly reviewed evidence, but they are not a READY recipe today.

## Operator starting state

1. Open and log into RuneLite on the authorized account.
2. Place the character at the supported Varrock East iron area used by the retained
   2026-09-03 proof.
3. Set the mining view once by hand, then stop adjusting zoom/camera.
4. Keep the Inventory tab visible and unobstructed. Keep the cursor away from the tab
   and slots so no tooltip covers Inventory evidence.
5. Do not restart RuneLite after discovering the current HWND for the run.

Never reuse historical HWND `3736178`. PREP and the miner must use the current live
window identity.

## Private local pose references

The three successful `.bgra` pose references are private/local inputs by design and are
not required to be tracked by Git. The miner and PREP now treat source cleanliness as
**tracked-file cleanliness** while still requiring the private pose references to exist
and verify exactly.

Required local references:

1. `diagnostics/different-rock-ore3-20260903/ore-01-clean.bgra`
2. `diagnostics/third-rock-ore4-20260903/ore-01-clean.bgra`
3. `diagnostics/third-rock-ore4-20260903/ore-04-reacquired.bgra`

The mining live path calls `verify_local_pose_references()` before constructing the
live mining backend. Missing, wrong-size, or invalid references produce STOP before any
mining input can be sent.

## Step 0 — mining plan, no live input

Before authorizing a live run, verify the mining wrapper itself remains fail closed:

```powershell
python tools\run_mining_to_full.py
```

Expected plan properties include:

- `mode = read_only_plan`
- `live_input_performed = false`
- geometry `1005 x 1078`
- DPI `96`
- Resource threshold `0.12`
- Resource landmarks `6`, quorum `5`, required zones `3`
- Inventory floor `0.8`, capacity `28`
- maximum one click per attempt
- navigation does not start on FULL
- three local pose references required
- tracked checkout must be clean
- untracked private pose references are permitted
- camera preparation authority is false

## Step 1 — read-only PREP diagnose

From the exact reviewed PREP checkout:

```powershell
python tools\runelite_prep.py
```

The default mode sends **zero setup or camera input**.

It records:

- current HWND and PID/TID/class/title identity;
- visible/minimized/foreground state;
- physical client area;
- DPI;
- exact local pose-reference verification;
- gameplay-chrome readiness;
- Inventory occupancy/confidence or exact UNKNOWN reason;
- Resource pose/registration result at unchanged `0.12`, `5/6`, all three zones;
- final frame identity/hash;
- a unique `result.json` receipt.

A read-only result may say `NOT READY` without attempting repair.

## Step 2 — explicit PREP apply, only for approved setup corrections

If diagnose reports a condition PREP is explicitly allowed to repair, run:

```powershell
python tools\runelite_prep.py --apply --confirm PREP_RUNELITE_FOR_MINING
```

Apply authority is PREP-only. It may:

- restore the exact rebound RuneLite HWND if minimized;
- foreground the exact rebound HWND;
- resize the measured **client area** to exact `1005 x 1078`;
- neutralize the cursor and settle tooltip state;
- recapture and reevaluate the current view.

It does **not** automatically alter camera/zoom today.

It does not change global Windows scaling. DPI other than exact `96` is STOP.

No reviewed Inventory-tab control point was found in the retained path, so PREP does
not invent a tab click. Inventory UNKNOWN/tab/tooltip obstruction is one precise
human-only correction followed by a fresh PREP invocation.

## READY gate

Only the actual frozen gate may publish READY:

- exact same rebound HWND/PID/TID/class/title identity;
- visible, unminimized, foreground RuneLite;
- client area exactly `1005 x 1078`;
- DPI exactly `96`;
- all three local pose references verify;
- gameplay chrome ready;
- Inventory occupancy known at confidence `>= 0.8`;
- Resource supported;
- exactly six unique retained landmark-distance records are present;
- at least `5/6` are within unchanged `0.12`;
- matched landmark count is at least `5/6`;
- exact required zone set is `north_west`, `north_east`, `south_west`;
- final cursor neutralization and cleanup complete.

A diagnostic score or a view that merely looks closer can never grant READY.

The receipt explicitly keeps mining, navigation, banking, Resource-release, and
Inventory-release authority false and records that PREP authority is relinquished.

## If PREP says Resource view is unsupported

STOP. Do not run an automatic camera search.

Tyler may set the supported starting mining view once and rerun read-only PREP. The
software may validate the view through the retained pose/registration path, but it does
not weaken the `.12 / 5-of-6 / three-zone` gate.

## Handoff to the miner

A PREP READY receipt does **not** start mining.

After PREP returns READY:

1. Preserve the READY receipt path and current HWND.
2. Make no resize, camera, compass, tab, or other PREP mutation during mining.
3. Tyler separately authorizes the exact mining execution SHA.
4. Invoke the mining wrapper separately:

```powershell
python tools\run_mining_to_full.py --live --hwnd <CURRENT_HWND> --authorize-execution-sha <EXACT_REVIEWED_SHA> --confirm MINE_TO_FULL_28_FAIL_CLOSED
```

5. Mining proceeds until `28/28` **or the first fail-closed STOP**.
6. Preserve the result either way. Navigation/banking stay outside today's mining-only
work item.

## Expected outcome for today's experiment

`28/28` remains the target, not a guaranteed result. Real evidence currently proves
repeated mining and fresh reacquisition across all three configured rocks, with the
strongest uninterrupted proof covering three ores. The three retained pose references
may not cover every new player landing position encountered over a full inventory.

A safe STOP after new-position reacquisition failure is a valid diagnostic outcome and
must not be bypassed by weakening thresholds, adding blind retries, or automatically
fiddling with the camera.

## Do not run stale proof tools

Do not use the old standalone tools that embed the historical HWND/start state for
today's run, including `run_three_rock_continuous_proof.py` and other historical
proof/calibration wrappers with fixed HWND constants. Use `runelite_prep.py` plus
`run_mining_to_full.py --hwnd <CURRENT_HWND>`.

## Current acceptance boundary

Offline tests can prove the PREP controller, Windows boundary, private-reference
preflight, exact fail-closed gates, and mining-plan invariants. They cannot prove the
current physical RuneLite client is READY or that an unrun full-inventory endurance
attempt will reach `28/28`.

Today's final PREP acceptance therefore still requires a genuine Windows-host
read-only/apply invocation that produces a current-host `READY FOR MINING` receipt.
