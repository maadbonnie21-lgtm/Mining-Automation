# ADR 0001: Durable transactions for offline navigation synthetic evidence

- Status: Proposed
- Date: 2026-09-01

## Context

Navigation development needs synthetic, offline evidence that can be inspected and
reviewed without contacting a live game surface. An interrupted write, concurrent
producer, retry, path alias, or later mutation must not make incomplete or changed
evidence appear complete. Mine-to-bank and bank-to-mine evidence must also remain
independently attributable; one direction is not proof of the other.

This decision covers only publication of offline navigation synthetic evidence. It
does not define production capture, interaction, route execution, or a reusable
artifact transaction framework.

## Decision

### Source-owned, passive sequencing

The durable acquisition transaction owns one newly constructed passive sequencer,
including the exact campaign, direction, route plan, detector/profile, capture
build/configuration/environment, source session, and ordered evidence head. The
transaction invokes only the accepted capture-only source and guarded detector
seams. This module supplies no live source, work discovery, input, or activation
capability, and no caller-supplied finalization can redefine its bound identities.

Every transaction and both of its final manifests carry these literal,
non-configurable values:

```yaml
live: false
input: false
activation: false
```

They are safety assertions, not defaults. Missing, configurable, or non-false
values invalidate the transaction. Evidence publication never marks a route or
location as production-supported.

### Separate acquisition and review roots

Each direction transaction has two physically separate, non-overlapping transaction
roots beneath caller-selected parent directories:

```text
<acquisition-parent>/<fresh-storage-slot>/
<review-parent>/<fresh-storage-slot>/
```

The acquisition transaction root contains source-owned synthetic observations and
provenance. The review transaction root contains review results and derived review
artifacts. Review may refer to acquisition artifacts only by transaction identity
and recorded content digest; it never writes into the acquisition root.

The acquisition content identity is its source-owned `campaign_id`; the review
content identity is its separately preregistered `review_id`. Storage-slot names
are not evidence authority and cannot relabel copied bytes: every record, terminal
manifest, and caller expectation repeats or hashes the content identities.

Every ancestor and each transaction root must be non-reparse, and the two resolved
transaction roots must be disjoint. A transaction root is append-only while being
built and immutable after its final manifest is created. No file within either root
is reopened for mutation.

### Exclusive creation and burned identities

Transaction directories and every file within them are created with exclusive
create semantics. Existing content is never truncated, replaced, merged, repaired,
or silently reused.

If the requested transaction path already exists, the writer fails without
opening, deleting, replacing, relabeling, or adopting it. Once any prefix has been
created, that transaction identity is burned: neither the same invocation nor a
later invocation may resume or retry it in place. A later acquisition or review
requires an externally assigned fresh identity and a fresh root; the API provides
no automatic retry or silent restart operation.

### Partial audit retention and commit markers

Files are written in source sequence using create-only names. A write or validation
failure leaves the transaction root and all successfully created artifacts in
place as partial audit evidence. Partial roots are never adopted, completed on a
later run, or deleted by the publication path. The absence of a valid final
manifest identifies them as incomplete.

The acquisition terminal manifest, `acquisition-finalization.json`, is created only
after all acquisition artifacts and `finalized-package.json` are closed and hashed.
Review starts only from that exact valid pair. The review terminal manifest,
`review-finalization.json`, is likewise created only after all truth records and
`independent-review.json` are closed and hashed. In each root, the terminal
manifest is the last file created and is the sole commit marker. It includes the
fixed safety assertions and binds the transaction identity, direction, source and
schema versions, ordered artifact inventory, lengths, and content digests. The
review lineage additionally binds the digest of the acquisition terminal manifest
it reviewed.

An audit record may describe a failure, but it cannot substitute for a final
manifest or turn a partial root into a committed transaction.

### Fail-closed path and integrity rules

Before creation, review, or consumption, paths are normalized and proven
to remain beneath their configured physical namespace. Symbolic links, junctions,
reparse points, hard-linked artifacts, path aliases, overlapping roots, and any
other indirection that prevents proving unique physical ownership are rejected;
they are not followed.

Review revalidates the acquisition manifest and every acquisition artifact before
writing review output. External consumption revalidates the relevant manifest,
inventory, sizes, digests, safety assertions, direction, and root containment. Any
missing file, unexpected file, link, alias, digest mismatch, or observed mutation
fails closed. The evidence is not repaired, promoted, activated, or treated as a
successful review.

### Independent directions

Each navigation direction has an independent source sequence, acquisition
transaction, review transaction, validation result, and commit decision. Evidence
for one direction is never inferred by reversing or re-labeling evidence from the
other, and failure or partial publication in one direction cannot commit or mutate
the other. A caller that requires a round trip must explicitly require two valid,
direction-matching review transactions.

### External expectations

External tools and reviewers must:

- treat a root without a valid final manifest as partial audit evidence only;
- use manifest validation, not directory existence, naming, timestamps, or aliases,
  to determine completion;
- consume acquisition evidence only after its acquisition manifest validates;
- consume reviewed evidence only after both the review manifest and its referenced
  acquisition manifest validate;
- preserve the transaction roots unchanged and publish annotations elsewhere;
- keep direction-specific results distinct; and
- never interpret synthetic evidence as live validation, input authorization,
  activation, or production support.

## Consequences

Interrupted and conflicting writes remain explainable, completed evidence is
content-addressably reviewable, and offline publication cannot silently acquire
live or input capability. Storage use increases because partial and superseded
transactions are retained, and consumers must perform full validation rather than
trusting filenames or directory presence.

The implementation is intentionally navigation-specific. It must not introduce a
generic transaction engine, general event store, cross-subsystem evidence bus, or
shared activation framework. Reuse outside offline navigation synthetic evidence
requires a separate architectural decision.
