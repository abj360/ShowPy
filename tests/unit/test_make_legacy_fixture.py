#!/usr/bin/env python3

"""
test_make_legacy_fixture.py --- tests the script that writes the v0.2.0 repository fixture

Contains:
    test_regression_legacy_fixture_oracle_ignores_hash_seed: writes the same expected show under
        two hash seeds, with sets sorted
"""

from pathlib import Path

from tests.helpers import TWO_HASH_SEEDS, outputs_under_hash_seeds

ORACLE_SCRIPT = """
import json, os
from pathlib import Path
from scripts.make_legacy_fixture import EXPECTED_FILE, build_fixture
out = Path({root!r}) / os.environ["PYTHONHASHSEED"]
build_fixture(out)
print(json.dumps(json.loads((out / EXPECTED_FILE).read_text())["show"], sort_keys=True))
"""


def test_regression_legacy_fixture_oracle_ignores_hash_seed(tmp_path: Path) -> None:
    outputs = outputs_under_hash_seeds(ORACLE_SCRIPT.format(root=str(tmp_path)), TWO_HASH_SEEDS)
    assert outputs[0] == outputs[1]
    assert '"tags": ["horn", "lead"]' in outputs[0]
