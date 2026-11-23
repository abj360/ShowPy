#!/usr/bin/env python3

"""
time.py --- strategies for count timelines with tempo changes, ramps and holds

Contains:
    timelines(): draws a counts timeline with tempo sections, ramps and holds inside a length
"""

from hypothesis import strategies as st

from e8s.time.models import Hold, TempoSection, Timeline, TimelineMode
from e8s.time.timeline import add_hold, add_tempo, new_timeline

MIN_TEMPO = 60.0
MAX_TEMPO = 200.0
MAX_SECTIONS = 3
MAX_HOLDS = 2
MAX_HOLD_S = 4.0
MAX_RAMP_COUNTS = 16.0

tempos = st.floats(min_value=MIN_TEMPO, max_value=MAX_TEMPO, allow_nan=False)


@st.composite
def _section(draw: st.DrawFn, total: float) -> TempoSection:
    at = draw(st.floats(min_value=0.0, max_value=total, exclude_min=True, allow_nan=False))
    if not draw(st.booleans()):
        return TempoSection(at_count=at, counts_per_minute=draw(tempos))
    ramp = draw(st.floats(min_value=0.5, max_value=MAX_RAMP_COUNTS, allow_nan=False))
    return TempoSection(
        at_count=at, counts_per_minute=draw(tempos), ramp_to=draw(tempos), ramp_counts=ramp
    )


@st.composite
def timelines(draw: st.DrawFn, total: float) -> Timeline:
    """Draws a counts timeline whose tempo sections and holds fall inside `total` counts.

    Args:
        draw: Hypothesis draw function.
        total: Length of the show in counts; sections and holds start inside it.
    """
    tl = new_timeline(TimelineMode.COUNTS, counts_per_minute=draw(tempos))
    if total <= 0:
        return tl
    for section in draw(st.lists(_section(total), max_size=MAX_SECTIONS)):
        tl = add_tempo(tl, section)
    hold_counts = st.floats(min_value=0.0, max_value=total, allow_nan=False)
    seconds = st.floats(min_value=0.0, max_value=MAX_HOLD_S, allow_nan=False)
    for at, length in draw(st.lists(st.tuples(hold_counts, seconds), max_size=MAX_HOLDS)):
        tl = add_hold(tl, Hold(at_count=at, seconds=length))
    return tl
