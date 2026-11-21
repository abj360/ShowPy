#!/usr/bin/env python3

"""
test_clock.py --- tests tempo lookup, count/seconds conversion with ramps and holds, and labels

Contains:
    test_*(): one behavior of showpy.time.clock each
    test_regression_describe_count_rounds_tenths_half_away_from_zero: rounds tenths of a second half
        away from zero, so 7.25 s prints as 0:07.3
    test_regression_unsorted_tempo_sections_are_sorted_or_rejected: converts counts as if sections
        were sorted, or rejects them
    test_regression_seconds_to_count_rejects_nan: rejects NaN seconds instead of returning 0.0
    test_regression_tempo_at_rejects_nan: rejects a NaN count with a ShowPyError, not an IndexError
    test_regression_timeline_clock_is_fresh_after_replace: drops the cached clock on replace and
        model_copy, so no conversion uses stale pieces
    test_regression_compiled_clock_refuses_what_the_models_refuse: refuses a tempo, ramp or hold
        value the models reject when it arrives through model_construct
    test_regression_describe_count_prints_large_counts_in_full: prints 1234567.5 rather than
        `1.23457e+06`
"""

import math
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from showpy.core.errors import ShowPyError, TimelineError, TimeRangeError
from showpy.core.util import approx_equal
from showpy.time.clock import (
    count_to_seconds,
    describe_count,
    seconds_per_count,
    seconds_to_count,
    tempo_at,
)
from showpy.time.models import Hold, MeasureSetting, TempoSection, Timeline, TimelineMode
from showpy.time.timeline import add_hold, add_tempo, new_timeline, set_measures

TOL = 1e-9
LN2 = math.log(2.0)


def _counts(cpm: float = 120.0) -> Timeline:
    return new_timeline(TimelineMode.COUNTS, counts_per_minute=cpm)


def _ramp(start: float, end: float, counts: float) -> Timeline:
    section = TempoSection(at_count=0.0, counts_per_minute=start, ramp_to=end, ramp_counts=counts)
    return Timeline(mode=TimelineMode.COUNTS, tempos=(section,))


def test_constant_tempo_conversion() -> None:
    tl = _counts(120.0)
    assert approx_equal(count_to_seconds(tl, 8.0), 4.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, 4.0), 8.0, abs_tol=TOL)
    assert approx_equal(seconds_per_count(tl, 3.0), 0.5, abs_tol=TOL)


def test_count_zero_is_second_zero() -> None:
    assert count_to_seconds(_counts(), 0.0) == 0.0
    assert seconds_to_count(_counts(), 0.0) == 0.0


def test_multiple_sections_accumulate() -> None:
    tl = add_tempo(_counts(120.0), TempoSection(at_count=16.0, counts_per_minute=60.0))
    assert approx_equal(count_to_seconds(tl, 24.0), 16.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, 12.0), 20.0, abs_tol=TOL)
    assert tempo_at(tl, 15.999) == 120.0
    assert tempo_at(tl, 16.0) == 60.0


def test_ramp_up_tempo_is_linear_in_counts_then_holds_target() -> None:
    tl = _ramp(60.0, 120.0, 60.0)
    assert approx_equal(tempo_at(tl, 0.0), 60.0, abs_tol=TOL)
    assert approx_equal(tempo_at(tl, 15.0), 75.0, abs_tol=TOL)
    assert approx_equal(tempo_at(tl, 60.0), 120.0, abs_tol=TOL)
    assert approx_equal(tempo_at(tl, 200.0), 120.0, abs_tol=TOL)


def test_ramp_up_seconds_use_log_formula() -> None:
    tl = _ramp(60.0, 120.0, 60.0)
    assert approx_equal(count_to_seconds(tl, 60.0), 60.0 * LN2, abs_tol=TOL)
    assert approx_equal(count_to_seconds(tl, 72.0), 60.0 * LN2 + 6.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, 60.0 * LN2 + 6.0), 72.0, abs_tol=TOL)


