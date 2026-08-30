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

### Track A real-frame diagnosis

The fresh real frame
`diagnostics/varrock-east-iron/frames/issue31-one-detent-preflight-368edfb8.raw`
was diagnosed before designing the second guidance policy. The unchanged
production evaluator matched `0/6` landmarks, returned every profiled resource
as `UNCERTAIN`, and exposed zero definitive target IDs. A diagnostic-only
wide search recovered three strict local matches, distributed across the three
macro zones, but at materially different offsets: west-lower `(39,-54)`,
south-path `(55,-10)`, and north-east `(-44,-45)`. The best shared offset
matched only `1/6` landmarks. The recorded conclusion was therefore
`insufficient_registration_evidence`, not an actionable coherent translation
or scale estimate.

This evidence retires zoom-only guidance as insufficient for the current real
camera envelope. It does not establish a replacement transform, prove that a
camera action will have its intended RuneLite effect, or count as successful
live reacquisition. The wide and independent recoveries remain diagnostic:
they cannot validate the scene, expose resources, or override the production
`0/6` result.

## Supported validation envelope

The validator is deliberately narrow:

- RuneLite must already be authenticated and show the intended Varrock East
  iron scene.
- The selected capture client must be exactly `1005 x 1078` pixels in the
  packaged pixel format.
- Camera input is limited to the reviewed compass point, the four OSRS camera
  keys, bounded validation-only middle-button camera drags from one reviewed
  open-viewport point, bounded and individually paced wheel motion at another
  reviewed viewport point, explicit bounded no-input settles, and an optional
  reviewed RuneLite reset-zoom key.
- No world tile, rock candidate, inventory slot, player, or navigation target
  is clicked.
- The run is development validation only. It does not make the location or
  camera recipe production-supported.

Every camera plan execution receives a fresh focus and client-geometry
preflight. A short or partial input receipt fails the run; it is never treated
as successful camera motion.

The live validator also owns the machine-global Windows named mutex
`Global\MiningAutomation.VarrockEastCameraValidationInput.v1` before it opens
capture, focuses RuneLite, or sends input. A concurrent invocation fails
immediately and performs none of those operations. The owner retains the lease
through input release, capture cleanup, provenance recheck, and exclusive,
fsynced report-and-digest publication. Dry runs remain no-input and do not
acquire the lease.

Inside that lease, and before constructing capture, the validator rejects any
legacy raw/BMP/draft/report artifact for the requested case prefix and creates
an exclusive, fsynced reservation marker. The reservation is permanent even
when the attempt fails, making the entire prefix single-use and preventing a
retry from discovering stale evidence only after moving the camera.

An abandoned mutex is not recovered as a clean handoff: the predecessor may
have died after a key/button down, so that invocation releases the transferred
mutex ownership and fails closed without capture, focus, or input. Every later
plan preflight proves the left and middle buttons plus Control and all four
arrow keys are globally up before focus or input, so an abandoned held input
cannot leak into a subsequent run. Failed or cross-thread mutex release keeps
the local process poisoned against another validator. Report targets are
rechecked inside the lease; if lease release fails after publication, only
artifacts proven to have been created by that invocation are retracted.

The camera-plan pointer coordinates are in the DPI-unaware target RuneLite
window's **logical client** space. The captured perception frame is also
indexed by its reviewed logical client pixels. Those are distinct from both
target-logical **screen** coordinates and Windows **physical screen/client**
coordinates; the validator does not treat a physical-client point as a frame
pixel index.

The control also binds the discovery-time RuneLite title and window class to a
fresh PID/thread/class/title identity snapshot. It revalidates that identity at
every readiness and final pointer seam; a recycled HWND cannot inherit focus or
geometry approval, and a growing title cannot pass via truncated-prefix reads.

