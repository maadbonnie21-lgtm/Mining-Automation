# Inventory perception

This subsystem turns one owned capture `Frame` into one `inventory_state`
observation and, through a strict adapter, the shared `InventoryState`. It is
platform-neutral: it uses frame pixels and frame-local coordinates only. It
does not know about Win32 handles, desktop positions, input, banking, or mining
decisions.

## Support status

The detector, geometry, reference classifier, replay workflow, and state
adapter are implemented and regression-tested with deterministic synthetic
frames. **No live RuneLite layout profile or empty-inventory reference is
production-validated yet.** Until reviewed real captures are added, a caller
must supply a reviewed `InventoryFrameProfile` and matching empty reference
through `inventory_detector_from_profile`.
Unsupported frame geometry is reported as an unknown inventory; it is never
silently assigned a guessed count.

The separately identified, non-activating positive-classifier V2 candidate is
documented in `INVENTORY_POSITIVE_CLASSIFIER_V2.md`. It diagnoses the current
real clean-positive confidence defect while leaving this V1 factory and all V1
replay expectations unchanged. Its frozen held-out run failed safely on a new
presentation guard, so it is not an approved production factory.

This boundary is intentional. Issue #9 establishes that a slot owns a 32 by 32
pixel interaction/counting region in a four-column by seven-row inventory. It
does not establish the live client anchor, row or column pitch, display mode,
theme, or UI scale. Those values must come from captures rather than from a
hard-coded desktop coordinate or a synthetic guess.

## Pipeline

1. An `InventoryRegionLocator` evaluates the current frame. The supplied
   exact-profile locator matches the frame's dimensions to a reviewed,
   frame-local profile.
2. `InventoryGridLayout` generates all 28 slot regions row-major from one
   region and the profile's row and column strides. There is no table of 28
   hand-entered coordinates.
3. `ReferenceInventoryClassifier` compares every slot with the corresponding
   slot in a reviewed empty-inventory reference. It also compares gutter guard
   pixels with that reference so a broad overlay that changes the reviewed
   gutters fails closed rather than becoming confident item detections.
4. The detector aggregates only when localization and all 28 slot decisions
   meet the configured confidence policy.
5. `inventory_detection_from_observation` is the sole evidence-schema parser;
   `inventory_state_from_observation` delegates to it and produces the shared
   `InventoryState` used by controller code.

Expected runtime misses, such as an unsupported frame size or insufficient
localization confidence, produce one well-formed unknown observation. Broken
component contracts, such as a locator returning an out-of-frame region or a
classifier returning malformed slot order, raise a typed inventory detector
error; the generic evaluation harness records that as a detector failure.

## Live profile composition

The public composition boundary takes exactly the two reviewed artifacts that
define one supported visual profile: an `InventoryFrameProfile` and an owned
empty-reference `Frame`.

```python
from mining_automation.perception.inventory import (
    ClassificationPolicy,
    InventoryFrameProfile,
    InventoryGridLayout,
    Region,
    inventory_detector_from_profile,
)

layout = InventoryGridLayout(
    profile_id="reviewed-profile-id",
    column_stride=36,  # measured from reviewed captures
    row_stride=36,
)
profile = InventoryFrameProfile(
    profile_id=layout.profile_id,
    frame_width=800,
    frame_height=600,
    region=Region(650, 300, layout.width, layout.height),
    layout=layout,
)
detector = inventory_detector_from_profile(
    profile,
    empty_reference_frame,
    policy=ClassificationPolicy(),
)
```

The numbers above illustrate the API only; they are not a supported RuneLite
profile. The factory requires the empty reference to have the profile's exact
full-frame dimensions, derives the reference region and layout from the
profile, and refuses mismatched calibration inputs. A replay-loaded frame is
the same consumer `Frame` type, so a reviewed `empty-reference` manifest case
can be passed directly without a capture or detector adapter.

This baseline intentionally composes one profile with one reference. A second
theme, scale, or layout needs its own reviewed profile/reference detector; it
must not be silently routed through the first profile's pixels.

