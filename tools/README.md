# Development Tools

This directory is for development-only utilities such as frame capture, annotation, replay, route validation, diagnostics inspection, and regression-case generation.

Rules:
- tools are not the production application
- production logic should not live only in a tool
- tools should consume the same typed contracts where practical
- captured failure cases should be promotable into tests/fixtures

## Perception evaluation

`evaluate_perception.py` is the thin command-line entry point for the display-free detector replay
and regression harness. Its reusable contracts, loading, comparison, and reporting logic live under
`mining_automation.perception`. See `docs/PERCEPTION_REPLAY.md` for the manifest schema, commands,
exit codes, and failure-to-regression workflow.

## Inventory fixture preparation

`prepare_inventory_fixture.py` converts only the validated top-down 32-bit
BGRA BMP emitted by the Windows capture diagnostic into the headerless bytes
required by replay schema v1. It rejects other BMP encodings and will not
replace an existing fixture unless `--force` is explicit.

```bash
python tools/prepare_inventory_fixture.py \
  reviewed/empty-reference.bmp \
  fixtures/frames/empty-reference.bgra
```

The tool reports the exact width, height, pixel format, and payload length for
the manifest entry. Visual/privacy review still happens before the raw output
is committed.

## Inventory live validation

`validate_inventory_live.py` is the thin entry point for a passive, one-frame
Windows/RuneLite inventory evidence workflow. Reusable capture, persistence,
reporting, and detector evaluation logic lives in
`mining_automation.perception.inventory`; the tool never contains detector or
Win32 logic.

```powershell
python tools/validate_inventory_live.py --case empty-reference --capture-build <HARNESS_COMMIT_SHA>
```

Until a reviewed live profile/reference detector factory is supplied, the
command completes in `capture-only / profile-not-configured` mode. See
`docs/INVENTORY_LIVE_VALIDATION.md` for artifact safety, optional reviewed
detector activation, exit codes, privacy review, and fixture promotion.

## Guided inventory validation session

`validate_inventory_session.py` is the thin entry point for the ordered,
resumable real-client release-gate evidence session. It prompts the operator to
prepare empty-reference, empty-validation, partial, full, wrong-tab, and
obstructed states, then delegates each capture to the existing one-frame
package workflow. It never manipulates RuneLite.

```powershell
python tools/validate_inventory_session.py --capture-build <HARNESS_COMMIT_SHA>
```

Ctrl+C pauses safely; the printed `--resume` command continues without
recapturing completed evidence. One owned session manifest and one unapproved
profile-review draft summarize cross-case geometry, hashes, provenance,
detector identity, missing evidence, and review blockers. See
`docs/INVENTORY_VALIDATION_SESSION.md` for the complete safety and review
contract.
