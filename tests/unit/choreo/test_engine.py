#!/usr/bin/env python3

"""
test_engine.py --- tests PathEngine positions, styles, timing, facing, segments and frames

Contains:
    test_regression_steps_easing_is_not_aliased_by_segment_probes: starts a segment on every step of
        a steps easing instead of aliasing between probes
    test_regression_positions_resolves_selection_expressions: resolves selection expressions such as
        "T1-T2" and "group:trumpets" in positions()
    test_regression_segments_stay_within_tolerance_on_periodic_motion: keeps every segment within
        0.02 m of the true path on a 4-cycle weave
    test_regression_speed_distance_matches_traced_path_length: reports the travelled distance of a
        4-cycle weave, not its 4 m chord
    test_regression_multi_turn_rotate_is_not_aliased_by_segment_probes: keeps an 8-turn rotate
        within tolerance though every eighth of it is the start point
    test_regression_polyline_corner_is_a_segment_end: ends a segment exactly on an untimed polyline
        corner under a non-linear easing
    test_regression_custom_motion_samples_are_not_forced_segment_ends: fits a 1-cycle weave with
        far fewer segments than its 256 sample pieces
    test_regression_unreachable_segment_bound_raises_path_error: raises SEGMENT_BOUND instead of
        accepting a segment it cannot bring within tol
    test_regression_segment_bound_hint_names_an_accepted_tol: names a tol that a retry accepts, for
        a probed motion too
    test_regression_segments_reject_a_tolerance_that_is_not_positive: rejects a zero, negative or
        non-finite segment tolerance instead of looping on it
    test_regression_facing_override_applies_to_holding_spot: applies a facing override to a spot on
        a HOLD row
    test_regression_toward_entity_override_uses_query_time_position: faces the other spot where it
        is at the query count, not at the row window
    test_regression_invalidate_accepts_a_one_shot_iterable: drops every named transition even when
        they come from a generator
    test_regression_invalidate_clears_override_selection_cache: clears the cached facing-override
        selections when caches are invalidated
    test_regression_spot_query_on_show_without_sets_raises_showpy_error: raises a ShowPyError, not
        IndexError, for a cast spot in a show with no sets
    test_regression_zero_count_transition_compiles_to_a_step: compiles and segments a 0-count
        transition instead of raising PathError
    test_regression_missing_placement_does_not_fail_other_spots: keeps other spots queryable when
        one spot lacks a placement in the next set
    test_regression_velocity_is_full_speed_near_a_window_kink: reports the moving speed just after a
        window start, not half of it
"""

import itertools
import math
import re

import numpy as np
import pytest

from showpy.choreo.engine import PROBE_TOL_SHARE, PathEngine
from showpy.choreo.path_models import (
    Easing,
    FacingMode,
    FacingOverride,
    PathRow,
    PathStyle,
    Route,
    Waypoint,
)
from showpy.core.errors import ContinuityError, PathError, ShowPyError, UnknownRefError
from showpy.core.ids import RouteId, SpotId
from showpy.core.units import vector_facing
from showpy.core.util import approx_equal
from showpy.geo.shapes import line
from showpy.show.models import Show
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.fixtures.shows import two_set_show
from tests.helpers import defect


def _engine(**rows: PathRow) -> PathEngine:
    return PathEngine(with_rows(rows))


def test_default_straight_move_is_linear_in_time() -> None:
    engine = PathEngine(two_set_show())
    assert approx_equal(engine.position("T1", 0.0), (0.0, 5.0))
    assert approx_equal(engine.position("T1", 4.0), (0.0, 7.0))
    assert approx_equal(engine.position("T1", 8.0), (0.0, 9.0))


def test_times_outside_the_show_clamp_to_first_and_last_sets() -> None:
    engine = PathEngine(two_set_show())
    assert approx_equal(engine.position("T2", -3.0), (2.0, 5.0))
    assert approx_equal(engine.position("T2", 99.0), (2.0, 9.0))


def test_local_time_within_a_transition() -> None:
    assert approx_equal(PathEngine(two_set_show()).position("T1", 2.0, local=TID), (0.0, 6.0))


