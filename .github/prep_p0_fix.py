"""One-shot P0 PREP hardening after adversarial review.

Removed by the companion workflow after focused verification succeeds.
"""

from pathlib import Path

CORE = Path("src/mining_automation/validation/runelite_prep.py")
TOOL = Path("tools/runelite_prep.py")
TESTS = Path("tests/test_runelite_prep.py")


# 1. Make the controller independently re-check the exact frozen Resource gate
# instead of trusting only the adapter's supported boolean / aggregate counts.
text = CORE.read_text(encoding="utf-8")
old = """RESOURCE_LANDMARK_QUORUM: Final[int] = 5
RESOURCE_REQUIRED_ZONE_COUNT: Final[int] = 3
PREP_CONFIRMATION: Final[str] = \"PREP_RUNELITE_FOR_MINING\"
"""
new = """RESOURCE_LANDMARK_QUORUM: Final[int] = 5
RESOURCE_REQUIRED_ZONE_COUNT: Final[int] = 3
RESOURCE_REQUIRED_ZONES: Final[frozenset[str]] = frozenset(
    (\"north_west\", \"north_east\", \"south_west\")
)
PREP_CONFIRMATION: Final[str] = \"PREP_RUNELITE_FOR_MINING\"
"""
if old not in text:
    raise SystemExit("Resource constant anchor missing")
text = text.replace(old, new, 1)

old = """    @property
    def frozen_resource_gate_passed(self) -> bool:
        return (
            self.resource_supported
            and self.matched_landmarks >= RESOURCE_LANDMARK_QUORUM
            and len(self.matched_zones) >= RESOURCE_REQUIRED_ZONE_COUNT
        )
"""
new = """    @property
    def frozen_resource_gate_passed(self) -> bool:
        # READY is never delegated to a diagnostic score or an adapter assertion.
        # Re-check all six retained landmark distances at the unchanged 0.12 ceiling
        # and require the exact three macro zones used by the released Resource gate.
        if len(self.landmark_distances) != RESOURCE_LANDMARK_COUNT:
            return False
        if len({name for name, _ in self.landmark_distances}) != RESOURCE_LANDMARK_COUNT:
            return False
        within_threshold = sum(
            distance <= RESOURCE_LANDMARK_DISTANCE_THRESHOLD
            for _, distance in self.landmark_distances
        )
        return (
            self.resource_supported
            and self.matched_landmarks >= RESOURCE_LANDMARK_QUORUM
            and within_threshold >= RESOURCE_LANDMARK_QUORUM
            and frozenset(self.matched_zones) == RESOURCE_REQUIRED_ZONES
        )
"""
if old not in text:
    raise SystemExit("Resource gate property anchor missing")
text = text.replace(old, new, 1)
CORE.write_text(text, encoding="utf-8")


# 2. Verify checkout cleanliness before creating local PREP evidence in both modes,
# keep dirty-checkout results typed, and re-check cleanliness before publishing READY.
text = TOOL.read_text(encoding="utf-8")
old = "from dataclasses import asdict\n"
new = "from dataclasses import asdict, replace\n"
if old not in text:
    raise SystemExit("dataclasses import anchor missing")
text = text.replace(old, new, 1)

old = """    if output.exists():
        print(f\"STOP: output path already exists: {output}\", file=sys.stderr)
        return 2
    output.mkdir(parents=True)

    try:
        git_sha = _exact_git_sha()
    except (OSError, subprocess.CalledProcessError) as exc:
        git_sha = \"0\" * 40
        backend: PrepBackend = _ConstructionFailureBackend(
            f\"Could not read exact Git SHA: {exc}\"
        )
    else:
        if mode is PrepMode.APPLY and not _checkout_clean():
            backend = _ConstructionFailureBackend(
                \"Apply PREP requires a clean Git checkout; commit/stash unrelated changes first.\"
            )
        else:
            try:
                backend = RealPrepBackend(
                    title_substring=args.title,
                    output=output,
                    prep_session_id=prep_session_id,
                )
            except Exception as exc:  # noqa: BLE001 - still emit machine receipt
                backend = _ConstructionFailureBackend(
                    f\"Could not construct real Windows PREP backend: \"
                    f\"{type(exc).__name__}: {exc}\"
                )

    result = run_runelite_prep(
"""
new = """    if output.exists():
        print(f\"STOP: output path already exists: {output}\", file=sys.stderr)
        return 2

    dirty_checkout = False
    try:
        git_sha = _exact_git_sha()
        checkout_clean = _checkout_clean()
    except (OSError, subprocess.CalledProcessError) as exc:
        git_sha = \"0\" * 40
        backend: PrepBackend = _ConstructionFailureBackend(
            f\"Could not read exact Git checkout state: {exc}\"
        )
    else:
        if not checkout_clean:
            dirty_checkout = True
            backend = _ConstructionFailureBackend(
                \"PREP requires a clean Git checkout before diagnosis or apply; \"
                \"commit/stash unrelated changes first.\"
            )
        else:
            try:
                backend = RealPrepBackend(
                    title_substring=args.title,
                    output=output,
                    prep_session_id=prep_session_id,
                )
            except Exception as exc:  # noqa: BLE001 - still emit machine receipt
                backend = _ConstructionFailureBackend(
                    f\"Could not construct real Windows PREP backend: \"
                    f\"{type(exc).__name__}: {exc}\"
                )

    # The default diagnostics path is repository-ignored. Create it only after the
    # exact checkout has been measured so PREP cannot make its own preflight dirty.
    output.mkdir(parents=True)

    result = run_runelite_prep(
"""
if old not in text:
    raise SystemExit("tool main preflight anchor missing")
