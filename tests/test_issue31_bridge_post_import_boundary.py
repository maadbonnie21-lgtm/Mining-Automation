"""Import-boundary regression for the read-only Issue #31 post verifier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ISOLATED_IMPORT = r"""
import importlib.util
import json
import sys
from pathlib import Path

repository = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository / "src"))
tool_path = repository / "tools" / "verify_issue31_bridge_r2_post.py"
spec = importlib.util.spec_from_file_location(
    "issue31_bridge_post_import_boundary_target",
    tool_path,
)
if spec is None or spec.loader is None:
    raise RuntimeError("could not load the Issue #31 offline verifier")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
blocked = (
    "mining_automation.validation.camera_input_lease",
    "mining_automation.validation.windows_camera",
)
print(json.dumps(sorted(name for name in blocked if name in sys.modules)))
"""


def test_offline_post_verifier_import_does_not_load_platform_input_modules() -> None:
    """The verifier must remain importable without either Windows input boundary."""

    repository = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _ISOLATED_IMPORT, str(repository)],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []
