# RuneLite PREP -> READY runbook — 2026-09-04

## Purpose

Normalize only the supported starting-client conditions needed before a separately
authorized mining-only `0 -> 28` attempt.

PREP is not a miner. It never authorizes or executes a mining click, navigation,
banking, item movement, Resource release, or Inventory release.

## Operator starting state

Tyler does only the human-owned setup that software must not invent:

1. Open and log into RuneLite on the explicitly authorized account.
2. Place the character in the supported Varrock East iron area used by the retained
   2026-09-03 successful mining evidence.
3. Keep the Inventory tab visible and unobstructed. Do not leave the cursor over the
   Inventory tab or slots.
4. Do not manually adjust zoom/camera while PREP is running.

PREP rediscoveries the current RuneLite HWND each session. Do not reuse or paste the
historical HWND from 2026-09-03.

## Step 1 — read-only diagnose

From a clean checkout of the reviewed PREP head:

```powershell
python tools\runelite_prep.py
```

This is the default mode and sends **zero setup/camera input**.

It reports and records:

- exact current HWND plus PID/TID/class/title identity;
- visible/minimized/foreground state;
- measured physical client area;
- DPI;
- all three retained local pose-reference checks;
- gameplay-chrome readiness;
- current Inventory occupancy/confidence or exact UNKNOWN reason;
- current Resource pose/registration verdict at unchanged `0.12`, `5/6`, all `3`
  zones;
- final frame identity/hash;
- one unique `result.json` receipt.

A read-only result may say `NOT READY` without attempting any repair.

## Step 2 — explicit PREP apply, only if needed

If the diagnose reports a condition PREP is explicitly allowed to correct, run:

```powershell
python tools\runelite_prep.py --apply --confirm PREP_RUNELITE_FOR_MINING
```

That confirmation grants **PREP-only** setup authority for this invocation.

The apply path may:

- restore the exact bound RuneLite HWND if minimized;
- explicitly foreground that exact HWND;
- resize the measured **client area** to exact `1005 x 1078`;
- move the cursor to the reviewed neutral client point and settle tooltip state;
- perform the bounded measured camera search using the existing reviewed Windows
  camera primitives.

It may not change global Windows scaling. DPI other than exact `96` is a STOP.

It may not invent an Inventory-tab click. No reviewed Inventory-tab control point was
found in the retained path, so Inventory UNKNOWN/tab/tooltip obstruction is a precise
human-only correction followed by a fresh PREP invocation.

## Camera rule

Camera preparation is closed loop:

`capture/evaluate -> one bounded correction -> settle -> fresh capture/evaluate`

If the current Resource gate already passes, PREP sends zero camera actions.

The initial bounded search retains the measured 2026-09-03 pitch experiment sequence
that immediately preceded the useful recalibrated view (`down 100 ms`, `down 100 ms`,
`up 50 ms`), followed only by one-detent local wheel probes if still necessary. This
sequence is a search order, **not** a READY recipe.

Four zoom-up events are not treated as canonical because the retained
`post-zoom-up-4-20260903` evidence remained `0/6`.

Only the actual frozen Resource gate may publish READY:

- landmark distance threshold `0.12` unchanged;
- at least `5/6` landmarks;
- all `3` required zones;
- supported Resource view.

A better diagnostic distance alone can never grant READY.

## READY means

PREP prints `READY FOR MINING` only after a final clean current observation proves:

- same rebound HWND/PID/TID/class/title identity;
- visible, unminimized, foreground RuneLite;
- measured client area exactly `1005 x 1078`;
- DPI exactly `96`;
- retained local pose references valid;
- gameplay chrome ready;
- Inventory occupancy known with confidence `>= 0.8`;
- Resource supported at unchanged `0.12`, `5/6`, all `3` zones;
- final cursor neutralization completed;
- no incomplete setup/camera/cleanup receipt.

The JSON receipt explicitly keeps mining, navigation, banking, Resource-release, and
Inventory-release authority false and records that PREP authority is relinquished.

## STOP behavior

Do not manually repair the client *mid-run*. PREP stops on the first unsafe or
unsupported condition, preserves the diagnostic receipt/local frames, releases any
PREP-owned held key/button state, and ends.

Typical exact STOP classes include:

- wrong DPI;
- missing retained pose reference;
- HWND/process/thread/class/title identity change or reuse;
- wrong client geometry in read-only mode;
- failed bounded resize/restore/focus;
- gameplay-chrome mismatch;
- Inventory UNKNOWN or confidence below `0.8`;
- partial/short Windows input receipt;
- held input before a camera operation;
- foreground loss;
- bounded camera search exhausted without the frozen Resource gate.

## Handoff to mining

A PREP READY receipt does **not** start mining.

After PREP ends:

1. Preserve the READY receipt path and current HWND printed by PREP.
2. Make no resize, camera, compass, tab, or other PREP mutation during mining.
3. Tyler separately authorizes the controlled mining-only run.
4. Invoke the existing mining-to-full entry point separately under its own exact
   confirmation/SHA/HWND controls.
5. Mining proceeds to exact `28/28` or stops fail closed.
6. Navigation/banking remain outside this work item.

## Current acceptance boundary

Offline tests can prove the PREP controller, safety matrix, and Windows API contracts.
They cannot prove the current physical RuneLite client is READY.

Today's PREP acceptance gate therefore still requires a genuine Windows run that
produces a current-host `READY FOR MINING` receipt before the separate `0 -> 28` test
is authorized.