def test_unknown_entity_raises() -> None:
    with pytest.raises(UnknownRefError):
        PathEngine(two_set_show()).position("Z9", 1.0)


def test_delayed_start_holds_then_moves_faster() -> None:
    engine = _engine(T1=PathRow(entity=SpotId("T1"), start=4.0))
    assert approx_equal(engine.position("T1", 4.0), (0.0, 5.0))
    assert approx_equal(engine.position("T1", 6.0), (0.0, 7.0))


def test_early_arrival_holds_at_target() -> None:
    engine = _engine(T1=PathRow(entity=SpotId("T1"), end=4.0))
    assert approx_equal(engine.position("T1", 6.0), (0.0, 9.0))


def test_timed_waypoint_is_reached_at_its_count() -> None:
    row = PathRow(
        entity=SpotId("T1"), style=PathStyle.POLYLINE, waypoints=(Waypoint(x=4.0, y=5.0, at=2.0),)
    )
    engine = _engine(T1=row)
    assert approx_equal(engine.position("T1", 2.0), (4.0, 5.0))
    assert approx_equal(engine.position("T1", 1.0), (2.0, 5.0))
    assert approx_equal(engine.position("T1", 8.0), (0.0, 9.0))


def test_untimed_waypoint_keeps_constant_speed() -> None:
    row = PathRow(
        entity=SpotId("T1"), style=PathStyle.POLYLINE, waypoints=(Waypoint(x=4.0, y=5.0),)
    )
    engine = _engine(T1=row)
    total = 4.0 + math.hypot(4.0, 4.0)
    expected_at_4 = 4.0 * total / 8.0
    assert approx_equal(engine.position("T1", 4.0)[0], 4.0 - (expected_at_4 - 4.0) / math.sqrt(2))


def test_waypoint_times_must_increase_within_window() -> None:
    row = PathRow(
        entity=SpotId("T1"), style=PathStyle.POLYLINE, waypoints=(Waypoint(x=4, y=5, at=9.0),)
    )
    with pytest.raises(PathError):
        _engine(T1=row).compile(TID)


def test_curve_passes_through_waypoints() -> None:
    row = PathRow(
        entity=SpotId("T1"), style=PathStyle.CURVE, waypoints=(Waypoint(x=2.0, y=7.0, at=4.0),)
    )
    assert approx_equal(_engine(T1=row).position("T1", 4.0), (2.0, 7.0), abs_tol=1e-6)


def test_hold_requires_target_equal_to_start() -> None:
    with pytest.raises(ContinuityError):
        _engine(T1=PathRow(entity=SpotId("T1"), style=PathStyle.HOLD)).compile(TID)
    show = with_targets({"T1": {"y": 5.0}})
    engine = PathEngine(
        with_rows({"T1": PathRow(entity=SpotId("T1"), style=PathStyle.HOLD)}, show=show)
    )
    assert approx_equal(engine.position("T1", 4.0), (0.0, 5.0))


def test_rotate_follows_arc_about_pivot() -> None:
    show = with_targets({"T2": {"x": 0.0, "y": 7.0}})
    row = PathRow(entity=SpotId("T2"), style=PathStyle.ROTATE, pivot=(0.0, 5.0), sweep_deg=90.0)
    engine = PathEngine(with_rows({"T2": row}, show=show))
    mid = engine.position("T2", 4.0)
    assert approx_equal(mid, (math.sqrt(2), 5.0 + math.sqrt(2)))


def test_rotate_that_misses_target_raises() -> None:
    row = PathRow(entity=SpotId("T2"), style=PathStyle.ROTATE, pivot=(0.0, 5.0), sweep_deg=45.0)
    with pytest.raises(ContinuityError):
        _engine(T2=row).compile(TID)


def test_rotate_without_pivot_raises() -> None:
    with pytest.raises(PathError):
        _engine(T2=PathRow(entity=SpotId("T2"), style=PathStyle.ROTATE)).compile(TID)


