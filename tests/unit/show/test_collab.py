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

import json
from datetime import UTC, datetime, timedelta

import pytest

from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.choreo.recipe_models import LineRecipe
from e8s.core.errors import (
    ConflictError,
    E8sError,
    LockedError,
    SchemaError,
    TimeRangeError,
    UnknownRefError,
)
from e8s.core.ids import FormId, SetId, SpotId, make_tid
from e8s.core.json_types import JsonValue
from e8s.core.selection import Selection
from e8s.geo.shapes import line
from e8s.show.anchors import Anchor
from e8s.show.changes import apply_change
from e8s.show.collab import highlight, pins, selection
from e8s.show.collab_models import (
    CanvasEvent,
    Comment,
    CountsOp,
    EventKind,
    FormOp,
    Highlight,
    PathsOp,
    PlacementOp,
)
from e8s.show.documents import dump_json
from e8s.show.events import apply_event, decode_event, events_since
from e8s.show.manage import add_set, set_counts
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.migrate import ReportBuilder
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin, unpin
from e8s.show.proposals import accept, preview_proposal, propose, reject
from e8s.show.show_io import load_show, save_show
from e8s.show.structured import COMMENTS_DOC
from e8s.show.workspace import Workspace, open_workspace
from tests.fixtures.shows import two_set_show
from tests.helpers import open_session

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)
FORM = Form(
    id=FormId("f1"),
    set=SetId("2"),
    recipe=LineRecipe(curve=line((0.0, 0.0), (6.0, 0.0))),
    members=(SpotId("T1"), SpotId("T2")),
)


def _move(entity: str, x: float, set_id: str = "2") -> PlacementOp:
    y = 9.0 if set_id == "2" else 5.0
    return PlacementOp(set_id=SetId(set_id), placement=Placement(entity=SpotId(entity), x=x, y=y))


def test_apply_change_applies_every_op_kind() -> None:
    paths = TransitionSpec(
        id=make_tid("1", "2"), counts=1.0, rows={SpotId("T1"): PathRow(entity=SpotId("T1"), end=4)}
    )
    show = apply_change(
        two_set_show(),
        [
            _move("T1", -3.0),
            FormOp(form=FORM),
            PathsOp(spec=paths),
            CountsOp(set_id=SetId("2"), counts=6),
        ],
    )
    assert show.set("2").get("T1").x == -3.0
    assert show.forms[FormId("f1")] == FORM
    assert show.set("2").forms == ("f1",)
    assert show.transition(("1", "2")).counts == 6.0
    assert show.transition(("1", "2")).rows[SpotId("T1")].end == 4


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
    assert whole.forms[FormId("f1")].locked
    assert pin(show, "form", "f1").forms[FormId("f1")].locked
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
        id=make_tid("2", "3"), counts=4.0, rows={SpotId("T2"): PathRow(entity=SpotId("T2"), end=2)}
    )
    edited = apply_change(
        base,
        [
            _move("T1", -4.0),
            FormOp(form=FORM),
            PathsOp(spec=paths),
            CountsOp(set_id=SetId("3"), counts=6),
        ],
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
    assert replayed.replace(revision=head.revision) == head
    assert events_since(agent, designer.revision) == []


def test_events_since_reports_comments() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    start = designer.revision
    comment = Comment(id="c1", anchor=Anchor.on_set("2"), text="tighten the line")
    designer.write(COMMENTS_DOC, dump_json([comment._dumped()]), at=AT + STEP)
    (event,) = events_since(agent, start)
    assert event.kind == "comment"
    assert event.payload == comment


def test_apply_event_rules() -> None:
    with pytest.raises(SchemaError):
        CanvasEvent.from_json(
            {
                "id": "e",
                "at": AT,
                "kind": "move",
                "payload": {"op": "counts", "set_id": "2", "counts": 4},
            }
        )
    with pytest.raises(SchemaError):
        CanvasEvent(id="e", at=AT, kind="move", payload=CountsOp(set_id=SetId("2"), counts=4))
    pinned = pin(two_set_show(), "entity", "T1")
    note = CanvasEvent(
        id="e", at=AT, kind="comment", payload=Comment(id="c", anchor=Anchor.on_set("2"), text="x")
    )
    assert apply_event(pinned, note) == pinned


def test_canvas_event_needs_explicit_force() -> None:
    pinned = pin(two_set_show(), "entity", "T1")
    move = CanvasEvent(id="e", at=AT, kind="move", payload=_move("T1", 3.0))
    with pytest.raises(LockedError):
        apply_event(pinned, move)
    assert apply_event(pinned, move, force=True).set("2").get("T1").x == 3.0


def _pin_event(kind: EventKind, reason: str = "") -> CanvasEvent:
    held = Pin(target="entity", id="T1", reason=reason)
    return CanvasEvent(id="e", at=AT, kind=kind, payload=held)


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
    event = CanvasEvent(id="e", at=AT, kind="pin", payload=Pin(target="set", id="1"))
    assert Pin(target="set", id="1") in [
        Pin(target=p.target, id=p.id) for p in apply_event(two_set_show(), event).pins
    ]


def test_propose_preview_and_reject() -> None:
    show = pin(two_set_show(), "entity", "T1")
    _, agent, designer = open_session(show, AT)
    base_revision = agent.revision
    proposal = propose(
        agent,
        [_move("T1", 1.0), _move("T2", 3.5), CountsOp(set_id=SetId("2"), counts=6)],
        "wider",
        "p1",
        AT + STEP,
    )
    assert (proposal.state, proposal.base_revision) == ("open", base_revision)
    designer.refresh()
    preview = preview_proposal(designer, "p1")
    assert [p.entity for p in preview.placements[SetId("2")]] == ["T1", "T2"]
    assert preview.counts == {"2": 6.0}
    assert preview.paths[0].counts == 6.0
    assert preview.blocked == ("placement T1 in set 2",)
    rejected = reject(designer, "p1", "too wide", AT + 2 * STEP)
    assert (rejected.state, rejected.reason) == ("rejected", "too wide")
    with pytest.raises(SchemaError):
        reject(designer, "p1", "again", AT + 3 * STEP)


def test_accept_saves_the_proposal_as_the_new_head() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    propose(agent, [_move("T3", -1.0)], "shift", "p1", AT)
    rev = accept(designer, "p1", AT + STEP)
    assert (rev.id, rev.at) == (designer.repo.head("s"), AT + STEP)
    assert load_show(designer).set("2").get("T3").x == -1.0


def test_accept_rebases_when_unrelated_items_moved() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    propose(agent, [_move("T3", -1.0)], "shift", "p1", AT)
    designer.refresh()
    other = apply_change(load_show(designer), [_move("T1", 8.0)])
    save_show(designer, other, AT + STEP)
    accept(designer, "p1", AT + 2 * STEP)
    head = load_show(designer)
    assert (head.set("2").get("T1").x, head.set("2").get("T3").x) == (8.0, -1.0)


def test_accept_conflicts_when_same_items_moved() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT)
    designer.refresh()
    save_show(designer, apply_change(load_show(designer), [_move("T1", 8.0)]), AT + STEP)
    with pytest.raises(ConflictError) as info:
        accept(designer, "p1", AT + 2 * STEP)
    assert info.value.context["conflicts"] == [
        "placement 2/T1 (drill): both sides changed this item differently"
    ]


