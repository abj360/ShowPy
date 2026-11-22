#!/usr/bin/env python3

"""
test_routes.py --- tests show-aware path builders: routes, follow, counter-march, L-moves, rotation

Contains:
    test_make_route_puts_members_on_the_route_behind_the_leader: puts each member on the route at
        its own arc length behind the leader
    test_make_route_rejects_unknown_spacing: refuses a spacing mode other than keep
    test_follow_and_set_targets_moves_everyone_along_route: moves every member to its place on the
        route
    test_follow_and_set_targets_respects_pins: raises LockedError for a pinned member
    test_follow_assigns_rows_and_window: gives every member a FOLLOW row and stores the route with
        its window
    test_counter_march_reverses_the_file: ends the file in reverse order and reaches the turn
    test_two_part_moves_x_first: reaches the corner at the split count
    test_rotate_path_about_center: keeps spots at their radius from the centroid
    test_regression_two_part_respects_locked_rows: raises LockedError for a locked row instead of
        rewriting it
    test_two_part_force_rewrites_locked_row: rewrites a locked row when forced
    test_two_part_edits_unlocked_row_of_stored_transition: edits unlocked rows freely
    test_regression_make_route_with_single_vertex_raises_path_error: raises a PathError, not
        IndexError, when the whole route is one point
    test_regression_make_route_merges_near_coincident_starts_onto_one_offset: gives two members
        whose starts lie within MIN_GAP the offset of their shared vertex, not a ValueError
    test_regression_make_route_takes_each_offset_from_its_own_start_vertex: gives a leader that
        starts back on a follower's point its own vertex's offset, not the follower's
    test_regression_counter_march_with_no_members_raises_path_error: raises a PathError, not
        IndexError, for an empty file
    test_regression_rotate_path_refuses_locked_row: raises LockedError for a locked row
    test_rotate_path_force_rewrites_locked_row: rewrites a locked row when forced
    test_regression_follow_and_set_targets_refuses_locked_row: raises LockedError for a locked row
    test_follow_and_set_targets_force_rewrites_locked_row: rewrites a locked row when forced
    test_follow_and_set_targets_force_moves_pinned_placement: moves a pinned placement when forced
    test_regression_counter_march_refuses_locked_row: raises LockedError for a locked row
    test_regression_counter_march_refuses_pinned_placement: raises LockedError for a pinned
        placement
    test_counter_march_force_rewrites_locked_row_and_facing: rewrites a locked row and its facing
        when forced
    test_counter_march_force_moves_pinned_placement: moves a pinned placement when forced
    test_regression_follow_cannot_replace_the_route_of_a_locked_row: raises LockedError when a
        locked row follows the route being replaced
    test_follow_force_replaces_the_route_of_a_locked_row: replaces it when forced
    test_regression_second_counter_march_keeps_a_locked_route: refuses to rewrite a locked
        counter-march route unless forced
    test_regression_make_route_offsets_members_that_share_a_start: gives each of two file members on
        one spot its own arc length, so the file stays on the route
    test_regression_route_builder_reads_stored_locks: refuses to edit a row the stored transition
        locks, though the caller clears it
    test_route_builders_accept_a_spec_the_show_does_not_store: edits a spec whose ID matches no
        stored transition
    test_route_builders_leave_unselected_locked_rows_alone: edits selected spots while another spot
        stays locked
    test_route_storage_accepts_valid_routes: stores distinct routes under their IDs, replaces one
        without locks and round-trips
    test_route_storage_ignores_locks_on_other_routes: adds a new route while a row of another route
        stays locked
"""

import math

import pytest

from showpy.choreo.engine import PathEngine
from showpy.choreo.match_ops import with_set
from showpy.choreo.models import PlacementPatch
from showpy.choreo.path_models import FacingMode, PathStyle, TransitionSpec
from showpy.choreo.paths import lock
from showpy.choreo.routes import (
    MIN_GAP,
    counter_march,
    follow,
    follow_and_set_targets,
    make_route,
    rotate_path,
    two_part,
)
from showpy.core.errors import LockedError, PathError
from showpy.core.ids import RouteId, format_tid
from showpy.core.util import approx_equal
from showpy.show.models import Show
from showpy.show.validate import validate_paths
from tests.fixtures.motion_shows import TID, with_targets
from tests.fixtures.shows import two_set_show

