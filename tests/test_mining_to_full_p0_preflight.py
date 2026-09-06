from pathlib import Path


def test_mining_live_preflight_allows_private_untracked_pose_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "tools/run_mining_to_full.py").read_text(encoding="utf-8")
    assert '"--porcelain", "--untracked-files=no"' in text
    assert "verify_local_pose_references(root)" in text
    assert text.index("verify_local_pose_references(root)") < text.index(
        "backend = WindowsMiningToFullBackend("
    )


def test_prep_cleanliness_allows_private_untracked_pose_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "tools/runelite_prep.py").read_text(encoding="utf-8")
    assert '_git("status", "--porcelain", "--untracked-files=no")' in text


def test_mining_plan_preserves_no_camera_no_navigation_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "tools/run_mining_to_full.py").read_text(encoding="utf-8")
    assert '"camera_preparation_authority": False' in text
    assert '"navigation_started_on_full": False' in text