On the reviewed Windows installation RuneLite is DPI-unaware while the
validator is per-monitor DPI-aware. Immediately before pointer input, the
Windows adapter obtains the physical screen position of client origin `(0,0)`,
converts that origin into RuneLite's target-logical screen space, adds the
reviewed logical-client delta there, and only then calls
`LogicalToPhysicalPointForPerMonitorDPI`. It reverses the final point through
`PhysicalToLogicalPointForPerMonitorDPI`, subtracts the same target-logical
screen origin, and requires the exact original logical-client point. A caller
that is not per-monitor aware, a failed transform, or a non-exact round trip
fails closed before input. This avoids both unsafe alternatives: passing a
client-relative point to an API that expects screen coordinates, and passing a
physical-screen result back to `ClientToScreen` as if it were client-relative.
Focus, exact geometry, button state, and top-level-window ownership are then
rechecked at the proven physical screen point before input. After every
`SetCursorPos`, the adapter reads the actual cursor position twice: once to
prove Windows did not clamp or misland it and again immediately before the
final root-window ownership check. A mismatch fails before the next pointer
phase or wheel event.

## Semantic key-release boundary

Every accepted arrow-key or reset-zoom key-up receives a fixed one-second
semantic client-consumption settle before the camera plan may start its next
action. `SendInput` acknowledges insertion into the Windows input stream, and
`GetAsyncKeyState` can prove the global key state is observably up after that
wait; neither fact alone proves that RuneLite's event thread consumed the
release before a following drag. The adapter therefore retains key ownership
through the full wait, then requires the key observably up and revalidates the
bound HWND identity, foreground focus, and exact `1005 x 1078` geometry. A
delayed or unobservable release, or any target change during the boundary,
fails closed before later input.

Lifecycle cleanup still attempts every adapter-owned key-up even after target
focus or geometry is lost. It applies the same fixed wait and observable global
up check, but deliberately does not require target readiness; cleanup must not
strand an injected key merely because RuneLite stopped being safe to operate.
The pre-key-down gate ensures the adapter never acquires or releases a key that
was already held by the user. Reports serialize this semantic wait and its
post-wait safety checks separately from each key's requested hold duration.

## Validation-only middle-drag primitive

The optional single-plan refinements `--yaw-drag-pixels` and
`--pitch-drag-pixels` use one narrow middle-button primitive. They are signed
logical-client displacements on the horizontal and vertical axes. Zero omits
the corresponding drag. These values are experiment inputs, not frozen
strategy constants; the canonical production-gated candidate strategy does
not accept them.

Every drag starts at reviewed open-viewport logical point `(200, 600)`. That
point is outside the fixed UI, frozen rock candidates, and frozen scene
landmarks. The contract permits exactly one nonzero axis per action, bounds its
absolute displacement to 256 logical pixels, and divides it into deterministic
moves of at most four logical pixels. The start, complete path, and endpoint
must remain in the explicit reviewed open-viewport rectangle `0 <= x < 520` and
`34 <= y < 850`; the exclusive right and bottom edges prevent a drag from
entering the frozen right or bottom UI. The generated path excludes the start
and includes the exact endpoint. The report serializes these exact bounds, so a
review never has to infer which parts of the `1005 x 1078` client were allowed.

Before middle-down, the adapter exact-round-trip maps and root-window checks
the start, every intermediate path point, and the endpoint while both left and
middle buttons are proven released. Immediately before every held move it
freshly maps and root-checks that destination again; after `SetCursorPos` it
requires exact cursor readback, focus, the bound HWND identity, exact geometry,
owned-middle state, released-left state, and target-root ownership. The same
checks repeat after each pacing settle. An overlay appearing after the initial
corridor scan therefore stops the drag before the cursor enters that point.

