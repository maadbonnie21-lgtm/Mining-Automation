# Resource release live-campaign readiness

Status: **`PREPARED_NOT_AUTHORIZED`**

This A8 checkpoint prepares the exact future enable-only checklist for the
passive constrained-v1 resource campaign. It does not authorize or execute the
campaign. It contains no live pixels, session, operator identity, reviewer
identity, receipt, `WorldState`, controller path, or input authority.

`LIVE_RESOURCE_CAMPAIGN_AUTHORIZED` remains one literal `False` assignment.
While it is false, `start` exits before reading repository provenance, creating
a session directory, or constructing a capture backend. No RuneLite, account,
camera, window, or input interaction occurs in readiness preparation.

## Immutable readiness manifest

Prepare the deterministic metadata-only manifest to a directory **outside the
Git repository**:

```powershell
python tools/resource_release_campaign.py prepare-live-readiness `
  --output C:\external-evidence\resource-live-readiness.json
```

The external location is deliberate: publishing the readiness artifact cannot
dirty the exact clean repository head that the manifest binds. Existing files
are never overwritten. The writer uses the campaign's ownership-safe exclusive
artifact publisher and writes an adjacent SHA-256 sidecar. It re-reads exact
Git provenance and reconstructs the source projection immediately before and
after publication. If either changes, preparation fails and returns no retained
root. A post-publication race may leave an internally hashed artifact, but it
is not accepted because no digest was returned; preserve it diagnostically and
use a new output path.

Preparation also proves from Git itself that the readiness head is one
non-merge direct child of exact accepted A7 head
`d34143f00835cdafc4ace2987b1b8202e7a0abfb`. The tracked campaign gate,
packaged profile, readiness implementation, and campaign CLI working bytes must
hash back to their exact `HEAD:<path>` blobs. This remains effective when a
file was hidden from ordinary status output with `assume-unchanged` or
`skip-worktree`. The complete 15-case plan, capture configuration, and
detector/profile identity are additionally checked against their frozen A7
roots and constants.

Retain the returned SHA-256 somewhere independent from the JSON and its
sidecar. Verify the exact artifact against that root and the same clean source
head:

```powershell
python tools/resource_release_campaign.py verify-live-readiness `
  --manifest C:\external-evidence\resource-live-readiness.json `
  --expected-sha256 <independently-retained-sha256>
```

Coordinated replacement of the manifest and sidecar does not pass with the old
external root. Supplying a new root for a modified manifest also does not pass:
verification reconstructs the complete fixed projection from current source.

## Frozen campaign and envelope

The manifest derives these values from the existing campaign and packaged
production profile rather than accepting caller overrides:

- the fixed 15-observation order and exact operator prompts;
- operator labels are unverified staging only;
- independent reviewer truth is required for every case;
- `1005x1078` `bgra8888` frames and reported DPI `96`;
- detector `profiled-resource:varrock-east-iron-v1@2.1.0`;
- profile `varrock-east-iron-v1`, schema 3, location
  `varrock-east-mine`, packaged four-resource order, and profile hash;
- structural landmark threshold `0.12`, quorum 5 of 6, and all three macro
  zones;
- backend `windows-runelite`, title match `RuneLite`, and source origin
  `source-owned-windows-runelite`;
- exactly one passive capture and one unchanged production evaluation per
  requested observation;
- no case selection, detector-controlled retry, automatic camera control,
  camera recovery, or input; and
- unsupported or uncertain view means zero targets and STOP.

Renderer identity is explicitly unobserved. Client/window and renderer
identity cannot be asserted by the operator; the former must be recorded by
the source-owned capture path for each observation and both remain subject to
independent envelope review. The readiness artifact does not invent future
environment values.

Every captured failure remains evidence. C1 completion requires externally
rooted completion-seal, review-package, release-summary, and follow-up
artifacts. C1 can never self-close C2. Replay adoption, final renderer/envelope
review, the source release decision, receipt issuance, and any later activation
remain separate source/review changes.

## Future enable-only checklist

The readiness manifest records the current exact clean A8 head as the only
permitted parent of a future live execution head. That future change must:

1. be a direct child of the lead-accepted A8 readiness head;
2. change only the source-owned
   `LIVE_RESOURCE_CAMPAIGN_AUTHORIZED: False -> True` assignment;
3. make no campaign, detector, threshold, quorum, zone, retry, envelope,
   camera, reviewer, receipt, or authority change;
4. pass exact-head focused tests, Ruff, strict Linux mypy, full pytest, and CI;
5. receive an explicit lead GitHub authorization naming that exact enabled
   40-character head; and
6. create a fresh private session on the enabled head only after that
   authorization.

The readiness artifact cannot fill in the future execution head, approve its
own parent, flip the gate, create a session, or self-authorize. The exact-head
lead authorization is an external release-control requirement, not a second
runtime state variable: the future source flip makes the program technically
capable of starting, but does not by itself satisfy that external authorization.
Sessions from a readiness head may not be reused.

## Authority boundary

All approval, release, receipt, activation, `WorldState`, controller, mining,
banking, navigation, input, and click authority fields are permanently false.
The manifest is preparation evidence only. The live resource campaign remains
unauthorized.
