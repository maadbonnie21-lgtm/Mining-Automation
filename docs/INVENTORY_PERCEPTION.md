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
must supply a reviewed `InventoryFrameProfile` and matching empty reference.
Unsupported frame geometry is reported as an unknown inventory; it is never
silently assigned a guessed count.

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
   pixels with that reference so a broad overlay or wrong panel cannot become
   28 confident item detections.
4. The detector aggregates only when localization and all 28 slot decisions
   meet the configured confidence policy.
5. `inventory_state_from_observation` is the sole public evidence parser and
   produces the shared `InventoryState` used by controller code.

Expected runtime misses, such as an unsupported frame size or insufficient
localization confidence, produce one well-formed unknown observation. Broken
component contracts, such as a locator returning an out-of-frame region or a
classifier returning malformed slot order, raise a typed inventory detector
error; the generic evaluation harness records that as a detector failure.

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
- `configuration_id`, which identifies the classifier algorithm, layout,
  policy, and reviewed empty-reference fingerprint
- ordered `slots`, each with index, row, column, region, state, confidence, and
  score diagnostics

Consumers must use `inventory_state_from_observation`; they must not parse this
mapping in controller logic. The adapter rejects wrong kinds, unknown schema
versions, malformed values, and incoherent label/count combinations before
constructing `InventoryState`.

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
crop or redact outside the evidence region when that does not change the
failure. Fixture payloads should stay small enough for normal repository and CI
use.

## Required live RuneLite validation

Before declaring any profile production-supported, collect reviewed empty,
partial, full, and obstructed frames from the actual consumer-facing capture
path. Validate at least:

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