After an acknowledged middle-down, RuneLite receives one fixed one-second
arming settle before the first path move. Each path move is followed by its
own fixed one-second client-consumption settle, including the final endpoint
before middle-up. An acknowledged middle-up then receives a fixed one-second
post-release settle because `SendInput` proves insertion into the Windows input
stream, not that RuneLite's event thread has consumed the button transition or
cursor motion. Live calibration demonstrated all three timing boundaries: a
shorter arming interval could drop the first drag motion, a 50-millisecond
per-move interval let exact repeated paths produce different camera geometry,
and a shorter release interval could let RuneLite consume the next cursor
relocation as prior-drag motion. The one-second arming, per-move, and release
boundaries are reviewed semantic gates rather than throughput optimizations;
they do not change a camera recipe. The adapter retains
ownership until the global middle state is observably up and focus, exact
geometry, cursor position, and target-root ownership are all reverified at the
unchanged final endpoint. A later
wheel action proves both left and middle buttons released before and after its
cursor relocation. This prevents that relocation from becoming unintended
camera motion while RuneLite still considers the drag active.

The adapter marks provisional middle-button ownership before `SendInput`,
attempts bounded middle-up cleanup in `finally` after every short count or
exception, and retains unresolved ownership for the outer lifecycle cleanup.
It never releases a left or middle button that preflight found already held by
the user. Any short down, move, or up receipt, unobservable release, or failed
post-release endpoint check fails the camera plan closed.

The report serializes the exact reviewed start, coordinate space, signed axis
delta, complete logical path, step count, step bound, arming settle, per-move
settle, final-settle inclusion, post-release settle and verification contract,
and the separate `1 / step_count / 1` middle-down/move/middle-up receipts. These
receipts prove attempted delivery; only the unchanged production camera
evaluation can prove reacquisition.

An illustrative no-input single plan can be inspected with:

```powershell
python tools/validate_varrock_east_camera.py --pitch-endpoint up --yaw-drag-pixels 8 --pitch-drag-pixels -5 --zoom-saturate-detents 96 --zoom-offset-detents -16 --dry-run
```

The numbers above are deliberately illustrative and do not establish a
reviewed recipe.

Before live pointer trials, run the native no-input mapping audit:

```powershell
python tools/diagnose_camera_pointer_mapping.py --output diagnostics/issue31-camera-mapping.json --save-capture diagnostics/issue31-camera-mapping-logical.bmp --save-annotated-capture diagnostics/issue31-camera-mapping-logical-marked.bmp --save-physical-screen-capture diagnostics/issue31-camera-mapping-physical.bmp --save-physical-screen-annotated-capture diagnostics/issue31-camera-mapping-physical-marked.bmp
```

It captures one private RuneLite client frame but sends zero cursor, mouse,
wheel, or key events. For the compass and wheel points it records the HWND,
physical and estimated target-logical geometry, caller/process/target DPI
awareness, target-reported and effective mapping DPI/scale, every corrected
intermediate, the exact reverse logical-client round trip, and comparison-only
values for the two rejected legacy orderings. The PrintWindow BMP marks
`(608,49)` in magenta and `(400,50)` in cyan in RuneLite's target-logical
raster. A separate foreground physical-screen client crop marks the final
mapped physical points. That capture is bracketed by exact checks of foreground
HWND, client origin, client geometry, and top-level ownership at both reviewed
points. The report keeps the coordinate spaces separate and publishes images
before JSON, so a report never claims a missing artifact. All paths are
exclusive and must resolve to distinct files. Visual inspection is still
required; the report never infers compass identity from a pixel hash.

## Legacy fixed candidate strategy (non-canonical)

The original validator exposes
`varrock-east-production-gated-search-v1@1.0.0`, a fixed center-out list of
eleven candidates. It remains available only for regression compatibility.
Exact-head real-client evidence showed that complete Win32 receipts can still
correspond to a semantic camera no-op inside RuneLite: one clean in-game repeat
changed projective geometry while the other retained byte-identical static
terrain. The fixed/open-loop path is therefore not canonical Issue #31
acceptance evidence and must not be used for another parameter grid.

Every legacy candidate is a complete independent reset in this order:

1. Click the reviewed fixed-UI compass point to face north, holding the
   injected left-button press for the fixed 100-millisecond semantic dwell.
2. Wait for the bounded, recorded post-compass settle interval.
3. Apply the candidate's bounded right-yaw offset from compass north.
4. Hold pitch up to its endpoint, then apply the candidate's bounded down-pitch
   offset.
