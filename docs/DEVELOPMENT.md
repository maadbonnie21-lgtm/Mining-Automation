# Development checks

Use Python 3.12 in a virtual environment, then install the project and its development tools:

```bash
python -m pip install -e '.[dev]'
```

Local virtual environments, editable-install metadata, coverage data, bytecode, and tool caches are
ignored by Git so running the checks does not pollute a change set.

Run the same checks as CI before opening a pull request:

```bash
ruff check .
mypy src
pytest --cov=mining_automation --cov-report=term-missing
```

## Tool version policy

The development dependencies use PEP 440 compatible-release constraints for the tool series
validated in CI. For example, `pytest~=9.1.1` accepts compatible pytest 9.1 patch fixes but not
an unreviewed 9.2 feature release. This gives fresh environments a repeatable feature set while
still allowing low-risk patch updates.

Pytest also declares its minimum accepted version in its own configuration, and Ruff enforces the
same supported 0.16 release series at startup. This makes an accidentally reused older global tool
fail clearly instead of applying a different check policy.

These constraints are intentionally not a fully locked environment. Python 3.12 patch releases,
build tooling, and transitive dependencies can still move. When a development tool needs a feature
or security update outside its current series, update its constraint in a focused pull request and
run the complete check suite.

## Ruff policy

Ruff's selected rule families are explicit so a change to Ruff's defaults cannot silently change
the repository's lint contract:

- `B`: likely bugs and unsafe patterns from flake8-bugbear
- `E4`, `E7`, `E9`: import, statement, and runtime-error subsets of pycodestyle
- `F`: undefined names, unused imports, and related Pyflakes checks
- `I`: deterministic import ordering
- `UP`: safe Python 3.12 syntax and deprecation upgrades

The selection is deliberately conservative. Broad style and framework-specific families should be
enabled only in a scoped change that first makes the existing baseline clean and documents the new
policy. Line-length enforcement is not enabled; `line-length = 100` remains available to formatters
and future tooling without creating unrelated lint churn.

## Shared contract conventions

Runtime contract validation rejects invalid data when a value object is created, before it can
reach control logic. Interaction regions use `(x, y, width, height)`: width and height must be
positive, while x and y may be negative for virtual desktops whose origin is left of or above the
primary display. OSRS worlds are sanity-checked as three-digit values from 301 through 999; this is
not a live-world or supported-world allowlist.

## Real-client camera validation

Issue #31's deterministic Varrock East camera harness is a development-only
validation composition point. It does not add camera input to production
perception or change the production scene thresholds, quorum, macro-zone, or
fail-closed policies. Read
[Camera reacquisition validation](CAMERA_REACQUISITION_VALIDATION.md) before
running it.

The former fixed candidate/open-loop strategy is retained for regression
compatibility but is no longer canonical after clean real-client evidence
proved that complete Windows receipts can correspond to a RuneLite camera
no-op. The latest fresh real frame matched `0/6` production landmarks. Wide
diagnostics found three strict local recoveries at noncoherent offsets, while
the best shared displacement matched only `1/6`; the v1 zoom-only selector is
therefore insufficient for that envelope, not safely actionable.

The session-bound V2 development policy permits one receipt-bound
compass-north bootstrap when v1 lacks distributed evidence and retains only
v1's exact reviewed zoom sign. A dominant yaw or pitch result can request one
signed four-pixel calibration probe, but the probe cannot authorize a
correction or establish scene acceptance. The arm seam reruns readiness and
unchanged production perception, compares only excluded-candidate structural
regions, then requires a strictly newer final commit frame to retain readiness
and the same cheap world-only guard. No-camera-input platform preflight runs
before that final commit capture, so a world change during preflight is
observed and vetoed before input. Every controlled button/key is rechecked at
the compass adapter's final seam. The accepted arm must remain less than one
second old at input. Any changed, ambiguous, stale, non-fresh, or invalid-clock
case stops before input. Use
`tools/analyze_issue31_servo_offline.py` for the required read-only proof; it
requires explicit production/readiness/count expectations for every private or
diagnostic frame group and rejects dirty or changing Git provenance.

The canonical development-only V2 boundary is one command:

```powershell
python tools/validate_varrock_east_camera.py north-bootstrap-v2 --case-prefix issue31-north-YYYYMMDD-HHMMSS
```

Use a permanently unique case prefix. The subcommand accepts no input-policy
overrides, requires the same clean exact Git head at every provenance seam,
and holds the global input lease through cleanup and exclusive report/SHA
publication. Exit `0` belongs only to unchanged production success;
`BOOTSTRAP_EXECUTED` is retained non-pass evidence and exits `1`, while setup
or evidence-publication failure exits `2`. Its private report includes the
complete V2/bootstrap evidence, input-request/receipt clocks and delivery
duration, and the fixed logical
compass-point/target-root policy without claiming a numeric pointer mapping.

The V2 library, deterministic tests, and a complete Windows input receipt are
not evidence of live RuneLite success. Production thresholds, landmark quorum,
macro zones, scene authority, and fail-closed resource exposure remain
unchanged; diagnostics and calibration probes never override production.

The lead-approved fixed system-identification boundary is:

```powershell
python tools/validate_varrock_east_camera.py fixed-aba-probe-v2 --case-prefix issue31-system-id-YYYYMMDD-HHMMSS
```

It accepts no control parameters. It captures two no-input baselines, executes
separate guarded horizontal `+4/-4` middle drags, and runs the equivalent
vertical A/B/A only after strict distributed horizontal response qualifies.
Every action has its own fresh arm/preflight/commit seam. The canonical private
report contains all eight per-action observations. Forward/reverse response is
measured only from each exact commit to its post frame, against all-pairs
same-pose offset and descriptor jitter. It also records closure, production
evaluations, exact input receipts, adapter/pointer policy, Git identity, and
SHA-256. Its conclusion is calibration evidence only and cannot override
the production scene gate or satisfy Issue #31 reacquisition acceptance.

All raw frames, BMP previews, unreviewed drafts, JSON reports, and report digest
sidecars stay beneath ignored `diagnostics/`. The camera recipe remains pending
until repeated real RuneLite trials and the complete 36-frame drift set both
pass on the same clean exact Git head.
