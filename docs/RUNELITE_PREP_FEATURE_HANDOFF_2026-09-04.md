# P0 RuneLite PREP -> READY Handoff — 2026-09-04

## Mission

Deliver the smallest reliable pre-mining feature needed for today's controlled real 0->28 mining-only run.

Owner priority today is explicit:

**PREP -> READY -> Tyler authorizes -> 0/28 -> 28/28 or fail-closed STOP.**

Banking, navigation polish, GUI, scheduling, broad integration and generalized hardening are secondary unless they directly remove a blocker to this sequence.

## Starting line

Repository: `maadbonnie21-lgtm/Mining-Automation`

Preserve mining PR #84 unchanged while PREP is implemented:

- branch: `chatgpt/mining-loop-28-ready-2026-09-04`
- exact head: `480c7524d3f9195281ed9dec9ce248da661999dd`

PREP branch already exists as a direct child:

`chatgpt/runelite-prep-ready-2026-09-04`

Do not rewrite #84 merely to add setup convenience.

Read first: `AGENTS.md`, `docs/MASTER_SPECIFICATION.md`, `docs/ARCHITECTURE.md`, `docs/ACCEPTANCE_CRITERIA.md`, `docs/LIVE_MINING_PROOF_2026-09-03.md`, `docs/MINING_LIVE_LOOP_AUDIT_2026-09-04.md`, `docs/MINING_TO_FULL_RUNBOOK_2026-09-04.md`, and current #84 comments.

## Proven real behavior to preserve

The 2026-09-03 evidence includes real 0->1, repeated mining after movement, different-rock mining, all three configured rocks individually, and one uninterrupted real 7->10 run with northwest -> southwest -> center, exactly 3 clicks, zero Tyler intervention, fresh Resource+Inventory reacquisition after movement, final Resource 6/6 across all 3 zones, and Inventory 10/28 at confidence 1.0.

The miner's proven order remains:

`clean current state -> select -> hover prove Mine Iron rocks -> one click -> passive +1 -> fresh post-movement reacquisition`

PREP normalizes the **starting** client only. It must not reset camera/zoom after each ore.

## Real setup failures that must not be repeated

1. Earlier automation/control restored/resized RuneLite to `1005 x 687`. Supported client geometry is exact **1005 x 1078** at **DPI 96**.
2. An initial real scene was **0/6 landmarks** because zoom/pitch/reference geometry differed.
3. `post-zoom-up-4-20260903` was still 0/6. Therefore four wheel events alone are not a canonical recipe.
4. Later `recalibrated-fresh-validation-20260903` detected available iron again and matched its retained reference closely. Reconstruct what actually helped from evidence instead of inventing a new camera ritual.
5. Inventory-tab hover can overlay an `Inventory` tooltip and force Inventory UNKNOWN. Clean perception must neutralize the cursor and settle first.

Inspect the exact historical evidence before writing camera policy, especially:

- `diagnostics/live-proof-resource/reports/preflight-supported-view.json`
- `zoom-trial-1.json`
- `post-zoom-up-4-20260903.json`
- `current-geometry-diagnosis-20260903.json`
- `foreground-focused-diagnosis-20260903.json`
- `post-down-hold-100ms-20260903.json`
- `post-down-hold-200ms-total-20260903.json`
- `post-up-hold-50ms-20260903.json`
- `recalibrated-fresh-validation-20260903.json`
- `manually-restored-view.json`
- wide-registration evidence

Do not assume filename chronology alone proves causality.

## Reuse existing code — do not rebuild

Window/camera primitives already exist:

- `src/mining_automation/validation/windows_camera.py`
- `src/mining_automation/validation/_camera_win32_calls.py`
- `src/mining_automation/validation/camera_plan.py`
- `src/mining_automation/validation/camera_coordinates.py`

These already provide/bound exact HWND identity, process/thread/class/title checks, foreground checks, client geometry checks, camera wheel, bounded middle drag, compass click, arrow-key control, coordinate mapping and cleanup.

Readiness/calibration assets already exist:

- `src/mining_automation/validation/client_readiness.py`
- `src/mining_automation/perception/live_pose_references.py`
- `tools/windows_capture_check.py`
- `tools/validate_varrock_east_live.py`
- `tools/solve_current_view_calibration.py`
- `tools/run_three_rock_continuous_proof.py`

Mining runtime already exists:

- `tools/run_mining_to_full.py`

Do not create a second mining controller inside PREP.

## Required feature

Prefer one clear entry point:

`tools/runelite_prep.py`

### Default: read-only diagnose

With no apply flag it must send zero setup input and report:

- discovered RuneLite HWND and current process/thread/class/title identity;
- visible/minimized/foreground state;
- exact client geometry;
- DPI;
- presence/validity of the local successful pose-reference files;
- fresh gameplay-chrome readiness;
- current Inventory occupancy/confidence or exact UNKNOWN reason;
- current Resource pose/registration verdict;
- matched landmark count/zones/distances;
- `READY` or one exact `NOT READY` reason.

### Explicit apply mode

Suggested shape:

`python tools\runelite_prep.py --apply --confirm PREP_RUNELITE_FOR_MINING`

