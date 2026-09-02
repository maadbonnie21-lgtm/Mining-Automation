# Development Tools

This directory is for development-only utilities such as frame capture, annotation, replay, route validation, diagnostics inspection, and regression-case generation.

Rules:
- tools are not the production application
- production logic should not live only in a tool
- tools should consume the same typed contracts where practical
- captured failure cases should be promotable into tests/fixtures

## Perception evaluation

`evaluate_perception.py` is the thin command-line entry point for the display-free detector replay and regression harness. Its reusable contracts, loading, comparison, and reporting logic live under `mining_automation.perception`. See `docs/PERCEPTION_REPLAY.md` for the manifest schema, commands, exit codes, and failure-to-regression workflow.

## Resource fixture workflow

`capture_resource_fixture.py` records deliberate live RuneLite frames through the merged Windows capture backend. Every capture is written as raw bytes, a BMP preview, and an explicitly **unreviewed** JSON draft. It never labels or overwrites a fixture.

`validate_varrock_east_live.py` is the low-friction real-machine validation entry point for the current Varrock East iron profile. One invocation captures a fresh unreviewed frame, runs the production detector, reports every scene-anchor similarity, compares the live frame against the local `available-01.raw` calibration reference when present, and writes a JSON report under `diagnostics/varrock-east-iron/reports/`. Its same-coordinate reference metrics are diagnostic evidence only; they do not change detector thresholds or declare a release pass.

`annotate_resource_fixture.py` adds frame-local resource annotations and performs the explicit review transition. This is internal dataset work; normal application users never enter regions or train detectors.

`build_resource_replay_manifest.py` promotes reviewed drafts into the merged replay-schema-v1 format. See `docs/RESOURCE_PERCEPTION.md` for the complete workflow and privacy rules.

`resource_release_campaign.py` owns the fixed constrained-v1 resource release
campaign. It creates a uniquely owned, resumable session; guides the 15 cases
in their frozen order; captures and evaluates exactly one frame for the next
case; seals the complete private evidence set; binds independent reviewer
truth only after deterministic artifacts are separately prepared and inspected;
and emits a strictly verifiable manifest-last privacy-safe review package plus
the exact PR #39 CLOSED/STILL_OPEN ledger. `verify-export` requires the
independently retained export manifest SHA-256, then rehashes and replays
that package without private pixels. `prepare-followup` uses the same retained
root to emit a deterministic, nonactivating replay-candidate queue and C2
envelope-review input artifact; `verify-followup` requires its separately
retained SHA-256 and strictly reconstructs its deny-only projections.
`prepare-replay-proposals` then requires both retained roots and emits only
manifest-last, privacy-safe fixture/evaluator proposal inputs for exact
source-owned replay candidates. It embeds the canonical privacy-safe follow-up
so `verify-replay-proposals` can reconstruct selection while requiring a
separately retained proposal-manifest root. None of these commands can adopt a
fixture, approve an envelope, release perception, activate runtime authority,
or grant input. `prepare-release-decision-readiness` independently rebinds the
original package, canonical follow-up, and conditional proposal roots into a
deny-only C2 candidate-envelope/source-record review packet;
`verify-release-review-packet` requires its separately retained SHA-256. The
proposed record is never granted, renderer identity remains unresolved, and
there are no approval or environment override flags. The live source gate is
intentionally false in this branch, so `capture-next` fails before opening a
Windows backend.
See `docs/RESOURCE_RELEASE_CAMPAIGN.md`.

- `validate_varrock_east_drift.py` — run the Issue #22 drift-safety and
  reacquisition diagnosis in one command:

  ```bash
  python tools/validate_varrock_east_drift.py --drift-frames diagnostics/issue18-drift-v3 --restored-frame diagnostics/varrock-east-iron/frames/reacquire-restored-20260818.raw
  ```

  It requires the complete 36-frame drift set, runs the unmodified production
  detector, prints every landmark distance/threshold/status and scene verdict,
  and compares the restored candidate with known drift frames using the same
  `0.12`, 5-of-6, three-zone structural rule. A shared +/-4px search and
  independent local minima are clearly marked diagnostic-only and never turn
  UNCERTAIN into a target. Candidate pixels and the reviewed fixed RuneLite UI
  rectangles are excluded from every diagnostic search. `--report <path>`
  additionally writes the same evidence as versioned JSON, including detector
  provenance and the objective frozen-coordinate policy values; reading JSON
  is not required. Exit `0` means both drift safety and restored-view production
  detection pass, `1` means an analyzed gate failed, and `2` means the inputs
  or invocation are invalid. The historical
  `--frames <dir> [--expect ...]` spelling remains available.

- `diagnose_varrock_east_wide.py` — search for larger diagnostic-only shared
  scene translations without changing the production detector. It uses the
  same centralized candidate and fixed-UI exclusions as the drift validator;
  a frozen landmark inside UI is rejected as unsafe rather than searched.
