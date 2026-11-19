#!/usr/bin/env python3

"""
test_collab.py --- tests change ops, pins, derived canvas events, proposals, highlights, selection

Contains:
    test_regression_counts_only_change_is_not_reported_as_a_paths_edit: reports a counts-only retime
        as set_counts, not edit_paths
    test_regression_events_since_replay_reproduces_a_form_deletion: replays a form deletion so the
        replayed show equals head
    test_regression_accepting_twice_says_the_proposal_was_accepted: names the accepted state when a
        proposal is accepted twice
    test_regression_proposal_against_unsaved_show_can_be_accepted: accepts a proposal made against a
        show that was saved afterwards
    test_regression_rescale_proposal_conflicts_with_a_newer_row_of_the_same_transition: refuses a
        rescale proposal after the designer edited that transition's rows
    test_regression_accepting_a_form_edit_does_not_resurrect_a_deleted_form: refuses, or keeps the
        form deleted, when accepting an edit of a deleted form
    test_regression_unpin_event_needs_force: refuses an unpin event unless forced
    test_regression_pin_event_replacing_a_pin_needs_force: refuses a pin event that replaces a pin
        unless forced
    test_regression_unpin_event_of_a_missing_pin_raises_unknown_ref: names the missing pin with or
        without force
"""

from datetime import UTC, datetime, timedelta

import pytest

from showpy.choreo.models import Form, PathRow, Placement, TransitionSpec
from showpy.core.errors import (
    ConflictError,
    LockedError,
    SchemaError,
    ShowPyError,
    TimeRangeError,
    UnknownRefError,
)
from showpy.core.ids import make_tid
from showpy.core.selection import Selection
from showpy.notes.models import Anchor
from showpy.show.changes import apply_change
from showpy.show.collab import (
    accept,
    apply_event,
    events_since,
    highlight,
    pins,
    preview_proposal,
    propose,
    reject,
    selection,
)
from showpy.show.documents import dump_json
from showpy.show.manage import add_set, set_counts
from showpy.show.models import (
    CanvasEvent,
    Comment,
    CountsOp,
    EventKind,
    FormOp,
    PathsOp,
    Pin,
    PlacementOp,
    Show,
)
from showpy.show.pinning import pin, unpin
from showpy.show.storage import Workspace, load_show, save_show
from showpy.show.structured import COMMENTS_DOC
from tests.fixtures.shows import two_set_show
from tests.helpers import open_session

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)
FORM = Form(id="f1", set="2", kind="line", geometry={"len": 6}, members=("T1", "T2"))


def _move(entity: str, x: float, set_id: str = "2") -> PlacementOp:
    y = 9.0 if set_id == "2" else 5.0
    return PlacementOp(set_id=set_id, placement=Placement(entity=entity, x=x, y=y))


def test_apply_change_applies_every_op_kind() -> None:
    paths = TransitionSpec(
        id=make_tid("1", "2"), counts=1.0, rows={"T1": PathRow(entity="T1", end=4)}
    )
    show = apply_change(
        two_set_show(),
        [_move("T1", -3.0), FormOp(form=FORM), PathsOp(spec=paths), CountsOp(set_id="2", counts=6)],
    )
    assert show.set("2").get("T1").x == -3.0
    assert show.forms["f1"] == FORM
    assert show.set("2").forms == ("f1",)
    assert show.transition(("1", "2")).counts == 6.0
    assert show.transition(("1", "2")).rows["T1"].end == 4


def test_apply_change_refuses_pinned_unless_forced() -> None:
    pinned = pin(two_set_show(), "entity", "T1", "solo")
    with pytest.raises(LockedError):
        apply_change(pinned, [_move("T1", 1.0)])
    assert apply_change(pinned, [_move("T1", 1.0)], force=True).set("2").get("T1").x == 1.0


def test_apply_change_unknown_refs() -> None:
    with pytest.raises(UnknownRefError):
        apply_change(two_set_show(), [_move("Z1", 0.0)])
    with pytest.raises(UnknownRefError):
        apply_change(
            two_set_show(), [PathsOp(spec=TransitionSpec(id=make_tid("2", "1"), counts=8))]
        )


