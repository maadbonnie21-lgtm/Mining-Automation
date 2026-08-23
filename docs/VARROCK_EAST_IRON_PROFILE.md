# Varrock East iron profile v1

This document records the calibrated state of the first real resource detector.
It supplements `docs/RESOURCE_PERCEPTION.md`; it does not expand the supported
operating envelope beyond the evidence listed here.

## Real data used

Five owner-captured frames were visually reviewed and sanitized before repository
inclusion:

1. available baseline with the player near the rock cluster
2. pre-depletion
3. first clearly depleted frame
4. late-depleted frame
5. first respawn frame

The reviewed sequence proves a real south-west node transition:

```text
available -> depleted -> remains depleted -> available
```

At the same time, the other three profiled iron patches remain available, which
provides a real mixed-state regression case.

The dataset is stored under:

```text
tests/fixtures/perception/varrock-east-iron-v1/
```

Its `REVIEW.md` documents ground truth, privacy sanitization, hashes, regions, and
remaining validation limits. Frames are stored as deterministic gzip-compressed exact
raw bytes and materialized losslessly into replay schema v1 before evaluation.

## Packaged profile

The application profile is packaged at:

```text
src/mining_automation/perception/profiles/varrock_east_iron_v1.json
```

`build_varrock_east_iron_detector()` is the production factory. The profile is
strictly tied to:

- frame geometry `1005 x 1078`
- pixel format `BGRA8888`
- the reviewed fixed camera/zoom
- the four frame-local 20x20 rock-surface patches
- the four reviewed scene anchors
- detector version `1.0.0`

Geometry, scene, obstruction, or colour mismatch returns `resource.uncertain`.
Only `resource.available` is adapted into a clickable `ResourceState` region.

## Classifier calibration

Each rock has its own real available-colour prototype. The south-west node has
real available and depleted prototypes from the full-cycle capture. The
provisional depleted prototype for the other three nodes is shared from that
same reviewed grey state.

Current thresholds:

