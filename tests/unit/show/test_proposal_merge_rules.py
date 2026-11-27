#!/usr/bin/env python3

"""
test_proposal_merge_rules.py --- tests the cross-item rules accept, is_stale and preview share

Contains:
    test_regression_shrink_conflicts_with_a_row_the_head_added_past_the_new_counts: refuses a
        retime that leaves a head-added row running past the new counts
    test_regression_rescale_shrink_conflicts_with_a_route_the_head_added: refuses a rescale
        shrink beside a head-added route
    test_rescale_conflicts_with_a_route_the_head_added: refuses a rescale beside a head-added route
        that stays inside the counts
    test_rescale_conflicts_with_a_facing_override_the_head_added: refuses a rescale beside a
        head-added override after the rescaled set
    test_rescale_merges_beside_a_facing_override_before_the_rescaled_set: keeps an override that
        ends before the rescaled set starts
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from e8s.choreo.path_models import FacingMode, FacingOverride, PathRow, Route, TransitionSpec
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError
from e8s.core.ids import RouteId, SetId, SpotId, make_tid
from e8s.geo.curves import Segment
from e8s.show.collab_models import ChangeOp, CountsOp
from e8s.show.manage import add_set
from e8s.show.models import Show
from e8s.show.proposals import accept, is_stale, preview_proposal, propose
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import Workspace
from tests.fixtures.shows import two_set_show
from tests.helpers import open_session

AT = datetime(2026, 11, 11, tzinfo=UTC)
STEP = timedelta(minutes=1)
SHORTER = 4.0


def _head_edits(base: Show, op: ChangeOp, edit: Callable[[Show], Show]) -> Workspace:
    """Proposes `op` against `base`, then saves the designer's `edit` on top."""
    _, agent, designer = open_session(base, AT)
    propose(agent, [op], "", "p1", AT + STEP)
    designer.refresh()
    save_show(designer, edit(load_show(designer)), AT + 2 * STEP)
    return designer


def _spec(
    show: Show,
    rows: Mapping[SpotId, PathRow] | None = None,
    routes: Mapping[RouteId, Route] | None = None,
) -> TransitionSpec:
    return TransitionSpec(
        id=make_tid("1", "2"),
        counts=show.set("2").counts,
        rows=rows or {},
        routes=routes or {},
    )


def _assert_refused_everywhere(designer: Workspace, kind: str, key: str) -> None:
    assert is_stale(designer, "p1")
    conflicts = preview_proposal(designer, "p1").conflicts
    assert [(c.key.kind, c.key.path) for c in conflicts] == [(kind, ("1-2", key))]
    with pytest.raises(ConflictError) as info:
        accept(designer, "p1", AT + 3 * STEP)
    assert info.value.code == ErrorCode.PROPOSAL_STALE


def test_regression_shrink_conflicts_with_a_row_the_head_added_past_the_new_counts() -> None:
    """Refuses to land a row whose window runs past the counts the proposal shortens to."""
    base = two_set_show()
    late = PathRow(entity=SpotId("T1"), start=6.0)
    designer = _head_edits(
        base,
        CountsOp(set_id=SetId("2"), counts=SHORTER),
        lambda head: head.replace(transitions=(_spec(head, rows={SpotId("T1"): late}),)),
    )
    _assert_refused_everywhere(designer, "path_row", "T1")


def _route(end: float) -> Route:
    return Route(
        id=RouteId("r1"),
        curve=Segment(a=(0.0, 0.0), b=(10.0, 0.0)),
        members=(SpotId("T1"),),
        offsets=(0.0,),
        travel=4.0,
        end=end,
    )


def test_regression_rescale_shrink_conflicts_with_a_route_the_head_added() -> None:
    """Refuses a rescale shrink beside a route the head added, which would land unscaled."""
    route = _route(7.2)
    designer = _head_edits(
        two_set_show(),
        CountsOp(set_id=SetId("2"), counts=SHORTER, rescale=True),
        lambda head: head.replace(transitions=(_spec(head, routes={RouteId("r1"): route}),)),
    )
    _assert_refused_everywhere(designer, "route", "r1")


def test_rescale_conflicts_with_a_route_the_head_added() -> None:
    """Refuses a rescale grow beside a head-added route, which would land mistimed."""
    designer = _head_edits(
        two_set_show(),
        CountsOp(set_id=SetId("2"), counts=2 * two_set_show().set("2").counts, rescale=True),
        lambda head: head.replace(transitions=(_spec(head, routes={RouteId("r1"): _route(4.0)}),)),
    )
    _assert_refused_everywhere(designer, "route", "r1")


def _with_override(head: Show, start: float, end: float) -> Show:
    override = FacingOverride(
        entities=(SpotId("T1"),), mode=FacingMode.AUDIENCE, start=start, end=end
    )
    return head.replace(facing_overrides=(override,))


def _three_sets() -> Show:
    return add_set(two_set_show(), "3", 4.0)


def test_rescale_conflicts_with_a_facing_override_the_head_added() -> None:
    """Refuses a rescale when the head added an override the rescale would shift."""
    rescale = CountsOp(set_id=SetId("2"), counts=2 * _three_sets().set("2").counts, rescale=True)
    designer = _head_edits(_three_sets(), rescale, lambda head: _with_override(head, 9.0, 11.0))
    assert is_stale(designer, "p1")
    conflicts = preview_proposal(designer, "p1").conflicts
    assert [(c.key.kind, c.key.path) for c in conflicts] == [("facing_override", ())]
    with pytest.raises(ConflictError):
        accept(designer, "p1", AT + 3 * STEP)


def test_rescale_merges_beside_a_facing_override_before_the_rescaled_set() -> None:
    """Keeps an override that ends before the rescaled set starts, which the rescale leaves."""
    rescale = CountsOp(set_id=SetId("3"), counts=2 * _three_sets().set("3").counts, rescale=True)
    designer = _head_edits(_three_sets(), rescale, lambda head: _with_override(head, 1.0, 3.0))
    assert not is_stale(designer, "p1")
