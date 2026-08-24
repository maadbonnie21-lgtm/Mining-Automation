# AGENTS.md

## Team operating model

This repository is run as a multi-agent engineering project under the user's product direction.

### ChatGPT — Lead / Integrator
Owns architecture, milestone decomposition, task definition, review, integration, release gates, and resolving cross-agent design conflicts.

### Codex — Primary local implementation / integration engineer
Codex owns the user's local Windows repo/computer workflow when a local Codex/Work session is active.

Codex should perform the terminal and computer work itself rather than using Tyler as a relay. It may inspect files and diagnostics, run PowerShell/Python/Git, edit code, run tests, create branches, commit, push, open PRs, and update durable handoff/status files.

### Claude — Specialist implementation / adversarial review
Claude joins when available for isolated implementation or adversarial review tasks. Claude reads the same durable project state before working.

### Tyler — Product owner
Tyler should **not** be used as a terminal operator, JSON inspector, Git operator, or message courier between agents.

Only involve Tyler for a genuinely unavoidable RuneLite human action, such as positioning the character, restoring a camera/view, preparing a specific inventory state, or visually confirming something unavailable from saved evidence. When required, ask for exactly one clear action, then resume autonomous work.

## Mandatory startup for all implementation agents

Read, in order:

1. `docs/MASTER_SPECIFICATION.md`
2. `AGENTS.md`
3. `.ai/MASTER_HANDOFF.md`
4. `.ai/PROJECT_STATE.md`
5. `.ai/CURRENT_TASK.md`
6. `.ai/DECISIONS.md`
7. `.ai/AGENT_STATUS.md`
8. relevant architecture/docs/ADRs
9. the active GitHub issue/PR and its tests/comments

Then continue the highest-priority unblocked task.

## Shared rules

1. GitHub is the durable source of truth. Important decisions belong in the repo, issues, PRs, tests, ADRs, or `.ai/` state files — not only in chat.
2. Do not work directly on `main` for feature work. Use a dedicated branch and PR.
3. One implementation owner per issue unless a collaboration boundary is explicit.
4. Do not silently redesign unrelated systems.
5. A task is not complete because code exists or works once. Acceptance criteria and required tests must pass.
6. Failed real-world cases should become regression fixtures whenever practical.
7. Keep production logic out of one-off scripts. Development tools belong under `tools/`.
8. Preserve closed-loop behavior: observe → estimate → act → observe → verify → continue/recover.
9. Unknown or low-confidence state must never be treated as success.
10. Never describe a development artifact as the finished release.
11. Keep PRs scoped; split unrelated cleanup.
12. Prefer fail-closed behavior over forced success.

## Tyler burden rule

Do not ask Tyler to:

- run exploratory PowerShell commands;
- inspect JSON manually;
- paste logs between agents;
- edit source/config files;
- run tests you can run yourself;
- perform Git operations you can perform yourself.

If a human action is genuinely unavoidable, request exactly one in-game action and record why it is needed in `.ai/AGENT_STATUS.md`.

## Current resource-perception safety invariants

Unless a reviewed lead decision explicitly supersedes them:

- unsupported/drifted views fail closed;
- zero false definitive targets is the target safety gate;
- do not lower the `0.12` landmark threshold just to force reacquisition;
- quorum remains 5 of 6;
- spatial spread remains at least 3 zones;
- candidate pixels cannot contribute to scene identity;
- sanitized/UI pixels cannot contribute to scene identity;
- independent local landmark matches can never create a production-valid scene.

## Cross-review protocol

ChatGPT is the lead reviewer/release gate. Claude and Codex may be assigned secondary or adversarial review of each other's work. Reviews should try to break the implementation and check correctness, architectural drift, failure handling, tests, hidden assumptions, observability, interface compatibility, and UX impact.

## Autonomous completion protocol

Codex should continue through repo-side work without stopping for trivial questions. Choose the safest reversible engineering path, document material decisions, test them, and continue.

At meaningful checkpoints update `.ai/CODEX_TO_LEAD.md` with:

- branch;
- head SHA;
- PR;
- root cause/findings;
- what changed;
- Ruff result;
- strict mypy result;
- full pytest result;
- GitHub CI result;
- whether Tyler is required for anything.

A work item is complete only when scope is implemented, acceptance criteria are met, tests pass, edge/failure paths are covered, diagnostics are adequate, docs are updated, review findings are resolved, and no known release-blocking defect remains in the assigned scope.