Successful construction does not declare the profile production-supported.
The exact locator recognizes frame geometry, not tab identity. A same-size
wrong-tab frame therefore reaches the classifier, and profile activation is
blocked until a real wrong-tab fixture proves that the complete detector
returns unknown. If it does not, the existing locator/classifier protocols
allow a targeted positive panel check without changing downstream contracts.

## Geometry and sprite ownership

The authoritative slot region is exactly 32 by 32 pixels. A layout declares
one column stride and one row stride, both at least 32 pixels. Its overall
region is derived as:

```text
width  = 3 * column_stride + 32
height = 6 * row_stride    + 32
```

Item artwork can be wider than its 32-pixel slot. Classification therefore
uses an inset ownership core inside each slot and never follows connected
pixels into another slot. Pixels in the slot border or gutter cannot, by
themselves, make the adjacent slot occupied. This is a deterministic ownership
rule, not an item-specific rule.

All regions in observations and fixture expectations are frame-local
`(x, y, width, height)` tuples. They are not desktop click coordinates.

## Baseline classification

The baseline is item-agnostic and dependency-free. For every pixel in the
inset core it compares canonical RGB values with the same pixel in the reviewed
empty reference. Alpha is ignored, and `gray8`, `rgb888`, `bgr888`, `rgba8888`,
and `bgra8888` payloads are supported.

Two multi-pixel signals form a deterministic score:

- the fraction of core pixels whose channel difference reaches the configured
  pixel-difference threshold; and
- the mean normalized RGB difference across the entire core.

The score weights are 70% changed-pixel fraction and 30% mean normalized RGB
difference. The shipped, uncalibrated policy uses a four-pixel core inset, a
channel-difference threshold of 24 (inclusive), an empty score at or below
`0.08`, and an occupied score at or above `0.22`. Scores strictly between those
limits are uncertain. Classified empty/occupied states begin at confidence
`0.5`; the detector separately requires every slot to reach `0.8` before it
publishes a count.

Pixels in the inventory region that belong to no 32-by-32 slot are guard
pixels. If more than 50% of those pixels differ from the reviewed reference at
the same inclusive channel threshold, classification reports an obstruction
and the detector publishes unknown. Horizontal gaps between slot rows receive a
stricter default: any changed row-gutter pixel reports an obstruction. That
keeps a half-panel tooltip or menu from sitting exactly on the 50% aggregate
boundary and becoming 14 false items, while a 36-by-32 icon may still spill
horizontally through column gutters without being counted twice. Both guard
limits must remain below 100%, so a configuration cannot silently disable all
obstruction rejection while claiming a guard.

A layout without horizontal row-gutter pixels cannot use this baseline visual
occlusion check. The detector will publish unknown when the reference
classifier explicitly reports that condition. A future classifier/localizer
combination would need an independently validated occlusion gate before such a
layout could be considered for production.

Scores at or below the empty threshold are `empty`; scores at or above the
occupied threshold are `occupied`; the band between them is `uncertain`.
Thresholds and the core inset are explicit, validated configuration. No ore
colour, item identity, single sampled pixel, or connected sprite component is
used.

The reference must match the supported client theme, scale, layout, and empty
slot rendering. A mismatched reference should be treated as an unsupported
profile and promoted to a regression fixture, not compensated for by lowering
confidence until a number appears.

## Confidence and unknown-state policy

A known count requires all of the following:

- localization meets the minimum confidence;
- exactly 28 decisions are returned in canonical row-major order;
- every decision is `empty` or `occupied`; and
- every slot meets the minimum slot confidence.

Known observation confidence is the minimum of localization confidence and all
slot confidences. It is deliberately not an average: one weak required slot
makes the aggregate weak. If any condition fails, `occupied_slots` is `null`,
the label is `unknown`, and aggregate confidence is `0.0`. Per-slot evidence is
retained when classification produced it; earlier localization or obstruction
failures retain their typed reason and configuration identity instead.

V2 does not lower this `0.8` publication requirement. Its fixed, separate
classifier requires the same raw occupied state plus distributed support across
the slot core and every coarse row/column. Seven-cell support clears `0.8`,
six-cell support remains below it, and raw ambiguity remains uncertain
regardless of spatial coverage. Publication still fails closed when any V2
presentation guard rejects the frame.