5. Scroll `+96` individually paced detents to the zoom endpoint, then `-17`
   detents back from it.

The ordered `(down-pitch seconds, right-yaw seconds)` candidates are:
`(0.60,0.05)`, `(0.58,0.05)`, `(0.62,0.05)`, `(0.56,0.05)`,
`(0.64,0.05)`, `(0.60,0.04)`, `(0.58,0.04)`, `(0.62,0.04)`,
`(0.60,0.06)`, `(0.58,0.06)`, and `(0.62,0.06)`.

After each complete candidate and settle, the tool captures a fresh frame and
runs only the unchanged production camera evaluation. It stops at the first
full production pass. Diagnostic registration, ORB features, similarity search,
and local minima have no selection authority. Exhausting the list fails closed.
The selected candidate frame is provisional search evidence and never counts
as either required confirmation frame.

The strategy ID/version, complete ordered candidate plans, every receipt and
candidate production evaluation, selected one-based candidate index, compass
click dwell, post-compass settle, yaw/pitch offsets, pitch endpoint, signed
detent counts, and wheel pacing interval are recorded. A complete Win32
event count proves only that Windows accepted the left-down and left-up events;
live RuneLite sampling showed that it does not prove the compass observed an
instantaneous pair. The adapter therefore waits 100 milliseconds between the
acknowledged left-down and left-up and revalidates the unchanged point before
release. The existing post-compass settle remains a distinct no-input plan
action after the click completes.

Endpoint saturation makes every candidate independent of the starting pitch or
zoom. Each wheel detent crosses the Win32 boundary as a separate event with a
fixed interval because RuneLite can coalesce a batch even when Windows
acknowledges every event in it. The adapter recomputes the mapped screen point
and rechecks its safety gates before every detent. The `single-plan` mode
likewise remains available only for bounded regression and diagnostic
reproduction. Neither open-loop mode is a canonical Issue #31 acceptance
command after the real semantic-no-op failure.

## Bounded closed-loop normalization

The existing v1 servo boundary is validation-only and feedback gated. Its
fixed sequence is:

1. capture and privately record a fresh frame;
2. evaluate fixed gameplay chrome as an input-readiness veto;
3. run the unchanged production camera evaluator;
4. stop immediately if production passes;
5. otherwise require a fully fail-closed production rejection;
6. compute world-only diagnostic guidance using all candidate and fixed-UI
   exclusions;
7. capture and privately record a dedicated pre-input arm frame;
8. rerun input readiness and the unchanged production evaluator on the arm
   frame; a readiness veto stops, a production pass succeeds without input,
   and anything other than an exact fail-closed rejection stops;
9. compare the decision and arm frames with the cheap world-only stale-guidance
   guard; any material or ambiguous structural change discards the pending
   sign and restarts from the arm frame without immediate input;
10. capture and privately record a final commit frame immediately before the
    input seam, require it to be strictly newer than the accepted arm frame,
    rerun the cheap readiness veto and the world-only arm guard, and reject any
    change or ambiguity without input;
11. independently prove that the accepted arm is less than one second old,
    recheck the deadline, and execute at most one reviewed primitive only when
    every prior gate is still valid; and
12. settle, capture, and repeat from the readiness veto.

These are deliberately separate authorities. Gameplay-chrome readiness can
only veto input. It cannot validate Varrock, contribute to world-scene
identity, or expose a resource. Diagnostic guidance carries
`can_accept=false`, can select only one bounded axis/sign, and cannot promote a
production-rejected frame. Only `evaluate_varrock_east_camera()` can end a
normalization boundary successfully.

The first guidance policy is intentionally refusal-oriented. It requires at
least five independently recovered world landmarks across all three macro
zones plus a low-residual distributed similarity fit. It authorizes only a
one-detent zoom sign when scale clearly dominates every uncalibrated transform
component. Yaw, pitch, incoherent minima, ambiguous axes, unsupported geometry,
and evidence inside the motion deadband all return
`INSUFFICIENT_GUIDANCE`. Candidate pixels, the minimap, compass, chat, and other
fixed UI are excluded from direction selection.