def _follow_show() -> PathEngine:
    route_curve = line((0.0, 5.0), (0.0, 15.0))
    show = with_targets({"T1": {"x": 0.0, "y": 9.0}, "T2": {"x": 0.0, "y": 7.0}})
    first = show.sets[0].with_placements({"T2": {"x": 0.0, "y": 3.0}})
    show = show.model_copy(update={"sets": (first, show.sets[1])})
    route = Route(
        id=RouteId("r"),
        curve=route_curve.construction(),
        members=(SpotId("T1"), SpotId("T2")),
        offsets=(0.0, -2.0),
        travel=4.0,
    )
    rows = {
        e: PathRow(entity=SpotId(e), style=PathStyle.FOLLOW, route=RouteId("r"))
        for e in ("T1", "T2")
    }
    return PathEngine(with_rows(rows, {"r": route}, show=show))


def test_follow_member_off_its_route_raises() -> None:
    with pytest.raises(PathError):
        _follow_show().compile(TID)


def test_follow_on_route_curve() -> None:
    route_curve = line((0.0, 3.0), (0.0, 15.0))
    show = with_targets({"T1": {"x": 0.0, "y": 9.0}, "T2": {"x": 0.0, "y": 7.0}})
    first = show.sets[0].with_placements({"T1": {"x": 0.0, "y": 5.0}, "T2": {"x": 0.0, "y": 3.0}})
    show = show.model_copy(update={"sets": (first, show.sets[1])})
    route = Route(
        id=RouteId("r"),
        curve=route_curve.construction(),
        members=(SpotId("T1"), SpotId("T2")),
        offsets=(2.0, 0.0),
        travel=4.0,
    )
    rows = {
        e: PathRow(entity=SpotId(e), style=PathStyle.FOLLOW, route=RouteId("r"))
        for e in ("T1", "T2")
    }
    engine = PathEngine(with_rows(rows, {"r": route}, show=show))
    assert approx_equal(engine.position("T2", 4.0), (0.0, 5.0))
    assert approx_equal(engine.position("T1", 4.0), (0.0, 7.0))


def test_custom_motion_leaves_the_line_and_lands_on_target() -> None:
    row = PathRow(
        entity=SpotId("T1"),
        style=PathStyle.CUSTOM,
        motion_fn="weave",
        motion_params={"amplitude": 1.0, "cycles": 0.5},
    )
    engine = _engine(T1=row)
    assert abs(engine.position("T1", 4.0)[0]) > 0.9
    assert approx_equal(engine.position("T1", 8.0), (0.0, 9.0))


def test_custom_motion_needs_registered_name() -> None:
    with pytest.raises(UnknownRefError):
        _engine(T1=PathRow(entity=SpotId("T1"), style=PathStyle.CUSTOM, motion_fn="nope")).compile(
            TID
        )


def test_easing_changes_timing_not_path() -> None:
    engine = _engine(T1=PathRow(entity=SpotId("T1"), easing=Easing(kind="ease_in")))
    assert approx_equal(engine.position("T1", 4.0), (0.0, 6.0))


def test_window_beyond_counts_raises() -> None:
    with pytest.raises(PathError):
        _engine(T1=PathRow(entity=SpotId("T1"), end=12.0)).compile(TID)


def test_default_facing_snaps_to_target_when_move_starts() -> None:
    engine = PathEngine(with_targets({"T1": {"facing": 90.0}}))
    assert approx_equal(engine.facing_at("T1", 0.0), 0.0)
    assert approx_equal(engine.facing_at("T1", 0.5), 90.0)


def test_turn_counts_blend_the_facing() -> None:
    show = with_targets({"T1": {"facing": 90.0}})
    engine = PathEngine(with_rows({"T1": PathRow(entity=SpotId("T1"), turn_counts=2.0)}, show=show))
    assert approx_equal(engine.facing_at("T1", 1.0), 45.0)


def test_travel_facing_points_along_motion() -> None:
    engine = _engine(T1=PathRow(entity=SpotId("T1"), facing_mode=FacingMode.TRAVEL))
    assert approx_equal(engine.facing_at("T1", 4.0), 180.0)
    against = _engine(T1=PathRow(entity=SpotId("T1"), facing_mode=FacingMode.AGAINST_TRAVEL))
    assert approx_equal(against.facing_at("T1", 4.0), 0.0)


