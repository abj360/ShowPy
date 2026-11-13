#!/usr/bin/env python3

"""
test_notes.py --- tests adding, editing, finding and drafting design notes

Contains:
    FakeDrafter: records calls and returns canned drafts, or raises a canned error
    test_*(): one behavior of showpy.notes.notes each
"""

from datetime import UTC, datetime

import pytest

from showpy.core.errors import GeometryError, SchemaError, UnknownRefError
from showpy.core.types import Region
from showpy.notes.models import Anchor, Note
from showpy.notes.notes import (
    accept_draft,
    add_note,
    edit_note,
    notes_for,
    notes_near,
    remove_note,
)
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show

AT = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)


def noted_show() -> Show:
    show = two_set_show()
    show = add_note(show, "open wide", Anchor.on_set("1"), "n1", AT, "pat", kind="goal")
    show = add_note(show, "push", Anchor.on_transition(("1", "2")), "n2", AT, "pat")
    show = add_note(show, "mark", Anchor.at_point((10.0, 10.0)), "n3", AT, "pat")
    show = add_note(show, "solo", Anchor.on_spots(["T4"]), "n4", AT, "pat", kind="todo")
    return show


def test_add_note_stores_explicit_id_time_and_author() -> None:
    note = noted_show().notes[0]
    assert (note.id, note.created_at, note.author, note.kind) == ("n1", AT, "pat", "goal")
    assert note.visible


def test_add_note_rejects_duplicate_id_and_blank_text() -> None:
    with pytest.raises(SchemaError):
        add_note(noted_show(), "again", Anchor.on_set("1"), "n1", AT, "pat")
    with pytest.raises(SchemaError):
        add_note(two_set_show(), "  ", Anchor.on_set("1"), "n9", AT, "pat")


def test_add_note_rejects_bad_kind() -> None:
    with pytest.raises(SchemaError):
        add_note(two_set_show(), "x", Anchor.on_set("1"), "n9", AT, "pat", kind="rant")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "anchor",
    [Anchor.on_set("9"), Anchor.on_transition(("2", "1")), Anchor.on_spots(["T1", "T9"])],
)
def test_add_note_rejects_unknown_anchor_targets(anchor: Anchor) -> None:
    with pytest.raises(UnknownRefError):
        add_note(two_set_show(), "x", anchor, "n9", AT, "pat")


def test_edit_note_changes_fields_but_not_id() -> None:
    show = edit_note(noted_show(), "n1", text="open wider", kind="todo")
    assert (show.notes[0].text, show.notes[0].kind) == ("open wider", "todo")
    with pytest.raises(SchemaError):
        edit_note(noted_show(), "n1", id="n7")
    with pytest.raises(UnknownRefError):
        edit_note(noted_show(), "n1", anchor=Anchor.on_set("9"))


def test_edit_and_remove_unknown_note_suggest_ids() -> None:
    with pytest.raises(UnknownRefError) as info:
        remove_note(noted_show(), "n5")
    assert "n" in info.value.hint
    with pytest.raises(UnknownRefError):
        edit_note(noted_show(), "zz", text="x")


def test_remove_note() -> None:
    assert [n.id for n in remove_note(noted_show(), "n2").notes] == ["n1", "n3", "n4"]


def test_notes_for_set_and_transition() -> None:
    show = noted_show()
    assert [n.id for n in notes_for(show, "1")] == ["n1"]
    assert [n.id for n in notes_for(show, ("1", "2"))] == ["n2"]
    assert notes_for(show, "2") == []
    with pytest.raises(UnknownRefError):
        notes_for(show, "7")


def test_notes_for_hides_drafts_unless_asked() -> None:
    hidden = Note(
        id="d1",
        text="x",
        anchor=Anchor.on_set("1"),
        author="agent",
        created_at=AT,
        visible=False,
    )
    show = noted_show().model_copy(update={"notes": (*noted_show().notes, hidden)})
    assert [n.id for n in notes_for(show, "1")] == ["n1"]
    assert [n.id for n in notes_for(show, "1", include_hidden=True)] == ["n1", "d1"]


def test_notes_near_point_and_spot_anchors() -> None:
    show = noted_show()
    assert [n.id for n in notes_near(show, (10.0, 11.0), 1.0)] == ["n3"]
    assert [n.id for n in notes_near(show, (6.0, 9.5), 0.5)] == ["n4"]
    assert notes_near(show, (6.0, 9.5), 0.5, set_id="1") == []
    assert [n.id for n in notes_near(show, (6.0, 5.0), 0.0, set_id="1")] == ["n4"]


def test_notes_near_area_anchor_and_off_field_spots() -> None:
    square = Region(exterior=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    show = add_note(two_set_show(), "zone", Anchor.on_area(square), "a1", AT, "pat")
    assert [n.id for n in notes_near(show, (2.0, 0.5), 1.0)] == ["a1"]
    off = show.set("1").with_placements({"T1": {"on_field": False}})
    show = show.model_copy(update={"sets": (off, show.set("2"))})
    show = add_note(show, "t1", Anchor.on_spots(["T1"]), "s1", AT, "pat")
    assert notes_near(show, (0.0, 5.0), 0.1) == []


def test_notes_near_rejects_negative_radius() -> None:
    with pytest.raises(GeometryError):
        notes_near(noted_show(), (0.0, 0.0), -1.0)


def test_accept_draft_adds_it_visible() -> None:
    draft = Note(
        id="draft-1",
        text="build",
        anchor=Anchor.on_set("2"),
        author="agent",
        created_at=AT,
        visible=False,
    )
    show = accept_draft(two_set_show(), draft)
    assert show.notes[-1].visible
    assert show.notes[-1].author == "agent"
    assert [n.id for n in notes_for(show, "2")] == ["draft-1"]
