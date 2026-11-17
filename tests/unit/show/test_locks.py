#!/usr/bin/env python3

"""
test_locks.py --- tests detecting edits to pinned or locked items

Contains:
    test_regression_locked_changes_sees_a_replaced_route: reports a locked row whose route changed
    test_locked_row_changes_ignores_unlocked_routes: stays quiet about routes no locked row uses
"""

from showpy.choreo.paths import lock
from showpy.choreo.routes import follow_and_set_targets, make_route
from showpy.core.ids import format_tid
from showpy.show.locks import locked_changes, locked_row_changes
from showpy.show.models import Show
from tests.fixtures.motion_shows import TID
from tests.fixtures.shows import two_set_show

FILE = ["T4", "T3", "T2", "T1"]


def _routed_show() -> Show:
    show = follow_and_set_targets(
        two_set_show(), TID, make_route(two_set_show(), TID, "T4", [(6.0, 15.0)], members=FILE)
    )
    return show.replace(transitions=(lock(show.transition(TID), ["T1"]),))


def test_regression_locked_changes_sees_a_replaced_route() -> None:
    """Reports a locked row whose referenced route differs or disappears."""
    before = _routed_show()
    spec = before.transition(TID)
    route_id, route = next(iter(spec.routes.items()))
    changed = spec.replace(routes={route_id: route.replace(end=3.0)})
    expected = [f"route {route_id} of path T1 in {format_tid(TID)}"]
    assert locked_changes(before, before.replace(transitions=(changed,))) == expected
    assert locked_row_changes(spec, spec.replace(routes={})) == expected


def test_locked_row_changes_ignores_unlocked_routes() -> None:
    """Reports nothing when the route belongs only to unlocked rows."""
    show = follow_and_set_targets(
        two_set_show(), TID, make_route(two_set_show(), TID, "T4", [(6.0, 15.0)], members=FILE)
    )
    spec = show.transition(TID)
    assert locked_row_changes(spec, spec.replace(routes={})) == []
