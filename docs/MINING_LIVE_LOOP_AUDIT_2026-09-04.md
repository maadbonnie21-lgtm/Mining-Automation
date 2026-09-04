# Mining live-loop audit — 2026-09-04

## Audit target

- Repository: `maadbonnie21-lgtm/Mining-Automation`
- Real-client preservation branch: `chatgpt/live-mining-proof-2026-09-03`
- Exact audited preservation head: `4477ccbf582271dfe5cb5524fd371ce5527714d9`
- Large preservation code commit: `cb68ab2973b378235bafda90bf7ebe878d6032e2`
- Base before preservation code: `7fcb5c0036e13b0cdce5cffa38db8140a8d1ba30`
- Strongest real result: `diagnostics/three-rock-continuous-final7-20260903/result.json`

This audit treats the preservation branch as evidence/work recovery. It does **not**
approve its complete 23,391-addition worktree delta as one release change.

## What the real 7 -> 10 run actually executed

| Evidence event | Real behavior | Code path that produced it |
|---|---|---|
| `clean_reacquisition` | Cursor moved to a neutral canvas point, tooltip was allowed to clear, one current frame was captured, Resource and Inventory were observed from that frame, and one source-ordered target was frozen. | `run_three_rock_continuous_proof.main` -> `evaluate_resource` -> `ProductionMiningPerceptionEvaluator.evaluate` -> `assemble_atomic_mining_world_state` -> `begin_mining_only_session` |
| pose validation | One exact pose profile was accepted, or one distributed registration was accepted, only after at least 5 of 6 landmarks matched across all 3 zones at the unchanged `0.12` distance ceiling. | `build_pose_detectors`, `evaluate_scene`, `register_translation`, `evaluate_resource` |
| `hover_proof` | The cursor moved to the interaction point from the frozen clean proposal. A newer hover-only frame proved the RuneLite primary action pattern for `Mine Iron rocks`. Resource was **not** re-decided from hover-altered pixels. | `mine_hover_signature` and the hover block in `run_three_rock_continuous_proof.main` |
| `single_click` | Exact HWND, foreground HWND, point ownership, cursor position, and coordinate round trip were checked. One `SendInput` mouse-down/up attempt was issued and an immutable attempt receipt was retained. | `RealWin32MiningInputDevice.dispatch_one_click` |
| `passive_verification` | No second click occurred while the character walked/mined. New frames were captured once per second. Inventory remained authoritative even when Resource could temporarily move out of the supported pose. Exact Inventory `+1` established the successful ore. | passive loop in `run_three_rock_continuous_proof.main` |
| next `clean_reacquisition` | Old target geometry was discarded. Resource and Inventory were reacquired from the new player position before another target was selected. | next loop iteration in `run_three_rock_continuous_proof.main` |
| final reacquisition | Resource returned supported with 6/6 landmarks in all 3 zones and Inventory was 10/28 at confidence 1.0. | final block in `run_three_rock_continuous_proof.main` |

The uninterrupted result therefore proves real client behavior, not just code or replay:
Inventory `7 -> 10`, exactly 3 clicks, target order northwest -> southwest -> center,
and zero Tyler intervention during the uninterrupted run.

## Why the proof scripts are not yet the 28/28 runtime

### `controlled_mining_runner.py`

This is a one-attempt runner. It captures one post-click frame after one fixed delay.
That is too early and too rigid for normal walking/mining latency. It also owns no
continuous retry-free loop to 28/28.

### `run_proven_mining_loop.py`

This contains a repeated loop, but it is a proof harness:

- hard-coded HWND;
- test-only permanent target exclusion environment variable;
- mission occupancy defaulted to a small proof number;
- a new Inventory evaluator is created on every iteration;
- result evidence is not yet the full 28/28 schema.

### `run_three_rock_continuous_proof.py`

This produced the strongest real proof, but it intentionally forces a three-rock
experiment:

- fixed three-ore count;
- completed rocks are excluded, preventing natural respawn reuse;
- fixed starting Inventory `7`;
- hard-coded HWND and output path;
- pose descriptors are rebuilt from local raw `.bgra` references;
- the loop logic is embedded in the proof CLI rather than production-owned.

These historical scripts should remain preserved as evidence, but they should no
longer be extended as competing controllers.

## Preservation commit scope classification

### REQUIRED FOR THE REAL MINING PATH

- `src/mining_automation/controlled_mining_runner.py`
- `src/mining_automation/validation/_camera_win32_calls.py`
- `src/mining_automation/validation/camera_coordinates.py`
- `src/mining_automation/validation/windows_camera.py`
- `src/mining_automation/perception/profiles/varrock_east_iron_v1.json`
- Inventory runtime dependencies directly used by the successful evaluator:
  `geometry.py`, `localization.py`, `classification.py`, `detector.py`,
  `configuration.py`, `adapter.py`, `positive_classifier_v2.py`,
  `positive_classifier_v3.py`, `positive_v3_prototypes.py`, and the packaged
  empty-reference region