This grants setup/camera prep only. It must never authorize mining clicks, navigation, banking, item movement, or perception release approval.

#### Window normalization

PREP may, before mining:

- restore the exact RuneLite window if minimized;
- foreground the bound HWND;
- resize the **client area** to exact `1005 x 1078`.

Requirements:

- never hard-code the old successful HWND `3736178`;
- rediscover/bind each session;
- detect HWND reuse via process/thread/class/title identity;
- measure the client area after every mutation;
- never assume outer-window size equals client size;
- use bounded correction and verify the final result;
- if DPI != 96, STOP rather than changing global Windows scaling today;
- do not add desktop-position coupling unless evidence proves it is required.

#### Camera normalization

Make this measured and closed-loop:

1. capture/evaluate current view;
2. if the frozen Resource gate already passes, send zero camera input;
3. otherwise use the existing bounded camera primitives;
4. after each bounded action/small deterministic action group, settle -> capture -> evaluate again;
5. stop immediately when an exact supported pose or permitted software registration satisfies the unchanged gate;
6. if the bounded search is exhausted, STOP and preserve evidence.

A diagnostic score may choose the next correction but may not declare READY. READY requires the real frozen Resource gate:

- threshold `0.12`
- 6 landmarks total
- at least `5/6`
- all `3` required zones
- supported Resource view

Do not weaken any threshold/quorum/zone rule and do not implement an endless camera random walk.

#### Inventory/gameplay readiness

Require gameplay chrome, unobstructed Inventory evidence, known occupancy and confidence >= `0.8` before declaring READY.

Inspect the repo for an already-reviewed safe Inventory-tab control. Reuse it if it exists. If it does not exist, do not invent a generic tab coordinate this morning; report one precise human action and keep every other prep step automatic.

## READY receipt

Write a unique JSON receipt containing at least:

- exact Git SHA and unique prep session ID;
- HWND + process/thread/class/title identity;
- geometry/DPI before and after;
- foreground/visibility/minimized state before and after;
- local pose-reference verification;
- gameplay-chrome verdict;
- Inventory occupancy/confidence or UNKNOWN reason;
- Resource verdict, pose/registration, matched count/zones/distances;
- every prep action and low-level completion receipt;
- final frame identity/hash;
- `ready_for_mining`;
- exact stop reason;
- `mining_input_authority: false`;
- `navigation_authority: false`;
- `banking_authority: false`.

PREP must end at the neutral cursor point and relinquish all setup/camera authority before mining starts.

For today, adding `--prep-receipt` to the miner is optional only if it is genuinely small/low-risk. Do not destabilize the working 28-run loop for convenience.

## Focused test matrix

Cover at least:

1. already-correct state -> READY with zero input;
2. wrong geometry -> read-only NOT READY;
3. simulated apply correction -> exact 1005x1078;
4. minimized/hidden -> not READY until explicitly repaired;
5. DPI != 96 -> STOP;
6. HWND identity reuse/change -> STOP;
7. missing local pose reference -> STOP;
8. gameplay chrome mismatch -> STOP before camera input;
9. already-supported camera -> zero camera actions;
10. partial/short camera input receipt -> STOP;
11. foreground loss -> STOP;
12. held mouse/key -> STOP;
13. bounded search cannot reach frozen gate -> STOP;
14. improved diagnostic metric with 0/6 can never become READY;
15. READY requires >=5/6 + all 3 zones at unchanged 0.12;
16. Inventory UNKNOWN/tooltip -> not READY;
17. prep-owned input cleanup on exceptions;
18. no mining/navigation/banking path in PREP;
19. second PREP run while already ready is effectively idempotent/no-input.

Use focused tests first.

## Afternoon acceptance gate

Good enough for the controlled 0->28 attempt when:

- PREP branch is clean;
- focused PREP/camera/window tests pass;
- read-only real-client diagnose succeeds;
- explicit PREP can reach a measured supported starting view;
- client is exact 1005x1078, DPI 96;
- HWND identity is stable;
- gameplay/Inventory readiness is clean;
- Resource passes unchanged 0.12 / 5-of-6 / 3-zone gate;
- a READY receipt is written;
- all prep input ends before mining;
- `run_mining_to_full.py` read-only plan still passes;
- there is no known defect likely to produce a wrong click in this exact supported starting envelope.

Target **zero known prep blockers** before the live attempt. Do not claim that an unrun endurance test cannot reveal a new real-world defect.

## Do not spend today on

Banking polish, navigation polish, GUI, scheduler, cursor randomization, generalized camera architecture, arbitrary-location support, broad #85 integration, PKI/WORM/malicious-admin defenses, model retuning without evidence, or any threshold weakening.

## Continuation rule

PR/CI/review/handoff is a soft gate. Keep doing safe offline audit, focused tests, dependency mapping, runbook work and periodic GitHub rechecks. Only a human-only live step that blocks all remaining safe work justifies stopping.

## End-of-work report

Return:

- `READY FOR 0->28: YES/NO`
- exact branch/SHA/PR/check state
- window prep status
- camera prep status
- gameplay/Inventory prep status
- final real Windows diagnose/prep evidence
- exact Tyler action, or `NONE`

Do not start banking/navigation in this work item.