#!/usr/bin/env python3

"""
test_paths.py --- tests the pure path-row editing functions

Contains:
    test_regression_switching_to_straight_drops_the_bend: UP-9.2-04
    test_regression_add_waypoint_clears_parameters_of_the_old_style: UP-9.2-06
    test_regression_delay_applies_to_follow_rows: UP-9.2-18
    test_regression_delay_rejects_a_waypoint_pushed_outside_the_window: UP-9.2-19
    test_regression_stagger_and_ripple_errors_hint_at_the_step: UP-9.2-20
    test_regression_add_waypoint_on_a_held_row_clears_old_parameters: UP-9.2-37
    test_regression_ripple_rejects_a_waypoint_pushed_outside_the_window: UP-9.2-38
"""

from collections.abc import Callable
from typing import TypeVar, cast

import pytest

from showpy.choreo.engine import PathEngine
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
from showpy.choreo.routes import follow, make_route
from showpy.core.errors import LockedError, PathError, UnknownRefError
from showpy.core.types import Point
from showpy.core.util import approx_equal
from showpy.show.models import Show
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


F = TypeVar("F", bound=Callable[..., object])
BEND: Point = (1.0, 7.0)
TWO_COUNT_DELAY = 2.0
LATE_STEP = 3.0
RIPPLE_TOTAL_PAST_END = 9.0
RIPPLE_TOTAL_INSIDE = 3.0
STALE_FIELDS = ("pivot", "sweep_deg", "motion_fn", "route")


def defect(defect_id: str) -> Callable[[F], F]:
    """Marks a test as an open audited defect: a strict xfail keyed on its ID."""

    def decorate(test: F) -> F:
        marked = pytest.mark.defect(defect_id)(test)
        return cast(F, pytest.mark.xfail(strict=True, reason=defect_id)(marked))

    return decorate


@defect("UP-9.2-04")
def test_regression_switching_to_straight_drops_the_bend() -> None:
    """Clears waypoints when a POLYLINE row becomes STRAIGHT (audit 9.2)."""
    bent = add_waypoint(SPEC, "T1", BEND)
    assert set_style(bent, ["T1"], PathStyle.STRAIGHT).row("T1").waypoints == ()


def _rotate_row() -> TransitionSpec:
    return set_style(SPEC, ["T1"], PathStyle.ROTATE, pivot=(0.0, 7.0), sweep_deg=90.0)


def _custom_row() -> TransitionSpec:
    return custom_path(SPEC, ["T1"], "weave")


def _follow_row() -> TransitionSpec:
    return set_style(SPEC, ["T1"], PathStyle.FOLLOW, route="r")


@defect("UP-9.2-06")
@pytest.mark.parametrize("make_spec", [_rotate_row, _custom_row, _follow_row])
def test_regression_add_waypoint_clears_parameters_of_the_old_style(
    make_spec: Callable[[], TransitionSpec],
) -> None:
    """Rejects or cleans a ROTATE, CUSTOM or FOLLOW row turned POLYLINE by a bend (audit 9.2)."""
    try:
        row = add_waypoint(make_spec(), "T1", BEND).row("T1")
    except PathError:
        return
    stale = {name: getattr(row, name) for name in STALE_FIELDS if getattr(row, name) is not None}
    assert row.style == PathStyle.POLYLINE
    assert stale == {}


def _followed() -> tuple[Show, TransitionSpec]:
    show = two_set_show()
    route = make_route(show, TID, "T1", [(0.0, 9.0)])
    return show, follow(show.transition(TID), route)


def _delayed(spec: TransitionSpec) -> TransitionSpec:
    return delay(spec, ["T1"], TWO_COUNT_DELAY)


def _staggered(spec: TransitionSpec) -> TransitionSpec:
    return stagger(spec, ["T2", "T1"], step=TWO_COUNT_DELAY)


def _rippled(spec: TransitionSpec) -> TransitionSpec:
    return ripple(spec, ["T2", "T1"], total=2 * TWO_COUNT_DELAY)


@defect("UP-9.2-18")
@pytest.mark.parametrize("shift", [_delayed, _staggered, _rippled])
def test_regression_delay_applies_to_follow_rows(
    shift: Callable[[TransitionSpec], TransitionSpec],
) -> None:
    """Holds a FOLLOW spot at its start through a delay, stagger or ripple (audit 9.2)."""
    show, followed = _followed()
    shifted = shift(followed)
    start = shifted.row("T1").start
    assert start >= TWO_COUNT_DELAY
    engine = PathEngine(show.model_copy(update={"transitions": (shifted,)}))
    assert approx_equal(engine.position("T1", start / 2, local=TID), show.set("1").get("T1").point)


@defect("UP-9.2-19")
def test_regression_delay_rejects_a_waypoint_pushed_outside_the_window() -> None:
    """Rejects, at edit time, a delay that pushes a timed waypoint before the window (audit 9.2)."""
    timed = add_waypoint(SPEC, "T1", BEND, at=1.0)
    with pytest.raises(PathError):
        delay(timed, ["T1"], TWO_COUNT_DELAY)


def _overflowing_stagger() -> TransitionSpec:
    return stagger(SPEC, ["T1", "T2", "T3", "T4"], step=LATE_STEP)


def _overflowing_ripple() -> TransitionSpec:
    return ripple(SPEC, ["T1", "T2"], total=RIPPLE_TOTAL_PAST_END)


@defect("UP-9.2-20")
@pytest.mark.parametrize("shift", [_overflowing_stagger, _overflowing_ripple])
def test_regression_stagger_and_ripple_errors_hint_at_the_step(
    shift: Callable[[], TransitionSpec],
) -> None:
    """Names the step size in the error when a stagger or ripple pushes a spot past the end."""
    with pytest.raises(PathError) as excinfo:
        shift()
    assert "step" in excinfo.value.hint


@defect("UP-9.2-37")
def test_regression_add_waypoint_on_a_held_row_clears_old_parameters() -> None:
    """Rejects or cleans a HOLD row, left over from a ROTATE row, that a bend turns POLYLINE."""
    held = hold(_rotate_row(), ["T1"])
    try:
        row = add_waypoint(held, "T1", BEND).row("T1")
    except PathError:
        return
    stale = {name: getattr(row, name) for name in STALE_FIELDS if getattr(row, name) is not None}
    assert row.style == PathStyle.POLYLINE
    assert stale == {}


@defect("UP-9.2-38")
def test_regression_ripple_rejects_a_waypoint_pushed_outside_the_window() -> None:
    """Rejects, at edit time, a ripple that starts a spot after its timed waypoint."""
    timed = add_waypoint(SPEC, "T2", BEND, at=1.0)
    with pytest.raises(PathError):
        ripple(timed, ["T1", "T2"], total=RIPPLE_TOTAL_INSIDE)
