#!/usr/bin/env python3

"""
test_engine_properties.py --- property tests: paths end on targets and segments stay in tolerance
"""

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.choreo.engine import PathEngine
from showpy.choreo.models import Easing, EasingKind, PathRow, PathStyle, Waypoint
from showpy.core.util import approx_equal
from tests.fixtures.motion_shows import TID, with_rows

EASINGS: list[EasingKind] = [
    "linear",
    "ease_in",
    "ease_out",
    "ease_in_out",
    "hold_then_burst",
    "steps",
]
TOL = 0.01


@st.composite
def rows(draw: st.DrawFn) -> PathRow:
    start = draw(st.floats(min_value=0.0, max_value=5.0))
    end = draw(st.floats(min_value=start + 0.5, max_value=8.0))
    style = draw(st.sampled_from([PathStyle.STRAIGHT, PathStyle.POLYLINE, PathStyle.CURVE]))
    n = draw(st.integers(min_value=0, max_value=3))
    coords = st.floats(min_value=-10.0, max_value=10.0)
    waypoints = tuple(Waypoint(x=draw(coords), y=draw(coords)) for _ in range(n))
    easing = Easing(kind=draw(st.sampled_from(EASINGS)), strength=draw(st.floats(1.0, 4.0)))
    return PathRow(
        entity="T1", style=style, start=start, end=end, waypoints=waypoints, easing=easing
    )


@settings(max_examples=60, deadline=None)
@given(rows())
def test_every_path_ends_on_its_target(row: PathRow) -> None:
    engine = PathEngine(with_rows({"T1": row}))
    assert approx_equal(engine.position("T1", 8.0, local=TID), (0.0, 9.0), tol=1e-6)
    assert approx_equal(engine.position("T1", 0.0, local=TID), (0.0, 5.0), tol=1e-6)


@settings(max_examples=150, deadline=None)
@given(rows())
def test_segments_track_positions_within_tolerance(row: PathRow) -> None:
    engine = PathEngine(with_rows({"T1": row}))
    for seg in (s for s in engine.segments(TID, tol=TOL) if s.entity == "T1"):
        for share in np.linspace(0.1, 0.9, 9):
            t = seg.t0 + share * (seg.t1 - seg.t0)
            linear = np.asarray(seg.p0) + share * (np.asarray(seg.p1) - np.asarray(seg.p0))
            actual = engine.position("T1", t, local=TID)
            assert np.linalg.norm(actual - linear) <= TOL + 1e-9
