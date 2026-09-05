# P0 self-audit — automatic camera failure — 2026-09-05

## Executive finding

The two automatic camera implementations exercised on 2026-09-04 must not be represented as working capability.

1. The first seven-step zoom/pitch sequence was an unsupported inference and failed on the real RuneLite client.
2. The later Issue #31 candidate ladder was copied from a retired research branch whose acceptance gate explicitly was not achieved. It also failed on the real client.

Both failures stopped before mining input, so the fail-closed boundary held. That safety result does not make either camera normalizer successful.

## Root-cause findings

- GitHub Issue #31 was closed **not planned, not claimed solved**. The preserved lead comment states that arbitrary supported-view camera reacquisition was not proven repeatable and that multiple controller hypotheses were falsified.
- The Issue #31 camera work targeted an older Resource profile lineage. The current September 3 mining path uses later locally retained position-specific pose references and software registration.
- The successful September 3 mining behavior came from a supported/calibrated view plus fresh post-movement pose reacquisition. It did not prove a universal deterministic camera normalization recipe.
- Green offline tests only proved bounded execution, receipt handling, and fail-closed behavior. They did not prove that camera actions converge on the real client.

## Corrective engineering direction

Do not add another open-loop camera action ladder.

The next startup implementation must:

1. preserve exact HWND / identity / 1005x1078 / DPI96 checks;
2. preserve Inventory floor 0.8 and UNKNOWN fail-closed;
3. preserve Resource threshold 0.12, 5/6 quorum, and all three zones;
4. use the retained successful September 3 pose frames as the visual truth;
5. attempt software registration/normalization against those frames before any camera input;
6. require an independently captured fresh frame to pass the unchanged Resource gate after registration;
7. expose zero target authority when registration is ambiguous or unsupported;
8. keep hover proof `Mine Iron rocks` and the one-click maximum before any input;
9. never label a current-frame calibration as final release evidence;
10. record transformed target identity and registration diagnostics in the PREP receipt.

## Self-audit rules for the next live command

No next live command may be issued merely because CI is green.

Before another real attempt, the exact head must have:

- a regression proving unknown Inventory cannot become false FULL;
- synthetic transform tests covering translation, scale, rotation, perspective, ambiguity, occlusion, and foreign scenes;
- a replay test using retained real successful pose frames;
- a negative replay test using known unsupported/drift evidence;
- an exact diff audit showing no weakened Resource/Inventory invariants;
- a read-only local diagnostic mode that prints the registration model and final fresh-frame gate result;
- a clear statement of what remains unproven until the real Windows run.

## Capability language

- **Engineering built** means code exists.
- **Offline tested** means deterministic tests/replays pass.
- **Proven on the real RuneLite client** requires actual live evidence.
- **Release ready** requires the separate release/provenance gates.

At this checkpoint, automatic arbitrary-camera startup is **not proven on the real RuneLite client** and must not be called working.