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

All raw frames, BMP previews, unreviewed drafts, JSON reports, and report digest
sidecars stay beneath ignored `diagnostics/`. The camera recipe remains pending
until repeated real RuneLite trials and the complete 36-frame drift set both
pass on the same clean exact Git head.