FILE = ["T4", "T3", "T2", "T1"]


def test_make_route_puts_members_on_the_route_behind_the_leader() -> None:
    route = make_route(two_set_show(), TID, "T4", [(6.0, 15.0)], members=FILE)
    assert route.members == tuple(FILE)
    assert route.offsets[0] > route.offsets[1] > route.offsets[-1]
    assert approx_equal(route.offsets[-1], 0.0)
    assert approx_equal(route.travel, 10.0)


def test_make_route_rejects_unknown_spacing() -> None:
    with pytest.raises(PathError):
        make_route(two_set_show(), TID, "T4", [(6.0, 15.0)], spacing="even")  # type: ignore[arg-type]


def test_follow_and_set_targets_moves_everyone_along_route() -> None:
    show = two_set_show()
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=FILE)
    moved = follow_and_set_targets(show, TID, route)
    second = moved.set("2")
    assert approx_equal(second.get("T4").point, (6.0, 15.0))
    assert approx_equal(second.get("T1").point, (6.0, 9.0))
    engine = PathEngine(moved)
    assert approx_equal(engine.position("T1", 8.0), (6.0, 9.0))


def test_follow_and_set_targets_respects_pins() -> None:
    show = with_targets({"T1": {"pinned": True}})
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=FILE)
    with pytest.raises(LockedError):
        follow_and_set_targets(show, TID, route)


def test_follow_assigns_rows_and_window() -> None:
    route = make_route(two_set_show(), TID, "T4", [(6.0, 15.0)], members=FILE)
    spec = follow(TransitionSpec(id=TID, counts=8.0), route, start=2.0)
    assert {e: spec.row(e).style for e in FILE} == dict.fromkeys(FILE, PathStyle.FOLLOW)
    assert {spec.row(e).route for e in FILE} == {route.id}
    stored = spec.routes[route.id]
    assert (stored.start, stored.end, stored.members) == (2.0, None, route.members)


def test_counter_march_reverses_the_file() -> None:
    show = counter_march(two_set_show(), TID, FILE, turn_at=(6.0, 8.0))
    engine = PathEngine(show)
    second = show.set("2")
    reversed_file = {"T4": (5.0, 2.0), "T3": (5.0, 4.0), "T2": (5.0, 6.0), "T1": (5.0, 8.0)}
    for spot, point in reversed_file.items():
        assert approx_equal(second.get(spot).point, point, abs_tol=1e-6)
    assert approx_equal(engine.position("T4", 8.0), second.get("T4").point)
    leader_path = engine.path_geometry("T4", TID)
    assert max(p[1] for p in leader_path) >= 8.0 - 1e-6


def test_two_part_moves_x_first() -> None:
    show = with_targets({"T1": {"x": 4.0, "y": 9.0}})
    spec = two_part(show, show.transition(TID), ["T1"], "x_first", split=4.0)
    engine = PathEngine(show.model_copy(update={"transitions": (spec,)}))
    assert approx_equal(engine.position("T1", 4.0), (4.0, 5.0))


def test_rotate_path_about_center() -> None:
    base = two_set_show()
    first = base.sets[0]
    pivot = (3.0, 5.0)
    rotated = {
        p.entity: {"x": pivot[0] - (p.x - pivot[0]), "y": pivot[1] - (p.y - pivot[1])}
        for p in first.placements
    }
    show = with_targets(rotated)
    spec = rotate_path(show, show.transition(TID), list(first.entity_ids()), "center", 180.0)
    engine = PathEngine(show.model_copy(update={"transitions": (spec,)}))
    mid = engine.position("T1", 4.0)
    assert approx_equal(math.dist(mid, pivot), 3.0)