The arm guard is also permanently non-authoritative. It compares only the six
frozen structural landmark regions, with candidate pixels, fixed UI, and
gameplay-readiness chrome excluded. Every landmark must remain strictly inside
the frozen material-change limits; one threshold-equal, changed, uncovered, or
otherwise ambiguous region discards the pending sign. A discard never chooses
a replacement sign and never permits input from the arm frame. The next cycle
must perform the full production/readiness/guidance path and then prove a new
fresh arm seam. Decision and arm frame IDs, monotonic capture timestamps,
payload hashes, readiness/production results, guard metrics, and the retained
or discarded primitive outcome remain immutable report evidence.

In the v1 servo, the final commit observation is a separate cheap final
pre-input commit seam, not a second scene evaluator and not an acceptance
authority. It must be strictly newer than the arm observation and must retain
both gameplay readiness and the same world-only structural guard. The
arm-to-input clock has its own frozen maximum of one second: an age equal to or
greater than that maximum is expired. Invalid, non-finite, or regressing
origin/final clock samples, capture or recording failure, a non-fresh commit
frame, readiness loss, or a guard reject all stop before physical input. The
no-camera-input platform preflight is completed before the final arm-age sample. The
accepted arm and commit frame IDs, capture timestamps, payload
hashes, age evidence, and commit result remain distinct report evidence.

### Session-bound multi-axis guidance V2

`issue31-world-only-multi-axis-guidance@2.0.0` is the smallest refusal-oriented
extension justified by the Track A result. It accepts an exact captured frame,
internally recomputes the frozen profile-bound v1 world evidence, and binds its
decision to the frame ID, monotonic capture timestamp, and payload SHA-256. A
caller cannot substitute handcrafted v1 evidence, an incomplete exclusion
set, or a bare heading-normalized flag. V2 preserves v1's exact reviewed zoom
sign when v1 is actionable; it does not widen the zoom acceptance envelope.

When v1 has no distributed fit because landmarks are insufficient, one
`CameraGuidanceV2Session` may reserve exactly one deterministic compass-north
bootstrap. A second selection in that session cannot reserve another north
action. Heading becomes normalized only after the session records the exact
complete one-action compass receipt bound to the reserved decision and frame.
The development-only north-bootstrap runner still performs a fresh arm and
final commit observation, including unchanged production evaluation at those
captured seams. Its no-camera-input Windows preflight completes before that
final commit capture. The commit's readiness and initial-to-arm-to-commit
world-only guards therefore observe any world change during preflight; only
the cheap platform/provenance checks, an all-controlled-input recheck, and the
final arm-age sample remain between that commit and the input request. It runs
at most that one primitive, captures a fresh post-action frame, and treats only
the unchanged production evaluator as success.

If a coherent world-only fit instead identifies yaw or pitch as dominant, V2
returns `CALIBRATION_REQUIRED`. It may describe one explicit signed
development-only probe consisting of a four-logical-pixel horizontal or
vertical middle drag. That pulse is calibration evidence only: V2 does not
infer or authorize a yaw/pitch correction from it, and a probe receipt or
improved diagnostic score cannot count as scene acceptance. Ambiguous axes,
incoherent evidence, an axis inside the deadband, or a zoom case refused by v1
remain no-input outcomes.

The initial experiment budget is at most eight feedback primitives and sixteen
recorded arm attempts for one normalization boundary. The separate arm bound
also terminates a changing-frame restart loop when the injected servo clock is
frozen. Absolute defensive limits additionally cap requested events, elapsed
time, and cumulative movement. Observed effect and useful
progress are separate requirements: a pulse must change at least three frozen
world landmarks across all three macro zones, and its next coherent zoom fit
must materially reduce `abs(log(scale))`. An increased error stops after one
pulse; two semantic no-ops or no-progress steps stop the boundary. A repeated
state, direction reversal, ambiguous guidance, readiness loss, non-fail-closed
production output, safety or receipt failure, or any exhausted budget also
stops input immediately. A complete OS receipt alone never proves that RuneLite
consumed an action.

