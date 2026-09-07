# 2026-09-06: demonstrated login and 28 iron; program proof still open

## Scope and evidence rules

This is a learning record, not a production release or a new runtime. The observed live checkout remained `98ff709b48ef44f28644e455e792cea9546710c6`. This documentation child must not replace or mutate an operator's running checkout. Private raw client captures remain local; paths below are relative to that checkout unless otherwise stated.

Tyler's controlling order is demonstrate in the actual client -> report the exact behavior/evidence -> integrate or reuse that behavior -> test the program. Preserve working behavior. Do not replace this order with speculative camera searches or generalized hardening. Source discussion: issue #94, especially comments 5562191075, 5562456846, 5562525404 and 5562621823.

## Login: demonstrated, existing implementation reused unchanged

At 16:52 CDT the original Codex operator demonstrated normal entry from an already-authenticated Play Now screen: one Play Now click, passive observation of Connecting/Loading, one click on the actual Welcome play button, then verified gameplay with a visibly empty inventory. No credentials, MFA/CAPTCHA handling, camera changes, mining, banking or item movement were used.

Local handoff: `outputs/LOGIN_HANDOFF_20260906.md`. Post-login native image: `outputs/login-gameplay-20260906-1652.bmp`. The lead independently viewed the gameplay image. This is an operator demonstration, not a newly executed program-login test.

The demonstrated behavior already exists in `src/mining_automation/validation/session_recovery.py:session_recovery_stage`, `tools/runelite_prep.py:RealPrepBackend.recover_session`, and `src/mining_automation/validation/runelite_prep.py:run_runelite_prep`. These login files are unchanged between `ac08bff702e29e7f0b7b549228e60edc03106822` and the observed checkout. Reuse them; do not rewrite them merely because a login feature was requested.

Separate retained program evidence in the LEAD-RELIABILITY checkout, `diagnostics/runelite-prep-live-prep-live-2d5a7392a57a/result.json` on `bd737a7b9baf44559d030b545e02bbf602258253`, records complete disconnect/Play Now/Welcome actions and gameplay at frame 46 with inventory 0 at confidence 1.0. Its later camera-readiness failure does not erase login success, but the overall receipt is NOT successful PREP or mining proof.

## Mining: actual operator 0 -> 28 completed

Finished at 17:20 CDT. Local record: `outputs/MINING_HANDOFF_20260906.md`. Original captures are in `outputs/mining-demonstration-20260906-1658/`, ending with `ore-28-full.bmp`. The lead independently inspected the full 28-slot inventory image.

The operator reports 31 attempts, 28 gains, and three zero-credit attempts (1, 2 and 4). The no-gain observations did not terminate the entire demonstration. No-gain alone does not prove that another player won; the initial uncertain interaction and ordinary depletion/competition must remain distinguishable where evidence permits.

Working sequence: fresh observation -> exact visible Mine Iron rocks hover or context-menu identity -> one mining attempt -> passive resolution and inventory verification -> fresh observation from the character's actual new position. When depleted, wait for a fresh available target or choose another available iron; do not credit ore or reuse stale geometry. A visible Mine Copper rock menu was cancelled, not mined.

The handoff claims three physical iron targets, but initially supplies only points from different views. Before those points become program data, resolve screen-versus-client coordinate space, stable physical rock identities, and recoverable per-rock counts/representative attempts. Do not infer distinct physical targets solely from different screen points, invent missing counts, or claim the all-three-rock acceptance criterion independently verified yet.

**This was operator-assisted mining, NOT autonomous program 0 -> 28.** No machine `result.json` was fabricated for it.

## Existing program recognizes the final demonstrated view

One camera-free PREP ran on unchanged `98ff709b48ef44f28644e455e792cea9546710c6` using `tools/runelite_prep_live.py --apply`, the exact execution SHA/HWND, and `PREP_RUNELITE_FOR_MINING`, with no camera-step argument.

Original receipt: `diagnostics/runelite-prep-live-prep-live-b2b472365131/result.json`, generated `2026-09-06T22:22:10.578627+00:00`. The lead read the complete receipt:

- READY true, stop reason none; inventory 28/28 at confidence 1.0.
- Resource supported, pose at_center, six of six landmarks, all three required zones; unchanged 0.12 threshold.
- Both initial and final window snapshots are 1005x1078 at DPI 96.
- Recorded actions are neutral-cursor operations only. No resize, camera action or mining click is recorded.
- Final original frame `003-clean.bgra`, SHA-256 `a7c60fbc81bf25c8227a00b5d6e10c522212eb0c3a1cc49656657645ae657a94`.

The operator BMPs have 1005x687 metadata. Do not claim that this PREP resized those images/the window: the receipt begins at 1005x1078 and records no resize. The source of that geometry difference remains to be reconciled. PREP readiness at FULL is not a program-owned mining run.

**Preserve the currently recognized ending view and source. No speculative recalibration is justified by this passing receipt.**

## Preserve the earlier program failure as a regression source

`diagnostics/mining-to-full-20260906-162731-70a27099/result.json` is the earlier actual program attempt on the same SHA. It issued three mining clicks with three distinct resource IDs. The last passive frame (`00025-iteration-03-passive-07.bgra`) reads three occupied slots at confidence 1.0. Subsequent Resource reacquisitions failed through `00056-iteration-04-clean.bgra`; terminal result is `reacquisition_blocked` / `resource_view_not_supported`. Only two `verified_progress` events were promoted because the final gain was followed by blocked Resource publication; do not confuse that summary count with the observed inventory.

A read-only replay of the original final frame on unchanged code traced `register_translation` returning at line 313, before projected-landmark validation: coherent matches by pose were at_start 0, at_northwest 1, at_southwest 1, and at_center 3 across only two zones. This frame does NOT demonstrate the previously diagnosed rejected-sixth-landmark boundary defect. Do not transplant that old patch and call this case fixed without evidence.

## Remaining acceptance work

The operator demonstration and current full-inventory PREP do not prove reliable program startup, all-three physical-rock use, autonomous 0 -> 28, route traversal, deposit/empty verification, return, or five full cycles. The next dispatched operator milestone is mine -> Varrock east bank with the full test iron inventory preserved, reporting checkpoints and arrival before deposit. It also prepares a safe bank/return inventory reset for a genuine new program mining test; never relabel a nonzero/full start as 0 -> 28.

Preserve the pickaxe and unrelated items. Login/authentication challenges remain human-owned; no security bypass. Normal competition is recoverable with fresh evidence, but unknown targets must not cause blind clicks or fabricated success. Final acceptance requires five consecutive complete cycles by one unchanged integrated program/configuration without manual or agent rescue.

## Coordination truth

The native status-only heartbeat `runelite-mining-status` was configured in the original Codex task at 17:05:51 CDT for ten-minute updates, first due 17:15:51. Configuration was observed; first notification delivery was not independently verified. Reporting callbacks have no game-input or second-operator authority. A later lead monitoring command was blocked by platform safety validation; no workaround was attempted. File/GitHub access remained usable. A written follow-up or GitHub comment cannot wake a stopped operator, and an unacknowledged handoff must not be reported as RUNNING.