def test_pin_entity_set_form_and_transition_flags() -> None:
    show = apply_change(two_set_show(), [FormOp(form=FORM)])
    entity = pin(show, "entity", "T1", "soloist")
    assert all(s.get("T1").pinned for s in entity.sets)
    assert not entity.sets[0].get("T2").pinned
    whole = pin(show, "set", "2")
    assert all(p.pinned for p in whole.set("2").placements)
    assert whole.forms["f1"].locked
    assert pin(show, "form", "f1").forms["f1"].locked
    paths = pin(show, "transition", "1->2").transition(("1", "2"))
    assert set(paths.rows) == {"T1", "T2", "T3", "T4"}
    assert all(row.locked for row in paths.rows.values())
    assert pins(pin(show, "transition", "1->2"))[0].id == "1→2"


def test_unpin_keeps_flags_other_pins_cover() -> None:
    show = pin(pin(two_set_show(), "entity", "T1"), "set", "2")
    released = unpin(show, "set", "2")
    assert released.set("2").get("T1").pinned
    assert not released.set("2").get("T2").pinned
    assert unpin(released, "entity", "T1") == two_set_show()
    locked = pin(two_set_show(), "transition", "1→2")
    assert unpin(locked, "transition", "1→2").transitions == ()
    with pytest.raises(UnknownRefError):
        unpin(two_set_show(), "set", "2")


def test_pin_unknown_targets() -> None:
    with pytest.raises(UnknownRefError):
        pin(two_set_show(), "entity", "Z9")
    with pytest.raises(UnknownRefError):
        pin(two_set_show(), "form", "nope")
    with pytest.raises(UnknownRefError):
        pin(two_set_show(), "transition", "2->1")


def test_pin_replaces_existing_record() -> None:
    show = pin(pin(two_set_show(), "set", "1", "a"), "set", "1", "b")
    assert [p.reason for p in pins(show)] == ["b"]


def _designer_edits(designer: Workspace) -> Show:
    base = load_show(designer)
    paths = TransitionSpec(
        id=make_tid("2", "3"), counts=4.0, rows={"T2": PathRow(entity="T2", end=2)}
    )
    edited = apply_change(
        base,
        [_move("T1", -4.0), FormOp(form=FORM), PathsOp(spec=paths), CountsOp(set_id="3", counts=6)],
    )
    edited = pin(edited, "entity", "T4", "anchor")
    save_show(designer, edited, AT + STEP)
    return edited


def test_events_since_replay_reproduces_head() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    base_revision = designer.revision
    base = load_show(designer)
    _designer_edits(designer)
    events = events_since(agent, base_revision)
    kinds = [e.kind for e in events]
    assert kinds[0] == "pin"
    assert {"move", "edit_form", "edit_paths", "set_counts"} <= set(kinds)
    assert all(e.revision == designer.revision for e in events)
    replayed = base
    for event in events:
        replayed = apply_event(replayed, event, force=True)
    head = load_show(designer)
    assert replayed.model_copy(update={"revision": head.revision}) == head
    assert events_since(agent, designer.revision) == []


def test_events_since_reports_comments() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    start = designer.revision
    comment = Comment(id="c1", anchor=Anchor.on_set("2"), text="tighten the line")
    designer.write(COMMENTS_DOC, dump_json([comment.model_dump(mode="python")]), at=AT + STEP)
    (event,) = events_since(agent, start)
    assert event.kind == "comment"
    assert Comment.model_validate(event.payload) == comment


def test_apply_event_rules() -> None:
    mismatched = CanvasEvent(
        id="e",
        at=AT,
        kind="move",
        payload={"op": "counts", "set_id": "2", "counts": 4},
    )
    with pytest.raises(SchemaError):
        apply_event(two_set_show(), mismatched)
    pinned = pin(two_set_show(), "entity", "T1")
    note = CanvasEvent(id="e", at=AT, kind="comment", payload={"text": "x"})
    assert apply_event(pinned, note) == pinned


