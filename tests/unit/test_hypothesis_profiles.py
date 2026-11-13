#!/usr/bin/env python3

"""
test_hypothesis_profiles.py --- checks the Hypothesis profiles registered in tests/conftest.py
"""

import os
from datetime import timedelta

from hypothesis import settings

from tests.conftest import DEFAULT_PROFILE, HYPOTHESIS_PROFILES, PROFILE_ENV


def test_profiles_are_registered_with_their_values() -> None:
    ci = settings.get_profile("ci")
    assert (ci.derandomize, ci.print_blob, ci.deadline) == (True, True, timedelta(milliseconds=500))
    nightly = settings.get_profile("nightly")
    assert (nightly.max_examples, nightly.deadline) == (2000, None)
    assert set(HYPOTHESIS_PROFILES) == {"ci", "nightly", "dev"}


def test_active_profile_follows_the_environment() -> None:
    expected = settings.get_profile(os.environ.get(PROFILE_ENV, DEFAULT_PROFILE))
    active = settings()
    assert active.max_examples == expected.max_examples
    assert active.derandomize == expected.derandomize
