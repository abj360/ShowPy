#!/usr/bin/env python3

"""
test_routes.py --- tests show-aware path builders: routes, follow, counter-march, L-moves, rotation
"""

import math

import pytest

from stage.choreo.engine import PathEngine
from stage.choreo.models import PathStyle, TransitionSpec
from stage.choreo.routes import (
    counter_march,
    follow,
    follow_and_set_targets,
    make_route,
    rotate_path,
    two_part,
)
from stage.core.errors import LockedError, PathError
from stage.core.util import approx_equal
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