def test_canvas_event_needs_explicit_force() -> None:
    pinned = pin(two_set_show(), "entity", "T1")
    move = CanvasEvent(
        id="e",
        at=AT,
        kind="move",
        payload=_move("T1", 3.0).model_dump(mode="json"),
    )
    with pytest.raises(LockedError):
        apply_event(pinned, move)
    assert apply_event(pinned, move, force=True).set("2").get("T1").x == 3.0


def _pin_event(kind: EventKind, reason: str = "") -> CanvasEvent:
    held = Pin(target="entity", id="T1", reason=reason)
    return CanvasEvent(id="e", at=AT, kind=kind, payload=held.model_dump())


def test_regression_unpin_event_needs_force() -> None:
    """Refuses an unpin event without force and leaves the pins as they were."""
    pinned = pin(two_set_show(), "entity", "T1", "solo")
    with pytest.raises(LockedError):
        apply_event(pinned, _pin_event("unpin"))
    assert pins(pinned) == pins(pin(two_set_show(), "entity", "T1", "solo"))
    assert apply_event(pinned, _pin_event("unpin"), force=True).pins == ()


def test_regression_pin_event_replacing_a_pin_needs_force() -> None:
    """Refuses a pin event that rewrites an existing record unless forced."""
    pinned = pin(two_set_show(), "entity", "T1", "solo")
    with pytest.raises(LockedError):
        apply_event(pinned, _pin_event("pin", "other"))
    replaced = apply_event(pinned, _pin_event("pin", "other"), force=True)
    assert [p.reason for p in replaced.pins] == ["other"]


@pytest.mark.parametrize("force", [False, True])
def test_regression_unpin_event_of_a_missing_pin_raises_unknown_ref(force: bool) -> None:
    """Reports an unpin event of a pin that does not exist as an unknown pin, forced or not."""
    with pytest.raises(UnknownRefError):
        apply_event(two_set_show(), _pin_event("unpin"), force=force)


def test_agent_pin_event_applies() -> None:
    event = CanvasEvent(id="e", at=AT, kind="pin", payload=Pin(target="set", id="1").model_dump())
    assert Pin(target="set", id="1") in [
        Pin(target=p.target, id=p.id) for p in apply_event(two_set_show(), event).pins
    ]


def test_propose_preview_and_reject() -> None:
    show = pin(two_set_show(), "entity", "T1")
    proposal = propose(
        show, [_move("T1", 1.0), _move("T2", 3.5), CountsOp(set_id="2", counts=6)], "wider", "p1"
    )
    assert (proposal.state, proposal.base_revision) == ("open", "")
    preview = preview_proposal(show, proposal)
    assert [p.entity for p in preview.placements["2"]] == ["T1", "T2"]
    assert preview.counts == {"2": 6.0}
    assert preview.paths[0].counts == 6.0
    assert preview.blocked == ("placement T1 in set 2",)
    rejected = reject(proposal, "too wide")
    assert (rejected.state, rejected.reason) == ("rejected", "too wide")
    with pytest.raises(SchemaError):
        reject(rejected, "again")


def test_accept_saves_the_proposal_as_the_new_head() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    proposal = propose(load_show(agent), [_move("T3", -1.0)], "shift", "p1")
    rev = accept(designer, proposal, AT + STEP)
    assert (rev.id, rev.at) == (designer.repo.head("s"), AT + STEP)
    assert load_show(designer).set("2").get("T3").x == -1.0


def test_accept_rebases_when_unrelated_items_moved() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    proposal = propose(load_show(agent), [_move("T3", -1.0)], "shift", "p1")
    other = apply_change(load_show(designer), [_move("T1", 8.0)])
    save_show(designer, other, AT + STEP)
    accept(designer, proposal, AT + 2 * STEP)
    head = load_show(designer)
    assert (head.set("2").get("T1").x, head.set("2").get("T3").x) == (8.0, -1.0)


