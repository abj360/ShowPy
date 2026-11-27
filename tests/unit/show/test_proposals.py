#!/usr/bin/env python3

"""
test_proposals.py --- tests the proposal record's invariants and the stored proposal lifecycle

Contains:
    test_regression_stored_proposal_is_parsed_as_strict_json: refuses NaN, repeated keys and a BOM
        in a stored proposal with STRICT_JSON
    test_proposal_survives_reopening_the_repository: reads a proposal back from a file repository
    test_rejected_proposal_keeps_its_reason: stores the reason and refuses a second close
    test_withdrawn_proposal_cannot_be_accepted: names the withdrawn state when accepting
    test_stale_is_derived_never_stored: derives staleness from the touched items, never stores it
    test_proposal_documents_are_not_show_changes: keeps proposal documents out of events and diffs
    test_accept_writes_the_show_and_the_proposal_in_one_revision: stores both in one revision
        whose parent is the accepted revision
    test_proposal_against_an_unsaved_show_is_accepted_without_overlap: accepts a root-based
        proposal against the head and never calls it stale
    test_propose_refuses_unsafe_and_taken_ids: refuses ids that are no path segment and repeats
    test_unknown_proposal_id_names_close_matches: lists close ids on a miss
    test_proposals_are_listed_by_id: sorts by id
    test_accept_of_a_stale_proposal_is_proposal_stale: names PROPOSAL_STALE with the overlap
    test_proposal_accepted__revision_is_set_exactly_when_accepted: refuses an accepted proposal
        without its revision and a revision on any other state
    test_proposal_reason_is_set_only_when_rejected: refuses a reason on any state but rejected
    test_accept_writes_the_change_and_the_state_in_one_revision: lands a merged change and the
        accepted state together after the base moved
    test_non_overlapping_proposal_lands_after_the_base_moved: keeps the designer's edit and the
        proposal's edit on different items
    test_preview_shows_the_merge_on_the_head: previews head against the merged show
    test_preview_of_a_conflicting_proposal_lists_only_its_conflicts: previews conflicts and
        nothing else
    test_is_stale_agrees_with_the_merge_conflicts: names the same items as accept refuses
    test_regression_preview_does_not_move_the_workspace: keeps the caller's base revision, so a
        stale write after a preview still conflicts
    test_rescale_merges_when_the_head_left_its_transition_alone: lands a rescale beside an edit
        elsewhere
    test_rescale_conflicts_with_a_row_the_head_added: refuses a rescale beside a head-added row
    test_rescale_conflicts_with_a_row_the_head_edited_to_the_rescaled_value: refuses an equal
        value the head wrote against the base's counts
    test_regression_accept_by_merge_respects_a_pin_added_after_the_base: refuses a merge that
        changes an item pinned on the head
    test_regression_accept_refuses_an_op_naming_a_spot_the_head_deleted: refuses, as a stale
        proposal, a placement, form, paths or row-facing op naming a spot the head deleted since
        the base
    test_regression_accept_refuses_a_proposal_naming_a_spot_never_in_the_cast: refuses a form op
        whose member was never in the cast, with is_stale and preview agreeing
    test_regression_new_form_under_a_pinned_set_is_locked: refuses, unless forced, a form op that
        adds a form to a pinned set, in apply_change and accept
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from e8s.cast.spots import delete_spots
from e8s.choreo.forms import line_form
from e8s.choreo.models import Placement
from e8s.choreo.path_models import EntityFacing, FacingMode, PathRow, TransitionSpec
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, E8sError, LockedError, SchemaError, UnknownRefError
from e8s.core.ids import SetId, SpotId, make_tid
from e8s.show.backend import digest
from e8s.show.changes import apply_change
from e8s.show.collab_models import (
    ChangeOp,
    CountsOp,
    FormOp,
    PathsOp,
    PlacementOp,
    Proposal,
    ProposalState,
)
from e8s.show.events import events_since
from e8s.show.file_repository import FileRepository
from e8s.show.history import diff_show
from e8s.show.history_models import Revision
from e8s.show.manage import add_set
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.pinning import pin
from e8s.show.proposals import (
    accept,
    get_proposal,
    is_stale,
    preview_proposal,
    proposals,
    propose,
    reject,
    withdraw,
)
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import Workspace, open_workspace
from tests.fixtures.shows import two_set_show
from tests.helpers import SHOW_ID, open_session

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)

STATES: tuple[ProposalState, ...] = ("open", "accepted", "rejected", "withdrawn")


def _proposal(state: ProposalState, *, accepted_revision: str = "", reason: str = "") -> Proposal:
    return Proposal(
        id="p1",
        base_revision="r1",
        change=(),
        state=state,
        accepted_revision=accepted_revision,
        reason=reason,
    )


def test_proposal_accepted_revision_is_set_exactly_when_accepted() -> None:
    assert _proposal("accepted", accepted_revision="r1").accepted_revision == "r1"
    with pytest.raises(SchemaError, match="accepted_revision"):
        _proposal("accepted")
    for state in STATES:
        if state != "accepted":
            with pytest.raises(SchemaError, match="accepted_revision"):
                _proposal(state, accepted_revision="r1")


def test_proposal_reason_is_set_only_when_rejected() -> None:
    assert _proposal("rejected", reason="too wide").reason == "too wide"
    assert _proposal("rejected").reason == ""
    for state in STATES:
        if state != "rejected":
            with pytest.raises(SchemaError, match="reason"):
                _proposal(state, accepted_revision="r1" if state == "accepted" else "", reason="x")


def _move(entity: str, x: float) -> PlacementOp:
    return PlacementOp(set_id=SetId("2"), placement=Placement(entity=SpotId(entity), x=x, y=9.0))


def test_proposal_survives_reopening_the_repository(tmp_path: Path) -> None:
    repo = FileRepository(tmp_path / "repo")
    saver = open_workspace(repo, SHOW_ID)
    save_show(saver, add_set(two_set_show(), "3", 4.0), AT)
    made = propose(open_workspace(repo, SHOW_ID), [_move("T3", -1.0)], "shift", "p1", AT + STEP)
    reopened = open_workspace(FileRepository(tmp_path / "repo"), SHOW_ID)
    assert get_proposal(reopened, "p1") == made
    assert proposals(reopened) == [made]
    assert (made.state, made.base_revision) == ("open", saver.revision)


def test_rejected_proposal_keeps_its_reason() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    rejected = reject(designer, "p1", "too wide", AT + 2 * STEP)
    assert (rejected.state, rejected.reason) == ("rejected", "too wide")
    assert get_proposal(open_workspace(designer.repo, SHOW_ID), "p1") == rejected
    with pytest.raises(SchemaError, match="rejected") as info:
        reject(designer, "p1", "again", AT + 3 * STEP)
    assert info.value.code == ErrorCode.PROPOSAL_CLOSED
    with pytest.raises(SchemaError, match="rejected"):
        accept(designer, "p1", AT + 3 * STEP)


def test_withdrawn_proposal_cannot_be_accepted() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    assert withdraw(agent, "p1", AT + 2 * STEP).state == "withdrawn"
    with pytest.raises(SchemaError, match="withdrawn"):
        accept(designer, "p1", AT + 3 * STEP)
    with pytest.raises(SchemaError, match="withdrawn"):
        withdraw(designer, "p1", AT + 3 * STEP)
    assert load_show(designer).set("2").get("T1").x == 0.0


def test_stale_is_derived_never_stored() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    propose(agent, [_move("T3", -1.0)], "other", "p2", AT + STEP)
    designer.refresh()
    assert not is_stale(designer, "p1")
    stored = bytes(designer.tree_at(designer.revision)["/proposals/p1.json"])
    moved = apply_change(load_show(designer), [_move("T1", 8.0)])
    save_show(designer, moved, AT + 2 * STEP)
    assert is_stale(designer, "p1")
    assert not is_stale(designer, "p2")
    assert designer.tree_at(designer.revision)["/proposals/p1.json"] == stored
    assert get_proposal(designer, "p1").state == "open"


def test_proposal_documents_are_not_show_changes() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    before = load_show(designer)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    designer.refresh()
    assert diff_show(before, load_show(designer)) == {}
    assert events_since(designer, before.revision) == []


def test_accept_writes_the_show_and_the_proposal_in_one_revision() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    designer.refresh()
    head = designer.revision
    revision = accept(designer, "p1", AT + 2 * STEP)
    assert revision.parent == head
    stored = get_proposal(designer, "p1")
    assert (stored.state, stored.accepted_revision) == ("accepted", head)
    assert load_show(designer).set("2").get("T1").x == -1.0
    assert designer.repo.head(SHOW_ID) == revision.id


def test_proposal_against_an_unsaved_show_is_accepted_without_overlap() -> None:
    repo = InMemoryRepository()
    agent, designer = open_workspace(repo, SHOW_ID), open_workspace(repo, SHOW_ID)
    made = propose(agent, [_move("T1", -1.0)], "shift", "p1", AT)
    assert made.base_revision == ""
    designer.refresh()
    save_show(designer, two_set_show().replace(revision=designer.revision), AT + STEP)
    assert not is_stale(designer, "p1")
    accept(designer, "p1", AT + 2 * STEP)
    assert load_show(designer).set("2").get("T1").x == -1.0
    assert get_proposal(designer, "p1").state == "accepted"


@pytest.mark.parametrize("bad", ["", "a/b", "..", "a b", ".hidden", "p1/"])
def test_propose_refuses_unsafe_and_taken_ids(bad: str) -> None:
    _, agent, _ = open_session(two_set_show(), AT)
    with pytest.raises(SchemaError) as info:
        propose(agent, [_move("T1", -1.0)], "", bad, AT + STEP)
    assert info.value.code == ErrorCode.BAD_ID
    propose(agent, [_move("T1", -1.0)], "", "p1", AT + STEP)
    with pytest.raises(E8sError) as taken:
        propose(agent, [_move("T1", -2.0)], "", "p1", AT + 2 * STEP)
    assert taken.value.code == ErrorCode.NAME_TAKEN


def test_unknown_proposal_id_names_close_matches() -> None:
    _, agent, _ = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "", "move-t1", AT + STEP)
    with pytest.raises(UnknownRefError, match="move-t1"):
        get_proposal(agent, "move-t")
    with pytest.raises(UnknownRefError):
        accept(agent, "nope", AT + 2 * STEP)


def test_proposals_are_listed_by_id() -> None:
    _, agent, _ = open_session(two_set_show(), AT)
    for name in ("b", "c", "a"):
        propose(agent, [_move("T1", -1.0)], name, name, AT + STEP)
    assert [p.id for p in proposals(agent)] == ["a", "b", "c"]


def test_accept_of_a_stale_proposal_is_proposal_stale() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    designer.refresh()
    save_show(designer, apply_change(load_show(designer), [_move("T1", 8.0)]), AT + 2 * STEP)
    with pytest.raises(ConflictError) as info:
        accept(designer, "p1", AT + 3 * STEP)
    assert info.value.code == ErrorCode.PROPOSAL_STALE
    assert info.value.context["conflicts"] == [
        "placement 2/T1 (drill): both sides changed this item differently"
    ]
    assert get_proposal(designer, "p1").state == "open"


def _moved_both_ways(at: datetime) -> tuple[Workspace, Workspace]:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), at)
    propose(agent, [_move("T3", -1.0)], "shift", "p1", at + STEP)
    designer.refresh()
    save_show(designer, apply_change(load_show(designer), [_move("T1", 8.0)]), at + 2 * STEP)
    return agent, designer


def test_accept_writes_the_change_and_the_state_in_one_revision() -> None:
    _, designer = _moved_both_ways(AT)
    head = designer.revision
    revision = accept(designer, "p1", AT + 3 * STEP)
    assert revision.parent == head
    assert get_proposal(designer, "p1").accepted_revision == head
    assert designer.repo.head(SHOW_ID) == revision.id
    assert load_show(designer).set("2").get("T3").x == -1.0


def test_non_overlapping_proposal_lands_after_the_base_moved() -> None:
    _, designer = _moved_both_ways(AT)
    assert not is_stale(designer, "p1")
    accept(designer, "p1", AT + 3 * STEP)
    landed = load_show(designer).set("2")
    assert (landed.get("T1").x, landed.get("T3").x) == (8.0, -1.0)


def test_preview_shows_the_merge_on_the_head() -> None:
    _, designer = _moved_both_ways(AT)
    preview = preview_proposal(designer, "p1")
    assert preview.conflicts == ()
    assert [p.entity for p in preview.placements[SetId("2")]] == ["T3"]
    assert preview.placements[SetId("2")][0].x == -1.0
    assert preview.proposal_id == "p1"


def test_preview_of_a_conflicting_proposal_lists_only_its_conflicts() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    designer.refresh()
    save_show(designer, apply_change(load_show(designer), [_move("T1", 8.0)]), AT + 2 * STEP)
    preview = preview_proposal(designer, "p1")
    assert [(c.key.kind, c.key.path) for c in preview.conflicts] == [("placement", ("2", "T1"))]
    assert preview.model_copy(update={"conflicts": ()}) == type(preview)(proposal_id="p1")


def test_regression_preview_does_not_move_the_workspace() -> None:
    """Keeps the caller's base revision, so a stale write after a preview still conflicts."""
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    stale = designer.revision
    assert stale != designer.repo.head(SHOW_ID)
    preview = preview_proposal(designer, "p1")
    assert preview.placements[SetId("2")][0].x == -1.0
    assert designer.revision == stale
    with pytest.raises(ConflictError):
        save_show(designer, load_show(designer), AT + 2 * STEP)