- scene confidence: `0.85` (legacy v2 path only; non-gating under v3)
- per-anchor confidence floor: `0.90` (Issue #13; **non-gating under v3**, see below)
- structural landmarks: 6, quorum 5, spanning >= 3 of 4 macro zones (Issue #18)
- landmark descriptor max distance: `0.12` (Issue #18)
- candidate minimum similarity: `0.65`
- candidate minimum available/depleted margin: `0.25`
- candidate signature maximum RGB distance: `60`
- scene-anchor signature maximum RGB distance: `50`
- occlusion grid: `2x2` per candidate, unanimous agreement required (new, Issue #13)
- sample step: `2`

The real replay suite, synthetic obstruction case, synthetic unsupported-scene
case, real-frame camera-drift and partial-occlusion cases (Issue #13),
shared-state adapter checks, deterministic replay check, and privacy-mask
checks all run in `tests/test_varrock_east_iron_real.py`.

## Issue #13 hardening: camera drift and occlusion

Three additions to the detector, applied to this profile and empirically
validated against all five real reviewed frames before being enabled here —
not guessed at:

**Per-anchor fail-closed floor.** `minimum_scene_confidence` (`0.85`) gates a
*weighted average* across the four anchors, which a single badly drifted
anchor can survive if the others stay strong: with these four equally
weighted anchors, one anchor's confidence can fall as low as `4 * 0.85 - 3 =
0.4` while the other three stay near `1.0` and the average still passes.
In-game camera rotation or zoom, unlike frame-size or pixel-format mismatch,
is not caught by any other check in this detector. `minimum_anchor_confidence`
(new field, set to `0.90`) closes that gap: every anchor must individually
clear it. `0.90` was chosen empirically — the worst observed per-anchor
confidence across all five real reviewed frames is `0.9996` (south-ground,
`available-01`), so `0.90` carries a wide safety margin against the real
dataset while being far stricter than the `~0.4` a single anchor could
otherwise silently degrade to.

**Partial occlusion defense via sub-region voting.** Each candidate's 20x20
region now splits into a 2x2 grid of 10x10 cells
(`occlusion_grid_columns`/`occlusion_grid_rows`), each classified
independently, with all four required to agree
(`minimum_occlusion_agreement: 1.0`) before the result is trusted. A
whole-region mean cannot tell "the rock's colour genuinely changed" apart
from "part of the rock is hidden behind something colour-different" — both
shift the same single average, and an occluder covering part of the region
can be blended into a still-confident, still-wrong answer. Verified against
all five real frames before enabling: splitting into quadrants produces
unanimous agreement on every one, with the weakest individual quadrant
similarity (`0.882`) still comfortably above the `0.65` minimum-similarity
threshold — so this is a strict addition of protection, not a narrower
passing envelope, confirmed against the real dataset rather than assumed.
Confidence for a grid-classified candidate is the *minimum* similarity among
the agreeing cells, not the whole-region mean or an average across cells —
deliberately more conservative, mirroring the same "confidence is the
weakest evidence, not the average" principle already used for inventory-slot
aggregation elsewhere in this codebase. This is why confidence values in the
fixture manifest are correctly lower than before this hardening (e.g.
`0.95`-`1.0` narrowed in places to `0.87`-`1.0`): the aggregate is now driven
by whichever quadrant agrees least, which is the intended, more honest
signal.

**Overlap validation.** No two candidate regions, and no candidate/anchor
pair, may geometrically overlap — checked at profile construction. A
hand-calibrated profile like this one is exactly the workflow most prone to a
copy-paste or eyeballing mistake leaving two windows over what is really one
physical rock, or a candidate window bleeding into an anchor's patch and
corrupting the scene-verification signal with the rock's own colour changes.
Nothing in the four regions calibrated here was found to overlap, so this
check changed nothing about this specific profile — it is purely a guard
against future recalibration mistakes.

Schema bumped to version 2 for the two new required profile-level and
candidate-level fields (`minimum_anchor_confidence`,
`occlusion_grid_columns`/`occlusion_grid_rows`/`minimum_occlusion_agreement`).
An unset `minimum_anchor_confidence` defaults to `0.0` (no additional floor)
and an unset grid defaults to `1x1` (today's whole-region behaviour), so
these are opt-in for any *other* profile — this one now opts into both.

**What this hardening is, and is not.** Camera-drift and occlusion coverage
here uses *real background pixels with a small, precisely computed synthetic
mutation* (one anchor patch or one candidate quadrant replaced with an exact
target colour, everything else byte-for-byte the real captured frame) — see
`tests/test_varrock_east_iron_real.py`. This is not a substitute for the real
deliberate-obstruction capture still listed in the release boundary below; it
proves the mechanism works correctly against this profile's real geometry and
real signatures, using a controlled, reproducible, exactly-quantified
mutation. A genuinely captured occlusion event (a real player sprite over a
real patch) may look different from any synthetic fill colour and should
still be captured and evaluated before this profile is called
occlusion-validated, not just occlusion-defended.

## Issue #18: structural scene validation and reacquisition

Schema v3 replaces the *gating* role of the four mean-RGB terrain anchors with
six spatially distributed structural landmarks under a quorum rule.

**Why the old anchors could not work.** Measured with the same structural
variance metric that now gates landmark calibration, the four legacy anchors
score `grass-west 1.75`, `grass-center 3.03`, `east-slope 0.78`,
`south-ground 3.27` -- against a discriminative floor of `8.0`. They never
carried information capable of telling one camera view from another. Giving any
single one of them veto power (the Issue #13 per-anchor floor) therefore
rejected scenes on evidence that could not distinguish views, which is the
confirmed cause of the reacquisition failure. They are still measured and
recorded as evidence; they no longer gate.

**The replacement.** Each landmark is a 48x48 region reduced to a 4x4 grid of
cell luminances, mean-centred and normalised by maximum absolute deviation. It
encodes internal structure rather than average colour and is invariant to a
uniform brightness change. The scene validates only when at least 5 of 6
landmarks match **and** the matching ones span at least 3 macro zones.

**Calibrated landmarks**, two per usable macro zone:

| landmark | region | zone | structural variance |
|---|---|---|---|
| `west-ridge` | (6,376,48,48) | north_west | 10.17 |
| `west-lower-ridge` | (6,448,48,48) | north_west | 13.35 |
| `south-path` | (258,784,48,48) | south_west | 17.53 |
| `south-central-edge` | (426,736,48,48) | south_west | 12.60 |
| `north-east-wall` | (702,88,48,48) | north_east | 8.75 |
| `east-bank-edge` | (678,448,48,48) | north_east | 16.38 |

The 2-per-zone layout is load-bearing: with one landmark per zone, losing any
single landmark would drop below the 3-zone requirement and the 5-of-6 quorum
would never actually tolerate an obstruction. The south-east quadrant is
unusable -- the inventory panel occupies it -- so three zones is the maximum
available and every one of them carries a spare.

**Threshold derivation.** Set by measured separation, not tuned:

- worst positive across all five reviewed rock-state frames: `0.00005`
- closest negative at 4px camera translation: `0.143`
- closest negative at 8px: `0.279`

`0.12` sits ~2400x above the worst positive and below the closest negative,
with no overlap between the distributions.

**Calibration guards.** `MINIMUM_STRUCTURAL_VARIANCE` (8.0) rejects a
featureless region at construction time, so "no generic grass/dirt patches" is
mechanically enforced. Landmarks may not overlap candidate regions, so rock
available/depleted transitions cannot alter scene validation -- measured at
<= 0.00006 descriptor movement across every reviewed depletion/respawn frame.

**Masked-region hazard.** The committed fixtures are privacy-sanitized: title
bar, chat/status area and lower-right inventory are solid black. A naive search
for "most structural region" ranks mask edges first, because they are perfectly
stable and extremely high-contrast -- and they hold live UI on a real machine.
No landmark may sit inside `y <= 33` or `y >= 850`; this is asserted in
`tests/test_issue18_scene_reacquisition.py`.

**Backward compatibility.** A profile with no `scene_landmarks` keeps the exact
v2 behaviour including the Issue #13 per-anchor floor. Schema v3 is additive.

## Release boundary

This is a calibrated development profile, not yet a claim of universal or
four-node release readiness. Before Issue #11 can close, collect and pass:

- a real depletion/respawn sequence for north-west
- a real depletion/respawn sequence for center
- a real depletion/respawn sequence for north-east
- a reviewed unsupported-location frame
- at least one deliberate real obstruction over a profiled sample patch (the
  Issue #13 occlusion defense is mechanism-validated against real pixels with
  a synthetic mutation — see above — not yet against a genuinely captured
  occlusion event)
- the 36 real camera-drift frames re-run against schema v3 via
  `tools/validate_varrock_east_drift.py --frames <dir>`: all rocks UNCERTAIN,
  zero false definitive targets (this branch does **not** claim that baseline
  was re-verified; the frames are not in the repository)
- a freshly restored supported camera view re-run with `--expect definitive`
- an ordinary RuneLite restart followed by supported-view reacquisition,
  re-run with `--expect definitive`
- repeated runs with the supported camera restored after ordinary client restart

PR #12 remains a draft until those gates pass.
