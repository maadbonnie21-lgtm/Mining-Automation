# P0 startup resolver acceptance gate — 2026-09-05

## Product objective

Prepare the supported Varrock East mining view without requiring Tyler to manually hunt the camera, then hand off to the separately authorized one-attempt-at-a-time mining loop.

## Required decision order

1. Bind the exact current RuneLite HWND, process/thread/class/title identity.
2. Restore/focus/resize only within explicit PREP authority.
3. Require client area exactly 1005x1078 and DPI exactly 96.
4. Neutralize the cursor and capture one clean frame.
5. Positively prove Inventory empty for the official 0->28 run; unknown appearance is UNKNOWN, never occupied/full.
6. Try exact retained September 3 pose matching.
7. Try existing bounded distributed software registration.
8. Try the new replay-tested software normalization model against the retained successful pose frames.
9. Capture a strictly newer clean frame and independently re-solve/revalidate the same transform family.
10. Publish READY only when the newer frame passes the unchanged 0.12 / 5-of-6 / all-three-zone Resource gate and exposes at least one AVAILABLE iron target.
11. End all PREP authority before the mining runtime begins.

## Forbidden shortcuts

- no open-loop camera action ladder;
- no claim that Codex Issue #31 solved arbitrary camera recovery;
- no threshold, quorum, or zone weakening;
- no tuning against independent release-validation evidence;
- no current-frame self-labeling that bypasses a newer validation frame;
- no candidate target inferred from the minimap, UI, or Inventory panel;
- no mining click unless hover proves `Mine Iron rocks`;
- no mining click based only on a registration score;
- no false `28/28` from unrecognized Inventory slot hashes;
- no automatic navigation or banking in this work item.

## Software-normalization safety requirements

A proposed visual transform must be rejected unless all are true:

- it is derived from world-viewport pixels only;
- it has enough spatially distributed correspondences;
- correspondence residuals are bounded;
- the transform is non-degenerate and orientation-preserving;
- projected Resource target points remain inside the reviewed world viewport;
- exact-pose ambiguity is rejected;
- a foreign scene is rejected;
- known drift/unsupported evidence remains unsupported;
- a strictly newer frame independently validates the transform and frozen Resource gate;
- the final READY receipt records source pose, transform kind, inlier count, residuals, and transformed target identity.

## Minimum offline matrix before another live command

- identity transform;
- translation;
- bounded scale;
- bounded rotation;
- bounded perspective;
- combined transform;
- partial occlusion;
- duplicate/ambiguous scene;
- foreign scene;
- reflected transform;
- degenerate transform;
- stale/repeated frame;
- retained real successful pose replay;
- known unsupported/drift replay;
- Inventory true-empty, one-slot, partial, full, obstruction, and unknown appearance;
- PREP READY never carries mining authority;
- mining still requires exact SHA/HWND/session and one click maximum.

## Capability language

Passing the offline matrix means **offline tested** only. The startup resolver becomes **proven on the real RuneLite client** only after a current-host run reaches READY from a deliberately non-ready camera state and then the miner performs verified progress from a strictly newer frame.