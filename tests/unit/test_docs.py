#!/usr/bin/env python3

"""
test_docs.py --- tests that ShowPy's own docs and code describe a standalone library

The check needs a source checkout; the Docker image tests the installed wheel without the
docs or the showpy source tree, so there it is skipped.

Contains:
    test_regression_docs_do_not_name_eight(): checks no doc or source names Eight, Eight-agent
        or stagecheck
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSUMER_NAMES = re.compile(r"\b(Eight|Eight-agent|stagecheck)\b")
# The Docker image copies pyproject.toml but not the showpy source, so both mark a checkout.
IS_SOURCE_CHECKOUT = (REPO_ROOT / "pyproject.toml").is_file() and (
    REPO_ROOT / "showpy" / "__init__.py"
).is_file()
STANDALONE_DOCS = (
    "CLAUDE.md",
    "README.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "docs/showpy-api-reference.md",
)


@pytest.mark.skipif(not IS_SOURCE_CHECKOUT, reason="needs a source checkout")
def test_regression_docs_do_not_name_eight() -> None:
    """Checks that no shipped doc or source names Eight, Eight-agent or stagecheck."""
    paths = [REPO_ROOT / name for name in STANDALONE_DOCS]
    paths += sorted((REPO_ROOT / "showpy").rglob("*.py"))
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in paths
        if CONSUMER_NAMES.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
