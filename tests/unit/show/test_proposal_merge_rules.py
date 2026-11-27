#!/usr/bin/env python3

"""
test_proposal_merge_rules.py --- tests the cross-item rules accept, is_stale and preview share

Contains:
    test_regression_shrink_conflicts_with_a_row_the_head_added_past_the_new_counts: refuses a
        retime that leaves a head-added row running past the new counts
    test_regression_rescale_shrink_conflicts_with_a_route_the_head_added: refuses a rescale
        shrink beside a head-added route
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from e8s.choreo.path_models import PathRow, Route, TransitionSpec
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError
from e8s.core.ids import RouteId, SetId, SpotId, make_tid
from e8s.geo.curves import Segment
from e8s.show.collab_models import ChangeOp, CountsOp
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


def test_regression_rescale_shrink_conflicts_with_a_route_the_head_added() -> None:
    """Refuses a rescale shrink beside a route the head added, which would land unscaled."""
    route = Route(
        id=RouteId("r1"),
        curve=Segment(a=(0.0, 0.0), b=(10.0, 0.0)),
        members=(SpotId("T1"),),
        offsets=(0.0,),
        travel=4.0,
        end=7.2,
    )
    designer = _head_edits(
        two_set_show(),
        CountsOp(set_id=SetId("2"), counts=SHORTER, rescale=True),
        lambda head: head.replace(transitions=(_spec(head, routes={RouteId("r1"): route}),)),
    )
    _assert_refused_everywhere(designer, "route", "r1")
