#!/usr/bin/env python3

"""
test_make_legacy_fixture.py --- tests the script that writes the released-tag repository fixtures

Contains:
    test_regression_legacy_fixture_oracle_ignores_hash_seed: builds the same oracle show under two
        hash seeds, with sets sorted, for every show the script writes
"""

import pytest

from tests.helpers import TWO_HASH_SEEDS, outputs_under_hash_seeds

ORACLE_SCRIPT = """
import json
from scripts.make_legacy_fixture import fixture_shows, oracle_json, v030_show
show = v030_show() if {kind!r} == "v030" else fixture_shows({kind!r})[-1]
print(json.dumps(oracle_json(show), sort_keys=True))
"""
TAGGED = '"tags": ["horn", "lead"]'


@pytest.mark.parametrize("kind", ["seeded", "example", "v030"])
def test_regression_legacy_fixture_oracle_ignores_hash_seed(kind: str) -> None:
    outputs = outputs_under_hash_seeds(ORACLE_SCRIPT.format(kind=kind), TWO_HASH_SEEDS)
    assert outputs[0] == outputs[1]
    assert (TAGGED in outputs[0]) == (kind != "v030")
