# P0 engineering worklog — 2026-09-05

## Current objective

Replace the failed open-loop camera attempts with an evidence-backed software startup resolver, keep the empty-Inventory fix, and independently audit the exact head before another real RuneLite command is issued.

## Completed corrective steps

- Preserved both failed live PREP attempts as failure evidence.
- Recorded the self-audit identifying the retired Issue #31 misuse and profile-lineage mismatch.
- Removed the two unproven open-loop camera ladders from the executable P0 path.
- Kept automatic window restore/focus/client-area resize and exact DPI/identity gates.
- Kept the proven-empty Inventory bootstrap that prevents unknown hashes from becoming false FULL.
- Added a read-only latest-PREP receipt summarizer.
- Defined the minimum software-normalization acceptance matrix.

## Active engineering lane

Build a world-viewport-only visual registration layer against the three retained September 3 successful pose frames. The layer must be replay-tested, ambiguity rejecting, and followed by a strictly newer independent validation capture before it can contribute to READY.

## Explicitly not complete

- No software normalizer is implemented yet.
- Automatic arbitrary-camera startup is not real-client proven.
- No new 0->28 live command is authorized at this checkpoint.
- Final Resource/Inventory release provenance is not complete.

## Next exact checkpoints

1. inspect current dependency and image-processing surfaces;
2. implement transform proposal and immutable receipt types;
3. add synthetic transform and negative-scene tests;
4. add a retained-pose replay path;
5. bind the transform into PREP without granting mining authority;
6. recheck invariants/diff/CI on the exact final head;
7. perform one read-only Windows diagnostic before any mining authorization.