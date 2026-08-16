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

`annotate_resource_fixture.py` adds frame-local resource annotations and performs the explicit review transition. This is internal dataset work; normal application users never enter regions or train detectors.

`build_resource_replay_manifest.py` promotes reviewed drafts into the merged replay-schema-v1 format. See `docs/RESOURCE_PERCEPTION.md` for the complete workflow and privacy rules.
