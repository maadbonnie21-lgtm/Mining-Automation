from pathlib import Path

# 1) Remove the private-pose-reference / clean-checkout deadlock in the miner.
path = Path("tools/run_mining_to_full.py")
text = path.read_text(encoding="utf-8")
old = "from mining_automation.mining_slice import (\n"
new = (
    "from mining_automation.perception.live_pose_references import (\n"
    "    verify_local_pose_references,\n"
    ")\n"
    "from mining_automation.mining_slice import (\n"
)
if old not in text or "verify_local_pose_references" in text:
    raise SystemExit("mining pose-verifier import anchor missing or already patched")
text = text.replace(old, new, 1)
old = '        dirty = _git("-C", str(root), "status", "--porcelain")\n'
new = (
    '        dirty = _git(\n'
    '            "-C", str(root), "status", "--porcelain", "--untracked-files=no"\n'
    '        )\n'
)
if old not in text:
    raise SystemExit("mining tracked-cleanliness anchor missing")
text = text.replace(old, new, 1)
old = '''    for value, label in (\n        (args.neutral_settle, "--neutral-settle"),\n        (args.hover_settle, "--hover-settle"),\n        (args.passive_interval, "--passive-interval"),\n    ):\n        if value < 0.0:\n            print(f"STOP: {label} must be non-negative", file=sys.stderr)\n            return 2\n\n    run_id = f"mining-to-full-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"\n'''
new = '''    for value, label in (\n        (args.neutral_settle, "--neutral-settle"),\n        (args.hover_settle, "--hover-settle"),\n        (args.passive_interval, "--passive-interval"),\n    ):\n        if value < 0.0:\n            print(f"STOP: {label} must be non-negative", file=sys.stderr)\n            return 2\n\n    # The three successful real-client pose frames are private/local by design.\n    # They may remain untracked, but their exact geometry and bytes must verify\n    # before any live backend or input device is constructed.\n    try:\n        pose_manifest = verify_local_pose_references(root)\n    except (FileNotFoundError, OSError, ValueError) as exc:\n        print(f"STOP: local pose reference preflight failed: {exc}", file=sys.stderr)\n        return 2\n\n    run_id = f"mining-to-full-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"\n'''
if old not in text:
    raise SystemExit("mining live-preflight insertion anchor missing")
text = text.replace(old, new, 1)
old = '''        "navigation_started_on_full": False,\n    }\n'''
new = '''        "navigation_started_on_full": False,\n        "local_pose_references_required": 3,\n        "tracked_checkout_required_clean": True,\n        "untracked_private_pose_references_permitted": True,\n        "camera_preparation_authority": False,\n    }\n'''
if old not in text:
    raise SystemExit("mining plan insertion anchor missing")
text = text.replace(old, new, 1)
old = '''        "success": result.success,\n        "detail": result.detail,\n        "events": list(result.events),\n        "invariants": {\n'''
new = '''        "success": result.success,\n        "detail": result.detail,\n        "events": list(result.events),\n        "pose_references": [\n            {\n                "pose_id": receipt.pose_id,\n                "relative_path": receipt.relative_path,\n                "sha256": receipt.sha256,\n                "byte_count": receipt.byte_count,\n            }\n            for receipt in pose_manifest.receipts\n        ],\n        "invariants": {\n'''
if old not in text:
    raise SystemExit("mining result-payload insertion anchor missing")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

