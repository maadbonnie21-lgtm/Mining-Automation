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
