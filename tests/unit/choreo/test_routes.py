#!/usr/bin/env python3

"""
test_routes.py --- tests show-aware path builders: routes, follow, counter-march, L-moves, rotation

Contains:
    test_regression_two_part_respects_locked_rows: raises LockedError for a locked row instead of
        rewriting it
    test_regression_two_part_force_rewrites_locked_row: rewrites a locked row when forced
    test_regression_two_part_edits_unlocked_row_of_stored_transition: edits unlocked rows freely
    test_regression_make_route_with_single_vertex_raises_path_error: raises a PathError, not
        IndexError, when the whole route is one point
    test_regression_counter_march_with_no_members_raises_path_error: raises a PathError, not
        IndexError, for an empty file
    test_regression_rotate_path_refuses_locked_row: raises LockedError for a locked row
    test_regression_rotate_path_force_rewrites_locked_row: rewrites a locked row when forced
    test_regression_follow_and_set_targets_refuses_locked_row: raises LockedError for a locked row
    test_regression_follow_and_set_targets_force_rewrites_locked_row: rewrites a locked row when
        forced
    test_regression_follow_and_set_targets_force_moves_pinned_placement: moves a pinned placement
        when forced
    test_regression_counter_march_refuses_locked_row: raises LockedError for a locked row
    test_regression_counter_march_refuses_pinned_placement: raises LockedError for a pinned
        placement
    test_regression_counter_march_force_rewrites_locked_row_and_facing: rewrites a locked row and
        its facing when forced
    test_regression_counter_march_force_moves_pinned_placement: moves a pinned placement when
        forced
    test_regression_follow_cannot_replace_the_route_of_a_locked_row: raises LockedError when a
        locked row follows the route being replaced
    test_regression_follow_force_replaces_the_route_of_a_locked_row: replaces it when forced
    test_regression_second_counter_march_keeps_a_locked_route: refuses to rewrite a locked
        counter-march route unless forced
    test_regression_make_route_offsets_members_that_share_a_start: gives each of two file members
        on one spot its own arc length, so the file stays on the route
"""

import math

import pytest

from showpy.choreo.engine import PathEngine
from showpy.choreo.match_ops import with_set
from showpy.choreo.models import FacingMode, PathStyle, TransitionSpec
from showpy.choreo.paths import lock
from showpy.choreo.routes import (
    counter_march,
    follow,
    follow_and_set_targets,
    make_route,
    rotate_path,
    two_part,
)
from showpy.core.errors import LockedError, PathError
from showpy.core.ids import format_tid
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
    assert spec.row("T2").style == PathStyle.FOLLOW
    assert spec.routes[route.id].start == 2.0


def test_counter_march_reverses_the_file() -> None:
    show = counter_march(two_set_show(), TID, FILE, turn_at=(6.0, 8.0))
    engine = PathEngine(show)
    second = show.set("2")
    assert second.get("T4").point[1] < 8.0
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


def test_regression_two_part_force_rewrites_locked_row() -> None:
    """Rewrites a locked row when the caller passes force=True."""
    show = two_set_show()
    locked = lock(show.transition(TID), ["T1"])
    rewritten = two_part(show, locked, ["T1"], "x_first", force=True)
    assert rewritten.row("T1").style == PathStyle.POLYLINE


def test_regression_two_part_edits_unlocked_row_of_stored_transition() -> None:
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


def test_regression_rotate_path_force_rewrites_locked_row() -> None:
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


def test_regression_follow_and_set_targets_force_rewrites_locked_row() -> None:
    """Rewrites a locked member row as FOLLOW when forced."""
    show = _locked_show()
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=FILE)
    moved = follow_and_set_targets(show, TID, route, force=True)
    assert moved.transition(TID).row("T1").style == PathStyle.FOLLOW


def test_regression_follow_and_set_targets_force_moves_pinned_placement() -> None:
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


def test_regression_counter_march_force_rewrites_locked_row_and_facing() -> None:
    """Rewrites a locked row and its facing when forced."""
    moved = counter_march(_locked_show(), TID, FILE, turn_at=(6.0, 8.0), force=True)
    row = moved.transition(TID).row("T1")
    assert row.style == PathStyle.FOLLOW
    assert row.facing_mode == FacingMode.TRAVEL


def test_regression_counter_march_force_moves_pinned_placement() -> None:
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


def test_regression_follow_force_replaces_the_route_of_a_locked_row() -> None:
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
    assert forced.transition(TID).routes["counter-march-T4"].members == ("T4", "T3")


def test_regression_make_route_offsets_members_that_share_a_start() -> None:
    """Keeps the file on the route when a follower starts on the leader's spot (nightly 2026-11-15).

    T1 stands on T4's spot, so the route passes (6, 5) twice: T1 at arc length 0, T4 at 4.
    """
    show = two_set_show()
    show = with_set(show, show.set("1").with_placements({"T1": {"x": 6.0, "y": 5.0}}))
    route = make_route(show, TID, "T4", [(6.0, 15.0)], members=["T4", "T3", "T1"])
    assert approx_equal(route.offsets, (4.0, 2.0, 0.0))
    assert approx_equal(route.travel, 10.0)
    moved = follow_and_set_targets(show, TID, route)
    report = validate_paths(moved, moved.transition(TID))
    assert report.ok, [(i.code, i.message) for i in report.issues if i.level == "error"]
