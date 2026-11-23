#!/usr/bin/env python3

"""
test_avoid.py --- tests advisory gap finding and fix suggestions

Contains:
    test_regression_reassign_hint_names_the_real_module: names e8s.choreo.match_ops.apply_match
        in the hint for a reassign fix
    test_regression_suggested_waypoint_is_reached_at_its_count_on_eased_rows: puts a suggested bend
        where it says, at the count it names, on an eased row
    test_regression_min_gaps_with_top_zero_returns_nothing: returns an empty list, or an E8sError,
        for top=0 instead of IndexError
    test_regression_min_gaps_ignores_spots_before_their_entrance: skips the time a spot is parked
        off the field, so it cannot collide there
    test_regression_suggested_waypoints_never_shrink_the_gap: returns only waypoint fixes that widen
        the pair's gap
    test_regression_avoid_helpers_accept_a_custom_motion_registry: analyses a show that uses a
        custom-registered motion
    test_regression_closest_approach_of_empty_track_raises_e8s_error: raises an E8sError, not
        IndexError, for an empty track
    test_regression_min_gaps_resolves_selector_expressions: reads `sel` as a selector expression,
        and refuses an unknown spot with UnknownRefError
    test_regression_min_gaps_refuses_negative_top: raises PathError for a negative top
    test_regression_fix_params_are_typed_by_kind: builds each kind's parameter model from a dict,
        refuses another kind's model and unknown keys
    test_regression_suggested_fixes_carry_typed_params: types the params of delay and waypoint
        suggestions and round-trips them through JSON
    test_regression_apply_fix_applies_a_stagger_fix: staggers starts from typed parameters
    test_regression_min_gaps_orders_noise_ties_by_spot_ids: lists pairs whose gaps differ only by
        float noise in spot ID order, so a 1-ulp shift keeps the order and the top pair
    test_regression_min_gaps_breaks_exact_ties_in_natural_id_order: lists T2-T10 before T3-T4
        when their gaps tie, as T2 sorts before T3
    test_regression_min_gaps_names_each_pair_in_natural_order: labels the pair of T2 and T10 as
        T2-T10, not T10-T2
"""

import math
from collections.abc import Callable
from typing import cast

import numpy as np
import pytest

from e8s.cast.rewrite import rewrite_spot_ids
from e8s.choreo.avoid import (
    apply_fix,
    closest_approach,
    min_gaps,
    separate_in_time,
    suggest_delays,
    suggest_reassignment,
    suggest_waypoint,
)
from e8s.choreo.engine import PathEngine
from e8s.choreo.engine_models import BendFix, DelayFix, Fix, ReassignFix, StaggerFix
from e8s.choreo.match_ops import with_set
from e8s.choreo.models import PlacementPatch
from e8s.choreo.motion import DEFAULT_MOTIONS, FloatArray, MotionRegistry
from e8s.choreo.path_models import Easing, PathRow, PathStyle
from e8s.core.errors import E8sError, PathError, SchemaError, UnknownRefError
from e8s.core.ids import SpotId
from e8s.core.json_types import JsonValue
from e8s.core.types import Point, Points
from e8s.core.util import approx_equal
from e8s.show.models import Show
from tests.fixtures.crossing_shows import crossing_show
from tests.fixtures.motion_shows import TID, with_rows
from tests.fixtures.shows import two_set_show
from tests.helpers import defect

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
    assert best.params == DelayFix(entity=SpotId("T1"), counts=4.0)
    assert approx_equal(best.new_gap, 4.0 / math.sqrt(10.0) - FOOTPRINTS, abs_tol=1e-6)
    assert approx_equal(best.max_speed_after, math.sqrt(2.0), abs_tol=1e-6)


def test_suggest_waypoint_routes_around() -> None:
    fixes = suggest_waypoint(crossing_show(), TID, "T1", "T2", clearance=0.8)
    best = fixes[0]
    assert best.kind == "waypoint"
    assert isinstance(best.params, BendFix)
    assert approx_equal(best.params.at, 4.0)
    offset = 1.4 / math.sqrt(2.0)
    assert approx_equal(best.params.point, (2.0 + offset, 7.0 - offset), abs_tol=1e-6)
    assert approx_equal(best.new_gap, 0.178385, abs_tol=1e-5)
    assert approx_equal(best.max_speed_after, 0.788987, abs_tol=1e-5)


def test_apply_fix_and_separate_in_time() -> None:
    show = crossing_show()
    fix = suggest_delays(show, TID, ("T1", "T2"))[0]
    spec = apply_fix(show.transition(TID), fix)
    assert isinstance(fix.params, DelayFix)
    assert spec.row(str(fix.params.entity)).start > 0.0
    staggered = separate_in_time(show.transition(TID), ["T1", "T2"], gap_counts=2.0)
    assert staggered.row("T2").start == 2.0


