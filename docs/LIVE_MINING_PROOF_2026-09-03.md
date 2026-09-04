# Real-client mining proof — 2026-09-03

## Proven results

RuneLite real-client automation mined four individually validated iron ore and then mined all three distinct configured rocks in one uninterrupted run. The supported client remained HWND `3736178`, client size `1005x1078`, DPI `96`.

| Inventory | Configured target | Dispatch ID | Evidence |
|---|---|---|---|
| `0 -> 1` | northwest | `dispatch-f91e189f5098` | [result](../diagnostics/frozen-hover-one-ore-ready-20260903/result.json) |
| `1 -> 2` | northwest after movement/reacquisition | `dispatch-31bc921e095f` | [result](../diagnostics/proven-mining-ore2-20260903/result.json) |
| `2 -> 3` | southwest; northwest excluded for this test only | `dispatch-8490a4d90f71` | [result](../diagnostics/different-rock-ore3-20260903/result.json) |
| `3 -> 4` | center; northwest and southwest excluded for this test only | `dispatch-6255bf02e1ef` | [result](../diagnostics/third-rock-ore4-20260903/result.json) |

The final continuous proof ran from Inventory `7 -> 10` with exactly three clicks in this order:

1. northwest — `dispatch-69788badb09c`, Inventory `7 -> 8`
2. southwest — `dispatch-218a97615c22`, Inventory `8 -> 9`
3. center — `dispatch-febf99797e2a`, Inventory `9 -> 10`

Evidence: [uninterrupted three-rock result](../diagnostics/three-rock-continuous-final7-20260903/result.json).

There was no Tyler intervention during that uninterrupted run. Each target came from a fresh Resource and Inventory observation, each point was required to show `Mine Iron rocks`, each action dispatched exactly one click, each ore was verified passively by Inventory `+1`, and Resource plus Inventory were reacquired after normal player movement. The final reacquisition was Resource READY with all `6/6` landmarks across all three zones and Inventory `10/28` at confidence `1.0`.

## Real-client lessons

- Capture and input must remain bound to the same HWND, geometry, and DPI. Foreground equality is checked immediately before input; the harness does not activate, move, resize, or minimize RuneLite.
- The cursor moves to a neutral canvas point before Resource and Inventory perception. Inventory-tab hover tooltips occlude slot evidence and correctly cause Inventory UNKNOWN.
- The actionable target is frozen from the clean neutral-cursor frame. Hover is an interaction proof, not a second Resource decision.
- A plausible rock-region center is insufficient. The exact point must expose the primary action `Mine Iron rocks`; one early region proved to be a neighboring copper rock.
- Character walking and camera following after a mining click are normal. The harness does not click again while walking or mining.
- Progress is established by passive newer frames and an exact Inventory increment, followed by fresh perception from the new player position.
- Movement can require a position profile or distributed affine registration. Registration preserves the reviewed landmark descriptors and gates; it does not convert UNKNOWN into READY.

## Failed attempts — not success evidence

- [unverified first dispatch](../diagnostics/mining_attempts/one-ore-current-view-20260903/session-a0096b60615b.json): no verified progress; foreground delivery was not proven.
- [foreground Resource UNKNOWN](../diagnostics/mining_attempts/one-ore-foreground-20260903/session-a0554cc416ad.json): zero clicks.
- [foreground mismatch](../diagnostics/mining_attempts/one-ore-focused-final-20260903/session-89302d392162.json): zero clicks.
- [Inventory tooltip UNKNOWN](../diagnostics/mining_attempts/one-ore-countdown-final-20260903/session-734c73c5fc98.json) and [repeat](../diagnostics/mining_attempts/one-ore-inventory-fix-final-20260903/session-6cda3f7e5c15.json): zero clicks.
- [movement without verified ore](../diagnostics/mining_attempts/one-ore-neutral-cursor-final-20260903/session-b02e9e2f3b58.json): click delivery succeeded, but no ore proof was observed.
- [clean pre-click Resource UNKNOWN](../diagnostics/mining_attempts/one-ore-proven-hover-final-20260903/session-7ec54744bba8.json): zero clicks.
- [wrong copper interaction point](../diagnostics/three-rock-continuous-20260903/result.json): `Mine Copper rocks` was observed, so the hover guard sent zero clicks.
- [new landing-position registration stop](../diagnostics/three-rock-continuous-final2-20260903/result.json): northwest mined `4 -> 5`; the next fresh scene failed closed.
- [two-rock partial run](../diagnostics/three-rock-continuous-final5-20260903/result.json): northwest and southwest mined `5 -> 7`; fallback registration then stopped before a third click.
- [ambiguous non-selected candidate](../diagnostics/three-rock-continuous-final6-20260903/result.json): Resource ensemble remained UNKNOWN and sent zero clicks.

The [interaction-point calibration](../diagnostics/interaction-point-calibration-b02e9e2f3b58/selected-interaction-point.json), [hover grid](../diagnostics/interaction-point-calibration-b02e9e2f3b58/action-text-grid.png), and [landmark comparison](../diagnostics/live-proof-resource/current-vs-reference-landmarks-20260903.png) retain the concise visual diagnosis.

Resource landmark threshold `0.12`, quorum `5/6`, all three required zones, and Inventory confidence floor `0.8` remained unchanged throughout.

## Local-only raw evidence

Raw `.bgra`, `.raw`, and bulk replay/capture directories remain local because they are large and may contain private client pixels. Their repo-relative locations are referenced by the committed result JSON files. No raw archives were deleted or rewritten.
