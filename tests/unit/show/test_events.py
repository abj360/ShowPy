#!/usr/bin/env python3

"""
test_events.py --- tests that canvas events derive from changed documents and replay to head

Contains:
    test_events_since_decodes_no_whole_show: decodes one document per changed document, never the
        unchanged rest of each show
    test_events_since_decodes_no_revision_without_show_documents: skips the show decode of a
        revision that changed no show document
    test_every_event_kind_round_trips_through_json: rebuilds each event kind from its JSON
    test_events_since_the_root_of_a_legacy_repository: derives events from a v0.2 repository's
        first revision
    test_event_ids_are_checked_on_decode: refuses an event ID that breaks the sheet ID rule
    test_events_since_replay_reproduces_head_after_random_edits: replays random edit sequences
        onto the base show and gets the head show
    test_regression_form_deletion_is_a_delete_form_event: derives and replays a form deletion
    test_regression_locked_form_deletion_needs_force: refuses replaying a locked form's deletion
    test_regression_transition_paths_edit_without_counts_change_is_reported: reports a changed
        path row as edit_paths
    test_regression_events_since_ignores_a_revision_record_that_hides_a_change: finds the
        documents a revision changed from its tree, not from its record's changed list
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.choreo.recipe_models import LineRecipe
from e8s.core.errors import LockedError, SchemaError
from e8s.core.ids import ID_MAX_LENGTH, FormId, SetId, SpotId, make_tid
from e8s.core.json_types import JsonValue
from e8s.geo.shapes import line
from e8s.show.anchors import Anchor
from e8s.show.changes import apply_change
from e8s.show.collab import highlight
from e8s.show.collab_models import (
    EVENT_PAYLOADS,
    CanvasEvent,
    Comment,
    CountsOp,
    FormOp,
    PathsOp,
    PlacementOp,
)
from e8s.show.documents import dump_json
from e8s.show.events import apply_event, decode_event, events_since
from e8s.show.manage import add_set
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin
from e8s.show.show_io import load_show, save_show
from e8s.show.storage import ROOT_REVISION
from e8s.show.structured import COMMENTS_DOC
from e8s.show.workspace import Workspace
from tests.fixtures.legacy_v020 import v020_workspace
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
EXTRA_SETS = 8
MAX_EXAMPLES = 25
MAX_EDITS = 6
EDIT_KINDS = ("move", "counts", "form_add", "form_delete", "paths", "pin")


def _wide_show() -> Show:
    show = add_set(two_set_show(), "3", 4.0)
    for number in range(4, 4 + EXTRA_SETS):
        show = add_set(show, str(number), 4.0)
    return show


def _move(entity: str, x: float, set_id: str = "2") -> PlacementOp:
    return PlacementOp(set_id=SetId(set_id), placement=Placement(entity=SpotId(entity), x=x, y=9.0))


def _save_edit(designer: Workspace, *ops: PlacementOp | FormOp | PathsOp | CountsOp) -> str:
    edited = apply_change(load_show(designer), list(ops), force=True)
    return save_show(designer, edited, AT + STEP).id


def test_events_since_decodes_no_whole_show() -> None:
    _, agent, designer = open_session(_wide_show(), AT)
    base = designer.revision
    edited = [
        _save_edit(designer, _move("T1", -1.0 - n, set_id)) for n, set_id in enumerate("2593")
    ]
    agent.show_at(base)
    before = agent._decode_cache.misses
    events = events_since(agent, base)
    changed = [path for r in agent.repo.revisions(agent.show_id)[1:] for path in r.changed]
    assert [(e.kind, e.revision) for e in events] == [("move", r) for r in edited]
    assert changed == [f"/sets/{set_id}/drill.json" for set_id in "2593"]
    assert agent._decode_cache.misses - before == len(changed)


def test_events_since_decodes_no_revision_without_show_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, agent, designer = open_session(_wide_show(), AT)
    base = designer.revision
    first = _save_edit(designer, _move("T1", -1.0))
    comment = Comment(id="c1", anchor=Anchor.on_set("2"), text="tighten the line")
    commented = designer.write(COMMENTS_DOC, dump_json([comment._dumped()]), at=AT + 2 * STEP).id
    last = _save_edit(designer, _move("T1", -2.0, "3"))
    requested: list[str] = []
    show_at = agent.show_at

    def recording_show_at(revision: str) -> Show | None:
        requested.append(revision)
        return show_at(revision)

    monkeypatch.setattr(agent, "show_at", recording_show_at)
    events = events_since(agent, base)
    assert [e.kind for e in events] == ["move", "comment", "move"]
    assert commented not in requested
    assert set(requested) <= {base, first, last}


def _every_event_kind() -> list[CanvasEvent]:
    pinned = Pin(target="set", id="2", reason="hold")
    payloads: dict[str, object] = {
        "move": _move("T1", 3.0),
        "edit_form": FormOp(form=FORM),
        "delete_form": FormOp(form=FORM),
        "edit_paths": PathsOp(spec=TransitionSpec(id=make_tid("1", "2"), counts=8.0)),
        "set_counts": CountsOp(set_id=SetId("2"), counts=6.0),
        "pin": pinned,
        "unpin": pinned,
        "comment": Comment(id="c1", anchor=Anchor.on_set("2"), text="tighten"),
        "highlight": highlight("pit", 10.0, at=AT, event_id="h1").payload,
    }
    return [
        CanvasEvent.model_validate({"id": f"e{n}", "at": AT, "kind": kind, "payload": payload})
        for n, (kind, payload) in enumerate(payloads.items())
    ]


def test_every_event_kind_round_trips_through_json() -> None:
    events = _every_event_kind()
    assert {e.kind for e in events} == set(EVENT_PAYLOADS)
    for event in events:
        raw = json.loads(json.dumps(event.to_json()))
        assert decode_event(raw) == event


def test_events_since_the_root_of_a_legacy_repository(tmp_path: Path) -> None:
    _, ws, expected = v020_workspace(tmp_path)
    first, second, head = expected["revisions"]
    events = events_since(ws, first)
    assert [(e.kind, e.revision) for e in events] == [
        ("edit_paths", second),
        ("pin", head),
        *[("move", head)] * 6,
    ]
    replayed = ws.show_at(first)
    assert replayed is not None
    for event in events:
        replayed = apply_event(replayed, event, force=True)
    assert replayed.replace(revision=head) == ws.show_at(head)
    assert events_since(ws, ROOT_REVISION) == events
    assert events_since(ws, head) == []


@pytest.mark.parametrize(
    "event_id",
    ["", " ", " e1", "e1 ", "e\n1", "e\x001", "e\u200b1", "e" * (ID_MAX_LENGTH + 1), "-e1", "con"],
)
def test_event_ids_are_checked_on_decode(event_id: str) -> None:
    raw: dict[str, JsonValue] = {
        "id": event_id,
        "at": AT.isoformat(),
        "kind": "highlight",
        "payload": {"zone": "pit", "ttl_s": 10.0},
    }
    with pytest.raises(SchemaError, match="bad event id"):
        decode_event(raw)


def test_regression_form_deletion_is_a_delete_form_event() -> None:
    with_form = two_set_show().replace(forms={"f1": FORM})
    _, agent, designer = open_session(with_form, AT)
    base_revision = designer.revision
    base = load_show(designer)
    save_show(
        designer,
        base.replace(forms={}, sets=(base.sets[0], base.sets[1].replace(forms=()))),
        AT + STEP,
    )
    (event,) = events_since(agent, base_revision)
    assert (event.kind, event.payload) == ("delete_form", FormOp(form=FORM))
    assert apply_event(base, event, force=True).forms == {}


def test_regression_locked_form_deletion_needs_force() -> None:
    locked = FORM.replace(locked=True)
    base = two_set_show().replace(forms={"f1": locked})
    event = CanvasEvent(id="e", at=AT, kind="delete_form", payload=FormOp(form=locked))
    with pytest.raises(LockedError):
        apply_event(base, event)
    assert apply_event(base, event, force=True).forms == {}


def test_regression_transition_paths_edit_without_counts_change_is_reported() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    base = designer.revision
    row = PathRow(entity=SpotId("T1"), start=2)
    spec = TransitionSpec(id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): row})
    _save_edit(designer, PathsOp(spec=spec))
    (event,) = events_since(agent, base)
    assert (event.kind, event.payload) == ("edit_paths", PathsOp(spec=spec))


def test_regression_events_since_ignores_a_revision_record_that_hides_a_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, agent, designer = open_session(_wide_show(), AT)
    base_revision = designer.revision
    base = load_show(designer)
    hidden = _save_edit(designer, _move("T1", -1.0))
    later = _save_edit(designer, _move("T2", -2.0, "3"))
    log = agent.repo.revisions(agent.show_id)
    forged = [r.replace(changed=()) if r.id == hidden else r for r in log]
    assert "/sets/2/drill.json" in next(r.changed for r in log if r.id == hidden)
    monkeypatch.setattr(agent.repo, "revisions", {agent.show_id: forged}.__getitem__)
    events = events_since(agent, base_revision)
    assert [(e.kind, e.revision) for e in events] == [("move", hidden), ("move", later)]
    replayed = base
    for event in events:
        replayed = apply_event(replayed, event, force=True)
    head = load_show(designer)
    assert replayed.replace(revision=head.revision) == head


def _edit(show: Show, kind: str, number: int) -> Show:
    if kind == "move":
        return apply_change(show, [_move("T1", float(number))], force=True)
    if kind == "counts":
        return apply_change(show, [CountsOp(set_id=SetId("3"), counts=4.0 + number)], force=True)
    if kind == "form_add":
        return apply_change(show, [FormOp(form=FORM)], force=True)
    if kind == "form_delete":
        kept = {k: f for k, f in show.forms.items() if k != "f1"}
        return show.replace(
            forms=kept,
            sets=tuple(s.replace(forms=tuple(f for f in s.forms if f != "f1")) for s in show.sets),
        )
    if kind == "paths":
        row = PathRow(entity=SpotId("T2"), start=number % 4)
        spec = TransitionSpec(id=make_tid("2", "3"), counts=4.0, rows={SpotId("T2"): row})
        return apply_change(show, [PathsOp(spec=spec)], force=True)
    return pin(show, "entity", "T4", f"hold {number}")


@settings(max_examples=MAX_EXAMPLES, deadline=None)
@example(["form_add", "form_delete"])
@example(["counts", "paths", "pin"])
@given(st.lists(st.sampled_from(EDIT_KINDS), min_size=1, max_size=MAX_EDITS))
def test_events_since_replay_reproduces_head_after_random_edits(kinds: list[str]) -> None:
    _, agent, designer = open_session(add_set(two_set_show(), "3", 4.0), AT)
    base_revision = designer.revision
    base = load_show(designer)
    for number, kind in enumerate(kinds):
        save_show(designer, _edit(load_show(designer), kind, number), AT + (number + 1) * STEP)
    replayed = base
    for event in events_since(agent, base_revision):
        replayed = apply_event(replayed, event, force=True)
    head = load_show(designer)
    assert replayed.replace(revision=head.revision) == head
