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

## Camera reacquisition validation

`analyze_issue31_servo_offline.py` is the no-input Phase 1 proof for the bounded
feedback design. It evaluates reviewed supported fixtures plus explicitly
labeled private/diagnostic frames through three separate authorities: fixed-UI
readiness (veto only), unchanged production camera evaluation (acceptance
only), and world-only diagnostic guidance (never acceptance). Its frozen
configuration also records the dedicated fresh arm-frame guard: immediately
before any future input, readiness and production are rerun and only unchanged
structural evidence may retain the already-pending sign. The guard can only
discard/restart and cannot validate a scene or expose resources. Every external
group must provide `--expect`, `--expect-readiness`, and `--require-count` for a
proof-eligible run. Canonical JSON records exact Git/command/configuration and
per-frame hashes/scalars without copying or embedding pixels; the adjacent
SHA-256 sidecar is written exclusively. The tool returns nonzero for incomplete
expectations, count/readiness/production mismatches, dirty or changing Git
state, or an authority-invariant failure.

The current Track A real-frame diagnosis is intentionally separate from input
authority. Production matched `0/6` landmarks. A wide diagnostic recovered
only three local landmarks at noncoherent offsets, and the best shared offset
matched `1/6`, so v1 zoom-only guidance is insufficient for that camera
envelope. These diagnostic minima cannot validate the scene or expose a
resource.

The follow-on V2 code lives in the reusable validation package rather than in
a one-off tool. It owns a one-time, receipt-bound compass-north bootstrap per
reacquisition session and preserves only v1's already-reviewed zoom sign. A
dominant yaw/pitch result may request one signed four-pixel calibration probe,
but the probe is not a correction and cannot become production acceptance.
Immediately before any servo input, a strictly newer final commit observation
must retain readiness and the world-only arm guard, and the accepted arm must
still be less than one second old. The canonical development-only live boundary
is deliberately tiny:

```powershell
python tools/validate_varrock_east_camera.py north-bootstrap-v2 --case-prefix issue31-north-YYYYMMDD-HHMMSS
```

Use a permanently unique prefix. Only optional private `--output` is also
accepted; title, coordinates, settle timing, detector/profile/V2 identity, and
the one compass primitive are fixed. Exit `0` is reserved for unchanged
production success, a completed `BOOTSTRAP_EXECUTED` run exits `1`, and setup
or publication failures exit `2`. The clean exact Git head and global input
lease span the complete evidence boundary. Its private report records full
bootstrap evidence, input-request-to-receipt timing, and receipt-backed
target-root checks but does not claim an uncaptured numeric pointer mapping.

`validate_varrock_east_camera.py` retains the development-only Issue #31
fixed-candidate harness for regression compatibility. That open-loop path is
no longer canonical after real evidence showed complete Windows receipts can
correspond to a RuneLite semantic no-op. At exact `1005 x 1078` client geometry
it applies its bounded, production-gated list of independent north/pitch/zoom
reset candidates across three perturbations, then requires at least two fresh
production-detector confirmations per perturbation. The selected candidate
frame is provisional and is never a confirmation. Candidate pixels, fixed UI,
and diagnostic searches cannot establish or override scene identity. No live
V2 closed-loop yaw/pitch/zoom command is currently published. Production
thresholds, quorum, macro zones, scene authority, and fail-closed resource exposure remain
unchanged by every diagnostic and calibration path.
Only one live validator process may operate at a time: a machine-global Windows
named mutex is acquired before capture/focus/input and held through cleanup and
report publication; a contender fails immediately without touching RuneLite.
Abandoned ownership also fails closed because global key/button state is then
indeterminate; every later plan preflight proves all validator-controlled keys
and the left button are released before focus/input. Failed mutex release
poisons the local process, and canonical evidence is retained only when report
publication ownership and lease release are both proven.
While holding the lease and before constructing capture, the tool durably
reserves the complete case prefix after rejecting any stale private artifacts.
That reservation remains after every outcome, so each case prefix is
permanently single-use.

The tool writes private raw/BMP/draft evidence plus a canonical JSON report and
exact `.sha256` sidecar under ignored `diagnostics/`. Acceptance evidence must
identify a clean exact Git head, detector/profile versions, plan version, and
command. The recipe is not accepted until the repeated real-client trials and
the complete 36-frame real drift set both pass without weakening production
policy. See
[`docs/CAMERA_REACQUISITION_VALIDATION.md`](../docs/CAMERA_REACQUISITION_VALIDATION.md)
for the boundary, protocol, privacy rules, and acceptance gate.

`diagnose_camera_pointer_mapping.py` is the Issue #31 native **no-input** DPI
mapping audit. It captures one private RuneLite client frame but sends no
cursor, mouse-button, wheel, or keyboard events. It records the rejected
legacy coordinate candidates and the production origin-based forward/reverse
mapping for the reviewed compass and wheel points. Its optional private raw
and annotated BMPs support visual review in two separate spaces: RuneLite's
PrintWindow target-logical raster and a no-input physical screen crop of the
foreground client. The latter marks final physical pointer points and is
bracketed by unchanged origin/geometry/focus/ownership checks. Output paths are
distinct and exclusive; the JSON report is written only after every requested
image succeeds.
