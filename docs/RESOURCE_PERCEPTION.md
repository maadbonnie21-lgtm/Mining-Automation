# Resource perception: Varrock East iron

Milestone: **M3A — First production resource perception**  
Initial location: **South-east Varrock mine / Varrock East Mine**  
Initial ore: **iron**

This milestone turns owned `capture.Frame` values into explicit, verified rock-state observations. It does not click, mine, navigate, bank, or make controller decisions.

## Observation contract

A profiled detector returns one observation per known resource candidate:

- `resource.available`
- `resource.depleted`
- `resource.uncertain`

Each observation carries:

- `detector_id` and the observation's detector version
- `label`: ore label, currently `iron`
- `location_id`
- `profile_id`
- `profile_schema_version`
- `resource_id`
- `state`
- frame-local `region` when the profiled geometry is valid
- sampled mean RGB and available/depleted similarities
- scene confidence and per-anchor confidence
- a concise reason

`resource_state_from_observation()` converts an individual detector output into
the shared `ResourceState` contract. It remains a generic diagnostic adapter and
does not prove that a production frame is complete.

The controller-preparation boundary is
`trust_varrock_east_iron_observations()`. Its policy is source-owned and has no
runtime or CLI overrides. It requires the exact
`profiled-resource:varrock-east-iron-v1@2.1.0` detector identity, schema v3,
`varrock-east-iron-v1` profile, `varrock-east-mine` location, all four expected
resource IDs exactly once, and one identical `FrameRef`. Any incomplete, mixed,
malformed, stale, or identity-mismatched ensemble returns zero resource states
and zero actionable targets. The caller must supply the source-owned current
capture `FrameRef`; all four observations must equal that exact identity. This
uses no inferred age threshold or configurable freshness window. In an accepted
complete ensemble, only an **available**
resource with its exact packaged frame-local candidate region is actionable;
depleted and uncertain resources never expose interaction regions. This is a
typed contract for later controller work, not WorldState or controller activation.

The future live call site must use
`capture_detect_trust_varrock_east_iron()`, the non-activating source-owned
assembly boundary. Its only argument is an already-open `CaptureSource`; it
captures one owned frame, constructs the packaged detector internally, runs the
guarded detector contract, and supplies that same frame's exact `FrameRef` to
the production trust boundary. It accepts no observation ensemble, detector,
frame token, identity, or policy override from its caller and returns only the
fail-closed trusted result. Capture/detector failures raise their existing typed
errors rather than manufacturing an empty success. This assembly still does not
create `WorldState`, select a target, authorize an interaction, or execute input.

## Why the detector is profile-driven

The first supported envelope is intentionally narrow and testable. A versioned profile declares:

- expected client-frame width, height, and pixel format
- stable scene-anchor patches
- known iron-rock candidate regions
- available and depleted colour signatures
- scene and candidate confidence thresholds

All coordinates are frame-local. The profile contains no desktop coordinates, and a normal application user never edits it. A geometry, pixel-format, scene, or colour mismatch produces `resource.uncertain` instead of a guessed state.

This is a deterministic baseline. Real regression results decide whether the profile strategy is sufficient or whether a later detector needs feature matching, segmentation, or a learned classifier.

## Real-frame capture workflow

Capture is deliberately separate from annotation. Recording a frame does **not** make it ground truth.

With RuneLite open on Windows:

```powershell
python tools/capture_resource_fixture.py `
  --output diagnostics/varrock-east-v1 `
  --dataset-id varrock-east-iron-v1 `
  --case-id available-baseline `
  --location-id varrock-east-mine `
  --frames 3 `
  --interval 1 `
  --tag real `
  --tag available-candidate
```

Each successful capture creates three files:

```text
frames/<case-id>.raw       exact owned frame bytes
previews/<case-id>.bmp     human-review preview
drafts/<case-id>.json      unreviewed annotation draft
```

Existing paths are never overwritten. A failed or partial capture is never silently labeled.

## Annotation and review

Annotation is internal dataset work, not end-user configuration.

```powershell
python tools/annotate_resource_fixture.py add `
  diagnostics/varrock-east-v1/drafts/available-baseline-001.json `
  --resource-id varrock-east-iron-01 `
  --state available `
  --region X Y WIDTH HEIGHT `
  --confidence-min 0.80 `
  --confidence-max 1.00

python tools/annotate_resource_fixture.py review `
  diagnostics/varrock-east-v1/drafts/available-baseline-001.json
```

A draft cannot be promoted until it contains at least one annotation and is explicitly marked `reviewed`.

Build a replay-schema-v1 manifest only from reviewed drafts:

```powershell
python tools/build_resource_replay_manifest.py `
  --draft-dir diagnostics/varrock-east-v1/drafts `
  --output diagnostics/varrock-east-v1/manifest.json
```

State is encoded in the observation kind while the ore remains the label, so the merged generic evaluator can objectively compare available, depleted, and uncertain cases without changing replay schema v1.

## Privacy and fixture review

Before any real frame enters the repository:

1. Open the BMP preview.
2. Confirm it contains only the intended game/client evidence.
3. Remove or sanitize chat, private messages, account details, credentials, or unrelated desktop content.
4. Verify every region and state from visible evidence rather than current detector output.
5. Prove the new case fails before a detector fix and passes afterward whenever it represents a regression.

Raw local capture folders are development artifacts and are not production-supported datasets merely because they exist.

## Initial support envelope

Production support is not yet claimed merely by this infrastructure. The Varrock East profile must be calibrated and pass real fixtures for:

- available iron rocks
- depleted iron rocks
- mixed states
- cursor/player obstruction
- neighboring copper/tin rocks and terrain clutter
- wrong location or unsupported camera
- low-confidence/uncertain frames
- deterministic repeated evaluation

The first profile may require a fixed RuneLite client geometry and a validated camera/zoom. Those assumptions must stay explicit until visual localization removes them.

## Research basis

Public OSRS references reviewed for this milestone report that:

- iron requires Mining level 15 and grants 35 Mining experience
- an ordinary iron rock respawns in about 5.4 seconds
- mined rocks become grey before regaining their ore colour
- the south-east Varrock mine is directly east of Varrock's southern entrance and contains iron, copper, and tin rocks
- Varrock East Bank is the intended nearby banking destination for the future route milestone

The structured research record is stored under `knowledge/mining/varrock_east_iron.json`. Knowledge about a mine is not the same as production support.

## Turning a failure into a regression

When a live session fails:

1. Save the exact owned frame and capture metadata.
2. Review/sanitize the preview.
3. Add objective annotations.
4. Promote the reviewed draft into the replay manifest.
5. Confirm the fixture is red for the original failure.
6. Fix the detector without weakening ground truth.
7. Run the entire replay suite and keep the case permanently.