def test_toward_point_and_entity_facing() -> None:
    point = _engine(
        T1=PathRow(
            entity=SpotId("T1"), facing_mode=FacingMode.TOWARD_POINT, facing_value=(10.0, 7.0)
        )
    )
    assert approx_equal(point.facing_at("T1", 4.0), 90.0)
    entity = _engine(
        T1=PathRow(
            entity=SpotId("T1"), facing_mode=FacingMode.TOWARD_ENTITY, facing_value=SpotId("T2")
        )
    )
    assert approx_equal(entity.facing_at("T1", 4.0), 90.0)


def test_fixed_facing_needs_degrees() -> None:
    with pytest.raises(PathError):
        _engine(T1=PathRow(entity=SpotId("T1"), facing_mode=FacingMode.FIXED)).compile(TID)


def test_facing_override_wins_inside_its_range() -> None:
    base = with_rows({"T1": PathRow(entity=SpotId("T1"), facing_mode=FacingMode.TRAVEL)})
    override = FacingOverride(
        entities=(SpotId("T1"),), mode=FacingMode.FIXED, value=270.0, start=2.0, end=4.0
    )
    engine = PathEngine(base.model_copy(update={"facing_overrides": (override,)}))
    assert approx_equal(engine.facing_at("T1", 3.0), 270.0)
    assert approx_equal(engine.facing_at("T1", 6.0), 180.0)


def test_velocity_and_state() -> None:
    engine = PathEngine(two_set_show())
    assert approx_equal(engine.velocity_at("T1", 4.0), (0.0, 0.5), abs_tol=1e-6)
    state = engine.state_at("T1", 4.0)
    assert state.moving
    assert state.on_field
    assert not PathEngine(two_set_show()).state_at("T1", 9.0).moving


def test_entrance_is_on_field_while_walking_on() -> None:
    base = two_set_show()
    first = base.sets[0].with_placements({"T1": {"on_field": False}})
    engine = PathEngine(base.model_copy(update={"sets": (first, base.sets[1])}))
    assert not engine.state_at("T1", 0.0).on_field
    assert engine.state_at("T1", 4.0).on_field


def test_positions_vectorized_in_selection_order() -> None:
    pts = PathEngine(two_set_show()).positions(4.0, ["T2", "T1"])
    assert approx_equal(pts, [[2.0, 7.0], [0.0, 7.0]])
    assert PathEngine(two_set_show()).positions(4.0).shape == (4, 2)


def test_sample_reports_transition_and_tau() -> None:
    frame = PathEngine(two_set_show()).sample(3.0)
    assert frame.tid == TID
    assert approx_equal(frame.tau, 3.0)
    assert len(frame.states) == 4


def test_frames_per_count_and_fps() -> None:
    engine = PathEngine(two_set_show())
    assert len(list(engine.frames(0.0, 8.0, per_count=2))) == 17
    assert len(list(engine.frames(0.0, 8.0, fps=4))) == 17


def test_straight_linear_move_is_one_segment() -> None:
    segments = [s for s in PathEngine(two_set_show()).segments(TID) if s.entity == "T1"]
    assert len(segments) == 1
    assert approx_equal(segments[0].p1, (0.0, 9.0))


def test_eased_segments_stay_within_tolerance() -> None:
    engine = _engine(
        T1=PathRow(entity=SpotId("T1"), easing=Easing(kind="ease_in_out", strength=3.0))
    )
    segments = [s for s in engine.segments(TID, tol=0.01) if s.entity == "T1"]
    assert approx_equal(segments[0].t0, 0.0)
    assert approx_equal(segments[-1].t1, 8.0)
    midway = [s for s in segments if approx_equal(s.t0, 4.0)]
    assert len(midway) == 1
    assert approx_equal(midway[0].p0, (0.0, 7.0))
    for seg in segments:
        mid_t = (seg.t0 + seg.t1) / 2
        expected = (np.asarray(seg.p0) + np.asarray(seg.p1)) / 2
        assert np.linalg.norm(engine.position("T1", mid_t, local=TID) - expected) <= 0.01 + 1e-9