def _rescale_session(rows: dict[SpotId, PathRow]) -> tuple[Workspace, Workspace]:
    show = two_set_show()
    spec = TransitionSpec(id=make_tid("1", "2"), counts=show.set("2").counts, rows=rows)
    _, agent, designer = open_session(show.replace(transitions=(spec,)), AT)
    rescale = CountsOp(set_id=SetId("2"), counts=show.set("2").counts * 2, rescale=True)
    propose(agent, [rescale], "", "p1", AT + STEP)
    designer.refresh()
    return agent, designer


def test_rescale_merges_when_the_head_left_its_transition_alone() -> None:
    _, designer = _rescale_session({SpotId("T1"): PathRow(entity=SpotId("T1"), start=2)})
    save_show(designer, apply_change(load_show(designer), [_move("T2", 7.0)]), AT + 2 * STEP)
    assert not is_stale(designer, "p1")
    accept(designer, "p1", AT + 3 * STEP)
    landed = load_show(designer)
    assert landed.transition(("1", "2")).rows[SpotId("T1")].start == 4
    assert landed.set("2").get("T2").x == 7.0


def test_rescale_conflicts_with_a_row_the_head_added() -> None:
    _, designer = _rescale_session({})
    added = TransitionSpec(
        id=make_tid("1", "2"),
        counts=load_show(designer).set("2").counts,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), start=1)},
    )
    save_show(designer, load_show(designer).replace(transitions=(added,)), AT + 2 * STEP)
    assert is_stale(designer, "p1")
    conflicts = preview_proposal(designer, "p1").conflicts
    assert [(c.key.kind, c.key.path) for c in conflicts] == [("path_row", ("1-2", "T1"))]
    assert "timed against the base's counts" in conflicts[0].reason
    with pytest.raises(ConflictError) as info:
        accept(designer, "p1", AT + 3 * STEP)
    assert info.value.code == ErrorCode.PROPOSAL_STALE