def test_ramp_down_seconds_and_inverse() -> None:
    tl = _ramp(120.0, 60.0, 60.0)
    assert approx_equal(tempo_at(tl, 30.0), 90.0, abs_tol=TOL)
    expected = 60.0 * math.log(4.0 / 3.0)
    assert approx_equal(count_to_seconds(tl, 30.0), expected, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, expected), 30.0, abs_tol=TOL)
    assert approx_equal(count_to_seconds(tl, 60.0), 60.0 * LN2, abs_tol=TOL)


def test_ramp_to_same_tempo_behaves_as_constant() -> None:
    tl = _ramp(90.0, 90.0, 16.0)
    assert approx_equal(count_to_seconds(tl, 30.0), 20.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, 20.0), 30.0, abs_tol=TOL)


def test_ramp_is_cut_short_by_next_section() -> None:
    tl = add_tempo(_ramp(60.0, 120.0, 60.0), TempoSection(at_count=30.0, counts_per_minute=200.0))
    assert approx_equal(tempo_at(tl, 29.0), 89.0, abs_tol=TOL)
    assert tempo_at(tl, 40.0) == 200.0
    at_cut = 60.0 * math.log(1.5)
    assert approx_equal(count_to_seconds(tl, 30.0), at_cut, abs_tol=TOL)
    assert approx_equal(count_to_seconds(tl, 40.0), at_cut + 3.0, abs_tol=TOL)


def test_hold_pauses_counts() -> None:
    tl = add_hold(_counts(120.0), Hold(at_count=8.0, seconds=3.0))
    assert approx_equal(count_to_seconds(tl, 8.0), 4.0, abs_tol=TOL)
    assert approx_equal(count_to_seconds(tl, 10.0), 8.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, 8.0), 10.0, abs_tol=TOL)


def test_inverse_returns_hold_count_across_whole_hold() -> None:
    tl = add_hold(_counts(120.0), Hold(at_count=8.0, seconds=3.0))
    for seconds in (4.0, 5.5, 7.0):
        assert approx_equal(seconds_to_count(tl, seconds), 8.0, abs_tol=TOL)
    assert seconds_to_count(tl, 7.0 + 1e-6) > 8.0


def test_hold_at_count_zero() -> None:
    tl = add_hold(_counts(120.0), Hold(at_count=0.0, seconds=2.0))
    assert count_to_seconds(tl, 0.0) == 0.0
    assert approx_equal(count_to_seconds(tl, 2.0), 3.0, abs_tol=TOL)
    assert seconds_to_count(tl, 1.0) == 0.0
    assert approx_equal(seconds_to_count(tl, 3.0), 2.0, abs_tol=TOL)


def test_hold_inside_ramp() -> None:
    tl = add_hold(_ramp(60.0, 120.0, 60.0), Hold(at_count=30.0, seconds=5.0))
    at_hold = 60.0 * math.log(1.5)
    assert approx_equal(count_to_seconds(tl, 30.0), at_hold, abs_tol=TOL)
    assert approx_equal(count_to_seconds(tl, 60.0), 60.0 * LN2 + 5.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, at_hold + 2.5), 30.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, 60.0 * LN2 + 5.0), 60.0, abs_tol=TOL)


def test_hold_at_tempo_change_boundary() -> None:
    tl = add_tempo(_counts(120.0), TempoSection(at_count=8.0, counts_per_minute=60.0))
    tl = add_hold(tl, Hold(at_count=8.0, seconds=1.0))
    assert approx_equal(count_to_seconds(tl, 8.0), 4.0, abs_tol=TOL)
    assert approx_equal(count_to_seconds(tl, 9.0), 6.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, 4.5), 8.0, abs_tol=TOL)


def test_seconds_per_count_follows_ramp() -> None:
    assert approx_equal(seconds_per_count(_ramp(60.0, 120.0, 60.0), 30.0), 60.0 / 90.0, abs_tol=TOL)


def test_seconds_mode_defaults_to_one_count_per_second() -> None:
    tl = new_timeline(TimelineMode.SECONDS)
    assert approx_equal(count_to_seconds(tl, 10.0), 10.0, abs_tol=TOL)
    assert approx_equal(seconds_to_count(tl, 7.5), 7.5, abs_tol=TOL)
    assert tempo_at(tl, 3.0) == 60.0


