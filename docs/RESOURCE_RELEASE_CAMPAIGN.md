# Passive Resource Release Campaign

This development-only workflow collects the remaining fresh real evidence in
the constrained-v1 release audit. It does not authorize game input, recover an
arbitrary camera, or change production perception. The production detector is
the fixed `profiled-resource:varrock-east-iron-v1@2.1.0` assembly. An
unsupported or uncertain view remains fail-closed and exposes no campaign
target authority.

> **LIVE RESOURCE CAMPAIGN NOT YET AUTHORIZED.** The source-owned gate is false
> in this branch. `capture-next` exits before constructing or opening the
> Windows capture backend. Do not run the real campaign until Tyler explicitly
> authorizes a separately reviewed enable-only change. Do not create the real
> session on this readiness head: sessions bind the exact head and cannot be
> carried across that future gate flip. Sessions created here are test/status
> evidence only.

## Frozen observation plan

The session contains 15 observations in one immutable order:

1. fresh exact-supported-view startup positive;
2. north-west available, depleted, and respawn;
3. center available, depleted, and respawn;
4. north-east available, depleted, and respawn;
5. genuine obstruction over one reviewed profiled sample or world landmark;
6. unsupported-location negative;
7. neighboring-copper negative;
8. neighboring-tin negative; and
9. terrain-clutter negative.

Each available/depleted/respawn state is a separate single observation, which
is why the nine release blockers expand to 15 captured cases. The operator may
manually stage only the next prompt. The prompt is stored permanently as an
unverified operator assertion; it is never copied into reviewer truth.

There is no case selector, title selector, detector selector, profile selector,
threshold input, retry option, or camera action in the capture command. One
invocation takes one frame and runs the unchanged production evaluator once.
An unsupported result is recorded as permanent evidence and advances the fixed
plan; the tool never retries in search of a supported image.

## Private session ownership and resume

Only after authorization, start from the clean exact enable-only Git head:

```powershell
python tools/resource_release_campaign.py start --operator-id <operator-id>
```

The command exclusively creates
`diagnostics/resource-release-campaigns/<unique-session-id>/`. That entire root
is ignored because it contains full private frames. `session.json` binds the
Git head and branch, campaign/configuration identity, packaged profile SHA-256,
detector/profile/schema/location, capture policy, host, operator, fixed plan,
and a unique session token. Every artifact has an adjacent SHA-256 sidecar.

Resume or inspect without changing evidence:

```powershell
python tools/resource_release_campaign.py status --session <private-session>
```

Status verifies the session and every completed prefix case before reporting
the one next prompt. Foreign, out-of-order, duplicated, partial, replaced, or
tampered evidence blocks the session. Existing artifacts are never overwritten.
The same clean head and branch are required for capture, sealing, review, and
release evaluation. A readiness-head session is intentionally unusable after
the live gate changes; create the owned real session only on the authorized
exact head.

After a future enable-only authorization, the capture spelling will be:

```powershell
python tools/resource_release_campaign.py capture-next --session <private-session> --confirm-staged-case <exact-next-case-id-from-status>
```

The confirmation is an acknowledgment of the one case already fixed by the
session, not a selector. It is checked again immediately before capture so a
stale or concurrent invocation cannot advance into a different case. The only
resumable acquisition failure is an explicit no-frame result: a later separate
invocation may acknowledge and attempt that same case again, while every
failed attempt remains hashed in the ledger. There is never an in-call retry.
Any failure after pixels are owned is terminal for that session.

The record retains exact frame geometry and pixel format, wall and monotonic
times, raw and report hashes, capture backend/build configuration, observable
RuneLite/window/DPI provenance, all detector observations/evidence, scene
landmark diagnostics, resource states, interaction regions, definitive and
actionable target IDs, stop reason, zero automatic retries, and zero input
events. Raw frames and private previews stay under the ignored private root.

After all 15 observations:

```powershell
python tools/resource_release_campaign.py seal --session <private-session>
```

The completion seal is an immutable ordered snapshot of all capture-report and
raw hashes. A detector failure is evidence and does not disappear from the
seal.

## Independent truth and privacy review

Review begins only after the full private campaign is sealed. First create the
deterministic privacy-safe artifacts for exactly one case:

```powershell
python tools/resource_release_campaign.py prepare-review --session <private-session> --case-id <case-id>
```

This masks every fixed RuneLite UI/privacy region with opaque pixels, creates
the gzip replay frame and BMP preview (or withholds pixels and emits metadata
only when geometry is unsupported), reruns the unchanged detector, and writes
an immutable preparation manifest. The reviewer must inspect those exact
artifacts before expressing truth. Then create the decision template:

```powershell
python tools/resource_release_campaign.py review-template --session <private-session> --case-id <case-id> --output <decision.json>
```

The template deliberately contains no operator-selected meaning or state. It
does contain the SHA-256 of the already-prepared artifact manifest, preventing
truth from being rebound to different pixels. A reviewer other than the
operator must independently provide:

- the actual case meaning;
- the exact ordered state of all four profiled iron resources;
- an explicit obstruction target for an obstruction case;
- a frame-local reviewed subject region for copper, tin, or terrain clutter;
- review time and notes; and
- explicit privacy confirmation.

When unsupported geometry forces pixels to be withheld, the reviewer cannot
infer the requested case from metadata. The only valid disposition is
`unreviewable-pixels-withheld` with all four resource truths explicitly
`UNCERTAIN` and no focal node, obstruction target, or negative subject region.
That case remains open and the public package exposes a canonical deny-only
authority summary.

Record that decision with:

```powershell
python tools/resource_release_campaign.py review --session <private-session> --decision <decision.json>
```

