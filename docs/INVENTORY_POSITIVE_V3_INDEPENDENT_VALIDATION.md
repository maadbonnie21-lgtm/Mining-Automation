# Inventory Positive Classifier V3 — Independent Validation Readiness

## Status and authority boundary

This document defines the future independent-validation path for the frozen
Inventory Positive Classifier V3 development candidate. It is a readiness and
evaluation protocol, not authorization to run the RuneLite campaign and not a
production activation decision.

The following remain unconditionally false, including when a future validation
report passes:

- `activation_allowed=false`;
- `promotion_allowed=false`;
- live campaign execution is not authorized by this work;
- mining authority, banking authority, and click authority are false; and
- no target IDs or interaction regions are exposed.

The readiness evaluator is deliberately absent from runtime inventory exports,
`Observation`, `InventoryState`, `WorldState`, `ActionIntent`, the application,
and controller wiring. No RuneLite, account, inventory, bank, item, tab, camera,
or input state is changed by the readiness path.

## Frozen V3 candidate

Independent validation evaluates exactly this candidate and may not rebind it:

- frozen Git head: `5975532b472a74d93f010e04ca44b2efa2a3ffd7`;
- analyzer: `inventory-positive-v3-full-slot-exact-development@3.0.0`;
- candidate classifier: `inventory-full-slot-exact-rgb-v3@3.0.0`;
- candidate detector: `inventory-positive-v3-development-candidate@3.0.0`;
- configuration ID:
  `inventory-positive-v3-development-4ee2d01517447655700bd0d49637b3f4221edcc950a07c270e0717c52999e72d`;
- model-configuration SHA-256:
  `57a0ed55328e4f6994f3f099d415e223a1bebb3954cab7ef44c2938dce14b634`;
- model-artifact SHA-256:
  `0722f1922c88ec011099fe83980b44f2b77637e2aa49a58d87245ff208cdf469`;
- prototype-occurrences SHA-256:
  `3c0dce4ca58ca44839dee2d25e7d4d3d8e1182a23dd042286791424de0f2e8f8`;
- prototype-source-set SHA-256:
  `3f6c957f5805c2be7e305a7aea57e9e41f2d15d5bd2fd7195af1af1620a92aee`;
- reviewed reference-region file SHA-256:
  `c46c43ecf972c05a34b968f5f232c886cb253eb511bd916f5f36670defad1df3`;
- reviewed reference RGB SHA-256:
  `4e94092712b2c03f02e9e63512bff520571c8f3715ddd4512bf4721ae72b09d6`;
- profile: `candidate-live-inventory-348867800b28a54e`, 1005x1078
  BGRA8888, inventory region `(567, 569, 158, 248)`, column stride 42,
  and row stride 36; and
- publication floor: 0.8, unchanged.

The evaluator also pins the Git blobs for every transitive source file that
defines frame interpretation, geometry, localization, baseline classification,
V3 policy, and prototypes. It must refuse an evaluator head that is not a
descendant of the frozen head or whose pinned source blobs differ.

## Development evidence is permanently development-only

The existing 16-case corpus is permanently assigned the role
`development-self-fit-only`:

- dataset ID: `inventory-live-candidate-safety-bb0d0e3f7ff1c73b`;
- manifest SHA-256:
  `2e518ce81dd291f8b7d055afad9ddc12acbc66e0e967845f8f2e548fe1644479`;
- validation eligible: false; and
- its case, session, capture, source-frame, and sanitized-region identities are
  frozen as development identities.

No development case identity, report, artifact, or fixture path may be relabeled
or counted as independent validation. A genuinely new capture may happen to
produce a byte-identical static inventory region; the report must disclose that
fact, while durable post-preregistration session/capture provenance supports
the independence claim and the source-owned approval registry remains its
authority. V3 must not be tuned again against the 16
development cases. Independent results also may not change thresholds,
references, prototypes, policy, configuration identity, or the claimed support
envelope.

## Preregistration before pixels

The source-owned preregistration is
`validation/inventory-positive-v3/preregistration.json`, with SHA-256
`9cd485f6b095018763ea481144ea183e089d670148ed7a79d3c11126a9736518`.
It binds the candidate, evidence roles, sequence, environment fields,
all-captures selection rule, and model firewall before any independent campaign
pixel exists.

The campaign contract is fixed in advance:

- one natural-fill session;
- every completed capture owned by the campaign is retained and evaluated in
  source order, with no result-based dropping or replacement;