def test_accept_conflicts_when_same_items_moved() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    proposal = propose(load_show(agent), [_move("T1", -1.0)], "shift", "p1")
    save_show(designer, apply_change(load_show(designer), [_move("T1", 8.0)]), AT + STEP)
    with pytest.raises(ConflictError) as info:
        accept(designer, proposal, AT + 2 * STEP)
    assert info.value.context["overlap"] == ["placement T1 in set 2"]


def test_accept_respects_pins() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    proposal = propose(load_show(agent), [_move("T1", -1.0)], "shift", "p1")
    save_show(designer, pin(load_show(designer), "entity", "T2"), AT + STEP)
    accept(designer, proposal, AT + 2 * STEP)
    save_show(designer, pin(load_show(designer), "entity", "T1"), AT + 3 * STEP)
    pinned_t1 = propose(load_show(designer), [_move("T1", 5.0)], "again", "p2")
    with pytest.raises(LockedError):
        accept(designer, pinned_t1, AT + 4 * STEP)


def test_highlight_and_selection() -> None:
    event = highlight("pit", 10.0, at=AT, event_id="h1")
    assert (event.kind, event.payload, event.revision) == (
        "highlight",
        {"zone": "pit", "ttl_s": 10.0},
        "",
    )
    tid_event = highlight(Anchor.on_transition(("1", "2")), at=AT, event_id="h2")
    assert tid_event.payload["anchor"]["tid"] == ["1", "2"]
    with pytest.raises(TimeRangeError):
        highlight("pit", 0.0, at=AT, event_id="h3")
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    assert selection(agent).ids == ()
    designer.write("/canvas/selection.json", Selection(ids=("T2", "T1")).model_dump_json(), at=AT)
    agent.refresh()
    assert selection(agent).ids == ("T2", "T1")


STRAIGHT_ROW = TransitionSpec(
    id=make_tid("1", "2"), counts=8.0, rows={"T1": PathRow(entity="T1", start=2)}
)
RETIMED_COUNTS = 12.0
RESCALED_COUNTS = 16.0


def _with_path_row(show: Show) -> Show:
    return show.model_copy(update={"transitions": (STRAIGHT_ROW,)})


@pytest.mark.defect("UP-9.7-32")
@pytest.mark.xfail(strict=True, reason="UP-9.7-32")
def test_regression_counts_only_change_is_not_reported_as_a_paths_edit() -> None:
    """Reports a counts-only retime as set_counts, not edit_paths (audit 9.7 collab)."""
    _, agent, designer = open_session(_with_path_row(two_set_show()), AT)
    base_revision = designer.revision
    retimed = set_counts(load_show(designer), "2", RETIMED_COUNTS)
    save_show(designer, retimed, AT + STEP)
    kinds = {event.kind for event in events_since(agent, base_revision)}
    assert "set_counts" in kinds
    assert "edit_paths" not in kinds


@pytest.mark.defect("UP-9.7-33")
@pytest.mark.xfail(strict=True, reason="UP-9.7-33")
def test_regression_events_since_replay_reproduces_a_form_deletion() -> None:
    """Replays a form deletion so the replayed show equals head (audit 9.7 collab)."""
    with_form = two_set_show().model_copy(update={"forms": {"f1": FORM}})
    _, agent, designer = open_session(with_form, AT)
    base_revision = designer.revision
    base = load_show(designer)
    save_show(designer, base.model_copy(update={"forms": {}}), AT + STEP)
    replayed = base
    for event in events_since(agent, base_revision):
        replayed = apply_event(replayed, event, force=True)
    assert replayed.forms == load_show(designer).forms


@pytest.mark.defect("UP-9.7-34")
@pytest.mark.xfail(strict=True, reason="UP-9.7-34")
def test_regression_accepting_twice_says_the_proposal_was_accepted() -> None:
    """Names the accepted state when a proposal is accepted twice (audit 9.7 collab)."""
    _, agent, designer = open_session(two_set_show(), AT)
    proposal = propose(load_show(agent), [_move("T1", -1.0)], "shift", "p1")
    accept(designer, proposal, AT + STEP)
    with pytest.raises(ShowPyError, match="accepted"):
        accept(designer, proposal, AT + 2 * STEP)


