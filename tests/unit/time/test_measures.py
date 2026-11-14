#!/usr/bin/env python3

"""
test_measures.py --- tests 1-based measure labels, measure lookups and mid-show length changes

Contains:
    test_*(): one behavior of showpy.time.measures each
    test_regression_*(): one audited measure defect each (zero, NaN, negative, duplicates)
"""

import math

import pytest
from pydantic import ValidationError as PydanticValidationError

from showpy.core.errors import ShowPyError, TimelineError, TimeRangeError
from showpy.core.util import approx_equal
from showpy.time.measures import count_to_measure, measure_to_count
from showpy.time.models import MeasureLabel, MeasureSetting, Timeline, TimelineMode
from showpy.time.timeline import new_timeline, set_measures

TOL = 1e-9


def _measured(setting: MeasureSetting) -> Timeline:
    return set_measures(new_timeline(TimelineMode.COUNTS, counts_per_minute=120.0), setting)


FOUR = MeasureSetting(counts_per_measure=4.0)


@pytest.mark.parametrize(
    ("count", "measure", "count_in_measure"),
    [(0.0, 1, 1.0), (3.5, 1, 4.5), (4.0, 2, 1.0), (130.0, 33, 3.0)],
)
def test_count_to_measure_is_one_based(count: float, measure: int, count_in_measure: float) -> None:
    label = count_to_measure(_measured(FOUR), count)
    assert label is not None
    assert label.measure == measure
    assert approx_equal(label.count_in_measure, count_in_measure, TOL)


def test_start_measure_offsets_numbering() -> None:
    pickup = MeasureSetting(counts_per_measure=4.0, start_measure=0)
    assert count_to_measure(_measured(pickup), 5.0) == MeasureLabel(measure=1, count_in_measure=2.0)


def test_measures_off_returns_none() -> None:
    tl = new_timeline(TimelineMode.COUNTS, counts_per_minute=120.0)
    assert count_to_measure(tl, 8.0) is None


def test_change_on_measure_boundary() -> None:
    tl = _measured(MeasureSetting(counts_per_measure=4.0, changes=((16.0, 3.0),)))
    assert count_to_measure(tl, 15.0) == MeasureLabel(measure=4, count_in_measure=4.0)
    assert count_to_measure(tl, 16.0) == MeasureLabel(measure=5, count_in_measure=1.0)
    assert count_to_measure(tl, 19.0) == MeasureLabel(measure=6, count_in_measure=1.0)
    assert approx_equal(measure_to_count(tl, 6), 19.0, TOL)


def test_change_mid_measure_starts_a_new_measure() -> None:
    tl = _measured(MeasureSetting(counts_per_measure=4.0, changes=((18.0, 3.0),)))
    assert count_to_measure(tl, 17.0) == MeasureLabel(measure=5, count_in_measure=2.0)
    assert count_to_measure(tl, 18.0) == MeasureLabel(measure=6, count_in_measure=1.0)
    assert count_to_measure(tl, 21.0) == MeasureLabel(measure=7, count_in_measure=1.0)
    assert approx_equal(measure_to_count(tl, 6), 18.0, TOL)
    assert approx_equal(measure_to_count(tl, 7, 2.0), 22.0, TOL)


def test_unsorted_changes_are_applied_in_count_order() -> None:
    tl = _measured(MeasureSetting(counts_per_measure=4.0, changes=((14.0, 2.0), (8.0, 3.0))))
    assert count_to_measure(tl, 8.0) == MeasureLabel(measure=3, count_in_measure=1.0)
    assert count_to_measure(tl, 14.0) == MeasureLabel(measure=5, count_in_measure=1.0)
    assert count_to_measure(tl, 16.0) == MeasureLabel(measure=6, count_in_measure=1.0)


def test_fractional_measure_length() -> None:
    tl = _measured(MeasureSetting(counts_per_measure=2.5))
    assert count_to_measure(tl, 5.0) == MeasureLabel(measure=3, count_in_measure=1.0)
    assert approx_equal(measure_to_count(tl, 3, 2.0), 6.0, TOL)