def test_regression_two_part_respects_locked_rows() -> None:
    """Raises LockedError for a locked row instead of rewriting it (audit 9.2; P0 #13)."""
    show = two_set_show()
    locked = lock(show.transition(TID), ["T1"])
    with pytest.raises(LockedError):
        two_part(show, locked, ["T1"], "x_first")


def test_two_part_force_rewrites_locked_row() -> None:
    """Rewrites a locked row when the caller passes force=True."""
    show = two_set_show()
    locked = lock(show.transition(TID), ["T1"])
    rewritten = two_part(show, locked, ["T1"], "x_first", force=True)
    assert rewritten.row("T1").style == PathStyle.POLYLINE


def test_two_part_edits_unlocked_row_of_stored_transition() -> None:
    """Edits an unlocked row of a stored transition without force."""
    show = two_set_show()
    stored = lock(show.transition(TID), ["T2"])
    show = show.replace(transitions=(stored,))
    edited = two_part(show, stored, ["T1"], "x_first")
    assert edited.row("T1").style == PathStyle.POLYLINE
    assert edited.row("T2").locked


def test_regression_make_route_with_single_vertex_raises_path_error() -> None:
    """Raises a PathError, not IndexError, when the whole route is one point (audit 9.2)."""
    show = two_set_show()
    start = show.set("1").get("T1").point
    with pytest.raises(PathError) as raised:
        make_route(show, TID, "T1", [start])
    assert raised.value.context == {"leader": "T1"}


def _moved(show: Show, spot: str, point: tuple[float, float]) -> Show:
    start = show.set(TID[0])
    return with_set(show, start.with_placements({spot: PlacementPatch(x=point[0], y=point[1])}))


def test_regression_make_route_merges_near_coincident_starts_onto_one_offset() -> None:
    """Gives members whose starts lie within MIN_GAP one shared offset, not a ValueError."""
    t3 = two_set_show().set(TID[0]).get("T3").point
    show = _moved(two_set_show(), "T4", (t3[0] + MIN_GAP / 2, t3[1]))
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=["T4", "T3"])
    assert route.offsets == (0.0, 0.0)
    assert approx_equal(route.travel, math.dist(t3, (6.0, 15.0)), abs_tol=1e-9)


def test_regression_make_route_takes_each_offset_from_its_own_start_vertex() -> None:
    """Gives a leader back on a follower's start the offset of its own vertex on the route."""
    t2 = two_set_show().set(TID[0]).get("T2").point
    show = _moved(two_set_show(), "T4", t2)
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=["T4", "T3", "T2"])
    assert route.offsets == (4.0, 2.0, 0.0)
    assert approx_equal(route.travel, math.dist(t2, (6.0, 15.0)), abs_tol=1e-9)


def test_regression_counter_march_with_no_members_raises_path_error() -> None:
    """Raises a PathError, not IndexError, for an empty file."""
    with pytest.raises(PathError) as raised:
        counter_march(two_set_show(), TID, [], turn_at=(6.0, 8.0))
    assert raised.value.context == {"transition": format_tid(TID)}


def _locked_show(entity: str = "T1") -> Show:
    show = two_set_show()
    return show.replace(transitions=(lock(show.transition(TID), [entity]),))


def test_regression_rotate_path_refuses_locked_row() -> None:
    """Raises LockedError when `rotate_path` meets a locked row."""
    show = _locked_show()
    with pytest.raises(LockedError):
        rotate_path(show, show.transition(TID), ["T1"], (0.0, 0.0), 90.0)


def test_rotate_path_force_rewrites_locked_row() -> None:
    """Rewrites a locked row as ROTATE when `force=True`."""
    show = _locked_show()
    spec = rotate_path(show, show.transition(TID), ["T1"], (0.0, 0.0), 90.0, force=True)
    assert spec.row("T1").style == PathStyle.ROTATE


def test_regression_follow_and_set_targets_refuses_locked_row() -> None:
    """Raises LockedError when a member row is locked."""
    show = _locked_show()
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=FILE)
    with pytest.raises(LockedError):
        follow_and_set_targets(show, TID, route)


