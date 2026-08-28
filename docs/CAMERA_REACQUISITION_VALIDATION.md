# Camera reacquisition validation

## Status and boundary

`tools/validate_varrock_east_camera.py` is a Windows/RuneLite development
validator for Issue #31. It is not production interaction, navigation, or
recovery code. It composes the existing production capture and resource
detector with a bounded camera-control adapter so a reviewed camera recipe can
be tested repeatedly on the real client.

The current recipe is **not accepted yet**. Acceptance requires both repeated
real-client reacquisition evidence and a fresh pass of the complete 36-frame
real drift set on the same exact Git head. A successful dry run, input receipt,
capture, or synthetic test is not evidence that the supported view was
reacquired.

This work changes no production perception policy. The packaged landmark
thresholds, five-of-six landmark quorum, three-macro-zone requirement,
candidate/resource classification rules, and fail-closed unsupported-view
behavior remain unchanged.

## Supported validation envelope

The validator is deliberately narrow:

- RuneLite must already be authenticated and show the intended Varrock East
  iron scene.
- The selected capture client must be exactly `1005 x 1078` pixels in the
  packaged pixel format.
- Camera input is limited to the reviewed compass point, the four OSRS camera
  keys, bounded and individually paced wheel motion at a reviewed viewport
  point, explicit bounded no-input settles, and an optional reviewed RuneLite
  reset-zoom key.
- No world tile, rock candidate, inventory slot, player, or navigation target
  is clicked.
- The run is development validation only. It does not make the location or
  camera recipe production-supported.

Every camera plan execution receives a fresh focus and client-geometry
preflight. A short or partial input receipt fails the run; it is never treated
as successful camera motion.

The reviewed capture/profile coordinates are RuneLite logical client
coordinates. On the reviewed Windows installation RuneLite is DPI-unaware
while the validator is per-monitor DPI-aware. Immediately before pointer
input, the Windows adapter maps each logical point through RuneLite's DPI
context and then into physical screen coordinates. This prevents display
scaling from turning a reviewed compass or wheel coordinate into a world
click. Focus, exact geometry, left-button state, and top-level-window ownership
are rechecked at the mapped point before input.

## Deterministic normalization plan

One normalization plan is frozen for an evidence run and is replayed in this
order:

1. Click the reviewed fixed-UI compass point to face north.
2. Wait for the bounded, recorded post-compass settle interval.
3. Optionally apply one bounded yaw offset from compass north.
4. Hold the selected pitch direction long enough to reach its endpoint and
   optionally apply one bounded opposite-direction pitch offset.
5. Establish zoom by either:
   - scrolling enough bounded detents to a zoom endpoint, then optionally
     moving a reviewed number of detents back from that endpoint; or
   - using the RuneLite reset-zoom key only when that exact client setting and
     resulting view have been reviewed.

The compass settle, yaw/pitch offsets, pitch endpoint, zoom mode, signed
detent counts, wheel pacing interval, plan identifier, and plan version are
recorded in the report. Endpoint saturation makes the result independent of
the starting pitch or zoom. Each wheel detent crosses the Win32 boundary as a
separate event with a fixed interval because RuneLite can coalesce a batch even
when Windows acknowledges every event in it. The adapter recomputes the mapped
screen point and rechecks its safety gates before every detent. The reset-key
alternative is not a portable default: its RuneLite configuration and
resulting view must be reviewed before evidence from it can be accepted.

Use `--dry-run` to inspect the bounded plans without opening capture or sending
input. For example, this prints an illustrative endpoint plan only; it does not
declare those values reviewed:

```powershell
python tools/validate_varrock_east_camera.py --pitch-endpoint down --zoom-saturate-detents -96 --dry-run
```

## Repeated trial protocol

A complete run applies three distinct deliberate perturbations. The current
protocol exercises yaw, the opposite pitch endpoint, and combined yaw/zoom.
Before the first baseline capture, it applies and records the normalization
plan once; the run therefore does not depend on Tyler manually preparing the
starting camera. For each trial it:

1. captures the starting view;
2. applies one perturbation and captures the perturbed view;
3. requires that the perturbed unsupported view fail closed;
4. replays the same normalization plan; and
5. captures at least two fresh, separated confirmation frames.

Every confirmation must pass the unchanged production detector. In particular,
the scene must match at least five of the six frozen world landmarks across at
least three explicit macro zones, and every profiled resource must have a
definitive production state. Every confirmation must also preserve the exact
ordered `(resource_id, state)` vector observed in that trial's supported
before frame. A natural resource transition during a trial therefore makes
that trial ineligible and requires a fresh run; definitive counts or confidence
cannot substitute for exact state equality. A pass in one trial cannot
compensate for a failure in another trial or confirmation.

The production scene identity remains world-only:

- rock candidate pixels classify resource state only after scene validation;
- fixed RuneLite UI and sanitized rectangles cannot establish scene identity;
- diagnostic local search, registration, or similarity results cannot override
  the production scene verdict; and
- uncertain scenes expose no definitive target.

## Private evidence and reports

Real client images are private development evidence. Each capture produces an
unreviewed raw frame, BMP preview, and JSON fixture draft beneath
`diagnostics/`. The camera run also produces a canonical JSON report and a
`.sha256` sidecar. The repository ignores the entire `diagnostics/` tree, so
none of these artifacts should be added to Git.

The report records frame SHA-256 values, production observations, each trial's
ordered expected resource-state vector, per-confirmation exact-state equality,
landmark distances and thresholds, input receipts, the exact plan, and trial
verdicts. It does not embed raw pixel payloads. Its JSON is serialized
deterministically with sorted keys, indentation, and one trailing newline. The
sidecar contains the SHA-256 of those exact report bytes; the digest is
intentionally not stored inside the report it hashes.

This report can mark only the camera protocol evidence as eligible. It
explicitly records combined Issue #31 acceptance as incomplete because it does
not contain a human review of the live resource states or the separate
same-head 36-frame drift proof. Those gates are joined in the PR handoff; the
camera report never emits an unqualified acceptance pass.

Release-quality evidence must be tied to:

- the full 40-character lowercase Git HEAD SHA;
- a clean worktree before and after the run, including no nonignored untracked
  implementation files;
- the detector ID and version;
- the profile ID;
- the camera plan ID and version; and
- the recorded command arguments.

The validator refuses a dirty-worktree run by default. `--allow-dirty` exists
only for development diagnosis: such a report is non-acceptance evidence even
if its camera trials pass.

Report and sidecar paths are exclusive. Existing evidence is never
overwritten. Use a new case prefix for a new run instead of deleting or reusing
prior evidence.

## Acceptance gate

Do not describe the recipe as accepted until all of the following are true on
the exact candidate head:

- all three real perturbations reacquire without manual coordinate hunting;
- at least two confirmation frames per perturbation pass the unchanged
  production detector;
- the production resource states on the supported view are correct;
- all 36 stored real drift frames remain `UNCERTAIN`;
- the drift set exposes zero false definitive targets;
- Ruff, strict mypy, the focused tests, the full test suite, and GitHub CI pass;
  and
- the real report SHA-256 and its exact clean Git head are recorded in the
  review handoff.

Run the existing drift validator against the private 36-frame set as described
in [the Varrock East profile documentation](VARROCK_EAST_IRON_PROFILE.md). The
camera report and drift report are complementary: neither one alone clears the
Issue #31 real-client gate.
