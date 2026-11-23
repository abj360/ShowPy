#!/usr/bin/env python3

"""
test_golden_opener.py --- replays the golden opener and compares every half-count to its fixture

Set E8S_UPDATE_GOLDEN=1 to rewrite the fixture; the diff then needs human review.
"""

import json
import os
from pathlib import Path
from typing import Any, TypedDict

import pytest

from e8s.choreo.engine import PathEngine
from e8s.core.ids import SpotId
from tests.golden.opener import opener_show

FIXTURE = Path(__file__).parent / "fixtures" / "opener_frames.json"
UPDATE_FLAG = "E8S_UPDATE_GOLDEN"
PER_COUNT = 2
DIGITS = 9
TOL = 1e-9


class FrameRecord(TypedDict):
    """Holds one replayed frame: global count and per-spot [x, y, facing, on_field]."""

    t: float
    # Any: each state mixes floats (x, y, facing) with a bool (on_field), as stored in JSON.
    states: dict[SpotId, list[Any]]


def _replay() -> list[FrameRecord]:
    show = opener_show()
    frames: list[FrameRecord] = []
    for frame in PathEngine(show).frames(0.0, 24.0, per_count=PER_COUNT):
        states = {
            s.entity: [
                round(s.position[0], DIGITS),
                round(s.position[1], DIGITS),
                round(s.facing, DIGITS),
                s.on_field,
            ]
            for s in frame.states
        }
        frames.append({"t": frame.t, "states": states})
    return frames


@pytest.mark.golden
def test_opener_matches_reviewed_fixture() -> None:
    frames = _replay()
    if os.environ.get(UPDATE_FLAG) == "1":
        FIXTURE.write_text(json.dumps(frames, indent=1, sort_keys=True) + "\n")
    expected: list[FrameRecord] = json.loads(FIXTURE.read_text())
    assert len(frames) == len(expected)
    for got, want in zip(frames, expected, strict=True):
        assert got["t"] == pytest.approx(want["t"], abs=TOL)
        for entity, state in want["states"].items():
            assert got["states"][entity][:3] == pytest.approx(state[:3], abs=TOL), (
                got["t"],
                entity,
            )
            assert got["states"][entity][3] == state[3]