def test_accept_respects_pins() -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT)
    designer.refresh()
    save_show(designer, pin(load_show(designer), "entity", "T2"), AT + STEP)
    accept(designer, "p1", AT + 2 * STEP)
    save_show(designer, pin(load_show(designer), "entity", "T1"), AT + 3 * STEP)
    propose(designer, [_move("T1", 5.0)], "again", "p2", AT + 4 * STEP)
    with pytest.raises(LockedError):
        accept(designer, "p2", AT + 5 * STEP)


def test_highlight_and_selection() -> None:
    event = highlight("pit", 10.0, at=AT, event_id="h1")
    assert (event.kind, event.payload, event.revision) == (
        "highlight",
        Highlight(zone="pit", ttl_s=10.0),
        "",
    )
    tid_event = highlight(Anchor.on_transition(("1", "2")), at=AT, event_id="h2")
    assert isinstance(tid_event.payload, Highlight)
    assert tid_event.payload.anchor == Anchor.on_transition(("1", "2"))
    with pytest.raises(TimeRangeError):
        highlight("pit", 0.0, at=AT, event_id="h3")
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    assert selection(agent).ids == ()
    designer.write(
        "/canvas/selection.json",
        json.dumps(Selection(ids=(SpotId("T2"), SpotId("T1"))).to_json()),
        at=AT,
    )
    agent.refresh()
    assert selection(agent).ids == ("T2", "T1")


STRAIGHT_ROW = TransitionSpec(
    id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): PathRow(entity=SpotId("T1"), start=2)}
)
RETIMED_COUNTS = 12.0
RESCALED_COUNTS = 16.0


def _with_path_row(show: Show) -> Show:
    return show.replace(transitions=(STRAIGHT_ROW,))


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
    with_form = two_set_show().replace(forms={"f1": FORM})
    _, agent, designer = open_session(with_form, AT)
    base_revision = designer.revision
    base = load_show(designer)
    save_show(designer, base.replace(forms={}), AT + STEP)
    replayed = base
    for event in events_since(agent, base_revision):
        replayed = apply_event(replayed, event, force=True)
    assert replayed.forms == load_show(designer).forms


def test_regression_accepting_twice_says_the_proposal_was_accepted() -> None:
    """Names the accepted state when a proposal is accepted twice (audit 9.7 collab)."""
    _, agent, designer = open_session(two_set_show(), AT)
    propose(agent, [_move("T1", -1.0)], "shift", "p1", AT)
    accept(designer, "p1", AT + STEP)
    with pytest.raises(E8sError, match="accepted"):
        accept(designer, "p1", AT + 2 * STEP)


