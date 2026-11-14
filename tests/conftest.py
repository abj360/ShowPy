#!/usr/bin/env python3

"""
conftest.py --- shared pytest setup: Hypothesis profiles and the benchmark size tier

Contains:
    pytest_plugins: loads the defect ledger plugin
    HYPOTHESIS_PROFILES: settings for the ci (default), nightly and dev profiles
    TIERS: benchmark tier name to (spots, sets)
    BenchTier: the chosen benchmark tier's name and size
    pytest_addoption(): adds --tier to choose the benchmark size
    pytest_configure(): registers the Hypothesis profiles and loads the one HYPOTHESIS_PROFILE names
    tier(): fixture returning the tier chosen with --tier
"""

import os
from dataclasses import dataclass
from typing import Any

import pytest
from hypothesis import settings

pytest_plugins = ["tests.ledger_plugin"]

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

TIER_OPTION = "--tier"
DEFAULT_TIER = "M"
TIERS: dict[str, tuple[int, int]] = {
    "S": (20, 4),
    "M": (100, 20),
    "L": (300, 64),
    "XL": (600, 120),
}


@dataclass(frozen=True)
class BenchTier:
    """Names a benchmark size.

    Attributes:
        name: Tier name: S, M, L or XL.
        spots: Spots in the benchmark show.
        sets: Sets in the benchmark show.
    """

    name: str
    spots: int
    sets: int


def pytest_addoption(parser: pytest.Parser) -> None:
    """Adds --tier, the benchmark size: S, M (default), L or XL."""
    sizes = ", ".join(f"{name}={size}" for name, size in TIERS.items())
    parser.addoption(
        TIER_OPTION,
        action="store",
        default=DEFAULT_TIER,
        choices=sorted(TIERS),
        help=f"benchmark size tier as (spots, sets): {sizes}",
    )


def pytest_configure() -> None:
    """Registers the Hypothesis profiles and loads the one HYPOTHESIS_PROFILE names."""
    for name, values in HYPOTHESIS_PROFILES.items():
        settings.register_profile(name, **values)
    settings.load_profile(os.environ.get(PROFILE_ENV, DEFAULT_PROFILE))


@pytest.fixture(scope="session")
def tier(request: pytest.FixtureRequest) -> BenchTier:
    """Returns the benchmark tier chosen with --tier."""
    name = str(request.config.getoption(TIER_OPTION))
    spots, sets = TIERS[name]
    return BenchTier(name, spots, sets)