def test_rescale_conflicts_with_a_row_the_head_edited_to_the_rescaled_value() -> None:
    _, designer = _rescale_session({SpotId("T1"): PathRow(entity=SpotId("T1"), start=2)})
    show = load_show(designer)
    equal = show.transition(("1", "2")).replace(
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), start=4)}
    )
    save_show(designer, show.replace(transitions=(equal,)), AT + 2 * STEP)
    assert is_stale(designer, "p1")


def test_is_stale_agrees_with_the_merge_conflicts() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    designer.refresh()
    assert not is_stale(designer, "p1")
    assert not preview_proposal(designer, "p1").conflicts
    save_show(designer, apply_change(load_show(designer), [_move("T1", 8.0)]), AT + 2 * STEP)
    assert is_stale(designer, "p1")
    assert preview_proposal(designer, "p1").conflicts
    with pytest.raises(ConflictError) as info:
        accept(designer, "p1", AT + 3 * STEP)
    assert info.value.context["conflicts"] == [
        f"placement 2/T1 (drill): {c.reason}" for c in preview_proposal(designer, "p1").conflicts
    ]


def test_regression_accept_by_merge_respects_a_pin_added_after_the_base() -> None:
    """Refuses a proposal that moves a spot the designer pinned after the proposal's base."""
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT + STEP)
    designer.refresh()
    save_show(designer, pin(load_show(designer), "entity", "T1"), AT + 2 * STEP)
    assert not is_stale(designer, "p1")
    with pytest.raises(LockedError):
        accept(designer, "p1", AT + 3 * STEP)
    assert get_proposal(designer, "p1").state == "open"
    assert load_show(designer).set("2").get("T1").x == 0.0