# 2) PREP must use the same tracked-only cleanliness rule. The local private pose
# references are mandatory inputs, not uncommitted source code.
path = Path("tools/runelite_prep.py")
text = path.read_text(encoding="utf-8")
old = 'def _checkout_clean() -> bool:\n    return _git("status", "--porcelain") == ""\n'
new = (
    'def _checkout_clean() -> bool:\n'
    '    return _git("status", "--porcelain", "--untracked-files=no") == ""\n'
)
if old not in text:
    raise SystemExit("PREP tracked-cleanliness anchor missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

# 3) Claude's independent reconstruction found no evidence-backed camera sequence
# that ever restored the working view. Keep existing injectable camera primitives
# available for tests/research, but production PREP performs zero camera input today.
path = Path("src/mining_automation/validation/runelite_prep.py")
text = path.read_text(encoding="utf-8")
old = '''# The first three operations reproduce the bounded pitch sequence that immediately\n# preceded the retained recalibrated working view on 2026-09-03. The wheel probes are\n# deliberately one-detent local probes, not the disproven "four zoom-up = READY"\n# folklore. Every step is followed by a fresh measured gate evaluation.\nDEFAULT_CAMERA_SEARCH_STEPS: Final[tuple[PrepCameraStep, ...]] = (\n    PrepCameraStep.PITCH_DOWN_100MS,\n    PrepCameraStep.PITCH_DOWN_100MS,\n    PrepCameraStep.PITCH_UP_50MS,\n    PrepCameraStep.WHEEL_POSITIVE_1,\n    PrepCameraStep.WHEEL_NEGATIVE_1,\n)\n'''
new = '''# 2026-09-04 independent audit: none of the retained zoom/pitch/manual-restoration\n# trials restored the frozen Resource gate. The working session came from retained\n# current-view calibration/pose references. Therefore production PREP sends zero\n# camera input today. The typed camera steps remain injectable for focused testing\n# and future explicitly reviewed evidence, but they are not a default search recipe.\nDEFAULT_CAMERA_SEARCH_STEPS: Final[tuple[PrepCameraStep, ...]] = ()\n'''
if old not in text:
    raise SystemExit("PREP default-camera anchor missing")
text = text.replace(old, new, 1)
old = '''            if not observation.frozen_resource_gate_passed:\n                for step in camera_steps:\n'''
new = '''            if not observation.frozen_resource_gate_passed and not camera_steps:\n                raise PrepOperationError(\n                    PrepStopReason.RESOURCE_SCENE_UNSUPPORTED,\n                    "Current view is not READY and no evidence-backed automatic camera "\n                    "normalization is authorized today. Set the supported mining view "\n                    "once, then rerun PREP; software registration may still validate it.",\n                )\n\n            if not observation.frozen_resource_gate_passed:\n                for step in camera_steps:\n'''
if old not in text:
    raise SystemExit("PREP camera-loop guard anchor missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

# 4) Add regression tests proving both P0 fixes and zero-default-camera behavior.
path = Path("tests/test_runelite_prep.py")
text = path.read_text(encoding="utf-8")
addition = '''\n\ndef test_default_apply_unsupported_view_sends_zero_camera_input() -> None:\n    backend = FakePrepBackend(\n        observations=[_observation(resource_supported=False, matched=0, zones=())]\n    )\n    result = run_runelite_prep(\n        backend,\n        mode=PrepMode.APPLY,\n        git_sha=GIT_SHA,\n        prep_session_id="prep-default-no-camera",\n        confirm=PREP_CONFIRMATION,\n    )\n    assert result.ready_for_mining is False\n    assert result.stop_reason is PrepStopReason.RESOURCE_SCENE_UNSUPPORTED\n    assert backend.camera_calls == []\n    assert "no evidence-backed automatic camera" in result.detail\n'''
if "test_default_apply_unsupported_view_sends_zero_camera_input" not in text:
    path.write_text(text + addition, encoding="utf-8")

Path("tests/test_mining_to_full_p0_preflight.py").write_text('''from pathlib import Path\n\n\ndef test_mining_live_preflight_allows_private_untracked_pose_inputs() -> None:\n    root = Path(__file__).resolve().parents[1]\n    text = (root / "tools/run_mining_to_full.py").read_text(encoding="utf-8")\n    assert '"--porcelain", "--untracked-files=no"' in text\n    assert "verify_local_pose_references(root)" in text\n    assert text.index("verify_local_pose_references(root)") < text.index(\n        "backend = WindowsMiningToFullBackend("\n    )\n\n\ndef test_prep_cleanliness_allows_private_untracked_pose_inputs() -> None:\n    root = Path(__file__).resolve().parents[1]\n    text = (root / "tools/runelite_prep.py").read_text(encoding="utf-8")\n    assert '_git("status", "--porcelain", "--untracked-files=no")' in text\n\n\ndef test_mining_plan_preserves_no_camera_no_navigation_authority() -> None:\n    root = Path(__file__).resolve().parents[1]\n    text = (root / "tools/run_mining_to_full.py").read_text(encoding="utf-8")\n    assert '"camera_preparation_authority": False' in text\n    assert '"navigation_started_on_full": False' in text\n''', encoding="utf-8")
