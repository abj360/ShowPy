#!/usr/bin/env python3

"""
test_paths.py --- tests the pure path-row editing functions
"""

import pytest

from showpy.choreo.models import Easing, FacingMode, PathRow, PathStyle, TransitionSpec
from showpy.choreo.paths import (
    add_waypoint,
    arrive_early,
    clear_waypoints,
    curve_path,
    custom_path,
    default_paths,
    delay,
    hold,
    lock,
    paths_table,
    remove_waypoint,
    ripple,
    set_easing,
    set_facing,
    set_pace,
    set_style,
    stagger,
    unlock,
    window,
)
from showpy.core.errors import LockedError, PathError, UnknownRefError
from showpy.core.util import approx_equal
from tests.fixtures.motion_shows import TID
from tests.fixtures.shows import two_set_show

SPEC = TransitionSpec(id=TID, counts=8.0)


def test_default_paths_is_empty_straight_spec() -> None:
    spec = default_paths(two_set_show(), TID)
    assert spec.counts == 8.0
    assert spec.rows == {}


def test_set_style_requires_style_params() -> None:
    with pytest.raises(PathError):
        set_style(SPEC, ["T1"], PathStyle.ROTATE)
    rotated = set_style(SPEC, ["T1"], PathStyle.ROTATE, pivot=(0.0, 0.0), sweep_deg=90.0)
    assert rotated.row("T1").pivot == (0.0, 0.0)


def test_set_style_rejects_unknown_params() -> None:
    with pytest.raises(PathError):
        set_style(SPEC, ["T1"], PathStyle.STRAIGHT, wobble=3)


def test_delay_keeps_end_by_default() -> None:
    row = delay(SPEC, ["T1"], 2.0).row("T1")
    assert row.window(8.0) == (2.0, 8.0)


def test_delay_without_keep_end_shifts_both_ends() -> None:
    spec = window(SPEC, ["T1"], 0.0, 4.0)
    assert delay(spec, ["T1"], 2.0, keep_end=False).row("T1").window(8.0) == (2.0, 6.0)
    with pytest.raises(PathError):
        delay(SPEC, ["T1"], 2.0, keep_end=False)


def test_hold_variants() -> None:
    assert hold(SPEC, ["T1"]).row("T1").style == PathStyle.HOLD
    assert hold(SPEC, ["T1"], until=3.0).row("T1").window(8.0) == (3.0, 8.0)
    assert hold(SPEC, ["T1"], from_=5.0).row("T1").window(8.0) == (0.0, 5.0)


def test_arrive_early_and_window() -> None:
    assert arrive_early(SPEC, ["T1"], 2.0).row("T1").window(8.0) == (0.0, 6.0)
    with pytest.raises(PathError):
        window(SPEC, ["T1"], 2.0, 10.0)


def test_waypoints_add_remove_clear() -> None:
    spec = add_waypoint(SPEC, "T1", (1.0, 1.0))
    spec = add_waypoint(spec, "T1", (2.0, 2.0), at=4.0, index=0)
    row = spec.row("T1")
    assert row.style == PathStyle.POLYLINE
    assert [(w.x, w.at) for w in row.waypoints] == [(2.0, 4.0), (1.0, None)]
    assert len(remove_waypoint(spec, "T1", 0).row("T1").waypoints) == 1
    with pytest.raises(PathError):
        remove_waypoint(spec, "T1", 5)
    cleared = clear_waypoints(spec, ["T1"]).row("T1")
    assert cleared.waypoints == ()
    assert cleared.style == PathStyle.STRAIGHT


def test_curve_path_sets_control_points() -> None:
    row = curve_path(SPEC, "T1", (1.0, 1.0), (2.0, 3.0)).row("T1")
    assert row.style == PathStyle.CURVE
    assert len(row.waypoints) == 2


def test_stagger_offsets_starts_in_order() -> None:
    spec = stagger(SPEC, ["T1", "T2", "T3"], step=1.0)
    assert [spec.row(e).start for e in ("T1", "T2", "T3")] == [0.0, 1.0, 2.0]
    reversed_spec = stagger(SPEC, ["T1", "T2", "T3"], step=1.0, direction="end")
    assert [reversed_spec.row(e).start for e in ("T1", "T2", "T3")] == [2.0, 1.0, 0.0]


def test_stagger_that_overruns_raises() -> None:
    with pytest.raises(PathError):
        stagger(SPEC, ["T1", "T2", "T3"], step=5.0)


def test_ripple_spreads_starts_over_total() -> None:
    spec = ripple(SPEC, ["T1", "T2", "T3"], 4.0)
    assert [spec.row(e).start for e in ("T1", "T2", "T3")] == [0.0, 2.0, 4.0]
    eased = ripple(SPEC, ["T1", "T2", "T3"], 4.0, curve="ease_in")
    assert eased.row("T2").start < 2.0


def test_set_facing_pace_and_easing() -> None:
    spec = set_facing(SPEC, ["T1"], FacingMode.FIXED, 90.0, turn_counts=1.0)
    assert spec.row("T1").facing_value == 90.0
    assert set_pace(SPEC, ["T1"], 2.0).row("T1").pace == 2.0
    assert set_easing(SPEC, ["T1"], "ease_in").row("T1").easing == Easing(kind="ease_in")


def test_custom_path_checks_registry() -> None:
    assert custom_path(SPEC, ["T1"], "weave", cycles=1).row("T1").motion_params == {"cycles": 1}
    with pytest.raises(UnknownRefError):
        custom_path(SPEC, ["T1"], "nope")


def test_locked_rows_refuse_edits_unless_forced() -> None:
    locked = lock(SPEC, ["T1"])
    with pytest.raises(LockedError):
        delay(locked, ["T1"], 1.0)
    assert delay(locked, ["T1"], 1.0, force=True).row("T1").start == 1.0
    assert not unlock(locked, ["T1"]).row("T1").locked


def test_edits_never_mutate_input() -> None:
    delay(SPEC, ["T1"], 1.0)
    assert SPEC.rows == {}


def test_paths_table_lists_only_non_default_rows() -> None:
    spec = delay(SPEC, ["T1"], 1.0)
    spec = spec.model_copy(update={"rows": {**spec.rows, "T2": PathRow(entity="T2")}})
    table = paths_table(spec)
    assert list(table["entity"]) == ["T1"]
    assert approx_equal(table.iloc[0]["start"], 1.0)
