#!/usr/bin/env python3

"""
test_avoid.py --- tests advisory gap finding and fix suggestions
"""

from collections.abc import Callable
from typing import TypeVar, cast

import numpy as np
import pytest

from showpy.choreo.avoid import (
    apply_fix,
    closest_approach,
    min_gaps,
    separate_in_time,
    suggest_delays,
    suggest_reassignment,
    suggest_waypoint,
)
from showpy.choreo.engine import PathEngine
from showpy.choreo.engine_models import Fix, GapInfo
from showpy.choreo.models import Easing, PathRow, PathStyle
from showpy.choreo.motion import DEFAULT_MOTIONS, FloatArray
from showpy.core.errors import PathError, ShowPyError
from showpy.core.types import Point, Points
from showpy.core.util import approx_equal
from tests.fixtures.crossing_shows import crossing_show
from tests.fixtures.motion_shows import TID, with_rows
from tests.fixtures.shows import two_set_show

FOOTPRINTS = 0.6


def test_closest_approach_between_crossing_segments() -> None:
    t, d = closest_approach(
        [0.0, 8.0], [(0.0, 0.0), (8.0, 0.0)], [0.0, 8.0], [(8.0, 0.0), (0.0, 0.0)]
    )
    assert approx_equal(t, 4.0)
    assert approx_equal(d, 0.0)


def test_closest_approach_with_different_station_times() -> None:
    t, d = closest_approach(
        [0.0, 2.0, 8.0], [(0.0, 0.0), (2.0, 0.0), (2.0, 0.0)], [0.0, 8.0], [(2.0, 3.0), (2.0, 3.0)]
    )
    assert approx_equal(d, 3.0)
    assert t >= 2.0 - 1e-9


def test_min_gaps_finds_the_crossing_first() -> None:
    gaps = min_gaps(crossing_show(), TID, top=3)
    first = gaps[0]
    assert {first.a, first.b} == {"T1", "T2"}
    assert approx_equal(first.tau, 4.0)
    assert approx_equal(first.gap, -FOOTPRINTS)


def test_min_gaps_on_parallel_line_reports_spacing() -> None:
    gaps = min_gaps(two_set_show(), TID, top=1)
    assert approx_equal(gaps[0].gap, 2.0 - FOOTPRINTS)


def test_min_gaps_respects_selection() -> None:
    gaps = min_gaps(crossing_show(), TID, sel=["T3", "T4"])
    assert [(g.a, g.b) for g in gaps] == [("T3", "T4")]


def test_suggest_delays_opens_the_gap() -> None:
    fixes = suggest_delays(crossing_show(), TID, ("T1", "T2"), max_delay=4)
    assert fixes
    best = fixes[0]
    assert best.kind == "delay"
    assert best.new_gap > 0.0
    assert best.max_speed_after >= 0.5


def test_suggest_waypoint_routes_around() -> None:
    fixes = suggest_waypoint(crossing_show(), TID, "T1", "T2", clearance=0.8)
    assert fixes[0].kind == "waypoint"
    assert fixes[0].new_gap > 0.0


def test_apply_fix_and_separate_in_time() -> None:
    show = crossing_show()
    fix = suggest_delays(show, TID, ("T1", "T2"))[0]
    spec = apply_fix(show.transition(TID), fix)
    assert spec.row(str(fix.params["entity"])).start > 0.0
    staggered = separate_in_time(show.transition(TID), ["T1", "T2"], gap_counts=2.0)
    assert staggered.row("T2").start == 2.0


def test_apply_reassign_fix_is_refused() -> None:
    fix = Fix(kind="reassign", params={}, new_gap=1.0, max_speed_after=0.5)
    with pytest.raises(PathError):
        apply_fix(crossing_show().transition(TID), fix)


def test_suggest_reassignment_uncrosses_a_swap() -> None:
    result = suggest_reassignment(crossing_show(), TID, ["T1", "T2"])
    assert result.crossings == 0
    targets = dict(zip(result.entities, result.target_points().tolist(), strict=True))
    assert approx_equal(targets["T1"], (0.0, 9.0))
    assert approx_equal(targets["T2"], (4.0, 9.0))


