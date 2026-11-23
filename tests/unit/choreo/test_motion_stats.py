#!/usr/bin/env python3

"""
test_motion_stats.py --- tests speed, travel direction and motion analysis over time

Contains:
    test_regression_motion_helpers_accept_a_custom_motion_registry: analyses a show that uses a
        custom-registered motion
    test_regression_energy_curve_rejects_negative_resolution: raises an E8sError, not numpy
        ValueError, for a negative per_count
    test_regression_energy_curve_rejects_zero_resolution: raises PathError for a per_count of 0
"""

import math
from collections.abc import Callable
from typing import cast

import numpy as np
import pytest

from e8s.choreo.engine import PathEngine
from e8s.choreo.motion import DEFAULT_MOTIONS, FloatArray, MotionRegistry
from e8s.choreo.motion_stats import (
    direction_of_travel,
    energy_curve,
    motion_contrast,
    phrase_profile,
    position_at,
    speed,
    speeds,
)
from e8s.choreo.path_models import PathRow, PathStyle
from e8s.core.errors import E8sError, PathError, TimeRangeError
from e8s.core.ids import SpotId
from e8s.core.types import Point, Points
from e8s.core.util import approx_equal
from e8s.show.models import Show
from e8s.time.models import Marker, MarkerKind
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.fixtures.shows import two_set_show
from tests.helpers import defect


def test_engine_speed_reports_distance_rate_and_seconds() -> None:
    stats = PathEngine(two_set_show()).speed("T1", TID)
    assert approx_equal(stats.distance, 4.0)
    assert approx_equal(stats.m_per_count, 0.5)
    assert approx_equal(stats.m_per_s, 1.0)
    assert approx_equal(stats.max_instant, 0.5)


def test_speed_window_and_delayed_move() -> None:
    show = with_rows({"T1": PathRow(entity=SpotId("T1"), start=4.0)})
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
    assert heading == pytest.approx(180.0)
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


type MotionsCall = Callable[[Show, MotionRegistry], object]
BAD_PER_COUNT = -1
MID_MOVE = 2.0


def _line_motion(u: FloatArray, start: Point, end: Point, params: object) -> Points:
    return np.linspace(start, end, len(u))


# motions= is the keyword the audit proposes; the current helpers have no such parameter.
def _speed_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], speed)(show, "T1", TID, motions=motions)


def _speeds_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], speeds)(show, TID, motions=motions)


def _contrast_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], motion_contrast)(show, TID, motions=motions)


def _position_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], position_at)(show, "T1", MID_MOVE, motions=motions)


def _direction_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], direction_of_travel)(
        show, "T1", TID, MID_MOVE, motions=motions
    )


def _energy_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], energy_curve)(show, motions=motions)


def _phrase_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], phrase_profile)(show, 0.0, 8.0, motions=motions)


@defect("UP-9.2-32")
@pytest.mark.parametrize(
    "call",
    [
        _speed_call,
        _speeds_call,
        _contrast_call,
        _position_call,
        _direction_call,
        _energy_call,
        _phrase_call,
    ],
)
def test_regression_motion_helpers_accept_a_custom_motion_registry(call: MotionsCall) -> None:
    """Analyses a show that uses a custom-registered motion (audit 9.2)."""
    registry = DEFAULT_MOTIONS.with_motion("probe", _line_motion)
    row = PathRow(entity=SpotId("T1"), style=PathStyle.CUSTOM, motion_fn="probe")
    plain = call(two_set_show(), DEFAULT_MOTIONS)
    assert repr(call(with_rows({"T1": row}), registry)) == repr(plain)


def test_regression_energy_curve_rejects_negative_resolution() -> None:
    """Raises an E8sError, not numpy ValueError, for a negative per_count (audit 9.2)."""
    with pytest.raises(E8sError):
        energy_curve(two_set_show(), per_count=BAD_PER_COUNT)


def test_regression_energy_curve_rejects_zero_resolution() -> None:
    """Raises PathError for a per_count of 0 instead of returning a one-sample curve."""
    with pytest.raises(PathError):
        energy_curve(two_set_show(), per_count=0)