@pytest.mark.parametrize(
    "raw",
    [
        b'{"id": "p1", "base_revision": NaN, "change": []}',
        b'{"id": "p1", "id": "p2", "base_revision": "", "change": []}',
        b'\xef\xbb\xbf{"id": "p1", "base_revision": "", "change": []}',
    ],
)
def test_regression_stored_proposal_is_parsed_as_strict_json(raw: bytes) -> None:
    repo, agent, _ = open_session(two_set_show(), AT)
    backend = repo.backend
    tree = {**backend.tree(SHOW_ID, agent.revision), "/proposals/p1.json": digest(raw)}
    revision = Revision(id=digest(raw), parent=agent.revision, at=AT + STEP)
    backend.commit(SHOW_ID, revision, tree, {digest(raw): raw})
    agent.refresh()
    for read in (lambda: get_proposal(agent, "p1"), lambda: proposals(agent)):
        with pytest.raises(SchemaError) as info:
            read()
        assert info.value.code == ErrorCode.STRICT_JSON


DELETED_SPOT = "T4"
GHOST_SPOT = "ZZ"
DELETED_SPOT_OPS: dict[str, ChangeOp] = {
    "placement": _move(DELETED_SPOT, -1.0),
    "form": FormOp(form=line_form("f1", ["T3", DELETED_SPOT], (0.0, 0.0), (4.0, 0.0), set_id="2")),
    "paths": PathsOp(
        spec=TransitionSpec(
            id=make_tid("1", "2"),
            counts=8.0,
            rows={SpotId(DELETED_SPOT): PathRow(entity=SpotId(DELETED_SPOT), start=2)},
        )
    ),
    "paths-facing": PathsOp(
        spec=TransitionSpec(
            id=make_tid("1", "2"),
            counts=8.0,
            rows={
                SpotId("T1"): PathRow(
                    entity=SpotId("T1"),
                    facing_mode=FacingMode.TOWARD_ENTITY,
                    facing_value=EntityFacing(entity=SpotId(DELETED_SPOT)),
                )
            },
        )
    ),
}


