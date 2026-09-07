# Mine-to-bank program integration — development candidate

This adds an executable, image-registered navigation component to the program.
It does not claim a completed live route, complete mine/bank/return cycle, or
production GUI release. The previously proven mining code remains unchanged.

## Evidence and behavior

The route profile is compiled from the recovered original frames of the
operator's clean 2026-09-07 13:14–13:19 traversal. Each goal is an observed
location, taken from the next settled pre-action frame or final arrival frame.
The minimap geometry is measured during compilation, not falsely attributed to
the original run. Historical absolute click coordinates are not replayed.

At runtime the program locates minimap chrome, registers reference terrain to
current terrain, projects the next observed place into the current minimap,
and advances only after fresh stationary observations verify arrival. A bounded
correction is allowed when the actual location remains in the recorded route
corridor. Missing evidence triggers bounded passive reacquisition, then STOP.
The route cannot start by attaching to an interior waypoint.

The final goal is beside the customer-side banker counter, not the doorway.
Counter imagery is separately verified. Arrival never opens the bank or moves
inventory items. Mining, deposit, and bank-to-mine execution are not enabled by
this route command.

## Window and input boundary

No resize, maximize, minimize, restore, move-window, or camera operation is used.
The exact HWND, process/thread identity, client origin/size, window rectangle,
maximized state, and DPI are bound and checked before/after capture and input.
Input checks foreground, occlusion, current frame age, pointer location, and
mouse-button state. A partial press or cancellation always attempts release.
Escape and a STOP file cancel execution.

The existing PrintWindow capture was found to render an approximately 0.8-scale
image with black padding on this machine. Therefore navigation uses physical
screen-region capture while RuneLite is foreground and unobscured, rather than
assuming PrintWindow pixels equal physical pointer coordinates. The original
mining capture implementation is not changed by this work.

`--focus-existing` makes one normal SetForegroundWindow request; it never
restores a minimized window or changes bounds. Otherwise the client must already
be foreground. Any geometry change aborts; it is never automatically repaired.

The initial support envelope is the demonstrated north-oriented minimap, known
route, and recorded healthy 23-HP display. Unknown health/orientation is STOP.
These restrictions are not claims of general game-state recognition.

## Entry points

Install the optional `navigation` dependencies for a normal package install.
For the tested local environment, install `requirements-navigation.txt` into
`.venv-route`. The `.route-deps` fallback is preview/compiler-only; live runs
do not add it to their import path. Receipts record interpreter and dependency versions.

Preview (no game input):

```text
python tools/run_mine_to_bank.py
mining-automation route
```

The first live pilot must use the exact reviewed clean commit and current HWND:

```text
.venv-route/Scripts/python.exe -I tools/run_mine_to_bank.py --live --hwnd CURRENT_HWND --title "RuneLite - Chief Luma" --authorize-execution-sha EXACT_SHA --confirm RUN_MINE_TO_BANK_NO_RESIZE --stop-after 1
```

Do not fill these arguments from an old session. The operator may launch and
stop the program, but must not choose or rescue its walking clicks.
The `--stop-after 1` pilot permits only ONE walking click; an unproved arrival
then stops without corrective input. `STAGE_PASS` is not full-route success. Omit `--stop-after` only for the reviewed
full-route trial after the first movement is confirmed live. The route writes
fresh frames, native click receipts, window identity, and result.json.

## Acceptance still required

1. Verify physical capture-to-pointer mapping without changing the client.
2. First program-owned movement, with no operator-selected click.
3. Full mine-to-counter route with zero human/Codex walking assistance.
4. Repeat the full run and check junction, westbound road, south entrance,
   closer endpoint, unchanged window geometry, and no unintended item actions.
5. Integrate an explicitly authorized fresh mining-FULL handover and validate
   that seam live before claiming mining automatically proceeds to the bank.

Automated and replay tests are regression guards, not substitutes for these
live acceptance steps.
