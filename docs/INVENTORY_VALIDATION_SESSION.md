# Guided inventory validation session

`tools/validate_inventory_session.py` coordinates the required real-client
inventory evidence into one guided, resumable Windows/RuneLite session. It is
built on the same passive one-capture implementation documented in
`INVENTORY_LIVE_VALIDATION.md`.

The tool never clicks, changes tabs, moves the mouse, drags items, types into
RuneLite, or prepares game state. It waits while the operator prepares each
requested state and captures only after Enter is pressed.

## Default one-command session

Run from the repository root on Windows with RuneLite open:

```powershell
python tools/validate_inventory_session.py --capture-build <HARNESS_COMMIT_SHA>
```

The default ordered plan is:

1. `empty-reference`
2. `empty-validation`
3. `partial`
4. `full`
5. `wrong-tab`
6. `obstructed`

Optional evidence can be appended without changing the required order:

```powershell
python tools/validate_inventory_session.py `
  --capture-build <HARNESS_COMMIT_SHA> `
  --include-case hover-drag `
  --include-case quantity-text
```

Every prompt repeats the exact state the operator should prepare. The label is
still stored as `operator-selected-unverified`; pressing Enter and capturing a
frame never establishes ground truth or a pass result.

## Safe pause and resume

Ctrl+C at a preparation prompt pauses the session. Completed cases remain
owned and are never recaptured. The command prints the exact resume command:

```powershell
python tools/validate_inventory_session.py --resume "<SESSION_DIRECTORY>"
```

The durable manifest is reconciled before continuing. A complete capture that
was written immediately before interruption is adopted only when it belongs to
the one current case durably marked `capturing` and its provenance exactly
matches the durable session. Complete evidence for a pending or later case,
foreign-provenance evidence, and partial or ambiguous orphan evidence are
preserved and block resume without overwrite or recapture.

The case plan and provenance are immutable after session creation. A
capture-only session cannot switch to a reviewed detector halfway through;
owned captures can be evaluated separately after review.

## Session artifacts

One unique directory is allocated below
`diagnostics/inventory-validation-sessions/`. It contains:

- `captures/`: one unique one-capture evidence directory per completed case;
- `session-report.json`: deterministic session schema v1;
- `inventory-profile-review.draft.json`: explicitly unapproved review draft.

Each case directory retains the original one-capture artifacts:

- exact owned BGRA payload;
- BMP preview;
- unreviewed replay-case draft;
- deterministic per-capture JSON report.

The session report records case order, completion state, relative report paths,
hashes, frame geometry, pixel format, reported DPI, window class, detector
mode/status, profile/configuration identity, occupied slots, confidence, and
reason where available.

## Cross-case review checks

The session summary flags, but never auto-corrects:

- incomplete required cases;
- inconsistent frame geometry;
- inconsistent pixel formats;
- inconsistent window classes;
- byte-identical reference and held-out empty captures;
- detector execution errors;
- absence of a reviewed live detector/profile;
- the mandatory privacy and ground-truth review boundary.

Capture-only evidence remains useful even when the detector is not configured.
It does not become production truth.

## Profile-review draft

The profile-review draft links the owned evidence and freezes the known logical
inventory model:

- 4 columns;
- 7 rows;
- 28 logical slots;
- authoritative slot size 32x32.

It deliberately leaves the frame-local inventory origin and row/column strides
unset. The tool does not guess coordinates from synthetic data or self-approve
a live profile. A reviewer must inspect the real captures, establish geometry,
separate reference/calibration/held-out evidence, verify wrong-tab and
obstruction fail-closed behavior, and approve profile/configuration identity
before any detector factory is activated.

## Reviewed-detector sessions

After an explicitly reviewed live detector factory exists:

```powershell
python tools/validate_inventory_session.py `
  --capture-build <HARNESS_COMMIT_SHA> `
  --reviewed-detector approved_inventory:build_detector
```

The exact captured frame for each case is evaluated through the production
`InventoryDetector` and strict `InventoryState` adapter. Unsupported geometry
or obstruction remains an unknown inventory with `occupied_slots=null` and
confidence `0.0`; a detector contract/execution failure is retained as a
session blocker and exits nonzero.

## Privacy and release boundary

Raw frames, BMPs, and RuneLite window identity can expose private information.
Review every artifact before sharing or committing it. The generated profile
file is a draft only, has `activation_allowed=false`, and cannot be treated as
release approval.
