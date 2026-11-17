#!/usr/bin/env python3

"""
test_golden_text.py --- compares text form slots to a human-reviewed fixture

Three texts (Latin caps, accented Latin with rotation and centering, Greek) lay out at fixed
heights and counts; their slots are stored rounded to 9 digits. Set SHOWPY_UPDATE_GOLDEN=1 to
rewrite tests/golden/fixtures/text_slots.json; the diff needs human review.

Contains:
    test_text_slots_match_reviewed_fixture: compares every slot of every text to the fixture
"""

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from showpy.choreo.forms import slots, text_form

pytestmark = pytest.mark.golden

FIXTURE = Path(__file__).parent / "fixtures" / "text_slots.json"
UPDATE_FLAG = "SHOWPY_UPDATE_GOLDEN"
HEIGHT_M = 4.0
AT = (0.0, 10.0)
DIGITS = 9
TOL = 1e-9
CASES: dict[str, dict[str, Any]] = {
    "go_team": {"text": "GO TEAM", "fonts": ["hershey-roman"], "n": 120},
    "equipe": {
        "text": "Équipe 2026!",
        "fonts": ["relief-singleline"],
        "n": 200,
        "rotation": 15.0,
        "align": "center",
        "anchor": "middle",
    },
    "greek": {"text": "ΑΒΓ", "fonts": ["hershey-greek"], "n": 45},
}


def _slots(case: dict[str, Any]) -> list[list[float]]:
    n = case["n"]
    options = {key: value for key, value in case.items() if key not in {"text", "n"}}
    form = text_form(
        "g", [f"S{i}" for i in range(n)], case["text"], height=HEIGHT_M, at=AT, n=n, **options
    )
    return [[round(float(v), DIGITS) for v in row] for row in slots(form)]


def test_text_slots_match_reviewed_fixture() -> None:
    current = {name: _slots(case) for name, case in CASES.items()}
    if os.environ.get(UPDATE_FLAG) == "1":
        FIXTURE.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    stored = json.loads(FIXTURE.read_text())
    assert stored.keys() == current.keys()
    for name, rows in stored.items():
        assert len(rows) == CASES[name]["n"]
        assert np.allclose(rows, current[name], atol=TOL, rtol=0.0), name
