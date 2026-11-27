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
    test_regression_is_stale_reads_the_proposal_at_the_head: judges a proposal the workspace's
        revision predates, as preview and accept do
    test_regression_is_stale_of_an_accepted_proposal_is_false_from_a_stale_workspace: sees the
        accepted state stored at the head
    test_regression_conflict_message_lists_five_and_counts_the_rest: bounds the message and the
        context list of a proposal with many conflicts
    test_rescale_to_the_same_counts_merges_beside_a_head_row_edit: leaves a transition alone when
        the rescale keeps its counts
    test_rescale_and_merge_report_a_row_both_flag_once: lists a row the merge and the rescale rule
        both flag one time
    test_regression_paths_proposal_conflicts_with_a_head_shrink_of_the_set: refuses proposed
        rows that no longer fit counts the head shortened
    test_regression_paths_proposal_route_conflicts_with_a_head_rescale_shrink: refuses a proposed
        route that no longer fits counts the head rescaled
    test_regression_paths_proposal_conflicts_with_its_own_row_past_the_counts: refuses a proposed
        row that never fit its transition
    test_regression_preview_without_a_saved_show_does_not_claim_there_are_no_revisions: names the
        missing show when the head holds only proposals
    test_rescale_merges_beside_a_facing_override_ending_at_the_rescaled_set_start: keeps an
        override that ends exactly where the rescaled set starts
    test_rescale_of_two_sets_conflicts_with_an_override_after_the_first: judges overrides from the
        earliest rescaled set
    test_shrink_conflicts_with_a_route_the_head_added: refuses a plain shrink beside a head route
    test_rescale_conflicts_with_a_facing_override_the_head_removed: refuses a rescale beside a
        head removal of an override it would shift
    test_regression_paths_proposal_conflicts_with_a_head_rescale: refuses a proposed row timed
        against counts the head rescaled
    test_regression_proposed_row_conflicts_with_a_head_rescale_of_a_stored_row: refuses the
        proposed row beside a stored row the head scaled
    test_regression_paths_proposal_conflicts_with_a_head_rescale_grow: refuses a proposed row
        after the head lengthened the set
    test_regression_paths_proposal_conflicts_with_a_plain_head_retime: refuses a proposed row
        after the head retimed without rescaling
    test_placement_proposal_merges_beside_a_head_retime: keeps a proposal that touches no row or
        route of the retimed transition
    test_regression_both_sides_rescaling_list_a_stored_row_once: lists a row the rescale rule and
        the head-retime rule both flag one time
    test_conflicts_come_in_key_order: orders the merge's and the rules' conflicts by item key
    test_proposed_route_conflicts_with_a_plain_head_grow: refuses a fitting proposed route after
        the head lengthened the set
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from e8s.choreo.models import Placement
from e8s.choreo.path_models import FacingMode, FacingOverride, PathRow, Route, TransitionSpec
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, StorageError
from e8s.core.ids import RouteId, SetId, SpotId, make_tid
from e8s.geo.curves import Segment
from e8s.show.collab_models import ChangeOp, CountsOp, PathsOp, PlacementOp
from e8s.show.manage import add_set, set_counts
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Show
from e8s.show.proposals import accept, is_stale, preview_proposal, propose
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import Workspace, open_workspace
from tests.fixtures.shows import two_set_show
from tests.helpers import SHOW_ID, open_session

AT = datetime(2026, 11, 11, tzinfo=UTC)
STEP = timedelta(minutes=1)
SHORTER = 4.0


def _head_edits(
    base: Show, op: ChangeOp | Sequence[ChangeOp], edit: Callable[[Show], Show]
) -> Workspace:
    """Proposes `op` against `base`, then saves the designer's `edit` on top."""
    _, agent, designer = open_session(base, AT)
    propose(agent, list(op) if isinstance(op, Sequence) else [op], "", "p1", AT + STEP)
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