def test_counts_mode_without_tempo_raises() -> None:
    with pytest.raises(TimelineError):
        count_to_seconds(Timeline(mode=TimelineMode.COUNTS), 1.0)


def test_cue_mode_without_tempo_raises() -> None:
    with pytest.raises(TimelineError) as info:
        seconds_to_count(new_timeline(TimelineMode.CUES), 1.0)
    assert "cue" in info.value.hint


def test_first_tempo_after_zero_raises() -> None:
    late = TempoSection(at_count=4.0, counts_per_minute=120.0)
    with pytest.raises(TimelineError):
        tempo_at(Timeline.model_construct(mode=TimelineMode.COUNTS, tempos=(late,)), 5.0)


def test_half_specified_ramp_raises() -> None:
    bad = TempoSection.model_construct(at_count=0.0, counts_per_minute=120.0, ramp_to=140.0)
    with pytest.raises(TimelineError):
        count_to_seconds(Timeline.model_construct(mode=TimelineMode.COUNTS, tempos=(bad,)), 4.0)


def test_negative_inputs_raise_time_range_error() -> None:
    with pytest.raises(TimeRangeError):
        count_to_seconds(_counts(), -1.0)
    with pytest.raises(TimeRangeError):
        seconds_to_count(_counts(), -0.5)
    with pytest.raises(TimeRangeError):
        tempo_at(_counts(), -2.0)


def test_describe_count_with_measures() -> None:
    tl = set_measures(_counts(168.0), MeasureSetting(counts_per_measure=4.0))
    assert describe_count(tl, 130.0) == "count 130 (m. 33), 0:46.4, 168 cpm"


def test_describe_count_without_measures_and_with_fraction() -> None:
    tl = add_hold(_counts(120.0), Hold(at_count=0.0, seconds=60.0))
    assert describe_count(tl, 24.6) == "count 24.6, 1:12.3, 120 cpm"


def test_describe_count_rounds_into_next_minute() -> None:
    assert describe_count(_counts(120.0), 119.99) == "count 119.99, 1:00.0, 120 cpm"


def test_describe_count_rounds_ramp_tempo() -> None:
    assert describe_count(_ramp(60.0, 61.0, 3.0), 1.0).endswith("60.3 cpm")


def test_regression_describe_count_rounds_tenths_half_away_from_zero() -> None:
    """Rounds tenths of a second half away from zero, so 7.25 s prints as 0:07.3."""
    from showpy.time.clock import describe_count
    from showpy.time.timeline import new_timeline

    timeline = new_timeline(TimelineMode.COUNTS, counts_per_minute=240.0)
    assert "0:07.3" in describe_count(timeline, 29.0)


REJECTED = (ShowPyError, PydanticValidationError)
SIX_SECONDS = 6.0
BIG_COUNT = 1234567.5


def _steady(cpm: float = 120.0) -> Timeline:
    return new_timeline(TimelineMode.COUNTS, cpm)


def test_regression_unsorted_tempo_sections_are_sorted_or_rejected() -> None:
    """Converts counts as if sections were sorted, or rejects them (audit 9.3)."""
    try:
        timeline = Timeline(
            mode=TimelineMode.COUNTS,
            tempos=(
                TempoSection(at_count=0.0, counts_per_minute=120.0),
                TempoSection(at_count=16.0, counts_per_minute=60.0),
                TempoSection(at_count=8.0, counts_per_minute=240.0),
            ),
        )
        seconds = count_to_seconds(timeline, 16.0)
    except REJECTED:
        return
    assert approx_equal(seconds, SIX_SECONDS, abs_tol=1e-9)


def test_regression_seconds_to_count_rejects_nan() -> None:
    """Rejects NaN seconds instead of returning 0.0 (audit 9.3)."""
    with pytest.raises(ShowPyError):
        seconds_to_count(_steady(), math.nan)


def test_regression_tempo_at_rejects_nan() -> None:
    """Rejects a NaN count with a ShowPyError, not an IndexError (audit 9.3)."""
    with pytest.raises(ShowPyError):
        tempo_at(_steady(), math.nan)


@pytest.mark.defect("UP-9.3-98")
@pytest.mark.xfail(strict=True, reason="UP-9.3-98")
def test_regression_describe_count_prints_large_counts_in_full() -> None:
    """Prints 1234567.5 rather than `1.23457e+06` (audit 9.3)."""
    assert "count 1234567.5," in describe_count(_steady(), BIG_COUNT)


