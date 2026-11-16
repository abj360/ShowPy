#!/usr/bin/env python3

"""
test_library_rules.py --- tests source-level rules that every showpy module must follow

Contains:
    find_model_copy_updates(): lists file and line of each model_copy call that passes update
    test_library_never_calls_model_copy_with_update: fails on any such call in showpy/
    test_model_copy_ban_detects_an_offending_call: proves the scan can fail
"""

import ast
from pathlib import Path

import pytest

LIBRARY_ROOT = Path(__file__).resolve().parents[2] / "showpy"
pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(), reason="needs a source checkout; the wheel test has no showpy tree"
)


def find_model_copy_updates(source: str, name: str) -> list[str]:
    """Lists `name:line` for each `model_copy(update=...)` call in the source."""
    return [
        f"{name}:{node.lineno}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_copy"
        and any(keyword.arg == "update" for keyword in node.keywords)
    ]


def test_library_never_calls_model_copy_with_update() -> None:
    """Requires every library edit to go through the revalidating FrozenModel.replace."""
    offenders = [
        hit
        for path in sorted(LIBRARY_ROOT.rglob("*.py"))
        for hit in find_model_copy_updates(
            path.read_text(encoding="utf-8"), str(path.relative_to(LIBRARY_ROOT.parent))
        )
    ]
    assert not offenders, "model_copy(update=) skips validators; use .replace(): " + ", ".join(
        offenders
    )


def test_model_copy_ban_detects_an_offending_call() -> None:
    """Reports the line of an update call and ignores a plain copy."""
    source = "a = x.model_copy()\nb = x.model_copy(update={'k': 1})\n"
    assert find_model_copy_updates(source, "m.py") == ["m.py:2"]
