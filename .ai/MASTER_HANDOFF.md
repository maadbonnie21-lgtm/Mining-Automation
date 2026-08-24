# MASTER HANDOFF

## Product

Build a production-grade, polished, vision-driven autonomous Old School RuneScape mining desktop application.

Canonical UX:

`OPEN APPLICATION → SELECT ORE → SELECT SUPPORTED MINE → SELECT WORLD → BUILD RUN/BREAK ROUTINE → PRESS START`

Normal users must not need terminal commands, coordinates, JSON editing, or manual model training.

## Engineering principles

- Closed-loop behavior: action → observation → verification → continue/recover.
- Unknown/unsupported visual state fails closed.
- Real failures become replay/regression fixtures whenever practical.
- GitHub is the durable source of truth.
- Main stays known-good; feature work uses branches/PRs.
- Do not pass prototypes off as the final product.
- Release only with no known release-blocking defect in supported scope.

## Team

- ChatGPT: lead architect/integrator/reviewer/release gate.
- Codex: primary local implementation/integration engineer and local computer operator.
- Claude: specialist implementation/adversarial reviewer when available.
- Tyler: product owner; only unavoidable human RuneLite actions.

## Current major subsystems

1. Windows/RuneLite capture — mature and real-machine validated.
2. Replay/regression infrastructure — mature.
3. Varrock East iron resource perception — advanced but real supported-view/reacquisition gate still open.
4. Inventory perception — advanced; repo-side guided validation tooling complete, real client validation still pending.
5. WorldState fusion — designed, not yet implemented.
6. Closed-loop mining controller — not yet implemented.
7. Navigation / banking / recovery / scheduler / GUI / packaging — later milestones.

## Resource perception invariants

- Four profiled Varrock East iron candidate regions.
- Visual states: available / depleted / uncertain.
- Scene schema v3 uses six structural landmarks.
- Quorum: 5 of 6.
- Spatial spread: at least 3 macro zones.
- Landmark max descriptor distance: 0.12.
- Candidate pixels cannot contribute to scene identity.
- Sanitized/UI pixels cannot contribute to scene identity.
- Independent landmark local minima are diagnostic-only.
- Unsupported/drifted scenes must remain fail-closed.
- Proven real drift gate: 36/36 drift frames UNCERTAIN, zero false definitive targets.

## Inventory perception invariants

- Inventory grid is 4 columns × 7 rows = 28 slots.
- Authoritative slot click/count region is 32×32.
- Wide sprites may visually extend but cannot double-count neighboring slots.
- Unknown inventory must remain `occupied_slots=None`, confidence `0.0`.
- Wrong geometry/tab/obstruction must fail closed.
- Operator case labels are not verified truth.

## Agent burden policy

Agents do local terminal/Git/test/diagnostic work themselves. Tyler is not the debugging harness.

When a task genuinely requires Tyler inside RuneLite, request one clear action, record the blocker, then continue autonomously afterward.