@pytest.mark.parametrize("op", DELETED_SPOT_OPS.values(), ids=DELETED_SPOT_OPS.keys())
def test_regression_accept_refuses_an_op_naming_a_spot_the_head_deleted(op: ChangeOp) -> None:
    """Refuses, as a stale proposal, an op naming a spot the head deleted since the base."""
    repo, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [op], "", "p1", AT + STEP)
    designer.refresh()
    save_show(designer, delete_spots(load_show(designer), DELETED_SPOT), AT + 2 * STEP)
    head = repo.head(SHOW_ID)
    assert is_stale(designer, "p1")
    with pytest.raises(ConflictError) as caught:
        accept(designer, "p1", AT + 3 * STEP)
    assert caught.value.code == ErrorCode.PROPOSAL_STALE
    assert DELETED_SPOT in caught.value.message
    assert repo.head(SHOW_ID) == head


def test_regression_accept_refuses_a_proposal_naming_a_spot_never_in_the_cast() -> None:
    """Refuses a form op whose member was never in the cast; is_stale and preview agree."""
    repo, agent, designer = open_session(two_set_show(), AT)
    form = line_form("f1", ["T3", GHOST_SPOT], (0.0, 0.0), (4.0, 0.0), set_id="2")
    propose(agent, [FormOp(form=form)], "", "p1", AT + STEP)
    designer.refresh()
    head = repo.head(SHOW_ID)
    assert is_stale(designer, "p1")
    assert [c.key.kind for c in preview_proposal(designer, "p1").conflicts] == ["form"]
    with pytest.raises(ConflictError) as caught:
        accept(designer, "p1", AT + 2 * STEP)
    assert GHOST_SPOT in caught.value.message
    assert repo.head(SHOW_ID) == head


def test_regression_new_form_under_a_pinned_set_is_locked() -> None:
    """Refuses a form op adding a form to a pinned set unless forced, in apply_change and accept."""
    pinned = pin(two_set_show(), "set", "2")
    add_form = FormOp(form=line_form("f9", ["T1", "T2"], (0.0, 0.0), (4.0, 0.0), set_id="2"))
    with pytest.raises(LockedError) as caught:
        apply_change(pinned, [add_form])
    assert "forms of set 2" in caught.value.message
    assert "f9" in apply_change(pinned, [add_form], force=True).set("2").forms
    repo, agent, designer = open_session(pinned, AT)
    propose(agent, [add_form], "", "p1", AT + STEP)
    designer.refresh()
    head = repo.head(SHOW_ID)
    with pytest.raises(LockedError):
        accept(designer, "p1", AT + 2 * STEP)
    assert repo.head(SHOW_ID) == head
