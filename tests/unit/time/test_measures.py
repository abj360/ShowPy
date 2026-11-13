#!/usr/bin/env python3

"""
test_measures.py --- tests 1-based measure labels, measure lookups and mid-show length changes
"""

import pytest

from stage.core.errors import TimelineError, TimeRangeError
from stage.core.util import approx_equal
from stage.time.measures import count_to_measure, measure_to_count
from stage.time.models import MeasureLabel, MeasureSetting, Timeline, TimelineMode
from stage.time.timeline import new_timeline, set_measures

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