- an aborted or restarted attempt receives a new campaign ID and the prior
  campaign is disclosed;
- operator stage labels are acquisition notes only and are unverified;
- the finalized manifest records the operator identity;
- truth is supplied after session completion through a separate review record
  whose reviewer identity differs from the operator; and
- no post-campaign calibration, prototype learning, training, model mutation,
  candidate rebinding, or threshold adjustment is permitted.

Changing the plan after seeing pixels invalidates independence. A new plan and
new campaign would require explicit lead review; it cannot retroactively repair
the observed campaign.

## Durable independent evidence package

The independent campaign uses its own dataset root and the role
`independent-validation-only`. It must never share the development fixture
directory. A finalized package has three separately hashed evidence records:

1. `validation-package.json` binds the preregistration hash and exact paths and
   SHA-256 values of the campaign manifest and reviewer-truth record. It states
   `training_allowed=false`, `prototype_eligible=false`, and
   `activation_allowed=false`.
2. The campaign manifest binds a unique dataset ID, campaign ID, source session,
   exact frozen candidate head, complete capture order, environment provenance,
   prior attempts, and every owned case and artifact hash.
3. The reviewer-truth record is created separately, identifies an independent
   reviewer, is bound to the finalized campaign-manifest SHA-256, and supplies
   the truth used for evaluation.

Those records can prove detector conformance, but cannot self-approve their own
independence. A fourth, source-owned authority lives at
`validation/inventory-positive-v3/approved-campaigns.json`. A lead-reviewed
registry entry must bind the exact package, campaign manifest, reviewer truth,
source session, campaign, and dataset hashes or IDs. Operator, reviewer, and
approver identities must be pairwise distinct. The initially empty registry has
SHA-256
`a2bc8cb0fa829fddb9a8fe672d1fa93b835ae57e5d037bba880dfbb96f3fd4ad`.
Dataset-local approval claims have no authority.

Durable session provenance includes its session and campaign IDs, start and end
UTC timestamps, operator identity, source-session report SHA-256, capture
configuration identity, capture build SHA, RuneLite build, Windows
version/scaling/DPI, client mode, theme, renderer, window class, frame geometry,
pixel format, and profile ID. The campaign manifest records a finalization time
after session completion; review must occur after finalization.

Durable capture provenance includes its unique capture and case IDs, capture
UTC timestamp, planned stage, unverified operator label, source capture-report
path and SHA-256, sanitized inventory-region path and SHA-256, and its position
in the immutable session order. The canonical source capture report binds that
region to the exact session, capture time, and environment. All canonical JSON
documents and evidence artifacts are read as immutable byte snapshots during
evaluation and checked again before the report is accepted.

## Contamination firewall

The evaluator constructs and identity-checks the frozen analyzer before opening
independent pixels or reviewer truth. It then enforces all of the following:

- independent dataset, campaign, session, capture, and case identities cannot
  reuse frozen development identities;
- development paths and validation paths are separate;
- validation documents must explicitly say `training_allowed=false` and
  `prototype_eligible=false`;
- validation cases cannot be exported into the prototype/model source;
- validation inputs have no calibration, learning, mutation, or write-back API;
- the candidate configuration, model hashes, prototype hashes, reference hash,
  and pinned source blobs are checked before evaluation;
- candidate identity is captured before and after evaluation and must be
  byte-for-byte equivalent;
- source package snapshots must remain unchanged throughout evaluation; and
- any validation region byte-identical to development evidence is disclosed in
  the per-case report rather than silently presented as novel evidence; region
  equality alone neither establishes nor disproves capture independence.

A validation failure remains a regression/evidence result. It never becomes an
automatic training example. Any later research response must occur in a
separate reviewed development cycle and cannot alter the frozen V3 campaign
result.

## Required future campaign sequence

The future passive natural-fill sequence is fixed as:

1. `empty` — reviewer truth requires exactly 0 occupied slots;
2. `early-partial` — more than 0 occupied slots;
3. `mid-partial` — strictly more than early partial;
4. `near-full` — strictly more than mid partial and fewer than 28; and
5. `full` — exactly 28 occupied slots.

All five positive checkpoints must be clean, ordinary-iron inventory views with
the inventory visible and no hover, drag, selected-item, quantity-text, or other
unsupported presentation recorded by the reviewer.

Required negative evidence follows the positive sequence:

- `wrong-tab` must independently review as wrong-tab visible with no count and
  must evaluate to UNKNOWN; and
