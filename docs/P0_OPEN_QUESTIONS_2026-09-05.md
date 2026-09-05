# P0 unresolved startup questions — 2026-09-05

1. Can the current arbitrary view be registered to exactly one retained successful pose with enough world-only correspondences?
2. Is a global affine/homography sufficient across the visible mine scene, or is piecewise/local warping required?
3. Can target centers be reprojected and still prove `Mine Iron rocks` reliably?
4. Does the latest empty-start Inventory implementation correctly track 0->1->... across real frames?
5. What negative real frames are locally available for the morning read-only replay?

These questions are blockers to claiming real-client readiness, not blockers to offline implementation.