def test_regression_reassign_hint_names_the_real_module() -> None:
    fix = Fix(kind="reassign", params={}, new_gap=1.0, max_speed_after=0.5)
    with pytest.raises(PathError) as info:
        apply_fix(crossing_show().transition(TID), fix)
    assert "showpy.choreo.match_ops.apply_match" in info.value.hint


F = TypeVar("F", bound=Callable[..., object])
PARKED_UNTIL = 7.0


def defect(defect_id: str) -> Callable[[F], F]:
    """Marks a test as an open audited defect: a strict xfail keyed on its ID."""

    def decorate(test: F) -> F:
        marked = pytest.mark.defect(defect_id)(test)
        return cast(F, pytest.mark.xfail(strict=True, reason=defect_id)(marked))

    return decorate


@defect("UP-9.2-17")
def test_regression_suggested_waypoint_is_reached_at_its_count_on_eased_rows() -> None:
    """Puts a suggested bend where it says, at the count it names, on an eased row (audit 9.2)."""
    row = PathRow(entity="T1", easing=Easing(kind="ease_in", strength=2.0))
    show = with_rows({"T1": row}, show=crossing_show())
    fix = suggest_waypoint(show, TID, "T1", "T2")[0]
    changed = apply_fix(show.transition(TID), fix)
    engine = PathEngine(show.model_copy(update={"transitions": (changed,)}))
    reached = engine.position("T1", fix.params["at"], local=TID)
    assert approx_equal(reached, fix.params["point"], tol=1e-6)


@defect("UP-9.2-28")
def test_regression_min_gaps_with_top_zero_returns_nothing() -> None:
    """Returns an empty list, or a ShowPyError, for top=0 instead of IndexError (audit 9.2)."""
    try:
        gaps = min_gaps(two_set_show(), TID, top=0)
    except ShowPyError:
        return
    assert gaps == []


@defect("UP-9.2-29")
def test_regression_min_gaps_ignores_spots_before_their_entrance() -> None:
    """Skips the time a spot is parked off the field, so it cannot collide there (audit 9.2)."""
    base = two_set_show()
    first = base.set("1").with_placements(
        {"T1": {"x": 2.0, "y": 5.0, "on_field": False}, "T2": {"x": 2.0, "y": 1.0}}
    )
    second = base.set("2").with_placements({"T1": {"x": 6.0, "y": 5.0}, "T2": {"x": 2.0, "y": 9.0}})
    show = with_rows(
        {"T1": PathRow(entity="T1", start=PARKED_UNTIL)},
        show=base.model_copy(update={"sets": (first, second)}),
    )
    assert min_gaps(show, TID, sel=["T1", "T2"], top=1)[0].gap > 0.0


@defect("UP-9.2-30")
def test_regression_suggested_waypoints_never_shrink_the_gap() -> None:
    """Returns only waypoint fixes that widen the pair's gap (audit 9.2)."""
    show = two_set_show()
    base_gap = min_gaps(show, TID, sel=["T1", "T2"], top=1)[0].gap
    fixes = suggest_waypoint(show, TID, "T1", "T2")
    assert [f.new_gap for f in fixes if f.new_gap < base_gap] == []


def _stay_on_the_line(u: FloatArray, start: Point, end: Point, params: object) -> Points:
    return np.linspace(start, end, len(u))


@defect("UP-9.2-31")
def test_regression_min_gaps_accepts_a_custom_motion_registry() -> None:
    """Measures gaps in a show that uses a custom-registered motion (audit 9.2)."""
    registry = DEFAULT_MOTIONS.with_motion("probe", _stay_on_the_line)
    row = PathRow(entity="T1", style=PathStyle.CUSTOM, motion_fn="probe")
    show = with_rows({"T1": row})
    # motions= is the keyword the audit proposes; the current signature has none.
    gaps_with_motions = cast(Callable[..., list[GapInfo]], min_gaps)
    gaps = gaps_with_motions(show, TID, top=1, motions=registry)
    assert approx_equal(gaps[0].gap, 2.0 - FOOTPRINTS, tol=1e-6)


@defect("UP-9.2-35")
def test_regression_closest_approach_of_empty_track_raises_showpy_error() -> None:
    """Raises a ShowPyError, not IndexError, for an empty track (audit 9.2)."""
    with pytest.raises(ShowPyError):
        closest_approach([], [], [0.0, 1.0], [(0.0, 0.0), (1.0, 1.0)])