- `row-obstruction` must independently review as inventory obstructed with no
  count and must evaluate to UNKNOWN.

Optional separately identified unsupported evidence may cover hover, drag,
selected-item, quantity text, gapped inventory, unexpected foreign items, or
another unexpected presentation. Unsupported or ambiguous cases must remain
UNKNOWN with zero confidence and no action authority.

## Reviewer truth is not an operator label

The operator may name the intended capture stage so the passive acquisition
sequence can proceed, but that label is never expected truth. After the campaign
manifest is finalized, an independent human reviewer records, per case:

- approval or rejection;
- exact occupied-slot count when the inventory is clean and visible;
- visibility class;
- whether the inventory contains only ordinary iron;
- hover, drag, selected-item, and quantity-text flags;
- the exact reviewed region SHA-256; and
- a review note where needed.

The reviewer record must not be generated from operator labels, model outputs,
or expected counts, and the reviewer identity must differ from the operator.
The evaluation tool derives its expected result only from the separately
hash-bound reviewer truth. Rejected, incomplete, contradictory, or unsupported
truth cannot produce detector conformance. Conformance alone cannot produce an
independent-validation pass without the source-owned approval-registry entry.

## Deterministic readiness and evaluation artifacts

The thin CLI is `tools/inventory_v3_independent_validation.py`. Both commands
require an exact clean evaluator head. Output directories must be new. Reports
are canonical, sorted JSON with LF line endings and adjacent `.sha256`
sidecars.

Exclusive output-directory creation is the transaction reservation shared by
normal tool invocations. Exactly one invocation can own a requested output;
losers stop before opening an artifact, and rollback only considers paths
recorded after that winner's exclusive create. Handles and full-write/readback
fingerprints remain live through the final exact-head check. Replacement by an
arbitrary process that ignores this reservation is treated as external
filesystem tampering, not as a second valid tool invocation; detected
replacement bytes are preserved and the run fails closed.

Prepare the offline readiness bundle and templates with:

```powershell
python tools/inventory_v3_independent_validation.py prepare --output diagnostics/inventory-positive-v3-independent-readiness/<exact-head> --expected-head <exact-clean-evaluator-head>
```

This writes the deterministic preregistration copy, campaign-manifest template,
source-session and capture templates, reviewer-truth template, approval-entry
template, validation-package template, readiness report, and a SHA-256 sidecar
for each. The approval template is a review aid only; a dataset-local copy
cannot replace a committed source-owned registry entry. Readiness reports zero live cases,
`campaign_execution_authorized=false`, `activation_allowed=false`, and deny-only
action authority. Running `prepare` does not capture anything and does not
authorize the future campaign.

Only after a separately authorized passive campaign has been completed,
finalized, and independently reviewed, evaluate its immutable package with:

```powershell
python tools/inventory_v3_independent_validation.py evaluate --dataset <finalized-independent-package-directory> --output diagnostics/inventory-positive-v3-independent-results/<exact-head> --expected-head <exact-clean-evaluator-head>
```

Evaluation writes
`inventory-positive-v3-independent-validation-report.json` and its SHA-256
sidecar. The report binds the exact evaluator head, preregistration, frozen model,
package/session/capture hashes, reviewer truth, per-case expected and actual
results, contamination checks, runtime analyzer-state hashes, approval-registry
hash, and candidate identity before and after the run. A package can report
`detector_conformance_passed=true` while remaining
`validation_status=approval-required` and `validation_passed=false`. Only an
exact source-owned registry match can produce an independent-validation PASS.
Even that PASS reports `activation_allowed=false` and
`promotion_allowed=false`.

## Controller-readiness contract

This readiness work is deny-only and has no runtime wiring:

- inventory UNKNOWN grants no mining or banking authority;
- an unvalidated or non-promoted V3 result grants no mining or banking
  authority;
- stale, mixed-frame, wrong-resource, unsupported-scene, or otherwise unproven
  resource evidence grants no click authority;
- all denied decisions expose an empty target set; and
- development results and validation reports are not production perception
  snapshots.

Only a future, independently validated, explicitly approved production
perception snapshot may eventually be considered by a separately reviewed
action-authority boundary. This protocol neither creates that snapshot nor
wires it to the controller.

## Current conclusion

The repository is prepared to generate deterministic offline readiness evidence
and, after separate authorization, evaluate a durable independent campaign
without changing V3. The live natural-mining campaign is not authorized by this
work and must not be run now. V3 remains frozen, nonactivating, and marked
`independent-campaign-required`.
