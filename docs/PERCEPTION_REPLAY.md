# Perception replay and regression fixtures

Milestone: **M2B — Perception infrastructure**

This is a development and test harness, not the finished application or a live capture backend. It
lets detectors consume the same immutable `capture.Frame` objects they receive in production while
running entirely from saved bytes with no display, RuneLite process, or Windows API.

The dependency path is deliberately one way:

```text
raw fixture bytes -> ReplayDataset -> Frame -> Detector(s) -> Observation(s) -> EvaluationReport
```

Replay never implements `CaptureBackend` or `CaptureSource`. A recorded artifact cannot model live
window availability, timing, resize, DPI, or capture lifecycle and must not pretend that it can.

## Detector contract

A detector exposes immutable identity/version metadata and a synchronous deterministic operation:

```python
from collections.abc import Sequence

from mining_automation.capture import Frame
from mining_automation.contracts import Observation
from mining_automation.perception import DetectorMetadata


class ExampleDetector:
    metadata = DetectorMetadata(detector_id="example", version="1.0.0")

    def detect(self, frame: Frame, /) -> Sequence[Observation]:
        return ()
```

The guarded runner materializes the returned sequence and rejects non-`Observation` values,
observations attached to any `FrameRef` other than the input frame, blank observation kinds, and an
`Observation.detector_version` that differs from the detector metadata. Detector exceptions are
preserved as the cause of a typed `DetectorExecutionError`; they never become empty successful
output.

The contract is platform-independent. Detectors consume pixels and shared contracts only—never a
Windows capture backend or live-source lifecycle.

## Manifest schema version 1

Manifests are strict UTF-8 JSON. Unknown fields are rejected so spelling mistakes cannot silently
weaken a regression. A schema change that alters encoding or comparison meaning requires a new
`schema_version`.

```json
{
  "schema_version": 1,
  "dataset_id": "synthetic-m2b",
  "cases": [
    {
      "case_id": "normal-001",
      "frame": {
        "path": "frames/normal-001.raw",
        "width": 2,
        "height": 1,
        "pixel_format": "rgb888"
      },
      "expected_observations": [
        {
          "kind": "resource",
          "label": "iron",
          "region": [0, 0, 1, 1],
          "confidence": {"min": 0.8, "max": 1.0}
        }
      ],
      "tags": ["normal"],
      "provenance": {
        "source": "synthetic",
        "issue": "#6"
      },
      "notes": "Tiny example; not production OSRS data"
    }
  ]
}
```

### Root and case fields

- `schema_version` is exactly `1` for this format.
- `dataset_id` is a stable non-empty identifier used in reports.
- `cases` is a non-empty ordered array. Case IDs are non-empty and unique.
- `frame.path` is a portable relative path beneath the manifest directory. Absolute paths,
  backslashes, traversal (`..`), symlink escapes, Windows device names, alternate-data-stream
  colons, and other platform-invalid path components are rejected.
- `frame.width` and `frame.height` are positive integers. Boolean values are not integers here.
- `frame.pixel_format` is one of the public `PixelFormat` values: `gray8`, `rgb888`, `bgr888`,
  `rgba8888`, or `bgra8888`.
- `expected_observations` is ordered and may be empty for a strict “detect nothing” case.
- `tags` contains unique non-empty free-form strings. Useful tags include `normal`, `obstruction`,
  `depletion-transition`, `inventory`, `bank`, and `navigation-checkpoint`; these are examples, not
  an allowlist.
- `provenance` is a string-to-string map for capture/build/issue references.
- `notes` is a free-form string for reviewer context.

Every expectation requires a non-empty, case-sensitive `kind`. `label`, `region`, and `confidence`
are optional constraints. Confidence ranges are inclusive; either or both finite bounds may be
present, bounds stay inside `[0, 1]`, and `min` cannot exceed `max`.

Regions are exact frame-local `(x, y, width, height)` rectangles. Their origin is non-negative,
their extents are positive, and they must fit inside the fixture frame. They are not desktop or
pointer interaction coordinates. Version 1 uses exact equality; a future IoU/tolerance policy must
be an explicit schema evolution rather than a silent relaxation.