Recording never creates or changes review pixels. It only binds the independent
decision to the exact preparation, session seal, capture report, private raw
hash, and source-owned capture origin. Review cannot activate perception. A
case that fails replay or disagrees with truth remains a permanent regression
candidate; it is never a reason to lower detector, obstruction, 5-of-6, or
three-zone policy.

Neighboring copper, tin, and terrain clutter may appear either inside or
outside a supported scene. Their reviewed subject region must not overlap any
published iron interaction region. If the surrounding scene is unsupported,
all profiled resources must additionally remain uncertain with zero targets.
The unsupported-location case always requires that complete fail-closed result.

## Release decision and review package

The exact post-campaign decision is recomputed from owned raw bytes rather than
trusting stored labels or reports:

```powershell
python tools/resource_release_campaign.py release --session <private-session> --output <release-summary.json>
```

The report lists each case, every C1 empirical blocker, and an aggregate flat
C2 blocker as `CLOSED` or `STILL_OPEN`. Its separate category matrix is the
authoritative per-gate C1/C2 view.
Missing truth, wrong meaning, state mismatch, replay mismatch, changed
sanitization, false target, or tampering keeps the corresponding blocker open.
Even when every C1 blocker closes, the aggregate C2 category, final-envelope
review, and source-owned release-record gates remain open; this harness cannot
self-promote or grant input authority. The failure-promotion C2 subgate is
closed only when no retained case failed; any retained failure reopens it and
lists the exact case IDs requiring later source-owned promotion.
Acceptance of this passive boundary is an external B decision and is never
self-closed by a campaign report.

Reported DPI `96` is source-owned as the required/candidate constrained-v1
envelope value pending fresh review. Capture preserves missing or other positive
DPI values as evidence, but every affected case fails release eligibility and
keeps its C1 blocker open. A recorded `96` is necessary, never sufficient, and
cannot close C2 review or approval.

| Captured `reported_dpi` | Evidence disposition | C1 eligibility |
| --- | --- | --- |
| `96` | retained and evaluated normally | eligible only if every other case check passes |
| missing | retained with explicit missing-DPI reason | blocker remains open |
| another positive value | retained with explicit non-96 reason | blocker remains open |

After all cases have explicit privacy review, export the shareable package:

```powershell
python tools/resource_release_campaign.py export-review --session <private-session> --output <new-review-package-directory>
```

The output directory must not exist. Reviewable cases contain sanitized replay
frames and previews; wrong-geometry cases contain metadata only and remain
open. The package also contains redacted window provenance, the exact passive
capture/no-retry configuration, canonical case-review files, the release
summary, and SHA-256 sidecars. Public production diagnostics are recomputed
from the sanitized replay rather than copied from private masked pixels.
Private full frames, native handles, reviewer identity, notes, and the literal
window title are excluded. `manifest.json` is written last so an incomplete
export cannot look complete.

Verify every hash, redaction boundary, replay, fixed-order case binding, and
release ledger in one command:

```powershell
python tools/resource_release_campaign.py verify-export --package <review-package-directory> --expected-manifest-sha256 <manifest-sha256-returned-by-export>
```

The expected manifest SHA-256 must be retained outside the package at export
time. A package and its sidecars can otherwise be rewritten together; internal
hash consistency alone cannot prove that the reviewer received the originally
published immutable snapshot.

Prepare the deterministic post-campaign replay-promotion queue and final-
envelope review inputs from that same externally rooted snapshot:

```powershell
python tools/resource_release_campaign.py prepare-followup --package <review-package-directory> --expected-manifest-sha256 <independently-retained-manifest-sha256> --output <new-followup-inputs.json>
```

This command verifies and snapshots the package once, then derives the output
only from the already-validated in-memory bytes. It does not reopen mutable
package JSON after verification. The canonical output and adjacent SHA-256
sidecar bind all 15 case/reviewer/source hashes, exact C1 results, retained
failure candidates, observed DPI/window/geometry facts, and the unresolved C2
inputs. Operator staging text, reviewer identity/notes, private paths, raw
pixels, and renderer guesses are excluded.

Retain the follow-up SHA-256 returned by `prepare-followup` outside the output
artifact and its sidecar. Verify the immutable follow-up snapshot before using
it for replay promotion or envelope review:

```powershell
python tools/resource_release_campaign.py verify-followup --inputs <followup-inputs.json> --expected-sha256 <independently-retained-followup-sha256>
```

The external root is mandatory because coordinated replacement of the JSON and
its adjacent sidecar would otherwise be internally consistent. Verification
also reconstructs the fixed case, C1, failure-origin, and C2 projections and
rejects any attempt to grant approval, release, promotion, activation, or input
authority.

A source-owned replayable retained failure is labeled only
`REPLAY_CANDIDATE`; a source-owned withheld-pixel failure is
`METADATA_ONLY_NO_PIXELS`. Injected/test failures are segregated as non-release
evidence and never enter the release promotion queue. No candidate becomes a
permanent regression until a separate reviewed source change commits the
fixture/evaluator and binds its Git hashes. The follow-up artifact cannot
approve the envelope, close B or C2, create a release record, promote a
fixture, activate perception, or grant input authority. Renderer identity
remains explicitly unobserved and requires external review.

## Fail-closed invariants

- Production perception is the sole scene and resource authority.
- The 5-of-6 landmark quorum and all-three-zone rule are unchanged.
- Candidate/resource pixels and fixed UI cannot establish scene identity.
- Diagnostics are recorded but never override production.
- Unsupported/uncertain views expose zero campaign authority and require STOP.
- No automatic camera recovery, input, bank, inventory, or item manipulation
  exists in this workflow.
- Injected test evidence is permanently stamped non-release and cannot close a
  real-evidence blocker; only the source-owned Windows capture path can do so.
- Operator staging labels cannot become reviewer truth.
