"""
notes.py --- adds, edits and finds design notes on sets, transitions, spots and areas

Contains:
    add_note(): adds a note with an explicit ID and time stamp
    edit_note(): changes a note's fields
    remove_note(): removes a note
    notes_for(): returns the notes anchored to a set or a transition
    notes_near(): returns the notes anchored at or around a point
    accept_draft(): adds a drafted note to the show, visible
"""

import math
from datetime import datetime
from typing import TYPE_CHECKING, Any

import shapely

from showpy.cast.select import select
from showpy.core.errors import GeometryError, SchemaError, UnknownRefError
from showpy.core.ids import SetId, TransitionId
from showpy.core.types import Meters, Point
from showpy.core.util import close_matches
from showpy.notes.models import Anchor, Note, NoteKind

if TYPE_CHECKING:
    from showpy.show.models import Show


def _check_anchor(show: "Show", anchor: Anchor) -> None:
    if anchor.set_id is not None:
        show.set_index(anchor.set_id)
    if anchor.tid is not None:
        show.transition(anchor.tid)
    if anchor.spots is not None:
        select(show, list(anchor.spots))


def _note_index(show: "Show", note_id: str) -> int:
    for index, note in enumerate(show.notes):
        if note.id == note_id:
            return index
    known = [n.id for n in show.notes]
    close = close_matches(note_id, known)
    raise UnknownRefError(
        f"unknown note {note_id!r}",
        hint=f"did you mean {', '.join(close)}?" if close else "check the note id",
        context={"note": note_id},
    )


# Any: fields are arbitrary Note fields, validated by the model.
def _build_note(fields: dict[str, Any]) -> Note:
    try:
        note = Note.model_validate(fields)
    except SchemaError as error:
        raise SchemaError(
            f"bad note: {error.context['errors'][0]['msg']}",
            hint=f"give only these fields: {', '.join(Note.model_fields)}",
            context={"fields": sorted(fields)},
        ) from error
    if not note.text.strip():
        raise SchemaError("note text is blank", hint="write some text", context={"note": note.id})
    return note


def _insert(show: "Show", note: Note) -> "Show":
    if any(n.id == note.id for n in show.notes):
        raise SchemaError(
            f"note {note.id!r} already exists",
            hint="pick a new note id, or use edit_note",
            context={"note": note.id},
        )
    _check_anchor(show, note.anchor)
    return show.model_copy(update={"notes": (*show.notes, note)})


def add_note(
    show: "Show",
    text: str,
    anchor: Anchor,
    note_id: str,
    at: datetime,
    author: str,
    kind: NoteKind = "idea",
) -> "Show":
    """Adds a note; the ID and time stamp are explicit, so the result is deterministic.

    Args:
        show: Show to add to.
        text: Note body; must not be blank.
        anchor: What the note is attached to; its set, transition or spots must exist.
        note_id: New, unique note ID.
        at: When the note was written.
        author: User name, or "agent".
        kind: goal, idea, todo or warning.

    >>> from datetime import UTC, datetime
    >>> from showpy.examples import two_set_show
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> add_note(two_set_show(), "go big", Anchor.on_set("2"), "n1", at, "pat").notes[0].text
    'go big'
    """
    fields = {"id": note_id, "text": text, "kind": kind, "anchor": anchor}
    note = _build_note({**fields, "author": author, "created_at": at})
    return _insert(show, note)


# Any: changes are arbitrary Note fields, validated by the rebuilt model.
def edit_note(show: "Show", note_id: str, /, **changes: Any) -> "Show":
    """Changes a note's fields; the ID is fixed.

    >>> from datetime import UTC, datetime
    >>> from showpy.examples import two_set_show
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> show = add_note(two_set_show(), "go", Anchor.on_set("2"), "n1", at, "pat")
    >>> edit_note(show, "n1", kind="todo").notes[0].kind
    'todo'
    """
    index = _note_index(show, note_id)
    if "id" in changes:
        raise SchemaError(
            "a note id cannot change",
            hint="remove the note and add a new one",
            context={"note": note_id},
        )
    edited = _build_note({**dict(show.notes[index]), **changes})
    _check_anchor(show, edited.anchor)
    notes = (*show.notes[:index], edited, *show.notes[index + 1 :])
    return show.model_copy(update={"notes": notes})