Known labels are `empty` for zero slots, `full` for 28, and `partial` otherwise.
The detector's shipped, uncalibrated localization threshold is `0.9`; both the
localization and slot publication thresholds must be greater than zero.

## Evidence schema version 1

Each detector call emits exactly one observation with kind `inventory_state`.
Its evidence contains JSON-friendly values:

- `evidence_schema_version`
- `label`
- `region` (or `null` when localization did not produce one)
- `occupied_slots` (`0..28` or `null`)
- `capacity` (`28`)
- `reason`
- `localization_confidence`
- `profile_id` (or `null` when no profile localized)
- `configuration_id`, which identifies the exact-profile frame geometry and
  anchor, classifier algorithm, layout, policy, reviewed empty-reference
  fingerprint, localization threshold, and detector slot-publication threshold
- ordered `slots`, each with index, row, column, region, state, confidence, and
  score diagnostics

Consumers must use the typed adapters; they must not parse this mapping in
controller logic or diagnostics tooling. The adapter rejects wrong kinds,
unknown schema versions, malformed values, and incoherent label/count
combinations before constructing `InventoryDetection` or `InventoryState`.

## One-command real-client evidence

The passive Windows harness captures one production-backend frame and creates
unique raw/BMP/draft/report artifacts without input automation or overwrite.
It safely operates in capture-only mode until a reviewed live profile/reference
factory exists, and it can run that future production `InventoryDetector` on
the exact captured frame without an architecture change. See
`INVENTORY_LIVE_VALIDATION.md` for the command, report schema, exit semantics,
privacy review, and replay-promotion workflow.

After a guided session is captured, use `INVENTORY_REVIEW_REPLAY_GATE.md` for
the independent truth-recording, reviewed-evidence-only candidate derivation,
unchanged-detector replay, privacy sanitization, and non-activation boundary.
The package panels are re-derived from durable owned frames during evaluation,
and the review record is reviewer-attributed and hash-bound to complete package
coverage. It is not a cryptographic signature. Capture-environment values
remain `operator-reported-bound` until a reviewer or lead separately approves
them.

## Replay and failure-promotion workflow

Real failures become permanent tests through the merged perception replay
harness:

1. Save the owned raw `Frame.payload` from the failed observation and record
   its width, height, and `PixelFormat` in a schema-v1 replay manifest.
2. Record provenance without sensitive account data: capture/backend build,
   client mode, UI scale, theme, detector version, detector configuration ID,
   and a short failure reason.
3. Add the expected `inventory_state` label, frame-local region, and confidence
   range. Keep the private occupied-slot assertion in an inventory-specific
   test through the adapter.
4. Reproduce the failure before changing classifier or profile values.
5. Make the smallest general fix, then run the entire replay corpus so the new
   case and all older cases pass.

Do not commit screenshots merely because they are convenient. Review them for
usernames, chat, notifications, plugin panels, and other private data first;
redact outside the evidence region when that does not change the failure.
Fixture payloads should stay small enough for normal repository and CI use.

### First live fixture intake

Collect two independent empty frames: one immutable `empty-reference` used to
construct the classifier and one `empty-validation` case used to prove the
detector does not merely recognize its own baseline. Ground truth for the
initial reviewed corpus must include:

| Case | Expected occupied slots | Expected label/confidence |
|---|---:|---|
| empty validation | `0` | `empty`, known |
| partial | exact reviewer-counted value | `partial`, known |
| full | `28` | `full`, known |
| obstructed | `null` | `unknown`, exactly `0.0` |
| wrong tab | `null` | `unknown`, exactly `0.0` |

The generic manifest checks label, region, and confidence. An
inventory-specific regression must also run every observation through
`inventory_state_from_observation` and assert the exact occupied count above,
the profile ID, the detector configuration ID, and a useful unknown reason.
`partial` by itself is not adequate ground truth because many incorrect counts
share that label.

The Windows validation harness currently saves a top-down, uncompressed,
32-bit BGRA BMP, while replay schema v1 stores headerless raw pixels. Do not
rename a BMP to `.raw`. Prefer retaining the original `Frame.payload`; if only
BMP files are supplied, use:

```bash
python tools/prepare_inventory_fixture.py INPUT.bmp OUTPUT.bgra
```

The development tool validates the BMP signature, 40-byte information header,
32-bit depth, no compression, negative/top-down height, pixel offset, and
exact payload length before extracting the BGRA bytes. This does not require a
capture-backend change.

Preserve the original full-frame dimensions when exact-profile localization
is under test. Privacy sanitization may zero or redact pixels outside the
inventory evidence region when that cannot affect the case, but cropping the
frame changes its geometry and no longer validates the live profile. Record
at least the capture and RuneLite builds, frame size and pixel format, Windows
DPI/scaling, client mode, theme, renderer, tab/overlay state, capture
configuration, sanitization status, reviewer, and review date in provenance.
Use these canonical string keys so the first live-corpus test can reject an
incomplete handoff: `capture_build`, `runelite_build`, `frame_size`,
`pixel_format`, `windows_dpi`, `windows_scaling`, `client_mode`, `theme`,
`renderer`, `inventory_tab_state`, `overlay_state`,
`capture_configuration_id`, `sanitization`, `reviewer`, `reviewed_at`, and
`validation_split`. Set `validation_split` to `reference`, `calibration`, or
`held-out`; do not put account names in provenance.

For the guided Windows session, the structured operator-reporting flags are
`--capture-build`, `--runelite-build`, `--windows-scaling-percent`,
`--client-mode`, `--runelite-theme`, `--renderer`, and
`--capture-configuration-id`; `--note` is repeatable supplemental provenance.
They are frozen when the session is created and cannot be replaced on resume.
For detector-run resume, the requested detector ID/version and configured
profile/configuration IDs must also match every completed capture before a new
backend is constructed.

### Calibration discipline

Use reviewed frames to propose an immutable `ClassificationPolicy`, then
validate it against separate held-out frames. Preserve a non-zero uncertainty
gap; do not move thresholds merely until every example produces a number. The
shipped defaults are deliberately conservative: with the detector's `0.8`
slot-publication threshold, the default score policy effectively requires a
score at or below approximately `0.032` for a known empty slot and at or above
approximately `0.688` for a known occupied slot. Initial real sprites may
therefore return unknown, which is safe and expected until calibration is
reviewed.

Pin the approved detector `configuration_id` in the live regression test. It
changes when the reviewed frame geometry or inventory anchor, canonical
reference pixels, layout, classification policy, localization threshold, or
slot-publication threshold changes. Re-run the entire synthetic and real
corpus for every candidate policy; calibration cases must not double as the
only validation cases.

The initial five visual states are the minimum intake, not enough evidence to
approve thresholds. Capture additional byte-distinct partial and full frames
with different item art for `calibration` and `held-out` splits. If the first
handoff contains only one partial and one full frame, it can validate the
composition and replay path but cannot establish a production calibration.

The current real-client evidence remains below this bar: the unchanged
production detector returns `unknown` for the reviewed partial and full iron
inventories; no frame visibly proves a genuine held/drag state; the clean
held-out detector-owned empty region is byte-identical to the reference; and
the earlier batch lacks the structured scaling/mode/theme/renderer/capture-
configuration provenance. Keep those real failures as permanent replays and
do not weaken thresholds, obstruction guards, slot ownership, confidence, or
unknown-state behavior to make them known.

## Required live RuneLite validation

Before declaring any profile production-supported, collect a reviewed empty
reference plus independent empty, partial, full, obstructed, and wrong-tab
frames from the actual consumer-facing capture path. Validate at least:

- fixed and any intended resizable modes;
- physical-pixel dimensions across supported Windows scaling and DPI changes;
- the real inventory anchor and row/column pitch;
- inventory tab hidden or replaced by another tab;
- default and intended alternate themes or stretched layouts;
- hover, selected, drag, quantity-text, menu, tooltip, and dialog states;
- RuneLite sidebar/plugin overlays;
- software and GPU rendering paths used by supported capture;
- low-contrast, dark, small, identical, and wide item sprites; and
- a completely full inventory of repeated items.

Synthetic green tests prove determinism, contracts, and safe failure behavior.
They do not establish live-client detection accuracy.
