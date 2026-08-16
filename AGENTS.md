# AGENTS.md

## Team

This repository is developed by a three-agent engineering team under the user's product direction.

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

Primary focus is deep subsystem implementation, especially capture, perception, visual-state extraction, and related testing unless an issue assigns otherwise.

### Codex — Implementation / Integration Engineer
Codex owns **all work explicitly assigned to `Codex` in GitHub**.

Primary focus is implementation-heavy foundation and integration work: shared contracts, state/control plumbing, diagnostics, CI/tooling, test infrastructure, refactors, subsystem adapters, and integration tasks unless an issue assigns otherwise.

Codex must not duplicate Claude 1's active issue or silently redesign Claude-owned subsystems. One issue has one implementation owner unless the issue explicitly defines a collaboration boundary.

## Required reading before any assignment

Every implementation agent must read:
1. `docs/MASTER_SPECIFICATION.md`
2. this file
3. `docs/ARCHITECTURE.md`
4. `docs/ACCEPTANCE_CRITERIA.md`
5. relevant ADRs
6. the assigned GitHub issue
7. related tests, open PRs, and review comments

The assigned owner is responsible for the complete engineering work required by the issue: production code, tests, fixtures, diagnostics, documentation, and review corrections.

## Shared rules

1. GitHub is the durable source of truth. Important decisions belong in the repository, issues, PRs, tests, or ADRs — not only in an AI chat.
2. Do not work directly on `main` for feature work. Use a dedicated branch and PR.
3. One implementation owner per issue. Do not independently implement another agent's active issue.
4. Do not silently redesign unrelated systems. Propose material architecture changes in an ADR or issue first.
5. A task is not complete because code exists or works once. Its acceptance criteria and required tests must pass.
6. Failed real-world cases should become regression fixtures whenever practical.
7. Keep production logic out of one-off scripts. Development tools belong under `tools/`.
8. Preserve closed-loop behavior: observe → estimate → act → observe → verify → continue/recover.
9. Unknown or low-confidence state must not be treated as success.
10. Production-supported locations must be explicitly validated; knowledge about a location does not equal support.
11. Never describe a development artifact as the finished release.
12. Keep PRs scoped. If unrelated cleanup is discovered, raise a separate issue unless it is required to make the assigned task correct.

## Cross-review protocol

Substantive PRs should receive critical review. ChatGPT is the lead reviewer and release gate. Claude 1 and Codex may be assigned secondary/adversarial review of each other's work.

Review for:
- correctness
- architectural drift
- missing failure handling
- weak tests
- hidden assumptions
- regressions
- observability/diagnostics
- interface compatibility
- UX impact

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
