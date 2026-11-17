#!/usr/bin/env python3

"""
test_make_legacy_fixture.py --- tests the script that writes the v0.2.0 repository fixture

Contains:
    test_regression_legacy_fixture_oracle_ignores_hash_seed: builds the same oracle show under two
        hash seeds, with sets sorted
"""

from tests.helpers import TWO_HASH_SEEDS, outputs_under_hash_seeds

ORACLE_SCRIPT = """
import json
from scripts.make_legacy_fixture import fixture_shows, oracle_json
print(json.dumps(oracle_json(fixture_shows()[-1]), sort_keys=True))
"""


def test_regression_legacy_fixture_oracle_ignores_hash_seed() -> None:
    outputs = outputs_under_hash_seeds(ORACLE_SCRIPT, TWO_HASH_SEEDS)
    assert outputs[0] == outputs[1]
    assert '"tags": ["horn", "lead"]' in outputs[0]
