# P0 startup resolver stop reasons — 2026-09-05

The owner output must distinguish at least:

- RuneLite/window identity mismatch;
- client geometry mismatch;
- DPI mismatch;
- gameplay chrome mismatch;
- Inventory UNKNOWN or not 0/28;
- missing/invalid retained pose references;
- no registration proposal;
- ambiguous multiple-pose registration;
- degenerate/reflected/out-of-bounds transform;
- insufficient distributed inliers;
- excessive reprojection residual;
- newer-frame transform instability;
- newer-frame Resource gate failure;
- no AVAILABLE iron target;
- cleanup failure.

Every stop reason grants zero mining input authority.