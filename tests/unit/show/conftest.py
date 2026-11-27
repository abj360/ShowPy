#!/usr/bin/env python3

"""
conftest.py --- shares fixtures among the show tests

Contains:
    no_reread_pauses: skips the pauses a torn-file reread waits, so damaged roots check at once
"""

import time

import pytest


@pytest.fixture
def no_reread_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces time.sleep with a no-op for one test."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