The V2 selector and north-bootstrap composition remain development/validation
only. The dedicated live command below exposes exactly the one reviewed
compass primitive, but its deterministic tests and bounded receipts do not
establish real RuneLite reacquisition. No live success is claimed here; Issue
#31 remains pending the repeated real-client trial protocol and same-head drift
proof below.

Within the actionable gate, v1 requires monotonic zoom-error reduction; a fit
that crosses any landmark/zone/coherence/axis-isolation boundary becomes
`INSUFFICIENT_GUIDANCE` immediately. Continuous residual or axis-margin
degradation inside that gate is recorded but is not yet a separately calibrated
controller score. Likewise, synthetic raster tests prove selector sign math,
not real RuneLite wheel consumption. The dedicated arm capture now closes the
readiness/pixel-freshness seam after the relatively expensive wide search; it
does not prove real RuneLite wheel consumption. An isolated one-detent real
sign/effect calibration and a real actionable unsupported view therefore
remain explicit lead-review gates before any live closed-loop command can be
enabled.

The read-only proof command automatically includes every reviewed supported
fixture, requires the complete 36-frame drift set, and binds its canonical
report to the exact clean Git head and frozen readiness/guidance/servo policy:

```powershell
python tools/analyze_issue31_servo_offline.py --fixture-manifest tests/fixtures/perception/varrock-east-iron-v1/manifest.json --fixture-root tests/fixtures/perception/varrock-east-iron-v1 --frames drift=diagnostics/issue18-drift-v3 --expect drift=fail-closed --expect-readiness drift=ready --require-count drift=36 --report diagnostics/issue31-servo-offline.json
```

Repeat `--frames LABEL=FILE_OR_DIRECTORY`, `--expect`,
`--expect-readiness`, and `--require-count` together for each private evidence
set. Omitting any expectation/count makes the report observational rather than
proof-eligible. The tool never instantiates input control, copies pixels, or
embeds discovered absolute frame paths; it writes only hashes/scalars to
exclusive JSON and `.sha256` outputs. Exact command arguments are retained
verbatim for provenance, so a report remains private when its invocation used
absolute private paths. A dirty or changing nonignored worktree, an exact count
mismatch, a readiness mismatch, a production mismatch, or an authority
invariant failure makes the proof ineligible and returns nonzero.

Use `--dry-run` to inspect the retained legacy plans without opening capture or
sending input. This is regression evidence only:

```powershell
python tools/validate_varrock_east_camera.py --normalization-strategy varrock-east-production-gated-search-v1 --dry-run
```

The dry run prints all eleven full candidates, the three perturbations, and
the worst-case bounded plan/input/frame counts while sending no input.

The canonical development-only V2 north-bootstrap command is:

```powershell
python tools/validate_varrock_east_camera.py north-bootstrap-v2 --case-prefix issue31-north-YYYYMMDD-HHMMSS
```

Replace the timestamp placeholder with a permanently unique case prefix. The
subcommand accepts only that required prefix and an optional private ignored
`--output` root. It fixes the RuneLite window title policy, detector/profile,
V2 selector, compass point, and settle interval; it exposes no dirty-tree,
dry-run, title, coordinate, or control override. It requires the same clean
exact Git head before the lease, inside the lease, and after cleanup, and holds
the global input lease through exclusive canonical JSON/SHA publication.

Exit `0` means only that the unchanged production evaluator passed. A complete
`BOOTSTRAP_EXECUTED` receipt is canonical non-pass evidence and exits `1`;
setup, provenance, lease, cleanup, clock, or publication errors exit `2`.
Private initial/arm/commit/post frames, full V2/guard/readiness/production
evidence, input-request-to-receipt timing, and the receipt-backed pointer
policy remain under ignored `diagnostics/`. The report explicitly does not
claim a captured numeric logical-to-physical pointer mapping.

### Fixed A/B/A camera system identification

