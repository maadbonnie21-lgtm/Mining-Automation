# Beta controls candidate - issue #119 / PR #121

This is the combined implementation candidate in Mining-Automation-BETA-INTEGRATED,
not live-accepted software. It includes the gem-capable core and the beta controls.
The original CONTINUOUS and Codex reliability checkouts are unchanged.
The launcher may be opened for settings; opening it never starts gameplay.

## Entry point and controls

Double-click `Launch Mining Beta.cmd` in this checkout to open the controls.
Gameplay Start still requires the existing exclusive issue #94 live-test handover. Select the existing
account window and press Start. Do not resize, restore, move or adjust RuneLite to
make an unsupported view pass. The first authorized Start may focus the existing,
non-minimized window once; losing focus later stops input.

Continuous has no cycle cap and no automatic breaks. Finite accepts any positive
integer, including 1000. Routine uses ordered active/break rows in minutes; rows can
be added, updated, copied, removed and reordered. Repeat loops the routine; otherwise
the final break ends logged out. Saved settings never silently restart an active task.

Normal Stop (F8) latches a drain: finish the current mining/outbound/bank/return
sequence, deposit the approved iron/gems, verify empty and bank closed, then freshly verify
the stationary canonical mine endpoint. No next cycle is started. During a break,
Stop cancels the scheduled login. A normal close request waits for this same drain.
Emergency Stop (F9) cancels owned input processes without attempting to walk home.
Failures report `return_not_completed`; they do not invent a successful return.

The nonactivating status panel is placed outside the game window. It shows phase,
cycles, deposited iron and gems, runtime, active/break countdown, wind-down overrun and reason.
Panel placement, GUI button operation, F8/F9 registration and emergency behavior
still require live verification. Clicking Stop with the mouse must be tested too;
a passing hotkey policy test does not prove pointer/focus handover works.

## Frozen-build input permission

Start requires `outputs/beta-live-handover.json`, supplied only after the lead's
explicit issue #94 release of the input slot. It must identify issue 94, the actual
handover comment, exact `git_sha`, exact `hwnd`, `input_owner_released: true`, a finite
future `expires_at_unix`, and the permitted `features`. Always require `launcher`,
`canonical_finish`, `emergency_stop` and the selected mode (`continuous`, `finite` or
`routine`). `smooth_cursor` and `varied_rock_points` require separate permission.
A configuration file is not itself proof that the lead released the slot.
No handover file has been fabricated by this worker. Dirty or changed source, another
known phase runner, duplicate launcher or mismatched window prevents Start.

## Scheduling and authentication gate

Active expiry drains the in-flight cycle. The UI displays any overrun honestly.
The requested break begins only after deliberate ordinary logout is positively
verified, not when the active timer expires or while returning/banking.
The break issues no game input. Resume uses the existing reviewed preauthenticated
Play/Welcome path and then fresh account/window, location, inventory, closed-bank
and rock observations. Unknown/challenge screens stop for owner attention; no
credentials, account switching or security workarounds are implemented.

**Missing input evidence:** there is no committed reviewed
`src/mining_automation/beta_profiles/logout.json`. Routine Start therefore refuses
input. A profile must be based on authorized live observations of the real logout
control and its result: ordinary control step(s), inward point/region, at least two
independent fingerprints, exact private 1005x1078 BGRA source hash, and issue #94
review provenance. The loader verifies every anchor against its retained source,
and the profile must match the frozen Git commit. Do not invent coordinates or
mark synthetic test fixtures live-reviewed. Private source pixels stay local.

## Pointer changes and inherited route repair

Smooth movement and varied inward rock points default OFF. A sampled point is
chosen before hover, and the same point is used for the exact fresh Mine Iron rocks
proof and native mouse-down. Motion is cancellable and checks the current window,
foreground, human pointer/button state and source age. Nothing randomizes terrain
destinations. A pre-down source expiry permits at most two consecutive fresh
reacquisitions through the existing miner; other errors do not restart a failed run.
Delivered inputs and ore counts remain accumulated truthfully.

Exact departure repair d1d2198189820dc7bf953f03d615845480542c7d was composed into
this inactive checkout with merge a8a5e393a5f0a192016c14d0ee7ff182690a5ab4. Its
route/profile/controller and native Resource+C authority methods are preserved.
The beta adapter also retains the native authority deadline through cursor travel
and immediately before dispatch. This is source integration, NOT a successful live
three-cycle result. The combined build also retains the core focus-recovery changes and native ruby crop fix.
Core source base: d4661b1d541310127921d86ac5eb828136b352da.
Beta source: 770e87e996f36debfd2ef4600cbac279c12a4a70.

## Evidence and current limitations

Each new session has status/result files, exact build and window identity, child
ownership records, immutable original phase results, separate hashed boundary
receipts, completed-cycle JSONL and sampled-input receipts. No existing acceptance
or failure evidence was deleted or rewritten. Exceptions cannot count as clean
uninterrupted endurance; deliberate break/resume is marked interrupted as well.

Bounded successful raw-frame retention is NOT implemented. The retention edit was
blocked by the tool safety gate; it was not retried by another route. A read-only
4 GiB free-space reserve refuses a new phase below that threshold. It does not
bound writes inside an active phase or make 1000 cycles fit on disk. Raw growth is an explicit long-run
blocker. Emergency button release under forced/hung-process cancellation and
mouse-operated Stop remain live/review gates, not proven by dummy process tests.

## Checks without GUI or RuneLite access

` .venv-beta\Scripts\python.exe -I tools\run_beta_launcher.py --check ` imports
only; it does not open the GUI, register hotkeys, capture a window or send input.
Focused tests cover policy, mock input, owned dummy process jobs and existing
navigation/banking interfaces. They are not a replacement for live acceptance.

Live acceptance remains NOT RUN for the beta: Start/duplicate prevention; continued
cycling beyond the old cap; Stop from mining/travel/banking; exact endpoint; immediate
Emergency/no further input; actual logout and timed break; Stop during break; ordinary
login plus fresh new ore; visible smooth motion and multiple proven points on the
same rock; motion cancellation; and three consecutive full cycles of the changed
integrated build. Keep PR #121 draft until the required evidence is reviewed on #94.

## Combined-build checks and test limit

See BETA_INTEGRATION.md for the actual checks on this combined build.
Every live QA run must end at or before ten complete cycles. The next core proof
is exactly three uninterrupted cycles; do not chain batches around the ten-cycle
ceiling. Continuous mode remains an unbounded product setting, not permission
for an unbounded test. No live handover or logout profile was invented here.