@pytest.mark.defect("UP-9.7-35")
@pytest.mark.xfail(strict=True, reason="UP-9.7-35")
def test_regression_proposal_against_unsaved_show_can_be_accepted() -> None:
    """Accepts a proposal made against a show that was saved afterwards (audit 9.7 collab)."""
    unsaved = two_set_show()
    proposal = propose(unsaved, [_move("T1", -1.0)], "shift", "p1")
    _, _, designer = open_session(unsaved, AT)
    revision = accept(designer, proposal, AT + STEP)
    assert load_show(designer).set("2").get("T1").x == -1.0
    assert revision.id == designer.revision


@pytest.mark.defect("UP-9.7-46")
@pytest.mark.xfail(strict=True, reason="UP-9.7-46")
def test_regression_rescale_proposal_conflicts_with_a_newer_row_of_the_same_transition() -> None:
    """Refuses a rescale proposal after the designer edited that transition's rows (audit 9.7)."""
    _, agent, designer = open_session(_with_path_row(two_set_show()), AT)
    rescale = CountsOp(set_id="2", counts=RESCALED_COUNTS, rescale=True)
    proposal = propose(load_show(agent), [rescale], "", "p1")
    newer = STRAIGHT_ROW.model_copy(update={"rows": {"T1": PathRow(entity="T1", start=4)}})
    designer_show = load_show(designer).model_copy(update={"transitions": (newer,)})
    save_show(designer, designer_show, AT + STEP)
    with pytest.raises(ConflictError):
        accept(designer, proposal, AT + 2 * STEP)


@pytest.mark.defect("UP-9.7-91")
@pytest.mark.xfail(strict=True, reason="UP-9.7-91")
def test_regression_accepting_a_form_edit_does_not_resurrect_a_deleted_form() -> None:
    """Refuses, or keeps the form deleted, when accepting an edit of a deleted form (audit 9.7)."""
    with_form = two_set_show().model_copy(update={"forms": {"f1": FORM}})
    _, agent, designer = open_session(with_form, AT)
    edited_form = FORM.model_copy(update={"geometry": {"len": 9}})
    proposal = propose(load_show(agent), [FormOp(form=edited_form)], "longer", "p1")
    save_show(designer, load_show(designer).model_copy(update={"forms": {}}), AT + STEP)
    try:
        accept(designer, proposal, AT + 2 * STEP)
    except ShowPyError:
        return
    assert "f1" not in load_show(designer).forms


@pytest.mark.parametrize("payload", [{}, {"op": "nope"}, {"op": "counts", "counts": "x"}])
def test_regression_canvas_event_with_bad_payload_is_a_schema_error(
    payload: dict[str, object],
) -> None:
    """Raises SchemaError, not pydantic's ValidationError, for a malformed op payload."""
    event = CanvasEvent(id="e", at=AT, kind="set_counts", payload=payload)
    with pytest.raises(SchemaError) as info:
        apply_event(two_set_show(), event)
    assert info.value.context["errors"]


@pytest.mark.parametrize("spelling", ["1->2", "1→2"])
def test_regression_transition_pin_events_need_force_in_either_spelling(spelling: str) -> None:
    """Refuses replacing or dropping a transition pin whichever way its ID is spelled."""
    pinned = pin(two_set_show(), "transition", "1->2", "keep")
    held = Pin(target="transition", id=spelling, reason="x")
    replace = CanvasEvent(id="e", at=AT, kind="pin", payload=held.model_dump())
    drop = CanvasEvent(id="e", at=AT, kind="unpin", payload=held.model_dump())
    for event in (replace, drop):
        with pytest.raises(LockedError):
            apply_event(pinned, event)
    assert [p.reason for p in apply_event(pinned, replace, force=True).pins] == ["x"]
    assert apply_event(pinned, drop, force=True).pins == ()