def test_apply_reassign_fix_is_refused() -> None:
    fix = Fix(kind="reassign", params=ReassignFix(), new_gap=1.0, max_speed_after=0.5)
    with pytest.raises(PathError):
        apply_fix(crossing_show().transition(TID), fix)


def test_suggest_reassignment_uncrosses_a_swap() -> None:
    result = suggest_reassignment(crossing_show(), TID, ["T1", "T2"])
    assert result.crossings == 0
    targets = dict(zip(result.entities, result.target_points().tolist(), strict=True))
    assert approx_equal(targets[SpotId("T1")], (0.0, 9.0))
    assert approx_equal(targets[SpotId("T2")], (4.0, 9.0))


def test_regression_reassign_hint_names_the_real_module() -> None:
    """Names e8s.choreo.match_ops.apply_match in the hint for a reassign fix."""
    fix = Fix(kind="reassign", params=ReassignFix(), new_gap=1.0, max_speed_after=0.5)
    with pytest.raises(PathError) as info:
        apply_fix(crossing_show().transition(TID), fix)
    assert "e8s.choreo.match_ops.apply_match" in info.value.hint


PARKED_UNTIL = 7.0


@defect("UP-9.2-17")
def test_regression_suggested_waypoint_is_reached_at_its_count_on_eased_rows() -> None:
    """Puts a suggested bend where it says, at the count it names, on an eased row (audit 9.2)."""
    row = PathRow(entity=SpotId("T1"), easing=Easing(kind="ease_in", strength=2.0))
    show = with_rows({"T1": row}, show=crossing_show())
    fix = suggest_waypoint(show, TID, "T1", "T2")[0]
    assert isinstance(fix.params, BendFix)
    changed = apply_fix(show.transition(TID), fix)
    engine = PathEngine(show.model_copy(update={"transitions": (changed,)}))
    reached = engine.position("T1", fix.params.at, local=TID)
    assert approx_equal(reached, fix.params.point, abs_tol=1e-6)


def test_regression_min_gaps_with_top_zero_returns_nothing() -> None:
    """Returns an empty list, or an E8sError, for top=0 instead of IndexError (audit 9.2)."""
    try:
        gaps = min_gaps(two_set_show(), TID, top=0)
    except E8sError:
        return
    assert gaps == []


@defect("UP-9.2-29")
def test_regression_min_gaps_ignores_spots_before_their_entrance() -> None:
    """Skips the time a spot is parked off the field, so it cannot collide there (audit 9.2)."""
    base = two_set_show()
    first = base.set("1").with_placements(
        {"T1": PlacementPatch(x=2.0, y=5.0, on_field=False), "T2": PlacementPatch(x=2.0, y=1.0)}
    )
    second = base.set("2").with_placements(
        {"T1": PlacementPatch(x=6.0, y=5.0), "T2": PlacementPatch(x=2.0, y=9.0)}
    )
    show = with_rows(
        {"T1": PathRow(entity=SpotId("T1"), start=PARKED_UNTIL)},
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


type MotionsCall = Callable[[Show, MotionRegistry], object]


# motions= is the keyword the audit proposes; the current signatures have none.
def _gaps_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], min_gaps)(show, TID, top=3, motions=motions)


def _delays_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], suggest_delays)(show, TID, ("T1", "T2"), motions=motions)


def _waypoint_call(show: Show, motions: MotionRegistry) -> object:
    return cast(Callable[..., object], suggest_waypoint)(show, TID, "T1", "T2", motions=motions)


@defect("UP-9.2-31")
@pytest.mark.parametrize("call", [_gaps_call, _delays_call, _waypoint_call])
def test_regression_avoid_helpers_accept_a_custom_motion_registry(call: MotionsCall) -> None:
    """Analyses a show that uses a custom-registered motion (audit 9.2)."""
    registry = DEFAULT_MOTIONS.with_motion("probe", _stay_on_the_line)
    row = PathRow(entity=SpotId("T1"), style=PathStyle.CUSTOM, motion_fn="probe")
    custom = with_rows({"T1": row}, show=crossing_show())
    assert repr(call(custom, registry)) == repr(call(crossing_show(), DEFAULT_MOTIONS))


def test_regression_closest_approach_of_empty_track_raises_e8s_error() -> None:
    """Raises an E8sError, not IndexError, for an empty track (audit 9.2)."""
    with pytest.raises(E8sError):
        closest_approach([], [], [0.0, 1.0], [(0.0, 0.0), (1.0, 1.0)])


def test_regression_min_gaps_resolves_selector_expressions() -> None:
    """Reads `sel` as a selector expression and refuses an unknown spot (review of L0.5)."""
    show = two_set_show()
    gaps = min_gaps(show, TID, "T1-T3")
    assert {(g.a, g.b) for g in gaps} == {("T1", "T2"), ("T1", "T3"), ("T2", "T3")}
    with pytest.raises(UnknownRefError):
        min_gaps(show, TID, ["T1", "Z9"])


