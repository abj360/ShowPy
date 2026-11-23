#!/usr/bin/env python3

"""
test_read_properties.py --- property tests: the floor readers agree with PathEngine exactly

Contains:
    test_frames_equal_repeated_sample: every frame equals PathEngine.sample at its count
    test_holds_keep_spots_still: frames inside a hold share its count and every spot stands still
"""

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from e8s.choreo.engine import PathEngine
from e8s.choreo.read import frames
from e8s.show.models import Show
from e8s.time.clock import count_to_seconds
from tests.strategies.show import shows

FRAME_RATES = (1.0, 7.5, 30.0)
MAX_SPOTS = 6
MAX_SETS = 4


@st.composite
def _ranges(draw: st.DrawFn, show: Show) -> tuple[float, float]:
    total = sum(s.counts for s in show.sets[1:])
    start = draw(st.floats(min_value=0.0, max_value=total))
    end = draw(st.floats(min_value=start, max_value=total))
    return start, end


@st.composite
def _reads(draw: st.DrawFn) -> tuple[Show, float, float, float]:
    show = draw(shows(max_spots=MAX_SPOTS, max_sets=MAX_SETS))
    start, end = draw(_ranges(show))
    return show, start, end, draw(st.sampled_from(FRAME_RATES))


@settings(max_examples=60, deadline=None)
@given(_reads())
def test_frames_equal_repeated_sample(read: tuple[Show, float, float, float]) -> None:
    show, start, end, fps = read
    framed = frames(show, start, end, fps=fps)
    engine = PathEngine(show)
    for row, count in enumerate(framed.counts.tolist()):
        states = {state.entity: state for state in engine.sample(count).states}
        for column, spot in enumerate(framed.spots):
            state = states[spot]
            assert framed.x[row, column] == state.position[0]
            assert framed.y[row, column] == state.position[1]
            assert framed.facing[row, column] == state.facing


@settings(max_examples=40, deadline=None)
@given(shows(max_spots=MAX_SPOTS, max_sets=MAX_SETS), st.sampled_from(FRAME_RATES))
def test_holds_keep_spots_still(show: Show, fps: float) -> None:
    total = sum(s.counts for s in show.sets[1:])
    framed = frames(show, 0.0, total, fps=fps)
    for hold in show.timeline.holds:
        if not 0.0 <= hold.at_count < total:
            continue
        begins = count_to_seconds(show.timeline, hold.at_count)
        inside = (framed.seconds >= begins) & (framed.seconds <= begins + hold.seconds)
        assert np.all(framed.counts[inside] == hold.at_count)
        for values in (framed.x, framed.y, framed.facing):
            assert np.all(values[inside] == values[inside][:1])