def test_measure_to_count_round_trips_label() -> None:
    tl = _measured(FOUR)
    assert approx_equal(measure_to_count(tl, 33, 3.0), 130.0, TOL)
    assert approx_equal(measure_to_count(tl, 1), 0.0, TOL)


def test_measure_to_count_with_measures_off_raises() -> None:
    with pytest.raises(TimelineError):
        measure_to_count(new_timeline(TimelineMode.COUNTS, counts_per_minute=120.0), 2)


def test_measure_to_count_rejects_out_of_range() -> None:
    tl = _measured(FOUR)
    with pytest.raises(TimeRangeError):
        measure_to_count(tl, 0)
    with pytest.raises(TimeRangeError):
        measure_to_count(tl, 2, 0.5)
    with pytest.raises(TimeRangeError):
        measure_to_count(tl, 2, 5.0)


def test_measure_to_count_rejects_count_past_short_measure() -> None:
    tl = _measured(MeasureSetting(counts_per_measure=4.0, changes=((18.0, 3.0),)))
    with pytest.raises(TimeRangeError):
        measure_to_count(tl, 5, 4.0)


def test_count_to_measure_rejects_negative_count() -> None:
    with pytest.raises(TimeRangeError):
        count_to_measure(_measured(FOUR), -1.0)


REJECTED = (ShowPyError, PydanticValidationError)


def _with_measures(setting: MeasureSetting) -> Timeline:
    return Timeline(mode=TimelineMode.SECONDS, measures=setting)


def _with_changes(changes: tuple[tuple[float, float], ...]) -> Timeline:
    return _with_measures(MeasureSetting(counts_per_measure=4.0, changes=changes))


@pytest.mark.defect("UP-9.3-109")
@pytest.mark.xfail(strict=True, reason="UP-9.3-109")
def test_regression_zero_measure_length_is_a_clean_error() -> None:
    """Rejects `counts_per_measure=0` with a ShowPyError, not ZeroDivisionError (audit 9.3)."""
    with pytest.raises(REJECTED):
        count_to_measure(_with_measures(MeasureSetting(counts_per_measure=0.0)), 4.0)


@pytest.mark.defect("UP-9.3-110")
@pytest.mark.xfail(strict=True, reason="UP-9.3-110")
def test_regression_nan_measure_length_is_a_clean_error() -> None:
    """Rejects a NaN measure length with a ShowPyError, not ValueError (audit 9.3)."""
    with pytest.raises(REJECTED):
        count_to_measure(_with_measures(MeasureSetting(counts_per_measure=math.nan)), 4.0)


@pytest.mark.defect("UP-9.3-111")
@pytest.mark.xfail(strict=True, reason="UP-9.3-111")
def test_regression_negative_measure_length_is_rejected() -> None:
    """Rejects a negative measure length instead of labeling measure -1 (audit 9.3)."""
    with pytest.raises(REJECTED):
        count_to_measure(_with_measures(MeasureSetting(counts_per_measure=-4.0)), 4.0)


@pytest.mark.defect("UP-9.3-112")
@pytest.mark.xfail(strict=True, reason="UP-9.3-112")
def test_regression_duplicate_change_counts_follow_input_order() -> None:
    """Rejects two changes at one count, or lets the later one win (audit 9.3)."""
    probe = 14.0
    try:
        first = count_to_measure(
            _with_measures(
                MeasureSetting(counts_per_measure=4.0, changes=((8.0, 3.0), (8.0, 5.0)))
            ),
            probe,
        )
        second = count_to_measure(
            _with_measures(
                MeasureSetting(counts_per_measure=4.0, changes=((8.0, 5.0), (8.0, 3.0)))
            ),
            probe,
        )
    except REJECTED:
        return
    assert first != second


@pytest.mark.defect("UP-9.3-113")
@pytest.mark.xfail(strict=True, reason="UP-9.3-113")
def test_regression_fractional_measure_numbers_are_rejected() -> None:
    """Rejects measure 2.5 instead of computing a count for it (audit 9.3)."""
    timeline = _with_measures(MeasureSetting(counts_per_measure=4.0))
    with pytest.raises(ShowPyError):
        measure_to_count(timeline, 2.5)  # type: ignore[arg-type]
