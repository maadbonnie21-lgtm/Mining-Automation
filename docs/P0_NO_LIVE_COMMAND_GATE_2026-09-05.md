# P0 no-live-command gate — 2026-09-05

A new real RuneLite command must not be issued from this branch until all conditions below are met on one exact head:

- the failed camera ladders are absent from the executable path;
- the empty Inventory false-FULL regression is green;
- the software registration/normalization implementation exists;
- replay and negative-scene tests are green;
- the final fresh-frame Resource gate remains 0.12 / 5-of-6 / all three zones;
- PREP carries no mining authority;
- one-click mining and hover proof regressions remain green;
- Windows and Ubuntu focused CI are green;
- the final diff is independently reviewed against this gate;
- the first Windows action is read-only diagnosis, not mining.

Until then, `tools/run_28_auto.py --live` is engineering-built code only and is not authorized for another real run.