#!/usr/bin/env python3

"""
test_fuzz_clock.py --- fuzzes the clock for non-finite results; only E8sError may escape

Contains:
    TIMELINES: draws timelines of tempo sections, ramps and holds within the stored bounds, one
        section and one hold per count
    test_fuzz_clock_results_are_finite(): gives finite seconds and counts for every timeline
"""

import math

from hypothesis import given
from hypothesis import strategies as st

from e8s.core.caps import MAX_COUNTS, MIN_TEMPO
from e8s.core.types import Count, Seconds
from e8s.time.clock import count_to_seconds, seconds_to_count
from e8s.time.models import Hold, TempoSection, Timeline, TimelineMode
from tests.helpers import only_e8s_errors

MAX_TEMPO = 1e6
MAX_HOLD_S = 1e6
MAX_SECTIONS = 5

tempos = st.one_of(st.floats(MIN_TEMPO, MAX_TEMPO), st.sampled_from([MIN_TEMPO, MAX_TEMPO]))
section_counts = st.floats(0.0, float(MAX_COUNTS))
steady_sections = st.builds(
    lambda at, cpm: TempoSection(at_count=at, counts_per_minute=cpm), section_counts, tempos
)
ramped_sections = st.builds(
    lambda at, cpm, to, n: TempoSection(
        at_count=at, counts_per_minute=cpm, ramp_to=to, ramp_counts=n
    ),
    section_counts,
    tempos,
    tempos,
    st.floats(1e-3, float(MAX_COUNTS)),
)
holds = st.builds(
    lambda at, seconds: Hold(at_count=at, seconds=seconds),
    section_counts,
    st.floats(0.0, MAX_HOLD_S),
)
TIMELINES = st.builds(
    lambda first, rest, held: Timeline(
        mode=TimelineMode.COUNTS,
        tempos=tuple(
            sorted(
                {
                    s.at_count: s
                    for s in [TempoSection(at_count=0.0, counts_per_minute=first), *rest]
                }.values(),
                key=lambda s: s.at_count,
            )
        ),
        holds=tuple(sorted({h.at_count: h for h in held}.values(), key=lambda h: h.at_count)),
    ),
    tempos,
    st.lists(st.one_of(steady_sections, ramped_sections), max_size=MAX_SECTIONS),
    st.lists(holds, max_size=MAX_SECTIONS),
)


def _assert_clock_results_are_finite(timeline: Timeline, count: Count) -> None:
    seconds: Seconds = count_to_seconds(timeline, count)
    assert math.isfinite(seconds), f"seconds {seconds}"
    back = seconds_to_count(timeline, seconds)
    assert math.isfinite(back), f"count {back}"


@given(TIMELINES, section_counts)
def test_fuzz_clock_results_are_finite(timeline: Timeline, count: Count) -> None:
    """Gives finite seconds and a finite count back for every timeline within the bounds."""
    only_e8s_errors(lambda: _assert_clock_results_are_finite(timeline, count), (timeline, count))
