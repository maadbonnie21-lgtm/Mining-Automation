"""Keep the English launcher labels readable after source integration."""

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "relative", ["src/mining_automation/beta_launcher.py", "tools/run_beta_launcher.py"]
)
def test_launcher_display_literals_are_not_mojibake(relative):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / relative).read_text(encoding="utf-8"))
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert all(value.isascii() for value in strings)
