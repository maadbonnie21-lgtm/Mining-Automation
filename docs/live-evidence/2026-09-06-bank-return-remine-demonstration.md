# 2026-09-06: bank, deposit, return, and another 28 iron demonstrated

## Result and scope

Tyler's live request was dispatched to the existing original Codex operator at 18:21:49 CDT. The requested sequence completed by 18:58 CDT: start at the mine with 28 iron -> Varrock East Bank -> deposit those ores -> empty inventory -> return to the same southeast mine -> another genuine 0-to-28 iron run -> stop game input at FULL.

This is **Codex-operated demonstration**, not an autonomous-program acceptance run. Local `outputs/MINING_LIVE_STATUS.md` reports COMPLETE and stopped game input. The lead independently viewed the bank-empty, returned-to-mine-empty, and final-full original captures via lossless local PNGs. The final inventory has 28 occupied iron slots.

The existing checkout remained `98ff709b48ef44f28644e455e792cea9546710c6`. A direct post-run Git check confirmed that SHA and an empty tracked-file status. No production code, profile, threshold, configuration, or camera experiment was introduced during this demonstration. This documentation change does not alter the live checkout.

## Original evidence

Operator handoff: `outputs/ROUTE_TO_BANK_HANDOFF_20260906.md` (83 lines when reviewed). Status: `outputs/MINING_LIVE_STATUS.md`, timestamp 18:58 CDT. All capture paths below are relative to `outputs/route-bank-return-mining-20260906-182246/`. Private raw client/bank images stay local, not in the public repository.

| Original capture | Meaning | Actual dimensions | SHA-256 |
| --- | --- | --- | --- |
| `001-mine-start-full.bmp` | Full inventory at starting mine | 1005x1078 | `bb1599c228119c0618b998681cfb8ee6c0759330379d92343dbdf9f5fbcb50d7` |
| `003-bank-empty.bmp` | Open bank and empty inventory after deposit | 1005x687 | `d9d29f22ff0990f3938d43c710f140f4c2722f4be351b138fa681755899c6ed1` |
| `004-back-at-mine-empty.bmp` | Quarry return with empty inventory | 1005x687 | `e2cbd55ee045b9b69ceca0865118ded617ecfb470d4528069c6673328a7c83b0` |
| `005-final-mine-full-28.bmp` | Second mining leg finished at 28 | 1005x687 | `110d1cc253daa74910700d50d6a9cd8045a3137cabd518c3cc25bd539828fc61` |

The lead measured these dimensions and hashes from the original BMP files. Correct the handoff's blanket statement that all native captures are 1005x687: the first capture is 1005x1078. Do not infer an unrecorded window resize or treat Sky screenshot coordinates as interchangeable with program capture/client coordinates. The earlier PREP b2b472365131 remains a separate receipt with its own unchanged initial/final geometry; it is not this run's program-mining proof.

## Observed route and session recovery

An ordinary disconnect occurred at the first outbound waypoint. The operator recovered via Disconnect OK -> existing authenticated Play Now -> passive Connecting/Loading -> Welcome Play -> gameplay, preserving the full inventory. No credentials, MFA/CAPTCHA bypass or authentication change occurred.

Outbound observations: southeast mine -> southern/eastern outer wall -> west along exterior wall to an opening -> east-side city-road intersection -> northbound road/service area -> correct bank building -> visible counter/bankers. There was an exploratory neighboring-service-building/fence detour. This is successful arrival with corrections, **not** an optimized or blindly replayable fixed route.

Return observations: bank interior -> outside bank/service-district intersection -> southbound road -> southern city edge/cabbage-field/outer-wall area -> east along wall. An incorrect southward leg reached the dark-wizard stone circle. The operator identified that position with the actual in-game map, corrected northeast/east through the wall/tree area, located the southeast-mine marker, and reached the quarry. The returned empty-inventory capture was independently reviewed.

Preserve the correct semantic checkpoints and distinguish the mistaken legs. Do not encode the service-building detour, dark-wizard detour, or screenshot points as the intended fixed-route recipe.

## Demonstrated bank action

A fresh right-click on a banker exposed `Bank Banker`; that action opened the actual bank interface. A fresh right-click on an iron inventory slot exposed `Deposit-All Iron ore`. The operator selected that item-specific action once and verified all 28 slots empty. The lead independently reviewed the open-bank/empty-inventory result image.

The operator reports no withdrawals, trades, equipment changes, generic deposit-inventory action, or unrelated item movement. An attempted bank-exit click instead opened Poll History; it was closed before continuing. Keep that as an observed UI-targeting defect, not part of the desired exit method.

## Second mining leg and ordinary recovery

The operator records 29 mining attempts, 28 verified gains, and one zero-credit event: attempt 10 left inventory at nine after movement. Old geometry was discarded, a currently available iron target was reacquired, and later gains continued to 28. An unchanged inventory alone is not proof of which competing player won; the supported fact is that a no-gain attempt did not end the run or receive invented credit.

The first three successful attempts were reported as three separately menu-proven physical iron rocks, each yielding one ore. Representative points in the handoff are in **Sky window-screenshot coordinate space**, change after movement, and must not be pasted into the program as reusable client coordinates. Fresh `Mine Iron rocks` menu/tooltip evidence preceded attempts; generic `Mine Rocks` and copper observations were rejected.

A Mining level-up dialog appeared after the first ore and was dismissed once before continuing. This is another real expected gameplay state to preserve and test when integrating, not a reason to declare the whole campaign failed.

Game input stopped when the final slot filled. The lead independently confirmed the final full-inventory image. Current location at completion is the mine, with the new 28 test iron preserved.

## Integration work still required

Reuse working saved-session login, item-specific iron deposit semantics, fresh target proof, post-movement reacquisition, no-credit attempt recovery, and exact-full stop. Integrate the demonstrated semantic route and correct UI targets only after resolving actual capture/client coordinate mapping. Add regressions from these actual observations rather than unrelated hardening or camera searches.

The previous program-owned three-ore Resource-reacquisition failure is not fixed by this demonstration. Autonomous program 0-to-28, autonomous route/bank/return, and five consecutive unchanged-program cycles remain unproven. Do not count this operator demonstration as any of those acceptance milestones.

Coordination/evidence references: issue #94, direct dispatch comment 5562944488, bank-result comment 5563064170, and return-result comment 5563110638. Routine ten-minute reporting is a separate status-only task and must not be mistaken for a running game operator. The requested live sequence is complete; further game actions are not part of this demonstration record.
