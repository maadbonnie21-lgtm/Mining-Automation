# Inventory review and replay gate

`tools/review_inventory_session.py` is a development-only gate between private
real-client capture sessions and any proposed live inventory profile. It creates
a deterministic, reduced review package, records independent reviewer truth,
derives a non-activating candidate from reviewed evidence, and replays the
unchanged production inventory detector against the owned source frames.

The gate does not approve or activate a profile. Its candidate always contains
`activation_allowed=false`, even when every current check passes. A separate
lead-owned release decision and reviewed detector factory are still required.

The first captured iron examples currently establish useful safety and geometry
evidence, but they do **not** establish a production-calibrated iron detector.
Unknown results for low-confidence real sprites remain release blockers; they
must not be converted into known counts by weakening thresholds or confidence
policy.

## Authority boundaries

The workflow keeps three kinds of information separate:

1. The operator-selected capture label is acquisition metadata only. It remains
   marked `operator-selected-unverified` and is never copied into a reviewer
   truth field.
2. A human reviewer establishes what is actually visible. The review record is
   attributed with a reviewer name and UTC review time and is content-bound to
   the package manifest and exact SHA-256 of every reviewed panel artifact. It
   also records explicit occupied-slot truth, evidence visibility, validation
   split, and adversarial visual flags. This is an attributed, hash-bound
   review record, not a cryptographic signature or identity attestation.
3. The existing production `InventoryDetector` produces the replay result. The
   gate has no policy, threshold, geometry, confidence, or activation override.

A capture label, a successful capture, a derived grid, or agreement on one case
cannot substitute for independent visual truth or production detector output.

## Prepare a privacy-reduced review package

Start from one or more completed guided inventory validation sessions. Use a
clean worktree at the exact 40-character commit being evaluated (ignored
private diagnostics may remain):

```powershell
python tools/review_inventory_session.py prepare `
  --session diagnostics/inventory-validation-sessions/<SESSION> `
  --output diagnostics/inventory-review/<PACKAGE> `
  --expected-head <EXACT_40_CHARACTER_HEAD>
