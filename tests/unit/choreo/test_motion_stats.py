#!/usr/bin/env python3

"""
test_motion_stats.py --- tests speed, travel direction and motion analysis over time
"""

import math

import pytest

from showpy.choreo.engine import PathEngine
from showpy.choreo.models import PathRow
from showpy.choreo.motion_stats import (
    direction_of_travel,
    energy_curve,
    motion_contrast,
    phrase_profile,
    position_at,
    speed,
    speeds,
)
from showpy.core.errors import TimeRangeError
from showpy.core.util import approx_equal
from showpy.time.models import Marker, MarkerKind
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.fixtures.shows import two_set_show


def test_engine_speed_reports_distance_rate_and_seconds() -> None:
    stats = PathEngine(two_set_show()).speed("T1", TID)
    assert approx_equal(stats.distance, 4.0)
    assert approx_equal(stats.m_per_count, 0.5)
    assert approx_equal(stats.m_per_s, 1.0)
    assert approx_equal(stats.max_instant, 0.5)


def test_speed_window_and_delayed_move() -> None:
    show = with_rows({"T1": PathRow(entity="T1", start=4.0)})
    stats = speed(show, "T1", TID)
    assert approx_equal(stats.max_instant, 1.0)
    assert approx_equal(stats.counts, 4.0)
    assert approx_equal(speed(show, "T1", TID, window=(0.0, 4.0)).distance, 0.0)


def test_speed_of_holding_spot_is_zero() -> None:
    stats = speed(with_targets({"T1": {"y": 5.0}}), "T1", TID)
    assert stats.distance == 0.0
    assert stats.counts == 0.0


def test_speeds_ranks_hardest_first() -> None:
    show = with_targets({"T3": {"y": 13.0}})
    table = speeds(show, TID)
    assert table.iloc[0]["entity"] == "T3"
    assert list(table.columns)[:2] == ["entity", "distance"]


def test_position_at_and_direction_of_travel() -> None:
    show = two_set_show()
    assert approx_equal(position_at(show, "T2", 4.0), (2.0, 7.0))
    heading = direction_of_travel(show, "T2", TID, 4.0)
    assert heading is not None
    assert approx_equal(heading, 180.0)
    held = with_targets({"T2": {"y": 5.0}})
    assert direction_of_travel(held, "T2", TID, 4.0) is None


def test_motion_contrast_uniform_move() -> None:
    stats = motion_contrast(two_set_show(), TID)
    assert stats.moving_share == 1.0
    assert approx_equal(stats.speed_spread, 0.0)
    assert approx_equal(stats.direction_variety, 0.0)


def test_motion_contrast_mixed_move() -> None:
    show = with_targets({"T1": {"y": 5.0}, "T2": {"x": 6.0, "y": 5.0}})
    stats = motion_contrast(show, TID)
    assert approx_equal(stats.moving_share, 0.75)
    assert stats.direction_variety > 0.0


def test_energy_curve_peaks_while_moving() -> None:
    curve = energy_curve(two_set_show(), per_count=1)
    assert approx_equal(curve.loc[4.0], 0.5)
    assert len(curve) == 9


def test_phrase_profile_splits_on_markers() -> None:
    show = two_set_show()
    timeline = show.timeline.model_copy(
        update={"markers": (Marker(label="B", at_count=4.0, kind=MarkerKind.REHEARSAL),)}
    )
    profile = phrase_profile(show.model_copy(update={"timeline": timeline}), 0.0, 8.0)
    assert list(profile["start"]) == [0.0, 4.0]
    assert approx_equal(profile["change"].tolist(), [2.0, 2.0])
    assert approx_equal(profile["energy"].tolist(), [0.5, 0.5])
    assert all(math.isfinite(d) for d in profile["density"])


def test_phrase_profile_rejects_empty_range() -> None:
    with pytest.raises(TimeRangeError):
        phrase_profile(two_set_show(), 4.0, 4.0)