def test_regression_is_stale_reads_the_proposal_at_the_head() -> None:
    """Judges a proposal made after the workspace's revision, not UnknownRefError."""
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [CountsOp(set_id=SetId("2"), counts=SHORTER)], "", "p1", AT + STEP)
    assert not is_stale(designer, "p1")
    assert not preview_proposal(designer, "p1").conflicts


def test_regression_is_stale_of_an_accepted_proposal_is_false_from_a_stale_workspace() -> None:
    """Reads the accepted state the head holds, so a closed proposal is never stale."""
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [CountsOp(set_id=SetId("2"), counts=SHORTER)], "", "p1", AT + STEP)
    designer.refresh()
    accept(agent, "p1", AT + 2 * STEP)
    save_show(agent, set_counts(load_show(agent), "2", 6.0), AT + 3 * STEP)
    assert not is_stale(designer, "p1")


def test_regression_conflict_message_lists_five_and_counts_the_rest() -> None:
    """Bounds the refusal text of a proposal that conflicts on many items."""
    names = ["T1", "T2", "T3", "T4"]

    def add_many(head: Show) -> Show:
        rows = {SpotId(n): PathRow(entity=SpotId(n), start=1.0) for n in names}
        routes = {RouteId(f"r{n}"): _route(4.0).replace(id=RouteId(f"r{n}")) for n in (1, 2)}
        return head.replace(transitions=(_spec(head, rows=rows, routes=routes),))

    rescale = CountsOp(set_id=SetId("2"), counts=2 * two_set_show().set("2").counts, rescale=True)
    designer = _head_edits(two_set_show(), rescale, add_many)
    with pytest.raises(ConflictError) as info:
        accept(designer, "p1", AT + 3 * STEP)
    assert "(+1 more)" in info.value.message
    listed = info.value.context["conflicts"]
    assert isinstance(listed, list)
    assert len(listed) == 5
    assert info.value.context["conflict_count"] == 6


def _edit_row(head: Show, start: float) -> Show:
    row = PathRow(entity=SpotId("T1"), start=start)
    return head.replace(transitions=(_spec(head, rows={SpotId("T1"): row}),))


def test_rescale_to_the_same_counts_merges_beside_a_head_row_edit() -> None:
    """Does not treat a rescale to the current counts as a retime of the transition."""
    base = _edit_row(two_set_show(), 2.0)
    same = CountsOp(set_id=SetId("2"), counts=base.set("2").counts, rescale=True)
    designer = _head_edits(base, same, lambda head: _edit_row(head, 3.0))
    assert not is_stale(designer, "p1")


def test_rescale_and_merge_report_a_row_both_flag_once() -> None:
    """Lists a row the merge and the rescale rule both flag a single time."""
    base = _edit_row(two_set_show(), 2.0)
    rescale = CountsOp(set_id=SetId("2"), counts=2 * base.set("2").counts, rescale=True)
    designer = _head_edits(base, rescale, lambda head: _edit_row(head, 3.0))
    conflicts = preview_proposal(designer, "p1").conflicts
    assert [(c.key.kind, c.key.path) for c in conflicts] == [("path_row", ("1-2", "T1"))]


def test_regression_paths_proposal_conflicts_with_a_head_shrink_of_the_set() -> None:
    """Refuses rows made against the longer counts once the head shortened the set."""
    base = two_set_show()
    late = PathRow(entity=SpotId("T1"), start=6.0)
    designer = _head_edits(
        base,
        PathsOp(spec=_spec(base, rows={SpotId("T1"): late})),
        lambda head: set_counts(head, "2", SHORTER),
    )
    _assert_refused_everywhere(designer, "path_row", "T1")


def test_regression_paths_proposal_route_conflicts_with_a_head_rescale_shrink() -> None:
    """Refuses a route made against the longer counts once the head rescaled the set down."""
    base = two_set_show()
    designer = _head_edits(
        base,
        PathsOp(spec=_spec(base, routes={RouteId("r1"): _route(7.0)})),
        lambda head: set_counts(head, "2", SHORTER, rescale=True),
    )
    _assert_refused_everywhere(designer, "route", "r1")


