# Combined beta candidate - September 8, 2026

## Installed candidate

Checkout: `C:/Users/Tyler/Mining-Automation-BETA-INTEGRATED`.
Entry: `Launch Mining Beta.cmd`. Opening it does not start gameplay.
A dedicated `.venv-beta` is installed for this checkout; it does not replace the
Codex core or CONTINUOUS environments or their saved evidence.

Sources: core `d4661b1d541310127921d86ac5eb828136b352da` (gem/crop repair plus
non-behavioral typing/test fixes) and beta `770e87e996f36debfd2ef4600cbac279c12a4a70`.
The source merge is `4f2d19b`; follow-up changes repair only beta compatibility.
The owner's latest request permits offline composition now. It does not supply
missing live proof or replace the outstanding Jagex app permission.

## Features brought together

- Launcher, saved settings, continuous/finite modes, ordered repeating run/break rows.
- Normal Stop/return-to-mine, Emergency Stop, duplicate-launch prevention, visible status.
- Smooth pointer travel, varied inward rock points, exact hover/click point retention.
- Existing mining, outbound route, item-specific iron/ruby deposit, bank X-close and return.
- Separate iron/gem totals through all phase adapters, retries and displayed status.

## Concrete integration repairs

The beta phase adapter now validates banking against this cycle's mining receipt
and adds actual deposited iron/gem counts instead of a hard-coded 28 iron.
The beta session accepts a full approved mixed load while preserving separate totals.
Its pre-click expiry wrapper preserves the first segment's item counts and all gem
gains across reacquisition. Invalid accounting still fails rather than becoming a pass.
Garbled English UI labels are repaired. Existing gameplay/target/freshness behavior
was not replaced with guessed clicks or new recognition thresholds.

## Verified without gameplay

- 536 focused tests passed on the combined source; source hashes remained unchanged.
- Two launcher-label regression tests passed.
- Repository Ruff and Linux-target strict mypy passed (113 source files).
- Dedicated-environment launcher import check passed.
- The real launcher opened and closed with native hotkeys registered/released and
  window discovery completed. Start was not pressed; actual gameplay hotkey response
  still needs live testing.
- Desktop shortcut installed: `Mining Beta - Test Candidate.lnk`.
- Actual Tk GUI opened; mode/settings and routine editing, save/reload, duplicate lease,
  no-target Start and visibility of Start/Stop/Emergency controls passed. Game access
  and hotkey events were replaced during that test; no gameplay was launched.
- Six required private reference frames copied locally with byte/hash equality.
  They remain untracked and were not uploaded to GitHub.

Local detailed receipts are under `outputs/`. Neither mocked phase execution nor
saved-frame replay is a live mining/deposit/cursor/logout result.

## Still pending

The deliberate logout profile is not present; run/break input cannot yet be called
ready. Real logout, timed logged-out break, ordinary login and fresh resumed mining
need authorized source evidence and a live test. There is no guessed logout target.
All combined-build live gameplay tests remain NOT RUN, including three consecutive
cycles, mouse movement with ore gains, normal Stop from each phase and emergency
button/key behavior during gameplay. The Jagex permission was not changed.
Bounded raw-frame retention remains unimplemented; no unlimited-endurance claim.

## Next live QA

First establish three uninterrupted core cycles on one fixed build, then perform
separate feature tests on the combined build. Each continuous live test is capped
at ten cycles and must not be extended by chained batches. Normal completion ends
at the mine with empty inventory and the bank closed; Emergency Stop stays immediate.
