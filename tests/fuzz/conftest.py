#!/usr/bin/env python3

"""
conftest.py --- shared setup for the fuzz targets under tests/fuzz

Contains:
    pytest_collection_modifyitems(): marks every test collected here as a fuzz test
"""

from collections.abc import Iterable
from pathlib import Path

import pytest

FUZZ_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: Iterable[pytest.Item]) -> None:
    """Marks each test under tests/fuzz as fuzz, so default runs skip it without per-test marks."""
    for item in items:
        if FUZZ_DIR in item.path.parents:
            item.add_marker(pytest.mark.fuzz)
