#!/usr/bin/env python3

"""
test_docs.py --- tests that ShowPy's own docs and code describe a standalone library

Contains:
    test_regression_docs_do_not_name_eight(): checks that no shipped doc or source names Eight
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_NAME = re.compile(r"\bEight\b")
STANDALONE_DOCS = ("CLAUDE.md", "README.md", "CONTRIBUTING.md", "pyproject.toml")


def test_regression_docs_do_not_name_eight() -> None:
    paths = [REPO_ROOT / name for name in STANDALONE_DOCS]
    paths += sorted((REPO_ROOT / "showpy").rglob("*.py"))
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in paths
        if PRODUCT_NAME.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
