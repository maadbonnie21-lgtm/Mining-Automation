# Claude Independent Project + Game-Plan Audit Handoff — PREP DAY — 2026-09-04

## Role

Act as an independent senior engineer / adversarial reviewer for `maadbonnie21-lgtm/Mining-Automation`.

Do not rubber-stamp the current plan. Determine whether today’s effort is aimed at the shortest safe path to a controlled real **0->28 mining-only run**.

Owner priority today is explicit:

**PREP -> READY -> Tyler authorizes -> real 0->28 mining-only run.**

Banking/navigation remain secondary until mining-only endurance is proven.

The afternoon target is **zero known prep blockers** in the exact supported envelope. Do not turn that into a promise that an unrun 0->28 endurance test cannot discover a new real defect.

## Reverify everything first

Inspect current GitHub heads, ancestry, diffs, CI and comments yourself. Do not trust this handoff as source truth.

Read:

1. `AGENTS.md`
2. `docs/MASTER_SPECIFICATION.md`
3. `docs/ARCHITECTURE.md`
4. `docs/ACCEPTANCE_CRITERIA.md`
5. `docs/LIVE_MINING_PROOF_2026-09-03.md`
6. `docs/MINING_LIVE_LOOP_AUDIT_2026-09-04.md`
7. `docs/MINING_TO_FULL_RUNBOOK_2026-09-04.md`
8. `STATUS.md`
9. `docs/RUNELITE_PREP_FEATURE_HANDOFF_2026-09-04.md`
10. current discussions for #82, #83, #84, #85, #74 and #80

## Checkpoints to reverify

Navigation hints only — confirm exact current state before relying on them:

- Resource #82 expected `bf32e3f0dfe22cc2c242f64707dfc7a374c0592a`
- Inventory #83 expected `d735610b8345c0c0fca560ee38b1023040c76ba7`
- Mining #84 expected `480c7524d3f9195281ed9dec9ce248da661999dd`
- PREP branch `chatgpt/runelite-prep-ready-2026-09-04`, direct child of #84 when created
- Navigation #74 expected `0f89a1b21f65e41e9c07c156b795ea137545802b`
- Banking #80 expected `b4dd05cd3fb593ae64516ef4ed3543e27ffce097`
- broad integration #85 expected `c6c0f33728eb27ab4689901e7b3c13cce24e7fd6`; treat as draft/audit surface unless evidence proves otherwise

## Trace the real evidence

Verify the repository’s 2026-09-03 claims directly:

- real 0->1 northwest
- real 1->2 after movement/reacquisition
- real 2->3 southwest
- real 3->4 center
- one uninterrupted real 7->10
- northwest -> southwest -> center
- exactly 3 clicks
- zero Tyler intervention during the uninterrupted run
- fresh Resource+Inventory after movement
- final Resource 6/6 across all 3 zones
- final Inventory 10/28 at confidence 1.0

Trace them to committed result JSON / curated evidence and exact code paths. Never conflate synthetic tests with real evidence.

# Primary audit — P0 PREP

Spend most of the audit here.

We want a separate PREP step that normalizes the starting RuneLite state and then gets out of the miner’s way.

## A. Window preparation

Real supported environment:

- client area `1005 x 1078`
- DPI `96`
- visible
- unminimized
- current exact HWND
- foreground before input

A real failure occurred when automation restored/resized RuneLite to `1005 x 687`.

Audit:

1. Are we measuring **client area**, not assuming outer-window dimensions?
2. Can Java/AWT restore/focus unexpectedly alter geometry?
3. What is the narrowest safe restore/resize mechanism?
4. Does PREP remeasure after every mutation?
5. Can it detect HWND reuse via process/thread/class/title identity?
6. Is desktop position actually needed, or should we avoid coupling to it?
7. Is DPI !=96 a STOP rather than an attempt to change global display scaling?
8. Does all window mutation cease before mining begins?

Give exact file/function recommendations.

## B. Camera / zoom preparation

Audit existing components before proposing new architecture:

- `src/mining_automation/validation/camera_plan.py`
- `src/mining_automation/validation/windows_camera.py`
- `src/mining_automation/validation/_camera_win32_calls.py`
- `src/mining_automation/validation/client_readiness.py`
- `tools/solve_current_view_calibration.py`
- `tools/validate_varrock_east_live.py`
- `tools/run_three_rock_continuous_proof.py`
- `src/mining_automation/perception/live_pose_references.py`

Answer:

1. What camera primitives already work and should be reused?
2. Can PREP detect an already-supported view and send zero camera input?
3. How should the next bounded correction be chosen from measured evidence?
4. What exact historical sequence took the client from bad view to later working/recalibrated view?
5. Does evidence support a deterministic normalization sequence, or only a measured closed-loop search?
6. How do we prevent endless camera trial-and-error?
7. Can the preserved pose references plus distributed registration establish readiness?
8. What must be revalidated after every camera action?
9. What hard action/search bounds should PREP use?

Inspect especially:

- `preflight-supported-view`
- `zoom-trial-1`
- `post-zoom-up-4-20260903`
- `current-geometry-diagnosis-20260903`
- `foreground-focused-diagnosis-20260903`
- `post-down-hold-100ms-20260903`
- `post-down-hold-200ms-total-20260903`
- `post-up-hold-50ms-20260903`
- `recalibrated-fresh-validation-20260903`
- `manually-restored-view`
- wide-registration evidence

Important: `post-zoom-up-4` was still 0/6. Do not turn four zoom events into folklore.

## C. Inventory / gameplay preparation

Audit whether PREP can establish:

- gameplay screen present;
- Inventory visible;
- no Inventory tooltip obstruction;
- occupancy known;
- confidence >= 0.8.

Find whether a reviewed safe Inventory-tab control/point already exists. If yes, identify it exactly. If not, do not recommend inventing a generic tab coordinate today; specify the one minimum human-only step.

## D. PREP / miner separation

This is critical.

PREP may repair the **starting** client/window/camera state.

Once PREP emits READY:

- PREP input authority ends;
- no resize/reposition/zoom/pitch normalization occurs during mining;
- the miner must preserve the proven behavior:
  `mine -> player moves -> passive +1 -> discard old geometry -> fresh perception from new position`.

Flag any design that tries to force the camera back after every ore as a regression.

# Second audit — can #84 actually run 0->28?

Inspect exact current #84 code and focus only on defects that can realistically break today’s experimental run.

Audit:

1. fresh current-state Resource+Inventory before every target;
2. dynamic available-iron selection;
3. respawn reuse;
4. clean target frozen before hover;
5. exact `Mine Iron rocks` hover proof;
6. one physical click per attempt;
7. passive waiting through normal walking/mining;
8. exact Inventory +1 progress;
9. fresh reacquisition after movement;
10. STOP on UNKNOWN/stale/ambiguous evidence;
11. 27->28 with no 29th click;
12. FULL stops before navigation;
13. local `.bgra` pose-reference requirements and paths on Tyler’s laptop;
14. any old hard-coded HWND / starting inventory remaining on the #84 runtime path;
15. any stale foreground/window fact capable of causing a wrong click;
16. whether the live command is operationally usable today.

Classify every finding as one of:

- `FUNCTIONAL BLOCKER FOR TODAY'S EXPERIMENT`
- `SAFETY BLOCKER FOR TODAY'S EXPERIMENT`
- `FORMAL RELEASE / PROVENANCE BLOCKER`
- `POST-28 CLEANUP`

Do not allow formal paperwork to masquerade as a functional blocker; do not dismiss a wrong-click risk as paperwork.

# Third audit — are we overengineering?

Judge work by:

> Does this directly make the real miner more likely to reach 28/28 today or remove a known blocker?

Identify work to defer, including if applicable:

- banking/navigation polish
- generalized provenance/artifact systems
- self-landing integration automation
- broad branch consolidation
- unrelated CI cleanup
- malicious-admin/PKI/WORM defenses outside the trusted single-Windows-user/host model
- GUI/scheduling/future systems
- generalized camera architecture not required by PREP

Be specific.

# Fourth audit — GitHub / topology

## #84

Is it still the best functional mining-to-28 line? What exact changes, if any, must be made before today’s test?

## PREP branch

Is it a clean child of #84? Has it duplicated/weakened mining behavior?

## #85

Audit stale source selection, self-accept/self-merge authority, unrelated scope and lineage mistakes. Do not recommend it as release parent merely because it is large.

## #82 / #83

Separate what is required for a **formal production/release claim** from what is required for a **controlled experimental functional 0->28 run**.

## #74 / #80

Ensure navigation/banking remain subordinate until mining-only proof.

# Non-negotiable invariants

Resource:

- threshold `0.12`
- six landmarks
- quorum `5/6`
- all three zones
- UNKNOWN/unsupported -> zero targets + STOP

Inventory:

- capacity 28
- floor `0.8`
- UNKNOWN fail-closed

Mining:

- fresh state before selection
- one click per attempt
- attempt != success
- strictly newer evidence
- no blind retry
- stale/mixed/ambiguous -> STOP
- fresh post-movement reacquisition
- no 29th click
- FULL stops before navigation

PREP:

- setup/camera mutation only before mining
- no mining click
- no banking/item/navigation input
- no camera normalization after mining begins

Evidence:

- synthetic never labeled real
- failed real runs stay failed
- preserve exact evidence

# Continuation rule

PR/CI/review/freeze/handoff is a soft gate. Do not end the session while safe read-only/adversarial work remains. Continue diff/ancestry inspection, test-gap analysis, real-evidence tracing, PREP fault-matrix design, run checklist validation, blocker classification and GitHub rechecks.

Only a human-only live-client step that blocks every remaining safe audit activity justifies stopping.

# Required return format

## 1. Executive verdict

Choose exactly one:

- `GO FOR AFTERNOON 0->28`
- `GO WITH CONDITIONS`
- `NO-GO`

Give the minimum conditions.

## 2. Top P0 PREP findings

Maximum 10. For each: severity, exact file/function/PR/SHA evidence, plain-English impact, smallest fix, and whether it must be fixed before today’s run.

## 3. Reuse map

Show what already exists for window discovery, client geometry, DPI, focus, camera wheel, camera drag, compass, key camera control, gameplay chrome, pose-reference verification, scene registration, Inventory readiness and mining-to-28. Explicitly say what must **not** be rebuilt.

## 4. Historical camera reconstruction

From exact evidence tell us:

- bad starting view;
- actual window/camera operations attempted;
- failed attempts;
- what changed before later working/recalibrated view;
- what is deterministic enough for PREP;
- what still requires measured closed-loop correction.

## 5. Today's critical path

Shortest sequence from current code to:

`PREP READY -> Tyler authorizes -> 0/28 -> 28/28 or fail-closed STOP`

Do not include banking/navigation before that point.

## 6. Afternoon run checklist

Make it usable by a non-developer owner.

## 7. Defer list

List work that should wait until after 28/28.

## 8. Tyler action

Say `NONE` unless there is a genuine human-only step. Do not ask Tyler to debug code or repeatedly fiddle with camera/zoom when software can measure/correct it.