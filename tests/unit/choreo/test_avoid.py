#!/usr/bin/env python3

"""
test_avoid.py --- tests advisory gap finding and fix suggestions
"""

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
from showpy.choreo.engine_models import Fix
from showpy.core.errors import PathError
from showpy.core.util import approx_equal
from tests.fixtures.crossing_shows import crossing_show
from tests.fixtures.motion_shows import TID
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