def test_regression_timeline_clock_is_fresh_after_replace() -> None:
    """Rebuilds the clock for every copy; a cached clock must never outlive its pieces."""
    base = _counts(120.0)
    assert count_to_seconds(base, 8.0) == 4.0
    slower = TempoSection(at_count=0.0, counts_per_minute=60.0)
    assert count_to_seconds(base.replace(tempos=(slower,)), 8.0) == 8.0
    assert count_to_seconds(base.model_copy(update={"tempos": (slower,)}), 8.0) == 8.0
    held = add_hold(base, Hold(at_count=4.0, seconds=1.0))
    assert count_to_seconds(held, 8.0) == 5.0
    assert count_to_seconds(base, 8.0) == 4.0


def test_clock_is_built_once_per_timeline() -> None:
    tl = _counts()
    assert tl.clock is tl.clock


# Any: bad values are exactly what the models refuse, so they are not of the declared types.
BAD_SECTIONS: list[dict[str, Any]] = [
    {"counts_per_minute": 0.0},
    {"counts_per_minute": -90.0},
    {"counts_per_minute": math.nan},
    {"counts_per_minute": math.inf},
    {"ramp_to": 0.0, "ramp_counts": 4.0},
    {"ramp_to": -5.0, "ramp_counts": 4.0},
    {"ramp_to": math.nan, "ramp_counts": 4.0},
    {"ramp_to": math.inf, "ramp_counts": 4.0},
    {"ramp_to": 90.0, "ramp_counts": math.nan},
    {"ramp_to": 90.0, "ramp_counts": math.inf},
    {"ramp_to": 90.0, "ramp_counts": -4.0},
    {"ramp_to": 90.0, "ramp_counts": 0.0},
    {"ramp_to": 90.0},
    {"ramp_counts": 4.0},
]


@pytest.mark.parametrize("fields", BAD_SECTIONS)
def test_regression_compiled_clock_refuses_what_the_models_refuse(fields: dict[str, Any]) -> None:
    """Raises ShowPyError, never ZeroDivisionError, NaN or a hang, for a skipped model check."""
    values: dict[str, Any] = {"at_count": 0.0, "counts_per_minute": 120.0}
    values.update(fields)
    section = TempoSection.model_construct(**values)
    tl = Timeline.model_construct(mode=TimelineMode.COUNTS, tempos=(section,))
    with pytest.raises(ShowPyError):
        count_to_seconds(tl, 4.0)
    with pytest.raises(ShowPyError):
        seconds_to_count(tl, 4.0)
    with pytest.raises(ShowPyError):
        tempo_at(tl, 4.0)


def test_clock_refuses_overlapping_holds() -> None:
    holds = (Hold(at_count=4.0, seconds=1.0), Hold(at_count=4.0, seconds=2.0))
    tl = Timeline.model_construct(mode=TimelineMode.COUNTS, tempos=_counts().tempos, holds=holds)
    with pytest.raises(TimelineError):
        count_to_seconds(tl, 8.0)


def test_clock_refuses_tempo_sections_out_of_order() -> None:
    sections = (
        TempoSection(at_count=0.0, counts_per_minute=120.0),
        TempoSection(at_count=16.0, counts_per_minute=60.0),
        TempoSection(at_count=8.0, counts_per_minute=240.0),
    )
    tl = Timeline.model_construct(mode=TimelineMode.COUNTS, tempos=sections)
    with pytest.raises(TimelineError):
        count_to_seconds(tl, 16.0)


def test_conversions_round_trip_across_holds_and_ramps() -> None:
    tl = add_hold(_ramp(60.0, 120.0, 16.0), Hold(at_count=8.0, seconds=2.0))
    tl = add_tempo(tl, TempoSection(at_count=24.0, counts_per_minute=90.0))
    for count in (0.0, 3.5, 8.0, 8.5, 16.0, 20.0, 24.0, 30.0):
        assert approx_equal(seconds_to_count(tl, count_to_seconds(tl, count)), count, abs_tol=TOL)