### Raw frame encoding

Version 1 stores the exact owned `Frame.payload` bytes—no header, compression, row padding, or
format conversion. The required byte length is:

```text
width * height * PixelFormat.bytes_per_pixel
```

The loader checks the file size before reading it, then constructs the consumer `Frame` through
`RawFrame` and `Frame.from_raw`. Missing files, short/oversized payloads, malformed metadata, and
unsupported versions raise typed replay errors. Exact raw bytes keep all current pixel formats
lossless without adding Pillow/OpenCV or a platform dependency. Encoded PNG/JPEG support would
require a later schema version that defines decoding and color semantics.

### Deterministic replay identity

Manifest order is canonical because future transition fixtures may depend on sequence. Each load
assigns frame IDs `1..N` and synthetic monotonic timestamps `0.0..N-1.0`. Original live IDs/times,
when relevant, belong in provenance. Repeated loads and iterations yield the same case order,
identity, metadata, and payload.

## Evaluation semantics

One or more detectors form an ensemble for a run. Their observations are combined per fixture, so
a generic case can require resource, inventory, landmark, and UI observations without embedding a
private detector owner in the manifest. Detector IDs must be unique in one run, and every detector
ID/version is retained in the report.

Optional expected fields use these canonical observation evidence keys:

- `label` compares with `Observation.evidence["label"]`.
- `region` compares with `Observation.evidence["region"]` as frame-local `(x, y, width, height)`.

Expected and actual observations are matched one-to-one within the same kind using a deterministic
minimum-cost assignment. One actual observation can never satisfy two expectations, and a broad
expectation cannot consume the only observation that satisfies a more specific one. Unpaired
expectations are `missing_observation`; unpaired output is `unexpected_observation`. Paired values
report `label_mismatch`, `region_mismatch`, and `confidence_mismatch` independently. Detector
exceptions and contract violations are `detector_error`. Any issue fails the case.

Reports contain a version, dataset and detector metadata, unique fixture counts, failing fixture
IDs in manifest order, case tags/provenance/notes, observation counts, and categorized details.
`EvaluationReport.render_text()` is concise for humans; `to_json()` is deterministic for tools.

## Command-line evaluator

Install the project development environment first, then run:

```bash
python tools/evaluate_perception.py \
  --manifest path/to/manifest.json \
  --detector package.detectors:build_resource_detector \
  --detector package.detectors:inventory_detector \
  --json-report evaluation.json
```

Each repeatable `--detector` value is `MODULE:ATTRIBUTE`. The attribute may be a detector instance,
a no-argument detector class, or a no-argument factory. Human output is written to stdout; the
optional JSON report is written only after fixture/detector setup succeeds.

Exit codes are stable:

- `0`: every fixture passed.
- `1`: at least one objective regression or detector execution/contract failure.
- `2`: usage, import/construction, manifest, fixture, or report-write error.

## Turning a real failure into a permanent regression

Use this red → fix → green workflow whenever practical:

1. Retain the failing consumer `Frame` and save its immutable `payload` exactly once. Record width,
   height, pixel format, relevant build/detector versions, issue, and circumstances.
2. Review the frame before committing it. Remove the case if it contains unrelated desktop content,
   personal data, credentials, chat, or anything outside the intended game surface.
3. Add the raw payload beneath the dataset directory and add one uniquely named manifest case. Write
   ground-truth expectations from what the frame objectively contains—not from current output. Add
   useful tags, provenance, and diagnostic notes.
4. Run the affected detector and prove the new case is red for the original failure. A fixture that
   already passes cannot prove the fix.
5. Correct the detector without weakening the expectation, then run the complete fixture set and
   prove the case and existing regressions are green.
6. Commit the payload, manifest change, detector fix, and focused regression test together. Keep
   production datasets reviewed and size-conscious; Issue #6 itself contains only generated tiny
   test bytes and no screenshots.

If a frame cannot be retained safely, preserve sanitized or synthetic reproducing evidence and note
that limitation in provenance rather than committing sensitive material.
