# Mining Automation

Canonical repository for the production-grade, vision-driven autonomous mining desktop application.

## Product workflow

**Open application → select ore → select supported mine → select world → build run/break routine → press Start.**

The application is expected to hide computer-vision, state estimation, navigation, banking, scheduling, verification, recovery, and diagnostics behind a polished desktop UI.

## Source of truth

Read these before contributing:

1. `docs/MASTER_SPECIFICATION.md`
2. `AGENTS.md`
3. `docs/ARCHITECTURE.md`
4. `docs/ACCEPTANCE_CRITERIA.md`
5. Your assigned GitHub issue

## Team

- **ChatGPT** — lead architecture, integration, task decomposition, acceptance criteria, cross-review, release gating.
- **Claude 1** — engineering work explicitly assigned to `Claude 1` in GitHub, including implementation, tests, documentation, and review fixes.

## Development rule

No detector, script, milestone build, test harness, or partial GUI is the finished product. A supported workflow is release-ready only after the integrated application passes its documented acceptance tests and has no known release-blocking defect inside the declared operating envelope.

## Repository layout

- `src/mining_automation/` — production application code
- `tests/` — unit, integration, regression, and acceptance tests
- `docs/` — architecture, specification, support envelope, decisions
- `knowledge/` — structured mining/location/bank knowledge
- `tools/` — development-only capture, replay, annotation, diagnostics tools
- `.github/` — CI and issue/PR workflow