def test_regression_paths_proposal_conflicts_with_its_own_row_past_the_counts() -> None:
    """Refuses a proposed row whose window never fit the transition it was stored on."""
    base = two_set_show()
    late = PathRow(entity=SpotId("T1"), start=base.set("2").counts + 1.0)
    designer = _head_edits(
        base,
        PathsOp(spec=_spec(base, rows={SpotId("T1"): late})),
        lambda head: head,
    )
    _assert_refused_everywhere(designer, "path_row", "T1")


def test_regression_preview_without_a_saved_show_does_not_claim_there_are_no_revisions() -> None:
    """Names the missing show, not missing revisions, when the head holds only proposals."""
    ws = open_workspace(InMemoryRepository(), SHOW_ID)
    propose(ws, [CountsOp(set_id=SetId("2"), counts=SHORTER)], "", "p1", AT)
    with pytest.raises(StorageError) as info:
        preview_proposal(ws, "p1")
    assert "no revisions" not in info.value.message
    assert "holds no saved show" in info.value.message


def test_rescale_merges_beside_a_facing_override_ending_at_the_rescaled_set_start() -> None:
    """Keeps an override ending exactly at the rescaled set's start, which the rescale leaves."""
    rescale = CountsOp(set_id=SetId("3"), counts=2 * _three_sets().set("3").counts, rescale=True)
    designer = _head_edits(_three_sets(), rescale, lambda head: _with_override(head, 6.0, 8.0))
    assert not is_stale(designer, "p1")


def test_rescale_of_two_sets_conflicts_with_an_override_after_the_first() -> None:
    """Judges overrides from the earliest rescaled set, so one inside the first set conflicts."""
    base = _three_sets()
    rescale = [
        CountsOp(set_id=SetId(set_id), counts=2 * base.set(set_id).counts, rescale=True)
        for set_id in ("2", "3")
    ]
    designer = _head_edits(base, rescale, lambda head: _with_override(head, 1.0, 3.0))
    assert is_stale(designer, "p1")


def test_shrink_conflicts_with_a_route_the_head_added() -> None:
    """Refuses a shrink without rescale beside a head route that no longer fits."""
    designer = _head_edits(
        two_set_show(),
        CountsOp(set_id=SetId("2"), counts=SHORTER),
        lambda head: head.replace(transitions=(_spec(head, routes={RouteId("r1"): _route(7.0)}),)),
    )
    _assert_refused_everywhere(designer, "route", "r1")


def test_rescale_conflicts_with_a_facing_override_the_head_removed() -> None:
    """Refuses a rescale when the head removed an override the rescale would have shifted."""
    base = _with_override(_three_sets(), 9.0, 11.0)
    rescale = CountsOp(set_id=SetId("2"), counts=2 * base.set("2").counts, rescale=True)
    designer = _head_edits(base, rescale, lambda head: head.replace(facing_overrides=()))
    assert is_stale(designer, "p1")


def _t1_row(start: float) -> Mapping[SpotId, PathRow]:
    return {SpotId("T1"): PathRow(entity=SpotId("T1"), start=start)}


def test_regression_paths_proposal_conflicts_with_a_head_rescale() -> None:
    """Refuses a row timed against the base's counts once the head rescaled the set."""
    base = two_set_show()
    designer = _head_edits(
        base,
        PathsOp(spec=_spec(base, rows=_t1_row(2.0))),
        lambda head: set_counts(head, "2", SHORTER, rescale=True),
    )
    _assert_refused_everywhere(designer, "path_row", "T1")


