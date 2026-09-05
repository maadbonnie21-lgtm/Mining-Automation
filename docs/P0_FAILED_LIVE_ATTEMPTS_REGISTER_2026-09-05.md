# P0 failed live PREP attempts register — 2026-09-05

These attempts are failure evidence only. They must not be cited as camera-normalization success or mining proof.

## Attempt 1 — seven-step zoom/pitch sequence

- Exact head: `5a0b1033be88e0073a2c272fb203708bcf378ee6`
- Live result: `camera_search_exhausted`
- Resource: `0/6` landmarks across `0/3` zones
- Mining started: no
- PREP authority after return: relinquished
- Mining input authority: false
- Disposition: retired; sequence was an unsupported inference.

## Attempt 2 — copied Issue #31 candidate ladder

- Exact head: `cc6e8e0d7e3c14ed2ac2fd36b26fc64f44746148`
- Local receipt: `diagnostics/prep-auto-28-20260904-232611-da88929d/result.json`
- Live result: `camera_search_exhausted`
- Resource: failed unchanged `0.12 / 5-of-6 / all-three-zone` gate
- Mining started: no
- PREP authority after return: relinquished
- Mining input authority: false
- Disposition: retired from P0; Issue #31 was explicitly closed not planned and not claimed solved.

## Safety result

Both attempts correctly sent zero mining input after PREP failed. This proves only that the fail-closed boundary held for these attempts. It does not prove camera setup or mining capability.

## Corrective action

The current P0 branch removes both open-loop ladders from the executable startup path. The next live command is blocked until a software-normalization resolver passes the acceptance matrix in `docs/P0_STARTUP_RESOLVER_REQUIREMENTS_2026-09-05.md` and is independently diff-audited.