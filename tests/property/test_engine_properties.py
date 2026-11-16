#!/usr/bin/env python3

"""
test_engine_properties.py --- property tests: paths end on targets and segments stay in tolerance

Contains:
    test_every_path_ends_on_its_target: starts and ends every drawn path on its placements
    test_segments_track_positions_within_tolerance: keeps waypoint-path segments within tol
    test_segments_track_periodic_custom_motions_within_tolerance: keeps segments of a weave or
        zigzag with 1 to 8 cycles within tol at every probe
"""

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.choreo.engine import PathEngine
from showpy.choreo.engine_compile import plan_positions
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
PROBE_SHARES = np.linspace(0.1, 0.9, 9)
MIN_HALF_CYCLES = 2
MAX_HALF_CYCLES = 16
PERIODIC_MOTIONS = ["weave", "zigzag"]


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


@st.composite
def periodic_rows(draw: st.DrawFn) -> PathRow:
    cycles = draw(st.integers(MIN_HALF_CYCLES, MAX_HALF_CYCLES)) / 2
    amplitude = draw(st.floats(min_value=0.1, max_value=2.0))
    easing = Easing(kind=draw(st.sampled_from(EASINGS)), strength=draw(st.floats(1.0, 4.0)))
    return PathRow(
        entity="T1",
        style=PathStyle.CUSTOM,
        motion_fn=draw(st.sampled_from(PERIODIC_MOTIONS)),
        motion_params={"cycles": cycles, "amplitude": amplitude},
        easing=easing,
    )


@settings(max_examples=40, deadline=None)
@given(periodic_rows())
def test_segments_track_periodic_custom_motions_within_tolerance(row: PathRow) -> None:
    engine = PathEngine(with_rows({"T1": row}))
    plan = engine.compile(TID).plans["T1"]
    for seg in engine.entity_segments("T1", TID, tol=TOL):
        taus = seg.t0 + PROBE_SHARES * (seg.t1 - seg.t0)
        linear = np.asarray(seg.p0) + np.outer(PROBE_SHARES, np.subtract(seg.p1, seg.p0))
        actual = plan_positions(plan, taus)
        assert np.max(np.linalg.norm(actual - linear, axis=1)) <= TOL + 1e-9