After the corrected north-bootstrap experiment left the real client at only
three independently recoverable landmarks and no coherent shared transform,
Issue #31 uses one bounded system-identification command before accepting or
retiring the landmark-only controller:

```powershell
python tools/validate_varrock_east_camera.py fixed-aba-probe-v2 --case-prefix issue31-system-id-YYYYMMDD-HHMMSS
```

The isolated subcommand accepts only a permanently unique case prefix and an
optional private ignored `--output` root. Axis, sign, movement, coordinates,
settle timing, title selection, dirty-tree operation, and production policy are
not command-line inputs. The reviewed strategy is frozen as:

1. capture two fresh no-input horizontal baselines one second apart;
2. execute one `+4` logical-pixel horizontal middle drag from `(200,600)`
   through the full decision/arm/preflight/commit/input/post seam;
3. execute the exact `-4` return only after a second independent guarded seam;
4. compare strict existing wide-search matches across both baselines plus each
   action's exact arm, commit, and post frame;
5. run the identical `+4/-4` vertical A/B/A only if horizontal evidence
   qualifies; and
6. stop all live probing when horizontal evidence does not qualify.

Each pulse is its own one-action `CameraPlan`; positive and return movement are
never combined open-loop. The machine-global input lease spans every frame,
action, cleanup attempt, provenance recheck, and canonical report/digest write.
The no-camera-input Windows preflight completes before each final commit frame.
Every action therefore receives a distinct fresh decision, arm, and commit
chain, exact geometry/focus/pointer/button checks, the exclusive arm-age gate,
and immediate post-action observation.

Every frozen landmark remains visible in the report, but only IDs that retain
their existing strict descriptor match in all eight observations participate.
A minimum at the edge of the existing wide-search radius is excluded as
truncated evidence. The forward effect is measured from the positive action's
exact commit to its post frame; the reverse effect is measured from the return
action's exact commit to its post frame. This prevents no-input movement before
commit from being credited to physical input.

Natural offset and descriptor jitter use every pair inside the no-input
same-pose chains: baseline-one/baseline-two/positive-arm/positive-commit and
positive-post/return-arm/return-commit. Both response vectors and both
tested-axis components must be strictly above those measured jitter bounds.
Descriptor threshold margin must also remain strictly above measured
descriptor-distance jitter, the reverse vector and tested-axis sign must oppose
the forward response, and the returned `(x,y)` offset must lie inside the full
pre-positive baseline envelope. The forward sign must be coherent across at
least three same IDs spanning exactly the profile's three frozen zones. A
production PASS is safety-valid and does not truncate the required return
pulse. No descriptor threshold, search radius, production threshold, quorum,
or macro-zone requirement is relaxed by this comparison.

The final conclusive report says exactly `control derivative usable` only when
both mandatory axes qualify, or `landmark controller retired` when a completed
axis does not. A safety, freshness, capture, provenance, or receipt failure is
reported as inconclusive rather than mislabeled as control-model evidence.
Exit `0` means one of the two system-identification conclusions was reached;
exit `1` means the bounded attempt was inconclusive; exit `2` is a setup,
lease, cleanup, provenance, or report-publication failure.

This remains calibration evidence only. The unchanged production camera
evaluation is still the sole scene-acceptance authority. Wide minima, a clean
control derivative, or a completed Windows receipt cannot validate the scene,
expose a resource, or satisfy the Issue #31 positive-side acceptance gate.

### Robust world registration R1 and the read-only view graph

The fixed horizontal A/B/A result retired the six-landmark controller for this
camera envelope. Its replacement starts as an **offline, read-only diagnostic**:
Robust Registration R1 analyzes exact saved frames and builds an evidence-backed
view graph. R1 never constructs a camera-input adapter, sends input, validates a
scene, exposes a resource, or changes a production observation. A graph path is
therefore a candidate for a future reviewed controller, not camera-reacquisition
acceptance.

Install the dedicated optional validation dependencies before running R1:

```powershell
python -m pip install -e ".[dev,vision-dev]"
```