def test_regression_proposal_against_unsaved_show_can_be_accepted() -> None:
    """Accepts a proposal made against a show that was saved afterwards (audit 9.7 collab)."""
    unsaved = two_set_show()
    repo = InMemoryRepository()
    designer = open_workspace(repo, "s")
    propose(open_workspace(repo, "s"), [_move("T1", -1.0)], "shift", "p1", AT)
    designer.refresh()
    save_show(designer, unsaved.replace(revision=designer.revision), AT + STEP)
    revision = accept(designer, "p1", AT + 2 * STEP)
    assert load_show(designer).set("2").get("T1").x == -1.0
    assert revision.id == designer.revision


@pytest.mark.defect("UP-9.7-46")
@pytest.mark.xfail(strict=True, reason="UP-9.7-46")
def test_regression_rescale_proposal_conflicts_with_a_newer_row_of_the_same_transition() -> None:
    """Refuses a rescale proposal after the designer edited that transition's rows (audit 9.7)."""
    _, agent, designer = open_session(_with_path_row(two_set_show()), AT)
    rescale = CountsOp(set_id=SetId("2"), counts=RESCALED_COUNTS, rescale=True)
    propose(agent, [rescale], "", "p1", AT)
    designer.refresh()
    newer = STRAIGHT_ROW.replace(rows={"T1": PathRow(entity=SpotId("T1"), start=4)})
    designer_show = load_show(designer).replace(transitions=(newer,))
    save_show(designer, designer_show, AT + STEP)
    with pytest.raises(ConflictError):
        accept(designer, "p1", AT + 2 * STEP)


def test_regression_accepting_a_form_edit_does_not_resurrect_a_deleted_form() -> None:
    """Refuses, or keeps the form deleted, when accepting an edit of a deleted form (audit 9.7)."""
    with_form = two_set_show().replace(forms={"f1": FORM})
    _, agent, designer = open_session(with_form, AT)
    edited_form = FORM.replace(recipe=LineRecipe(curve=line((0.0, 0.0), (9.0, 0.0))))
    propose(agent, [FormOp(form=edited_form)], "longer", "p1", AT)
    designer.refresh()
    save_show(designer, load_show(designer).replace(forms={}), AT + STEP)
    try:
        accept(designer, "p1", AT + 2 * STEP)
    except E8sError:
        return
    assert "f1" not in load_show(designer).forms


@pytest.mark.parametrize("payload", [{}, {"op": "nope"}, {"op": "counts", "counts": "x"}])
def test_regression_canvas_event_with_bad_payload_is_a_schema_error(
    payload: dict[str, object],
) -> None:
    """Raises SchemaError, not pydantic's ValidationError, for a malformed op payload."""
    with pytest.raises(SchemaError) as info:
        CanvasEvent.from_json({"id": "e", "at": AT, "kind": "set_counts", "payload": payload})
    assert info.value.context["errors"]


@pytest.mark.parametrize("spelling", ["1->2", "1→2"])
def test_regression_transition_pin_events_need_force_in_either_spelling(spelling: str) -> None:
    """Refuses replacing or dropping a transition pin whichever way its ID is spelled."""
    pinned = pin(two_set_show(), "transition", "1->2", "keep")
    held = Pin(target="transition", id=spelling, reason="x")
    replace = CanvasEvent(id="e", at=AT, kind="pin", payload=held)
    drop = CanvasEvent(id="e", at=AT, kind="unpin", payload=held)
    for event in (replace, drop):
        with pytest.raises(LockedError):
            apply_event(pinned, event)
    assert [p.reason for p in apply_event(pinned, replace, force=True).pins] == ["x"]
    assert apply_event(pinned, drop, force=True).pins == ()


def _event_json(**extra: JsonValue) -> dict[str, JsonValue]:
    return {
        "id": "h1",
        "at": AT.isoformat(),
        "kind": "highlight",
        "payload": {"zone": "pit", "ttl_s": 10.0},
        "revision": "",
        **extra,
    }


def test_event_payload_rejects_unknown_fields() -> None:
    """Refuses unknown fields on the event and inside its payload."""
    with pytest.raises(SchemaError):
        decode_event(_event_json(author="pat"))
    raw = _event_json()
    with pytest.raises(SchemaError):
        decode_event({**raw, "payload": {"zone": "pit", "ttl_s": 10.0, "colour": "red"}})
    with pytest.raises(SchemaError):
        CanvasEvent.from_json(
            {"id": "e", "at": AT, "kind": "set_counts", "payload": {"op": "counts", "extra": 1}}
        )


def test_regression_v02_event_with_actor_decodes() -> None:
    """Decodes a v0.2 event, drops its actor and records the drop in the report."""
    report = ReportBuilder(from_version=1)
    event = decode_event(_event_json(actor="agent"), report=report)
    assert event == highlight("pit", 10.0, at=AT, event_id="h1")
    assert len(report.build().repairs) == 1
    assert "agent" in report.build().repairs[0]
    assert decode_event(_event_json(actor="agent")) == event