def test_segments_are_contiguous_in_time() -> None:
    row = PathRow(
        entity=SpotId("T1"), style=PathStyle.CURVE, waypoints=(Waypoint(x=3.0, y=7.0),), start=1.0
    )
    segments = [s for s in _engine(T1=row).segments(TID) if s.entity == "T1"]
    assert segments[0].t0 == 0.0
    assert segments[-1].t1 == 8.0
    assert all(a.t1 == b.t0 for a, b in itertools.pairwise(segments))


def test_path_geometry_runs_start_to_end() -> None:
    pts = PathEngine(two_set_show()).path_geometry("T1", TID)
    assert approx_equal(pts[0], (0.0, 5.0))
    assert approx_equal(pts[-1], (0.0, 9.0))


def test_compile_all_and_invalidate() -> None:
    engine = PathEngine(two_set_show())
    compiled = engine.compile_all()
    assert list(compiled) == [TID]
    assert set(compiled[engine.show.transition_id(TID)].plans) == {"T1", "T2", "T3", "T4"}
    engine.invalidate(transitions=[TID])
    assert engine.compile(TID).counts == 8.0


def test_facing_override_resolves_selection_expressions() -> None:
    base = with_rows({"T1": PathRow(entity=SpotId("T1"), facing_mode=FacingMode.TRAVEL)})
    override = FacingOverride(
        entities="T1-T2", mode=FacingMode.FIXED, value=270.0, start=2.0, end=4.0
    )
    engine = PathEngine(base.model_copy(update={"facing_overrides": (override,)}))
    assert approx_equal(engine.facing_at("T2", 3.0), 270.0)
    assert approx_equal(engine.facing_at("T3", 3.0), 0.0)


def test_regression_steps_easing_is_not_aliased_by_segment_probes() -> None:
    """Starts a segment on every step of a steps easing instead of aliasing between probes."""
    row = PathRow(entity=SpotId("T1"), end=1.0, easing=Easing(kind="steps", strength=4.0))
    engine = _engine(T1=row)
    segments = [s for s in engine.segments(TID, tol=0.01) if s.entity == "T1"]
    moving = [s for s in segments if s.t1 <= 1.0]
    starts = {round(s.t0, 9): s.p0 for s in moving}
    for share, y in ((0.25, 6.0), (0.5, 7.0), (0.75, 8.0)):
        assert approx_equal(starts[share], (0.0, y))
    assert approx_equal(moving[-1].p1, (0.0, 9.0))


def test_regression_positions_resolves_selection_expressions() -> None:
    """Resolves selection expressions such as "T1-T2" and "group:trumpets" in positions()."""
    engine = PathEngine(two_set_show())
    assert approx_equal(engine.positions(4.0, "T1-T2"), [[0.0, 7.0], [2.0, 7.0]])
    assert approx_equal(engine.positions(4.0, "group:trumpets").shape, (4, 2))


SEGMENT_TOL = 0.02
PROBES_PER_SEGMENT = 25
TRACE_SAMPLES = 4001
EIGHT_TURNS = 8 * 360.0
MAX_ONE_CYCLE_SEGMENTS = 32
HINT_TOL_MARGIN = 1.01
WEAVE_ROW = PathRow(
    entity=SpotId("T1"), style=PathStyle.CUSTOM, motion_fn="weave", motion_params={"cycles": 4}
)


def _segment_deviation(engine: PathEngine, entity: str) -> float:
    worst = 0.0
    for seg in engine.entity_segments(entity, TID):
        for share in np.linspace(0.0, 1.0, PROBES_PER_SEGMENT):
            t = seg.t0 + share * (seg.t1 - seg.t0)
            linear = np.array(seg.p0) + share * (np.array(seg.p1) - np.array(seg.p0))
            true = engine.position(entity, t, local=TID)
            worst = max(worst, float(np.linalg.norm(linear - true)))
    return worst


def _traced_length(engine: PathEngine, entity: str) -> float:
    taus = np.linspace(0.0, 8.0, TRACE_SAMPLES)
    pts = np.array([engine.position(entity, float(t), local=TID) for t in taus])
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())