def test_regression_min_gaps_refuses_negative_top() -> None:
    """Raises PathError for a negative top instead of slicing the list from the end."""
    with pytest.raises(PathError):
        min_gaps(two_set_show(), TID, top=-1)


def _fix_document(kind: str, params: JsonValue) -> dict[str, JsonValue]:
    return {"kind": kind, "params": params, "new_gap": 1.0, "max_speed_after": 1.0}


def test_regression_fix_params_are_typed_by_kind() -> None:
    """Builds each kind's parameter model from a dict, refusing another kind's model and keys."""
    delay_fix = Fix.model_validate(_fix_document("delay", {"entity": "T1", "counts": 2.0}))
    assert delay_fix.params == DelayFix(entity=SpotId("T1"), counts=2.0)
    stagger = Fix.model_validate(_fix_document("stagger", {"order": ["T1", "T2"], "step": 1.0}))
    assert stagger.params == StaggerFix(order=(SpotId("T1"), SpotId("T2")), step=1.0)
    assert Fix.model_validate(_fix_document("reassign", {})).params == ReassignFix()
    bend = BendFix(entity=SpotId("T1"), point=(1.0, 2.0), at=3.0)
    with pytest.raises(SchemaError):
        Fix(kind="delay", params=bend, new_gap=1.0, max_speed_after=1.0)
    with pytest.raises(SchemaError):
        Fix.model_validate(_fix_document("delay", {"entity": "T1", "counts": 2.0, "at": 1.0}))
    with pytest.raises(SchemaError):
        Fix.model_validate(_fix_document("delay", {"entity": "T1"}))


def test_regression_suggested_fixes_carry_typed_params() -> None:
    """Types the params of delay and waypoint suggestions and round-trips them through JSON."""
    delays = suggest_delays(crossing_show(), TID, ("T1", "T2"))
    bends = suggest_waypoint(crossing_show(), TID, "T1", "T2")
    assert all(isinstance(f.params, DelayFix) for f in delays)
    assert all(isinstance(f.params, BendFix) for f in bends)
    for fix in (delays[0], bends[0]):
        assert Fix.model_validate_json(fix.model_dump_json()) == fix


def test_regression_apply_fix_applies_a_stagger_fix() -> None:
    """Staggers starts from typed parameters, as separate_in_time does."""
    params = StaggerFix(order=(SpotId("T1"), SpotId("T2")), step=2.0)
    fix = Fix(kind="stagger", params=params, new_gap=1.0, max_speed_after=1.0)
    spec = apply_fix(crossing_show().transition(TID), fix)
    assert (spec.row("T1").start, spec.row("T2").start) == (0.0, 2.0)


def _t3_nudged(direction: float) -> Show:
    """Moves T3 1 ulp along x, towards `direction`, in both sets."""
    show = two_set_show()
    for set_id in TID:
        x = math.nextafter(show.set(set_id).get("T3").point[0], direction)
        show = with_set(show, show.set(set_id).with_placements({"T3": PlacementPatch(x=x)}))
    return show


@pytest.mark.parametrize("direction", [-math.inf, math.inf])
def test_regression_min_gaps_orders_noise_ties_by_spot_ids(direction: float) -> None:
    """Lists pairs whose gaps differ only by float noise in spot ID order.

    The trumpets stand 2 m apart, so their neighbour gaps tie; moving T3 by 1 ulp used to reorder
    them by the noise, and with `top=1` change which pair is listed.
    """
    for top in (1, 3):
        expected = [(g.a, g.b) for g in min_gaps(two_set_show(), TID, top=top)]
        assert [(g.a, g.b) for g in min_gaps(_t3_nudged(direction), TID, top=top)] == expected


def test_regression_min_gaps_breaks_exact_ties_in_natural_id_order() -> None:
    """Lists pairs whose gaps tie exactly in natural spot ID order, so T2 comes before T10.

    The trumpets stand 2 m apart; with T1 renamed T10, plain string order listed T10-T2 first.
    """
    pairs = [(g.a, g.b) for g in min_gaps(_t1_renamed_t10(), TID, top=3)]
    assert pairs == [("T2", "T3"), ("T2", "T10"), ("T3", "T4")]


def _t1_renamed_t10() -> Show:
    return rewrite_spot_ids(two_set_show(), lambda spot: "T10" if spot == "T1" else spot)


def test_regression_min_gaps_names_each_pair_in_natural_order() -> None:
    """Names the pair of T2 and T10 as T2-T10; plain string order named it T10-T2."""
    pairs = {(g.a, g.b) for g in min_gaps(_t1_renamed_t10(), TID, top=3)}
    assert ("T2", "T10") in pairs
    assert ("T10", "T2") not in pairs
