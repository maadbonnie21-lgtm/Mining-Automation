# Varrock East iron v1 — real fixture review

This dataset contains five owner-captured RuneLite frames from one fixed camera/window
envelope at Varrock East Mine. Each case was visually reviewed before ground truth was
written.

## Cases

| Case | Reviewed ground truth |
|---|---|
| `available-01` | Four available iron patches; player nearby without covering the profiled patches |
| `lower-left-full-cycle-019` | Four available patches immediately before depletion |
| `lower-left-full-cycle-020` | South-west patch depleted; three other patches available |
| `lower-left-full-cycle-028` | South-west patch still depleted; three other patches available |
| `lower-left-full-cycle-029` | South-west patch respawned; all four patches available |

The sequence therefore proves available → depleted → persistent depleted → available
for the south-west node, plus mixed-state evaluation while the other nodes remain
available.

## Privacy sanitization

The repository does **not** contain the unmodified owner captures or their local draft
metadata. Before inclusion, every stored frame retained its original geometry and
relevant game-world pixels but masked:

- the RuneLite title bar
- the lower chat/status area
- the lower-right inventory/interface area

Machine name, native window handle, and window title were removed from committed
provenance. The manifest retains SHA-256 hashes of both the private original and the
sanitized fixture so the reviewed transformation remains auditable without publishing
the original file.


## Compression at rest

The committed frame payloads use deterministic gzip only to keep repository and CI
size reasonable. `manifest.json` remains ordinary replay schema v1 and names the
exact `.raw` payloads. `materialize_gzip_replay_dataset()` expands each adjacent
`.raw.gz` file, enforces the declared decompressed byte count, computes the exact
decompressed SHA-256, and requires equality with that case's reviewed
`provenance.sanitized_sha256`. It writes the raw files exclusively and only then
hands the ordinary manifest to the merged replay loader. A same-length one-byte
change is rejected and all partial output is removed. No lossy image encoding or
pixel conversion occurs.

## Profile regions

The four 20×20 frame-local regions are deliberately small, visible rock-surface patches.
They are both the classification evidence and the conservative interaction regions
exposed by `ResourceState` when a target is available:

- north-west: `(263, 409, 20, 20)`
- south-west: `(295, 490, 20, 20)`
- center: `(405, 424, 20, 20)`
- north-east: `(590, 365, 20, 20)`

## Current validation boundary

Real depletion/respawn evidence currently exists for the south-west node. The other
three nodes have reviewed real available evidence, and their depleted prototype is
provisionally shared from the south-west node because depleted iron uses the same grey
visual family in this fixed scene. Each remaining node still needs its own real
depletion/respawn sequence before the profile can be labeled release-ready for all four
nodes. PR #12 remains a draft until that gate and unsupported-scene collection pass.