def test_regression_segments_stay_within_tolerance_on_periodic_motion() -> None:
    """Keeps every segment within 0.02 m of the true path on a 4-cycle weave (audit 9.2; P0 #6)."""
    engine = _engine(T1=WEAVE_ROW)
    assert _segment_deviation(engine, "T1") <= SEGMENT_TOL + 1e-9


def test_regression_speed_distance_matches_traced_path_length() -> None:
    """Reports the travelled distance of a 4-cycle weave, not its 4 m chord (audit 9.2; P0 #6)."""
    engine = _engine(T1=WEAVE_ROW)
    assert approx_equal(engine.speed("T1", TID).distance, _traced_length(engine, "T1"), abs_tol=0.2)


def _eight_turn_engine() -> PathEngine:
    show = with_targets({"T2": {"x": 2.0, "y": 5.0}})
    row = PathRow(
        entity=SpotId("T2"), style=PathStyle.ROTATE, pivot=(0.0, 5.0), sweep_deg=EIGHT_TURNS
    )
    return PathEngine(with_rows({"T2": row}, show=show))


def test_regression_multi_turn_rotate_is_not_aliased_by_segment_probes() -> None:
    """Keeps an 8-turn rotate within tolerance though every eighth of it is the start point."""
    assert _segment_deviation(_eight_turn_engine(), "T2") <= SEGMENT_TOL + 1e-9


def test_regression_polyline_corner_is_a_segment_end() -> None:
    """Ends a segment exactly on an untimed polyline corner under a non-linear easing."""
    row = PathRow(
        entity=SpotId("T1"),
        style=PathStyle.POLYLINE,
        waypoints=(Waypoint(x=3.0, y=6.0),),
        easing=Easing(kind="ease_in_out", strength=3.0),
    )
    ends = [seg.p1 for seg in _engine(T1=row).entity_segments("T1", TID)]
    assert any(approx_equal(end, (3.0, 6.0), abs_tol=1e-9) for end in ends)


def test_regression_custom_motion_samples_are_not_forced_segment_ends() -> None:
    """Fits a 1-cycle weave with far fewer segments than its 256 sample pieces."""
    row = WEAVE_ROW.replace(motion_params={"cycles": 1})
    assert len(_engine(T1=row).entity_segments("T1", TID)) <= MAX_ONE_CYCLE_SEGMENTS


def test_regression_unreachable_segment_bound_raises_path_error() -> None:
    """Raises SEGMENT_BOUND instead of accepting a segment it cannot bring within tol."""
    with pytest.raises(PathError) as info:
        _eight_turn_engine().entity_segments("T2", TID, tol=1e-15)
    assert info.value.code == "SEGMENT_BOUND"
    assert set(info.value.context) == {"entity", "tid", "tol", "error"}


def test_regression_segment_bound_hint_names_an_accepted_tol() -> None:
    """Names, for a probed motion, a tol under which the failing segment is accepted."""
    row = PathRow(entity=SpotId("T1"), style=PathStyle.CURVE, waypoints=(Waypoint(x=2.0, y=7.0),))
    with pytest.raises(PathError) as info:
        _engine(T1=row).entity_segments("T1", TID, tol=1e-15)
    named = re.search(r"tol above ([0-9.e+-]+) m", info.value.hint)
    assert named is not None
    error = info.value.context["error"]
    assert isinstance(error, float)
    assert float(named.group(1)) * HINT_TOL_MARGIN * PROBE_TOL_SHARE >= error


@pytest.mark.parametrize("tol", [0.0, -0.02, math.nan, math.inf])
def test_regression_segments_reject_a_tolerance_that_is_not_positive(tol: float) -> None:
    """Rejects a zero, negative or non-finite segment tolerance instead of looping on it."""
    with pytest.raises(ShowPyError):
        PathEngine(two_set_show()).segments(TID, tol=tol)


def _override_show(rows: dict[str, PathRow], override: FacingOverride, y_hold: bool) -> Show:
    show = with_targets({"T1": {"y": 5.0}} if y_hold else {})
    return with_rows(rows, show=show).model_copy(update={"facing_overrides": (override,)})