def remove_note(show: "Show", note_id: str) -> "Show":
    """Removes a note.

    >>> from datetime import UTC, datetime
    >>> from showpy.examples import two_set_show
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> show = add_note(two_set_show(), "go", Anchor.on_set("2"), "n1", at, "pat")
    >>> remove_note(show, "n1").notes
    ()
    """
    index = _note_index(show, note_id)
    return show.model_copy(update={"notes": (*show.notes[:index], *show.notes[index + 1 :])})


def notes_for(
    show: "Show", target: SetId | TransitionId, include_hidden: bool = False
) -> list[Note]:
    """Returns the notes anchored to a set (an ID) or a transition (a pair), in show order.

    Hidden notes, such as unaccepted drafts, are left out unless `include_hidden` is set.

    >>> from datetime import UTC, datetime
    >>> from showpy.examples import two_set_show
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> show = add_note(two_set_show(), "go", Anchor.on_set("2"), "n1", at, "pat")
    >>> [n.id for n in notes_for(show, "2")]
    ['n1']
    """
    if isinstance(target, str):
        show.set_index(target)
        anchor = Anchor.on_set(target)
    else:
        show.transition(target)
        anchor = Anchor.on_transition(target)
    return [n for n in show.notes if n.anchor == anchor and (n.visible or include_hidden)]


def _spot_points(show: "Show", note: Note, set_id: SetId | None) -> list[Point]:
    sets = show.sets if set_id is None else (show.set(set_id),)
    spots = set(note.anchor.spots or ())
    return [p.point for s in sets for p in s.placements if p.on_field and p.entity in spots]


def _is_near(show: "Show", note: Note, p: Point, radius: Meters, set_id: SetId | None) -> bool:
    anchor = note.anchor
    if anchor.point is not None:
        return math.dist(anchor.point, p) <= radius
    if anchor.area is not None:
        return bool(anchor.area.to_shapely().distance(shapely.Point(p)) <= radius)
    if anchor.spots is not None:
        return any(math.dist(q, p) <= radius for q in _spot_points(show, note, set_id))
    return False


def notes_near(
    show: "Show",
    p: Point,
    radius: Meters,
    set_id: SetId | None = None,
    include_hidden: bool = False,
) -> list[Note]:
    """Returns notes anchored within `radius` of a point, in show order.

    Point anchors match by distance, area anchors by distance to the area, and spot anchors
    when any anchored spot's on-field placement in any set (or only in `set_id`) is close.

    >>> from datetime import UTC, datetime
    >>> from showpy.examples import two_set_show
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> show = add_note(two_set_show(), "lead", Anchor.on_spots(["T1"]), "n1", at, "pat")
    >>> [n.id for n in notes_near(show, (0.0, 5.5), 1.0)]
    ['n1']
    """
    if radius < 0:
        raise GeometryError(
            "radius must not be negative", hint="pass radius >= 0", context={"radius": radius}
        )
    if set_id is not None:
        show.set_index(set_id)
    return [
        n
        for n in show.notes
        if (n.visible or include_hidden) and _is_near(show, n, p, radius, set_id)
    ]


def accept_draft(show: "Show", draft: Note) -> "Show":
    """Adds a drafted note to the show and makes it visible.

    >>> from datetime import UTC, datetime
    >>> from showpy.examples import two_set_show
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> draft = Note(id="draft-1", text="go", anchor=Anchor.on_set("1"), author="agent",
    ...              created_at=at, visible=False)
    >>> accept_draft(two_set_show(), draft).notes[0].visible
    True
    """
    return _insert(show, draft.model_copy(update={"visible": True}))