```

Repeat `--session` to combine independently captured batches. All source frames
must have one common geometry and use BGRA8888 pixels.

Preparation is label-blind. It finds one conservative bottom-right review crop
from active pixels without consulting any case label, then writes a 288 by 360
panel-only BGRA payload and BMP for each case. The package does not contain full
frames, window titles, or free-form capture notes. It binds every artifact to
its source capture/report/session hashes and writes:

- `review-package.json` and `review-package.sha256`;
- `panel-artifacts/*.panel.bgra`;
- `panel-artifacts/*.panel.bmp`; and
- `review-record.template.json`, whose truth fields are all blank.

The reduced artifacts are not trusted merely because they already exist in the
package directory. Package loading and evaluation re-derive each panel BGRA and
BMP from the durable owned frame, then require exact byte equality. The loader
also enforces contiguous package order, unique session/capture identities,
complete case coverage, declared common geometry/pixel format, exact referenced
paths and hashes, and the source-session metadata binding. A missing,
reordered, substituted, or stale referenced artifact fails before detector
evaluation.

Capture-environment values in the package have status
`operator-reported-bound`. Their bytes and source-session association are
protected by the package binding, but the values are not independently
measured or reviewer-approved merely because they are present. A reviewer or
lead must separately approve them before production activation.

The original full frames remain private source evidence. Do not commit or share
them. Review the reduced package itself before publication as well; inventory
art, menus, and overlays are intentional visual evidence, but unrelated private
content is not.

## Record independent reviewer truth

Copy the blank template to a separate review record and fill it only after
visually inspecting every panel artifact. The record must cover every package
case and retain the package manifest hash and each panel's raw SHA-256.

For each case the reviewer explicitly records:

- `decision`: `approved` or `rejected`;
- `validation_split`: `reference`, `calibration`, `held-out`, `negative`, or
  `adversarial`;
- `visibility`: `inventory-visible`, `wrong-tab-visible`, or
  `inventory-obstructed`;
- the exact `occupied_slots` count for a visible inventory, or `null` for a
  wrong tab or obstruction;
- whether item hover, selected-item, genuine drag, or quantity-text evidence is visible;
- whether the operator's intended state is visually confirmed;
- item-art tags used to prove evidence diversity; and
- whether the case is the one reviewed full-inventory geometry source.

An approved reference must be a clear visible empty inventory. Exactly one
approved 28-slot frame must be marked as the geometry source. Rejected evidence
requires a reason and cannot silently contribute to candidate derivation.

Do not mark selected/use-item mode as a drag unless the reviewed pixels prove a
held or dragged item. Truth describes the pixels, not the operator's intention.

## Derive and evaluate a candidate

Run the gate with the same source sessions, immutable package, completed review
record, and exact clean head:

```powershell
python tools/review_inventory_session.py evaluate `
  --session diagnostics/inventory-validation-sessions/<SESSION> `
  --package diagnostics/inventory-review/<PACKAGE> `
  --review diagnostics/inventory-review/<REVIEW>.json `
  --output diagnostics/inventory-review-results/<RUN> `
  --fixture-output diagnostics/inventory-sanitized-fixtures/<RUN> `
  --expected-head <EXACT_40_CHARACTER_HEAD>
```

Repeat `--session` exactly as in package preparation. Output directories are
created exclusively; the tool does not overwrite an earlier run.

Candidate geometry is derived only from the approved empty reference and the
approved 28-slot geometry frame. The whole-frame RGB difference must yield
exactly one 4-by-7 lattice with the authoritative 32-by-32 slot ownership and a
non-empty row gutter. No operator label supplies an anchor or stride. Ambiguous,
missing, out-of-frame, or review-crop-escaping geometry fails the gate.

The gate constructs the candidate with `inventory_detector_from_profile` and
the normal production defaults. It runs that detector twice per case:

1. on the exact owned full frame; and
2. on a full-size reconstruction whose pixels outside the candidate inventory
   region are zero.

The two observations must be exactly equal. This proves that publishing the
region-only replay payload does not alter detector behavior; it does not prove
that the detector's visual answer is correct.

## Pass/fail rules

An approved normal visible inventory requires the exact reviewer-counted value,
the corresponding known label, positive confidence, and no failure reason.

Wrong-tab and obstruction cases must fail closed with:

- label `unknown`;
- `occupied_slots=null`;
- confidence exactly `0.0`; and
- a non-empty diagnostic reason.

Item-hover, selected-item, genuine drag, and quantity-text cases may either
produce the exact reviewed count or the same fail-closed unknown result. They
may never produce an incorrect known count.

The report remains blocked if any case disagrees or if required release evidence
is missing. The gate explicitly reports gaps for:

- missing exact capture-build SHA, RuneLite build, Windows scaling, matching
  reported DPI, client mode, theme, renderer, capture-configuration identity,
  or a stable window class;
- no reviewed held-out empty frame, or held-out detector-owned empty pixels
  byte-identical to the reference;
- no ordinary reviewed held-out partial or full frame;
- no reviewed wrong-tab negative or quantity-text adversarial frame;
- fewer than two distinct reviewed obstruction examples;
- no reviewer-confirmed item-hover evidence;
- no reviewer-confirmed held/drag evidence;
- no reviewed wide-sprite evidence;
- fewer than three byte-distinct varied-art positive evidence sets.

An operator-intent mismatch remains visible in the review record, but it is not
itself release truth and does not invalidate an otherwise useful case. For
example, a capture staged as `hover-drag` can become valid selected-item
evidence when the pixels prove selection but not dragging; the separate missing
drag-evidence requirement remains blocked.

These gaps require better evidence or an independently reviewed classifier
calibration. They are not permission to lower the obstruction guard, slot
ownership rule, score thresholds, publication confidence, or unknown-state
policy.

### Current real-corpus status

The current captured corpus has not closed this release gate. In particular:

- the reviewed real partial and full iron inventories remain `unknown` under
  the unchanged production detector, rather than the exact known counts;
- the first capture staged as `hover-drag` proves selected/use-item
  presentation, while the later passive batch separately proves a genuine
  hover-only presentation; neither visibly proves a genuinely held or dragged
  item;
- the independently captured clean empty detector-owned region is
  byte-identical to the reference region, so capture independence is recorded
  but empty-region pixel variation is not yet demonstrated; and
- the earlier capture batch omitted required Windows scaling, client mode,
  RuneLite theme, renderer, and capture-configuration provenance. Later
  operator-reported values cannot be retroactively attached to that immutable
  batch.

Those are evidence/calibration blockers, not detector-policy exceptions. The
partial/full failures must remain permanent replay cases, and the profile must
remain non-activating until independently reviewed evidence closes the gaps.

The command returns `0` only when case agreement and every listed release gap
are clear, `1` when the evaluated gate remains blocked, and `2` for setup,
integrity, provenance, or execution errors.

## Outputs and provenance

Evaluation writes:

- `candidate-profile.json`, always non-activating;
- `review-replay-report.json` and its SHA-256 sidecar;
- detector-owned region payloads under `sanitized-replay/`; and
- optionally, a separate sanitized fixture dataset under `--fixture-output`.

The canonical report records the exact Git head, detector ID/version and
configuration ID, package and review hashes, source payload identity, reviewer
truth, current detector result, agreement rule, and remaining release gaps.
Every path and hash is verified on load. A changed package, review record,
source session, report, or artifact is rejected rather than silently replayed.

The hashes prove content integrity and binding; they do not authenticate the
human reviewer or convert operator-reported environment metadata into reviewed
facts.

The sanitized fixture manifest reconstructs the original full-frame geometry
with zero pixels outside the detector-owned region and retains both reviewer
truth and the current safety expectation. Schema v2 records the exact generator
head and derives a dataset identity from the candidate profile and immutable
case evidence. The replay reader preserves schema-v1 safety-corpus compatibility
while requiring the stronger provenance for newly generated v2 fixtures. It is
a permanent regression input, not a production activation artifact.

## Turning real failures into permanent regressions

For every reviewed real failure:

1. prove the failure on the exact owned frame with the unchanged detector;
2. retain only the verified detector-owned region when exact/sanitized outputs
   are equal;
3. record reviewer truth and immutable source/report/session hashes;
4. add the sanitized case to the permanent inventory replay corpus;
5. reproduce the failure before changing production code or calibration;
6. make the smallest general correction without weakening fail-closed policy;
7. rerun every existing synthetic and real inventory replay; and
8. require a separately held-out reviewed set before release approval.

If a frame cannot be sanitized without changing its observation, keep it
private and report the evidence limitation. Do not replace an objective replay
with an operator label or a synthetic approximation merely to make the gate
green.

## Release boundary

A review/replay report is necessary evidence, not sufficient release approval.
Production activation still requires an explicitly reviewed immutable profile,
reference, policy/configuration identity, held-out real-client validation,
complete provenance, permanent regression coverage, and lead approval. Until
that boundary closes, callers must continue to treat the live profile as
unsupported and preserve unknown/fail-closed behavior.