@defect("UP-9.2-07")
def test_regression_facing_override_applies_to_holding_spot() -> None:
    """Applies a facing override to a spot on a HOLD row (audit 9.2)."""
    override = FacingOverride(
        entities=(SpotId("T1"),), mode=FacingMode.FIXED, value=90.0, start=2.0, end=6.0
    )
    rows = {"T1": PathRow(entity=SpotId("T1"), style=PathStyle.HOLD)}
    show = _override_show(rows, override, y_hold=True)
    assert approx_equal(PathEngine(show).facing_at("T1", 4.0), 90.0, abs_tol=1e-6)


@defect("UP-9.2-08")
def test_regression_toward_entity_override_uses_query_time_position() -> None:
    """Faces the other spot where it is at the query count, not at the row window (audit 9.2)."""
    override = FacingOverride(
        entities=(SpotId("T1"),),
        mode=FacingMode.TOWARD_ENTITY,
        value=SpotId("T4"),
        start=0.0,
        end=8.0,
    )
    show = _override_show({"T1": PathRow(entity=SpotId("T1"), start=4.0)}, override, y_hold=False)
    engine = PathEngine(show)
    query = 1.0
    offset = engine.position("T4", query) - engine.position("T1", query)
    expected = vector_facing((float(offset[0]), float(offset[1])))
    assert approx_equal(engine.facing_at("T1", query), expected, abs_tol=0.5)


def _three_set_show() -> Show:
    base = two_set_show()
    third = base.sets[1].model_copy(update={"id": "3"})
    return base.model_copy(update={"sets": (*base.sets, third)})


@defect("UP-9.2-22")
def test_regression_invalidate_accepts_a_one_shot_iterable() -> None:
    """Drops every named transition even when they come from a generator (audit 9.2)."""
    engine = PathEngine(_three_set_show())
    engine.compile_all()
    engine.invalidate(transitions=iter([TID, ("2", "3")]))
    assert engine._cache == {}


@defect("UP-9.2-23")
def test_regression_invalidate_clears_override_selection_cache() -> None:
    """Clears the cached facing-override selections when caches are invalidated (audit 9.2)."""
    override = FacingOverride(entities="all", mode=FacingMode.FIXED, value=90.0, start=0.0, end=4.0)
    engine = PathEngine(two_set_show().model_copy(update={"facing_overrides": (override,)}))
    engine.facing_at("T1", 2.0)
    engine.invalidate(sets=["1", "2"])
    assert engine._override_cache == {}


@defect("UP-9.2-24")
def test_regression_spot_query_on_show_without_sets_raises_showpy_error() -> None:
    """Raises a ShowPyError, not IndexError, for a cast spot in a show with no sets (audit 9.2)."""
    engine = PathEngine(two_set_show().model_copy(update={"sets": ()}))
    with pytest.raises(ShowPyError):
        engine.position("T1", 1.0)


@defect("UP-9.2-25")
def test_regression_zero_count_transition_compiles_to_a_step() -> None:
    """Compiles and segments a 0-count transition instead of raising PathError (audit 9.2)."""
    base = two_set_show()
    instant = base.sets[1].model_copy(update={"counts": 0.0})
    engine = PathEngine(base.model_copy(update={"sets": (base.sets[0], instant)}))
    assert list(engine.compile_all()) == [TID]
    assert engine.segments(TID) == []


@defect("UP-9.2-26")
def test_regression_missing_placement_does_not_fail_other_spots() -> None:
    """Keeps other spots queryable when one spot lacks a placement in the next set (audit 9.2)."""
    base = two_set_show()
    broken = base.sets[1].model_copy(update={"placements": base.sets[1].placements[1:]})
    engine = PathEngine(base.model_copy(update={"sets": (base.sets[0], broken)}))
    assert approx_equal(engine.position("T2", 4.0), (2.0, 7.0))


@defect("UP-9.2-34")
def test_regression_velocity_is_full_speed_near_a_window_kink() -> None:
    """Reports the moving speed just after a window start, not half of it (audit 9.2)."""
    engine = _engine(T1=PathRow(entity=SpotId("T1"), start=2.0, end=6.0))
    velocity = engine.velocity_at("T1", 2.00005, local=TID)
    assert approx_equal(float(np.linalg.norm(velocity)), 1.0, abs_tol=1e-3)
