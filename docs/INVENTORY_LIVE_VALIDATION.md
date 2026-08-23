# Inventory live validation

`tools/validate_inventory_live.py` is a passive, one-command Windows/RuneLite
evidence workflow for the inventory detector. One invocation captures exactly
one fresh client-area frame through `WindowsCaptureBackend`, saves the owned
pixels, and writes an unreviewed JSON report. It does not click, select a tab,
drag, type, retry, or otherwise manipulate RuneLite.

Capture completion is not visual validation. The operator selects a case label
only to describe the intended setup; the report always stores that label as
`operator-selected-unverified` and never derives an expected slot count or a
pass result from it.

## First capture and capture-only mode

Run the command from the repository root on Windows with RuneLite open and the
inventory tab manually visible:

```powershell
python tools/validate_inventory_live.py --case empty-reference --capture-build <HARNESS_COMMIT_SHA>
```

The supported labels are `empty-reference`, `empty-validation`, `partial`,
`full`, `wrong-tab`, `obstructed`, `hover-drag`, and `quantity-text`. They are
an exact allowlist; spelling and case variants are rejected before capture.
Optional provenance can be added with `--runelite-build` and repeatable
`--note` arguments. `--title` changes the case-insensitive window-title match;
its default remains the production backend's `runelite` match.

No reviewed live profile/reference ships yet. With no detector option, the
normal result is therefore:

```text
capture-only / profile-not-configured
```

That result is safe and successful evidence collection, not a detector verdict.
The raw frame, BMP, draft, and report are retained for privacy and ground-truth
review.

## Reviewed-detector mode

After a live `InventoryFrameProfile` and its independent empty-reference
`Frame` have been reviewed, expose a no-argument factory that composes them
with `inventory_detector_from_profile`. Then add its import specification:

```powershell
python tools/validate_inventory_live.py --case empty-validation --capture-build <HARNESS_COMMIT_SHA> --reviewed-detector approved_inventory:build_detector
```

The factory must return the existing production `InventoryDetector`. The
harness runs it against the exact owned `Frame` that is written to disk, then
passes its sole observation through the strict inventory diagnostic and
`InventoryState` adapters. The report records detector/version identity,
configured and observed profile/configuration identity, occupied slots,
confidence, region, slot-state counts, and a fail-closed reason.

Unsupported geometry remains a configured detector run with an `unknown`
inventory, `null` occupied slots, confidence `0.0`, and a reason. It is not
downgraded to capture-only. A detector execution or contract failure is written
as `detector-error`, retains the captured evidence, and exits nonzero.

The optional factory is the activation seam for future reviewed calibration.
The harness does not create a profile from the fresh capture, load synthetic
calibration as live truth, or auto-promote an `empty-reference` label.

## Artifacts and report contract

Every run atomically allocates a new directory below
`diagnostics/inventory-live/`; timestamp collisions receive numeric suffixes.
There is no force or overwrite option. The directory contains:

- `frame.bgra`: exact owned `Frame.payload`;
- `frame.bmp`: top-down 32-bit BGRA diagnostic image of the same pixels;
- `replay-case.draft.json`: explicitly unreviewed promotion candidate with no
  executable `expected_observations`; and
- `report.json`: deterministic inventory live-validation report schema v1.

The report uses run-relative artifact paths and records the raw/BMP hashes,
captured frame geometry and pixel format, frame identity and monotonic time,
UTC wall time, reported DPI when available, selected window title/class,
operator label, build notes, and detector outcome. It never contains a
`passed` field.

Exit codes are stable:

- `0`: capture and all artifacts completed, including capture-only or a known
  or fail-closed unknown detector observation; this does not mean validation
  passed;
- `1`: capture, persistence, or configured-detector execution/contract error;
- `2`: command setup, reviewed-detector loading, or unsupported-environment
  error.

## Review and replay promotion

Frames and window titles may expose account names, chat, notifications, plugin
panels, or other private data. Inspect all four artifacts before sharing or
committing them. A reviewer must independently establish the visual ground
truth, record the exact occupied count where applicable, and add the complete
live-fixture provenance described in `INVENTORY_PERCEPTION.md`.

Only then should the owned raw frame be promoted into replay schema v1 with
explicit expected observations and an inventory-specific count assertion.
Keep the reference, calibration, and held-out sets separate. A successful
capture or agreement with the operator's label is never sufficient promotion
evidence.