def test_follow_and_set_targets_force_rewrites_locked_row() -> None:
    """Rewrites a locked member row as FOLLOW when forced."""
    show = _locked_show()
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=FILE)
    moved = follow_and_set_targets(show, TID, route, force=True)
    assert moved.transition(TID).row("T1").style == PathStyle.FOLLOW


def test_follow_and_set_targets_force_moves_pinned_placement() -> None:
    """Moves a pinned member onto the route when forced."""
    show = with_targets({"T1": {"pinned": True}})
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=FILE)
    moved = follow_and_set_targets(show, TID, route, force=True)
    assert approx_equal(moved.set("2").get("T1").point, (6.0, 9.0))


def test_regression_counter_march_refuses_locked_row() -> None:
    """Raises LockedError when a file member row is locked."""
    with pytest.raises(LockedError):
        counter_march(_locked_show(), TID, FILE, turn_at=(6.0, 8.0))


def test_regression_counter_march_refuses_pinned_placement() -> None:
    """Raises LockedError when a file member is pinned."""
    show = with_targets({"T1": {"pinned": True}})
    with pytest.raises(LockedError):
        counter_march(show, TID, FILE, turn_at=(6.0, 8.0))


def test_counter_march_force_rewrites_locked_row_and_facing() -> None:
    """Rewrites a locked row and its facing when forced."""
    moved = counter_march(_locked_show(), TID, FILE, turn_at=(6.0, 8.0), force=True)
    row = moved.transition(TID).row("T1")
    assert row.style == PathStyle.FOLLOW
    assert row.facing_mode == FacingMode.TRAVEL


def test_counter_march_force_moves_pinned_placement() -> None:
    """Moves a pinned file member when forced."""
    show = with_targets({"T1": {"pinned": True}})
    moved = counter_march(show, TID, FILE, turn_at=(6.0, 8.0), force=True)
    assert approx_equal(moved.set("2").get("T1").point, (5.0, 8.0))


def test_regression_follow_cannot_replace_the_route_of_a_locked_row() -> None:
    """Raises LockedError when `follow` replaces the route a locked row follows."""
    show = follow_and_set_targets(
        two_set_show(), TID, make_route(two_set_show(), TID, "T4", [(6.0, 15.0)], members=FILE)
    )
    show = show.replace(transitions=(lock(show.transition(TID), ["T1"]),))
    route = show.transition(TID).routes
    detour = make_route(show, TID, "T4", [(6.0, 12.0)], members=["T4"])
    with pytest.raises(LockedError):
        follow(show.transition(TID), detour.replace(id=next(iter(route))))


def test_follow_force_replaces_the_route_of_a_locked_row() -> None:
    """Replaces the locked row's route when `force=True`."""
    show = follow_and_set_targets(
        two_set_show(), TID, make_route(two_set_show(), TID, "T4", [(6.0, 15.0)], members=FILE)
    )
    spec = lock(show.transition(TID), ["T1"])
    route_id = next(iter(spec.routes))
    detour = make_route(show, TID, "T4", [(6.0, 12.0)], members=["T4"]).replace(id=route_id)
    forced = follow(spec, detour, force=True)
    assert forced.routes[route_id].members == ("T4",)


def test_regression_second_counter_march_keeps_a_locked_route() -> None:
    """Refuses a second counter-march that rewrites a route a locked row follows."""
    show = counter_march(two_set_show(), TID, FILE, (6.0, 15.0))
    locked = show.replace(transitions=(lock(show.transition(TID), ["T1"]),))
    with pytest.raises(LockedError):
        counter_march(locked, TID, ["T4", "T3"], (6.0, 15.0))
    forced = counter_march(locked, TID, ["T4", "T3"], (6.0, 15.0), force=True)
    assert forced.transition(TID).routes[RouteId("counter-march-T4")].members == ("T4", "T3")


