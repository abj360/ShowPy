#!/usr/bin/env python3

"""
test_proposals.py --- tests the proposal record's invariants and the stored proposal lifecycle

Contains:
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
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from e8s.choreo.models import Placement
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, E8sError, SchemaError, UnknownRefError
from e8s.core.ids import SetId, SpotId
from e8s.show.changes import apply_change
from e8s.show.collab_models import PlacementOp, Proposal, ProposalState
from e8s.show.events import events_since
from e8s.show.file_repository import FileRepository
from e8s.show.history import diff_show
from e8s.show.manage import add_set
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.proposals import (
    accept,
    get_proposal,
    is_stale,
    proposals,
    propose,
    reject,
    withdraw,
)
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import open_workspace
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
    assert info.value.context["overlap"] == ["placement T1 in set 2"]
    assert get_proposal(designer, "p1").state == "open"
