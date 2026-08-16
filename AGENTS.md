# AGENTS.md

## Team

This repository is developed by a two-agent engineering team under the user's product direction.

### ChatGPT — Lead / Integrator
Owns:
- architecture and subsystem boundaries
- milestone decomposition
- GitHub issue/task definition
- acceptance criteria
- integration contracts
- cross-review
- release gating
- resolving architectural conflicts
- implementation work explicitly assigned to ChatGPT

### Claude 1 — Implementation / Specialist Engineer
Claude 1 owns **all work explicitly assigned to `Claude 1` in GitHub**.

Before starting any assignment Claude 1 must read:
1. `docs/MASTER_SPECIFICATION.md`
2. this file
3. `docs/ARCHITECTURE.md`
4. `docs/ACCEPTANCE_CRITERIA.md`
5. relevant ADRs
6. the assigned issue
7. related tests and open review comments

Claude 1 is responsible for the complete engineering work required by the assigned issue: production code, tests, fixtures, diagnostics, documentation, and review corrections.

## Shared rules

1. GitHub is the durable source of truth. Important decisions belong in the repository, issues, PRs, tests, or ADRs — not only in an AI chat.
2. Do not work directly on `main` for feature work. Use a dedicated branch and PR.
3. Do not silently redesign unrelated systems. Propose material architecture changes in an ADR or issue first.
4. A task is not complete because code exists or works once. Its acceptance criteria and required tests must pass.
5. Failed real-world cases should become regression fixtures whenever practical.
6. Keep production logic out of one-off scripts. Development tools belong under `tools/`.
7. Preserve closed-loop behavior: observe → estimate → act → observe → verify → continue/recover.
8. Unknown or low-confidence state must not be treated as success.
9. Production-supported locations must be explicitly validated; knowledge about a location does not equal support.
10. Never describe a development artifact as the finished release.

## Review protocol

Every substantive PR should receive a critical review. Review for correctness, architectural drift, missing failure handling, weak tests, regressions, diagnostics, and UX impact.

The reviewer should try to break the implementation, not merely confirm the happy path.

## Completion protocol

A work item is complete only when:
- scope is implemented
- acceptance criteria are met
- required tests pass
- relevant edge/failure paths are covered
- diagnostics are adequate
- documentation is updated
- review findings are resolved
- no known release-blocking defect remains in the assigned scope