def test_regression_make_route_offsets_members_that_share_a_start() -> None:
    """Keeps the file on the route when a follower starts on the leader's spot (nightly 2026-11-15).

    T1 stands on T4's spot, so the route passes (6, 5) twice: T1 at arc length 0, T4 at 4.
    """
    show = two_set_show()
    show = with_set(show, show.set("1").with_placements({"T1": PlacementPatch(x=6.0, y=5.0)}))
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=["T4", "T3", "T1"])
    assert approx_equal(route.offsets, (4.0, 2.0, 0.0))
    assert approx_equal(route.travel, 10.0)
    moved = follow_and_set_targets(show, TID, route)
    report = validate_paths(moved, moved.transition(TID))
    assert report.ok, [(i.code, i.message) for i in report.issues if i.level == "error"]


def test_regression_route_builder_reads_stored_locks() -> None:
    """Refuses to edit a row the stored transition locks, though the caller's copy clears it."""
    show = _locked_show()
    stored = show.transition(TID)
    cleared = stored.replace(rows={e: row.replace(locked=False) for e, row in stored.rows.items()})
    with pytest.raises(LockedError):
        two_part(show, cleared, ["T1"], "x_first")
    with pytest.raises(LockedError):
        rotate_path(show, cleared, ["T1"], (0.0, 0.0), 90.0)
    forced = two_part(show, cleared, ["T1"], "x_first", force=True)
    assert forced.row("T1").style == PathStyle.POLYLINE


def test_route_builders_accept_a_spec_the_show_does_not_store() -> None:
    """Edits a spec whose ID matches no stored transition and stores nothing in the show."""
    show = two_set_show()
    assert show.transitions == ()
    spec = TransitionSpec(id=TID, counts=show.set(TID[1]).counts)
    assert two_part(show, spec, ["T1"], "x_first").row("T1").style == PathStyle.POLYLINE
    assert rotate_path(show, spec, ["T1"], (0.0, 0.0), 90.0).row("T1").style == PathStyle.ROTATE
    assert show.transitions == ()


def test_route_builders_leave_unselected_locked_rows_alone() -> None:
    """Edits the selected spots without force while another spot's row stays locked."""
    show = _locked_show("T2")
    spec = show.transition(TID)
    rotated = rotate_path(show, spec, ["T1"], (0.0, 0.0), 90.0)
    bent = two_part(show, spec, ["T1"], "y_first")
    for edited, style in ((rotated, PathStyle.ROTATE), (bent, PathStyle.POLYLINE)):
        assert edited.row("T1").style == style
        assert edited.row("T2") == spec.row("T2")
        assert edited.row("T2").locked


def test_route_storage_accepts_valid_routes() -> None:
    """Stores distinct routes under their IDs, replaces one without locks, and round-trips."""
    show = two_set_show()
    spec = TransitionSpec(id=TID, counts=show.set(TID[1]).counts)
    first = make_route(show, TID, "T4", [(6.0, 15.0)], members=["T4", "T3"], route_id="r1")
    second = make_route(show, TID, "T2", [(2.0, 15.0)], members=["T2", "T1"], route_id="r2")
    spec = follow(follow(spec, first), second)
    assert dict(spec.routes) == {RouteId("r1"): first, RouteId("r2"): second}
    detour = make_route(show, TID, "T4", [(6.0, 12.0)], members=["T4", "T3"], route_id="r1")
    replaced = follow(spec, detour)
    assert replaced.routes[RouteId("r1")] == detour
    assert replaced.routes[RouteId("r2")] == second
    assert TransitionSpec.model_validate_json(replaced.model_dump_json()) == replaced


def test_route_storage_ignores_locks_on_other_routes() -> None:
    """Adds a new route while a row that follows another route stays locked."""
    show = two_set_show()
    spec = TransitionSpec(id=TID, counts=show.set(TID[1]).counts)
    first = make_route(show, TID, "T4", [(6.0, 15.0)], members=["T4", "T3"], route_id="r1")
    locked = lock(follow(spec, first), ["T3"])
    other = make_route(show, TID, "T2", [(2.0, 15.0)], members=["T2", "T1"], route_id="r2")
    stored = follow(locked, other)
    assert stored.row("T3").locked
    assert stored.routes[RouteId("r1")] == first
    assert stored.row("T1").route == RouteId("r2")
