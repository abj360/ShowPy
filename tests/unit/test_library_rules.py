#!/usr/bin/env python3

"""
test_library_rules.py --- tests source-level rules that every showpy module must follow

Contains:
    find_model_copy_updates(): lists file and line of each model_copy call that passes update
    test_library_never_calls_model_copy_with_update: fails on any such call in showpy/
    test_model_copy_ban_detects_an_offending_call: proves the scan can fail
    find_citations(): lists file and line of each ADR, ledger-ID or docs-path citation
    test_library_cites_no_adr_docs_or_ledger_ids: fails on any such citation in showpy/
    test_citation_scan_flags_a_docstring_and_a_comment: proves the scan can fail
    test_citation_scan_ignores_runtime_strings: proves plain string literals are not flagged
"""

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

CITATION_PATTERN = re.compile(r"\bADR\b|\bUP-\d+\.\d+-\d+\b|\bdocs/")
MIN_LIBRARY_FILES = 100
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


def find_citations(source: str, name: str) -> list[str]:
    """Lists `name:line` for each ADR, ledger-ID or docs-path citation in a docstring or comment."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is None or CITATION_PATTERN.search(doc) is None:
            continue
        first = node.body[0]
        for offset, text in enumerate(doc.splitlines()):
            if CITATION_PATTERN.search(text):
                hits.append(f"{name}:{first.lineno + offset}")
    hits.extend(
        f"{name}:{token.start[0]}"
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and CITATION_PATTERN.search(token.string)
    )
    return sorted(hits)


def test_library_cites_no_adr_docs_or_ledger_ids() -> None:
    """Requires docstrings and comments to state the rule, never point at an ADR or ledger ID."""
    paths = sorted(LIBRARY_ROOT.rglob("*.py"))
    assert len(paths) >= MIN_LIBRARY_FILES, f"scan found only {len(paths)} library files"
    offenders = [
        hit
        for path in paths
        for hit in find_citations(
            path.read_text(encoding="utf-8"), str(path.relative_to(LIBRARY_ROOT.parent))
        )
    ]
    assert not offenders, "state the rule itself instead of citing it: " + ", ".join(offenders)


def test_citation_scan_flags_a_docstring_and_a_comment() -> None:
    """Reports a planted ADR in a docstring, a ledger ID in a comment and a docs path."""
    source = (
        '"""Module.\n\nSee ADR 0003.\n"""\n'
        "# bug UP-1.2-03\n"
        "def f() -> None:\n"
        '    """Reads docs/notes.md."""\n'
    )
    assert find_citations(source, "m.py") == ["m.py:3", "m.py:5", "m.py:7"]


def test_citation_scan_ignores_runtime_strings() -> None:
    """Leaves a plain string literal alone, since only docstrings and comments are scanned."""
    assert find_citations('MESSAGE = "see docs/guide for ADR 5"\n', "m.py") == []
