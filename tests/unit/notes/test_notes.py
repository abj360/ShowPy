#!/usr/bin/env python3

"""
test_notes.py --- tests adding, editing, finding and drafting design notes in the notes document

Contains:
    test_*(): one behavior of showpy.notes.notes each
    test_notes_functions_warn_until_1_0_0: warns DeprecatedWarning on every call of the six
        leaving functions and on `showpy.Note` and `showpy.NotesDocument`, naming 1.0.0
    test_notes_functions_refuse_the_notes_of_another_show: raises SchemaError for a notes
        document whose show is not the show passed
    test_regression_degenerate_area_anchor_never_leaks_shapely_errors: rejects a two-point area
        anchor, or finds it without a Shapely ValueError
    test_regression_accept_draft_accepts_a_hidden_draft_already_in_the_show: makes a hidden draft
        that is already in the notes document visible
    test_regression_accept_draft_rejects_blank_text: rejects a draft whose text is blank, as
        add_note does
    test_regression_note_rejects_a_naive_datetime: rejects a created_at without a time zone, which
        cannot be compared
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

import showpy
from showpy.choreo.models import PlacementPatch
from showpy.core.deprecation import DeprecatedWarning
from showpy.core.errors import GeometryError, SchemaError, ShowPyError, UnknownRefError
from showpy.core.ids import NoteId
from showpy.core.types import Region
from showpy.notes.models import Note, NotesDocument
from showpy.notes.notes import (
    accept_draft,
    add_note,
    edit_note,
    notes_for,
    notes_near,
    remove_note,
)
from showpy.show.models import Anchor
from tests.fixtures.shows import two_set_show

AT = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)
SHOW = two_set_show()
EMPTY = NotesDocument(show=SHOW.id)
LEAVING_TEXT = "is deprecated since 0.4.0 and leaves the library in 1.0.0."

leaving = pytest.mark.filterwarnings("ignore::showpy.core.deprecation.DeprecatedWarning")


def _draft(note_id: str = "draft_1", text: str = "build", set_id: str = "2") -> Note:
    return Note(
        id=NoteId(note_id),
        text=text,
        anchor=Anchor.on_set(set_id),
        author="agent",
        created_at=AT,
        visible=False,
    )


def noted() -> NotesDocument:
    notes = add_note(EMPTY, SHOW, "open wide", Anchor.on_set("1"), "n1", AT, "pat", kind="goal")
    notes = add_note(notes, SHOW, "push", Anchor.on_transition(("1", "2")), "n2", AT, "pat")
    notes = add_note(notes, SHOW, "mark", Anchor.at_point((10.0, 10.0)), "n3", AT, "pat")
    return add_note(notes, SHOW, "solo", Anchor.on_spots(["T4"]), "n4", AT, "pat", kind="todo")


CALLS: dict[str, Callable[[], object]] = {
    "add_note": lambda: add_note(EMPTY, SHOW, "go", Anchor.on_set("1"), "n1", AT, "pat"),
    "edit_note": lambda: edit_note(noted(), SHOW, "n1", text="go"),
    "remove_note": lambda: remove_note(noted(), "n1"),
    "notes_for": lambda: notes_for(EMPTY, SHOW, "1"),
    "notes_near": lambda: notes_near(EMPTY, SHOW, (0.0, 0.0), 1.0),
    "accept_draft": lambda: accept_draft(EMPTY, SHOW, _draft()),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_notes_functions_warn_until_1_0_0(name: str) -> None:
    with pytest.warns(DeprecatedWarning) as caught:
        CALLS[name]()
    assert f"`showpy.notes.notes.{name}` {LEAVING_TEXT}" in [str(w.message) for w in caught]
    with pytest.warns(DeprecatedWarning, match=r"leaves the library in 1\.0\.0"):
        assert showpy.Note is Note
    with pytest.warns(DeprecatedWarning, match=r"leaves the library in 1\.0\.0"):
        assert showpy.NotesDocument is NotesDocument


@leaving
def test_notes_functions_refuse_the_notes_of_another_show() -> None:
    other = NotesDocument(show="other")
    with pytest.raises(SchemaError, match="other"):
        add_note(other, SHOW, "go", Anchor.on_set("1"), "n1", AT, "pat")
    with pytest.raises(SchemaError):
        notes_for(other, SHOW, "1")
    with pytest.raises(SchemaError):
        accept_draft(other, SHOW, _draft())


@leaving
def test_add_note_stores_explicit_id_time_and_author() -> None:
    note = noted().notes[0]
    assert (note.id, note.created_at, note.author, note.kind) == ("n1", AT, "pat", "goal")
    assert note.visible


@leaving
def test_add_note_rejects_duplicate_id_and_blank_text() -> None:
    with pytest.raises(SchemaError):
        add_note(noted(), SHOW, "again", Anchor.on_set("1"), "n1", AT, "pat")
    with pytest.raises(SchemaError):
        add_note(noted(), SHOW, "again", Anchor.on_set("1"), "N1", AT, "pat")
    with pytest.raises(SchemaError):
        add_note(EMPTY, SHOW, "  ", Anchor.on_set("1"), "n9", AT, "pat")


@leaving
def test_add_note_rejects_bad_kind() -> None:
    with pytest.raises(SchemaError):
        add_note(EMPTY, SHOW, "x", Anchor.on_set("1"), "n9", AT, "pat", kind="rant")  # type: ignore[arg-type]


@leaving
@pytest.mark.parametrize(
    "anchor",
    [Anchor.on_set("9"), Anchor.on_transition(("2", "1")), Anchor.on_spots(["T1", "T9"])],
)
def test_add_note_rejects_unknown_anchor_targets(anchor: Anchor) -> None:
    with pytest.raises(UnknownRefError):
        add_note(EMPTY, SHOW, "x", anchor, "n9", AT, "pat")


@leaving
def test_edit_note_changes_fields_but_not_id() -> None:
    notes = edit_note(noted(), SHOW, "n1", text="open wider", kind="todo")
    assert (notes.notes[0].text, notes.notes[0].kind) == ("open wider", "todo")
    with pytest.raises(SchemaError):
        edit_note(noted(), SHOW, "n1", id="n7")
    with pytest.raises(UnknownRefError):
        edit_note(noted(), SHOW, "n1", anchor=Anchor.on_set("9"))


@leaving
def test_edit_and_remove_unknown_note_suggest_ids() -> None:
    with pytest.raises(UnknownRefError) as info:
        remove_note(noted(), "n5")
    assert "n" in info.value.hint
    with pytest.raises(UnknownRefError):
        edit_note(noted(), SHOW, "zz", text="x")


@leaving
def test_remove_note() -> None:
    assert [n.id for n in remove_note(noted(), "n2").notes] == ["n1", "n3", "n4"]


@leaving
def test_notes_for_set_and_transition() -> None:
    notes = noted()
    assert [n.id for n in notes_for(notes, SHOW, "1")] == ["n1"]
    assert [n.id for n in notes_for(notes, SHOW, ("1", "2"))] == ["n2"]
    assert notes_for(notes, SHOW, "2") == []
    with pytest.raises(UnknownRefError):
        notes_for(notes, SHOW, "7")


@leaving
def test_notes_for_hides_drafts_unless_asked() -> None:
    notes = noted()
    notes = notes.replace(notes=(*notes.notes, _draft("d1", "x", "1")))
    assert [n.id for n in notes_for(notes, SHOW, "1")] == ["n1"]
    assert [n.id for n in notes_for(notes, SHOW, "1", include_hidden=True)] == ["n1", "d1"]


@leaving
def test_notes_near_point_and_spot_anchors() -> None:
    notes = noted()
    assert [n.id for n in notes_near(notes, SHOW, (10.0, 11.0), 1.0)] == ["n3"]
    assert [n.id for n in notes_near(notes, SHOW, (6.0, 9.5), 0.5)] == ["n4"]
    assert notes_near(notes, SHOW, (6.0, 9.5), 0.5, set_id="1") == []
    assert [n.id for n in notes_near(notes, SHOW, (6.0, 5.0), 0.0, set_id="1")] == ["n4"]


@leaving
def test_notes_near_area_anchor_and_off_field_spots() -> None:
    square = Region(exterior=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    notes = add_note(EMPTY, SHOW, "zone", Anchor.on_area(square), "a1", AT, "pat")
    assert [n.id for n in notes_near(notes, SHOW, (2.0, 0.5), 1.0)] == ["a1"]
    off = SHOW.set("1").with_placements({"T1": PlacementPatch(on_field=False)})
    show = SHOW.model_copy(update={"sets": (off, SHOW.set("2"))})
    notes = add_note(EMPTY, show, "t1", Anchor.on_spots(["T1"]), "s1", AT, "pat")
    assert notes_near(notes, show, (0.0, 5.0), 0.1) == []


@leaving
def test_notes_near_rejects_negative_radius() -> None:
    with pytest.raises(GeometryError):
        notes_near(noted(), SHOW, (0.0, 0.0), -1.0)


@leaving
def test_accept_draft_adds_it_visible() -> None:
    notes = accept_draft(EMPTY, SHOW, _draft())
    assert notes.notes[-1].visible
    assert notes.notes[-1].author == "agent"
    assert [n.id for n in notes_for(notes, SHOW, "2")] == ["draft_1"]


REJECTED = (ShowPyError, PydanticValidationError)
NAIVE_TIME = datetime(2026, 11, 11)  # noqa: DTZ001 - the tests check it is rejected


@leaving
def test_regression_degenerate_area_anchor_never_leaks_shapely_errors() -> None:
    """Rejects a two-point area anchor, or finds it without a Shapely ValueError (audit 9.3)."""
    try:
        anchor = Anchor.on_area(Region(exterior=((0.0, 0.0), (1.0, 1.0))))
        notes = add_note(EMPTY, SHOW, "area", anchor, "n1", AT, "pat")
        notes_near(notes, SHOW, (0.0, 0.0), 1.0)
    except REJECTED:
        return


@leaving
def test_regression_accept_draft_accepts_a_hidden_draft_already_in_the_show() -> None:
    """Makes a hidden draft that is already in the notes document visible (audit 9.3)."""
    draft = _draft("d1", "go", "1")
    hidden = EMPTY.replace(notes=(draft,))
    assert accept_draft(hidden, SHOW, draft).notes[0].visible


@leaving
def test_regression_accept_draft_rejects_blank_text() -> None:
    """Rejects a draft whose text is blank, as add_note does (audit 9.3)."""
    with pytest.raises(SchemaError):
        accept_draft(EMPTY, SHOW, _draft("d1", "   ", "1"))


def test_regression_note_rejects_a_naive_datetime() -> None:
    """Rejects a created_at without a time zone, which cannot be compared (audit 9.3)."""
    with pytest.raises(REJECTED):
        Note(
            id=NoteId("n1"),
            text="go",
            anchor=Anchor.on_set("1"),
            author="pat",
            created_at=NAIVE_TIME,
        )


@leaving
def test_regression_add_note_keeps_the_anchor_payload_error() -> None:
    bad_anchor = {"kind": "set", "set_id": "1", "point": (0.0, 0.0)}
    with pytest.raises(SchemaError) as info:
        add_note(
            EMPTY,
            SHOW,
            "text",
            bad_anchor,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
            "n1",
            datetime(2026, 1, 1, tzinfo=UTC),
            "me",
        )
    assert info.value.message == "set anchor needs only set_id"
