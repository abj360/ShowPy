#!/usr/bin/env python3

"""
conftest.py --- shared pytest setup: Hypothesis profiles

Contains:
    HYPOTHESIS_PROFILES: settings for the ci (default), nightly and dev profiles
    pytest_configure(): registers the Hypothesis profiles and loads the one HYPOTHESIS_PROFILE names
"""

import os
from typing import Any

import pytest
from hypothesis import settings

PROFILE_ENV = "HYPOTHESIS_PROFILE"
DEFAULT_PROFILE = "ci"
CI_DEADLINE_MS = 500
NIGHTLY_EXAMPLES = 2000
DEV_EXAMPLES = 10
# Any: Hypothesis settings take keyword values of mixed types.
HYPOTHESIS_PROFILES: dict[str, dict[str, Any]] = {
    "ci": {"derandomize": True, "print_blob": True, "deadline": CI_DEADLINE_MS},
    "nightly": {"max_examples": NIGHTLY_EXAMPLES, "deadline": None},
    "dev": {"max_examples": DEV_EXAMPLES},
}


def pytest_configure(config: pytest.Config) -> None:
    """Registers the Hypothesis profiles and loads the one HYPOTHESIS_PROFILE names."""
    for name, values in HYPOTHESIS_PROFILES.items():
        settings.register_profile(name, **values)
    settings.load_profile(os.environ.get(PROFILE_ENV, DEFAULT_PROFILE))