text = text.replace(old, new, 1)

old = """    if (
        mode is PrepMode.APPLY
        and isinstance(backend, _ConstructionFailureBackend)
        and \"clean Git checkout\" in backend.detail
    ):
        result = RunelitePrepResult(
            schema_version=result.schema_version,
            mode=result.mode,
            git_sha=result.git_sha,
            prep_session_id=result.prep_session_id,
            started_monotonic_s=result.started_monotonic_s,
            ended_monotonic_s=result.ended_monotonic_s,
            initial_window=result.initial_window,
            final_window=result.final_window,
            pose_references=result.pose_references,
            observations=result.observations,
            actions=result.actions,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail=backend.detail,
        )
    receipt = _write_result(output, result)
"""
new = """    if dirty_checkout and isinstance(backend, _ConstructionFailureBackend):
        result = replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail=backend.detail,
        )
    # A custom evidence path or an external process must not leave a READY receipt
    # that the separately authorized miner would immediately reject as dirty.
    if result.ready_for_mining and not _checkout_clean():
        result = replace(
            result,
            ready_for_mining=False,
            stop_reason=PrepStopReason.DIRTY_CHECKOUT,
            detail=(
                \"Checkout became dirty during PREP; READY is withheld until the \"
                \"exact mining checkout is clean.\"
            ),
        )
    receipt = _write_result(output, result)
"""
if old not in text:
    raise SystemExit("tool dirty-result anchor missing")
text = text.replace(old, new, 1)
TOOL.write_text(text, encoding="utf-8")


# 3. Make fake evidence obey the six-landmark contract and add adversarial tests for
# threshold/zone assertions and the PREP -> miner clean-checkout handoff.
text = TESTS.read_text(encoding="utf-8")
old = """        matched_landmarks=matched,
        matched_zones=zones,
        landmark_distances=((\"a\", 0.01),) if matched else ((\"a\", 0.5),),
        diagnostic_score=score,
"""
new = """        matched_landmarks=matched,
        matched_zones=zones,
        landmark_distances=tuple(
            (f\"landmark-{index}\", 0.01 if index < matched else 0.5)
            for index in range(6)
        ),
        diagnostic_score=score,
"""
if old not in text:
    raise SystemExit("test observation anchor missing")
text = text.replace(old, new, 1)

append = r'''


def test_ready_independently_rechecks_landmark_distances_at_point_12() -> None:
    observation = replace(
        _observation(resource_supported=True, matched=5),
        landmark_distances=tuple(
            (f"landmark-{index}", 0.13 if index < 5 else 0.5)
            for index in range(6)
        ),
    )
    backend = FakePrepBackend(observations=[observation])
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.RESOURCE_SCENE_UNSUPPORTED


def test_ready_rejects_three_wrong_zone_names_even_with_five_matches() -> None:
    backend = FakePrepBackend(
        observations=[
            _observation(
                resource_supported=True,
                matched=5,
                zones=("wrong_a", "wrong_b", "wrong_c"),
            )
        ]
    )
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is False
    assert result.stop_reason is PrepStopReason.RESOURCE_SCENE_UNSUPPORTED


def test_exact_five_of_six_distances_and_all_three_zones_can_pass() -> None:
    backend = FakePrepBackend(
        observations=[
            _observation(
                resource_supported=True,
                matched=5,
                zones=("north_west", "north_east", "south_west"),
            )
        ]
    )
    result = _run(backend, mode=PrepMode.READ_ONLY, confirm=None)
    assert result.ready_for_mining is True


def test_default_prep_evidence_is_ignored_before_separate_miner_handoff() -> None:
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "/diagnostics/runelite-prep-*/" in ignore

    tool = (root / "tools/runelite_prep.py").read_text(encoding="utf-8")
    clean_index = tool.index("checkout_clean = _checkout_clean()")
    mkdir_index = tool.index("output.mkdir(parents=True)")
    assert clean_index < mkdir_index
    assert "if mode is PrepMode.APPLY and not _checkout_clean()" not in tool
    assert "if result.ready_for_mining and not _checkout_clean():" in tool
'''
if "test_ready_independently_rechecks_landmark_distances_at_point_12" in text:
    raise SystemExit("P0 adversarial tests already present")
text += append
TESTS.write_text(text, encoding="utf-8")