`vision-dev` currently freezes NumPy `2.5.2` and headless OpenCV `5.0.0.93`.
They are not normal project dependencies and are not imported by the production
package surface. R1 records the resolved versions and deterministic OpenCV
settings in its report; Linux CI and native Windows development both exercise
the same optional group.

The canonical corpus command is:

```powershell
.venv\Scripts\python.exe tools\analyze_issue31_robust_registration.py --expected-head <40-char-sha> --report diagnostics\issue31-robust-registration-r1\issue31-robust-registration-r1.json
```

The tool loads only the frozen canonical corpus: reviewed supported fixtures,
all 36 stored real drift frames, the saved A/B/A observations and their
receipt-proven transitions, explicitly labeled risky/disconnected views, and
the current unsupported pose. It binds the run to the requested clean exact Git
head. It writes deterministic JSON plus an adjacent `.sha256` sidecar beneath
ignored `diagnostics/`; raw private pixels remain outside the report and Git.

Every feature and correspondence must lie in trusted world pixels. The mask
excludes every resource/candidate region, fixed RuneLite UI and readiness
chrome, and every sanitized or otherwise non-world area. Descriptor support
that crosses an exclusion is rejected as well. Matching is bidirectional and
mutual, with deterministic ordering and outlier rejection. Registration reports
the source/target feature counts, forward/reverse match counts, mutual matches,
inliers, inlier ratio, per-zone inlier counts and spatial coverage, median and
p90 reprojection residual, forward/reverse cycle error, overlap, conditioning,
scale/distortion, and rejection reason.

R1 evaluates translation, similarity, affine, and homography in increasing
complexity and selects the lowest-complexity model that meets the frozen
evidence policy. A homography is not presumed. If no single model explains the
distributed world evidence without excessive residual, distortion, poor
conditioning, or inadequate coverage, the edge is rejected as a global-model
failure consistent with parallax/non-planarity.

The view graph does not accept a pairwise fit by itself. An edge must have
robust inliers distributed across all three existing macro zones and must also
participate in a consistent graph cycle. Explicitly disconnected endpoints are
used to count false edges. Saved input receipts label only the exact directed
transitions they actually produced; visual similarity never invents an action
or its inverse. The report therefore keeps these separate:

- a cycle-verified visual path, which describes saved-view connectivity; and
- a receipt-backed directed action path, which is the only basis for the exact
  conclusion `offline controller path available`.

When no receipt-backed path reaches an exact reviewed production-supported
anchor, the report instead emits
`missing graph link: <exact missing relationship/sample needed>`. That result
identifies one smallest additional evidence edge; it does not authorize a live
probe. Any future live use of R1 requires a separate architecture review and
must still end in a fresh pass of the unchanged production detector.

## Repeated trial protocol

A complete run applies three distinct deliberate perturbations. The current
protocol exercises yaw, the opposite pitch endpoint, and combined yaw/zoom.
Before the first baseline capture, it runs and records the candidate strategy;
the run therefore does not depend on Tyler manually preparing the starting
camera. For each trial it:

1. captures the starting view;
2. applies one perturbation and captures the perturbed view;
3. requires that the perturbed unsupported view fail closed;
4. replays the same ordered independent candidate strategy; and
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

If a fresh before-frame is not a production pass, the session preserves that
frame and stops before deliberate perturbation. Any focus, overlay, pointer,
receipt, settle, capture, recording, or production-evaluation exception aborts
immediately; only release cleanup is allowed after the error.

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
landmark distances and thresholds, input receipts, every candidate attempt,
selection identity, any exact logical drag path and pacing configuration, and
trial verdicts. Candidate frames are explicitly marked as non-confirmations.
It does not embed raw pixel payloads. Its version-2 JSON
is serialized deterministically with sorted keys, indentation, and one trailing newline. The
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

The complete case-prefix namespace is exclusive. Existing evidence is never
overwritten, and the durable reservation remains after both successful and
failed attempts. Always use a new case prefix for a new run instead of deleting
or reusing prior evidence.

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