def test_regression_proposed_row_conflicts_with_a_head_rescale_of_a_stored_row() -> None:
    """Refuses the proposed row when the head scaled the stored row it sits beside."""
    stored = PathRow(entity=SpotId("T2"), start=4.0)
    base = two_set_show()
    base = base.replace(transitions=(_spec(base, rows={SpotId("T2"): stored}),))
    proposed = _spec(base, rows={SpotId("T2"): stored, **_t1_row(2.0)})
    designer = _head_edits(
        base, PathsOp(spec=proposed), lambda head: set_counts(head, "2", SHORTER, rescale=True)
    )
    _assert_refused_everywhere(designer, "path_row", "T1")


def test_regression_paths_proposal_conflicts_with_a_head_rescale_grow() -> None:
    """Refuses a row that would land at the same absolute count in a longer transition."""
    base = two_set_show()
    designer = _head_edits(
        base,
        PathsOp(spec=_spec(base, rows=_t1_row(6.0))),
        lambda head: set_counts(head, "2", 2 * base.set("2").counts, rescale=True),
    )
    _assert_refused_everywhere(designer, "path_row", "T1")


def test_regression_paths_proposal_conflicts_with_a_plain_head_retime() -> None:
    """Refuses a proposed row after a head retime that did not rescale: it cannot be told apart."""
    base = two_set_show()
    designer = _head_edits(
        base,
        PathsOp(spec=_spec(base, rows=_t1_row(2.0))),
        lambda head: set_counts(head, "2", SHORTER),
    )
    _assert_refused_everywhere(designer, "path_row", "T1")


def test_placement_proposal_merges_beside_a_head_retime() -> None:
    """Keeps a proposal that changes no row or route of the transition the head retimed."""
    move = PlacementOp(set_id=SetId("2"), placement=Placement(entity=SpotId("T1"), x=-2.0, y=9.0))
    designer = _head_edits(two_set_show(), move, lambda head: set_counts(head, "2", SHORTER))
    assert not is_stale(designer, "p1")
    accept(designer, "p1", AT + 3 * STEP)
    assert load_show(designer).set("2").counts == SHORTER


def test_regression_both_sides_rescaling_list_a_stored_row_once() -> None:
    """Lists a row once when the rescale rule and the head-retime rule both flag it."""
    base = _edit_row(two_set_show(), 2.0)
    rescale = CountsOp(set_id=SetId("2"), counts=SHORTER, rescale=True)
    designer = _head_edits(base, rescale, lambda head: set_counts(head, "2", SHORTER, rescale=True))
    conflicts = preview_proposal(designer, "p1").conflicts
    assert [(c.key.kind, c.key.path) for c in conflicts] == [("path_row", ("1-2", "T1"))]
    with pytest.raises(ConflictError) as info:
        accept(designer, "p1", AT + 3 * STEP)
    assert info.value.message.count("path_row 1-2/T1") == 1
    assert info.value.context["conflict_count"] == 1


@pytest.mark.parametrize("rescale", [False, True])
def test_conflicts_come_in_key_order(rescale: bool) -> None:
    """Orders merge and rule conflicts by item key, whichever way the head retimed."""
    base = two_set_show()
    stored = {SpotId("T2"): PathRow(entity=SpotId("T2"), start=1.0)}
    base = base.replace(transitions=(_spec(base, rows=stored),))
    designer = _head_edits(
        base,
        PathsOp(spec=_spec(base)),
        lambda head: set_counts(head, "2", 6.0, rescale=rescale),
    )
    conflicts = preview_proposal(designer, "p1").conflicts
    assert [(c.key.kind, c.key.path) for c in conflicts] == [
        ("path_row", ("1-2", "T2")),
        ("transition", ("1-2",)),
    ]


def test_proposed_route_conflicts_with_a_plain_head_grow() -> None:
    """Refuses a route that still fits once the head lengthened the set: its timing is stale."""
    base = two_set_show()
    designer = _head_edits(
        base,
        PathsOp(spec=_spec(base, routes={RouteId("r1"): _route(6.0)})),
        lambda head: set_counts(head, "2", 12.0),
    )
    _assert_refused_everywhere(designer, "route", "r1")
