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

- scene confidence: `0.85`
- candidate minimum similarity: `0.65`
- candidate minimum available/depleted margin: `0.25`
- candidate signature maximum RGB distance: `60`
- scene-anchor signature maximum RGB distance: `50`
- sample step: `2`

The real replay suite, synthetic obstruction case, synthetic unsupported-scene
case, shared-state adapter checks, deterministic replay check, and privacy-mask
checks all run in `tests/test_varrock_east_iron_real.py`.

## Release boundary

This is a calibrated development profile, not yet a claim of universal or
four-node release readiness. Before Issue #11 can close, collect and pass:

- a real depletion/respawn sequence for north-west
- a real depletion/respawn sequence for center
- a real depletion/respawn sequence for north-east
- a reviewed unsupported-location frame
- at least one deliberate real obstruction over a profiled sample patch
- repeated runs with the supported camera restored after ordinary client restart

PR #12 remains a draft until those gates pass.