- the three proof CLIs and their curated result JSON/PNG/Markdown evidence

### SUPPORTING / TEST

- `tests/test_controlled_mining_runner.py`
- `tests/test_windows_camera_nonactivating.py`
- interaction-point and pose calibration tools
- concise real failure/success JSON and visual diagnostics
- `validation/client_readiness.py`

### HISTORICAL C / INVENTORY WORK — PRESERVE, DO NOT PULL INTO THE MINING CHANGE BY DEFAULT

- live campaign/session CLIs and campaign orchestration
- V2/V3 evaluation suites
- independent-validation implementation
- perimeter-forensics implementation
- review gate and sanitized replay modules
- fixture-preparation and research/prototype utilities not imported by the live loop

These files may remain valuable C-lane evidence. Their presence in the preservation
commit is not evidence that they are all required by the next mining-only change.

### NOT ON THE CURRENT MINING CRITICAL PATH

- generalized camera plans or camera-control work not used by the non-activating
  capture/one-click path
- navigation, banking, packaging polish, and unrelated hardening before 28/28

## Release/provenance findings that must stay explicit

1. **Real-client behavior is proven.** The preserved `7 -> 10` result is genuine
   RuneLite evidence.
2. **The preservation branch is not release-ready.** It has no exact-head CI run at
   `4477ccbf582271dfe5cb5524fd371ce5527714d9`.
3. `controlled_mining_runner.py` constructs Resource and Inventory release identities
   from hard-coded metadata. That is not the final source-owned receipt path.
4. `positive_classifier_v3.py` describes itself as offline-only and non-activating,
   while the proof evaluator calls it directly. That is acceptable evidence of what
   worked on the real client, but it cannot silently replace the accepted C release
   lineage.
5. Position registration preserved `0.12`, 5/6, and all three zones, but its exact
   pose reference pixels are local-only. A clean fresh checkout cannot reproduce all
   pose detectors until descriptors/regions are promoted as reviewed, non-private
   configuration.
6. The modified Resource profile keeps the required geometry, threshold, quorum, and
   zones. Its 42 additions / 46 deletions still need an exact minimal-diff review
   against accepted Resource lineage before release integration.

## Consolidation decision

The continuation branch adds one production-owned orchestration module:

`src/mining_automation/mining_loop_runtime.py`

It reuses the existing atomic `mining_slice.py` state machine for proposal, receipt,
strict-newer reobservation, exact `+1`, FULL, and no downstream authority. It owns the
repeated control order only:

`clean current state -> frozen proposal -> hover proof -> one click -> passive wait -> exact +1 -> fresh clean reacquisition -> state-machine reobserve`

The new live CLI is:

`tools/run_mining_to_full.py`

It is read-only by default and requires an exact clean Git SHA, exact HWND, explicit
live flag, and a fixed confirmation token before any input. It stops at 28/28 and does
not enter navigation.

The historical proof scripts remain append-only evidence adapters; they are not the
new controller.

## Real-defect regression matrix added

The focused runtime tests cover:

1. wrong foreground -> zero clicks;
2. wrong geometry, DPI, hidden, or minimized -> zero clicks;
3. Inventory tooltip/UNKNOWN -> zero clicks;
4. Resource UNKNOWN -> zero clicks;
5. missing/wrong hover action -> zero clicks;
6. `Mine Copper rocks` -> zero clicks;
7. hover frame cannot replace the frozen clean Resource proposal;
8. normal movement with Resource unavailable for decision does not trigger a click or
   immediate failure while passive Inventory remains known;
9. bounded no-progress -> STOP with no retry;
10. exact `+1` -> clean reacquisition and continuation;
11. stale prior-position proposal/geometry -> STOP before the next click;
12. three distinct rocks selected dynamically from current state;
13. a respawned rock may be selected again from fresh current state;
14. `27 -> 28` -> COMPLETE with no 29th click;
15. Inventory decrease or `+>1` -> ambiguous STOP;
16. passive Inventory UNKNOWN or confidence below 0.8 -> STOP;
17. replayed dispatch identity -> STOP;
18. stale post-progress reacquisition or changed Inventory release identity -> STOP.

## Remaining line between this branch and real 28/28

- pass focused and exact-head CI on Windows and Linux;
- confirm the real Windows adapter can load the local pose references on Tyler's
  machine;
- run the single explicit live command from a clean exact continuation SHA;
- preserve the complete uninterrupted result;
- only after 28/28, connect FULL to the accepted navigation/banking handoff.
