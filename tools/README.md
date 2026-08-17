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
