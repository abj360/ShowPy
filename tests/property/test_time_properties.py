#!/usr/bin/env python3

"""
test_time_properties.py --- checks count/seconds round-trips and monotonicity on random timelines
"""

from hypothesis import given
from hypothesis import strategies as st

from showpy.core.util import approx_equal
from showpy.time.clock import count_to_seconds, seconds_to_count
from showpy.time.models import Hold, TempoSection, Timeline, TimelineMode

ROUND_TRIP_TOL = 1e-6
MAX_COUNT = 512.0

tempo_values = st.floats(min_value=20.0, max_value=300.0)
counts = st.floats(min_value=0.0, max_value=MAX_COUNT)


@st.composite
def tempo_sections(draw: st.DrawFn) -> tuple[TempoSection, ...]:
    starts = draw(st.lists(st.floats(min_value=1.0, max_value=MAX_COUNT), max_size=4, unique=True))
    sections = []
    for at in [0.0, *sorted(starts)]:
        has_ramp = draw(st.booleans())
        sections.append(
            TempoSection(
                at_count=at,
                counts_per_minute=draw(tempo_values),
                ramp_to=draw(tempo_values) if has_ramp else None,
                ramp_counts=draw(st.floats(min_value=0.5, max_value=64.0)) if has_ramp else None,
            )
        )
    return tuple(sections)


@st.composite
def timelines(draw: st.DrawFn) -> Timeline:
    hold_counts = draw(st.lists(counts, max_size=3, unique=True))
    holds = tuple(
        Hold(at_count=at, seconds=draw(st.floats(min_value=0.0, max_value=10.0)))
        for at in sorted(hold_counts)
    )
    return Timeline(mode=TimelineMode.COUNTS, tempos=draw(tempo_sections()), holds=holds)


@given(timelines(), counts)
def test_count_round_trips_through_seconds(tl: Timeline, count: float) -> None:
    assert approx_equal(
        seconds_to_count(tl, count_to_seconds(tl, count)), count, abs_tol=ROUND_TRIP_TOL
    )


@given(timelines(), counts, counts)
def test_count_to_seconds_is_monotonic(tl: Timeline, a: float, b: float) -> None:
    low, high = sorted((a, b))
    assert count_to_seconds(tl, low) <= count_to_seconds(tl, high)


@given(timelines(), st.floats(min_value=0.0, max_value=600.0), st.floats(0.0, 600.0))
def test_seconds_to_count_is_monotonic(tl: Timeline, a: float, b: float) -> None:
    low, high = sorted((a, b))
    assert seconds_to_count(tl, low) <= seconds_to_count(tl, high)


@given(timelines(), st.floats(min_value=0.0, max_value=600.0))
def test_count_found_for_seconds_never_lies_later(tl: Timeline, seconds: float) -> None:
    assert count_to_seconds(tl, seconds_to_count(tl, seconds)) <= seconds + ROUND_TRIP_TOL
