#!/usr/bin/env python3

"""
notes.py --- adds, edits, finds and drafts design notes on sets, transitions, spots and areas

Contains:
    NoteDrafter: describes an injected service that proposes notes from a show concept
    add_note(): adds a note with an explicit ID and time stamp
    edit_note(): changes a note's fields
    remove_note(): removes a note
    notes_for(): returns the notes anchored to a set or a transition
    notes_near(): returns the notes anchored at or around a point
    draft_notes(): asks a drafter for suggested notes, returned hidden
    accept_draft(): adds a drafted note to the show, visible
"""

import math
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

import shapely
from pydantic import ValidationError as PydanticValidationError

from stage.cast.select import select
from stage.core.errors import GeometryError, SchemaError, UnknownRefError, ValidationError
from stage.core.ids import SetId, TransitionId, format_tid
from stage.core.types import Meters, Point
from stage.core.util import close_matches
from stage.notes.models import Anchor, DraftedNote, Note, NoteKind

if TYPE_CHECKING:
    from stage.show.models import Show

AGENT_AUTHOR = "agent"
DRAFT_ID_PREFIX = "draft"
MAX_DRAFTED_NOTES = 50


class NoteDrafter(Protocol):
    """Describes an injected service, such as an LLM client, that proposes design notes."""

    def draft(self, show_summary: str, concept: str) -> list[DraftedNote]:
        """Returns proposed notes for a show, given its summary and the designer's concept."""
        ...


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
    except PydanticValidationError as error:
        raise SchemaError(
            f"bad note: {error.errors()[0]['msg']}",
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
    >>> from tests.fixtures.shows import two_set_show
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
    >>> from tests.fixtures.shows import two_set_show
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
    >>> from tests.fixtures.shows import two_set_show
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
    >>> from tests.fixtures.shows import two_set_show
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
    >>> from tests.fixtures.shows import two_set_show
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


def _summary(show: "Show") -> str:
    sets = ", ".join(f"{s.id} ({s.counts:g} counts)" for s in show.sets) or "none"
    groups = ", ".join(
        f"{g.key} x{sum(1 for s in show.cast.spots if s.group == g.key)}"
        for g in show.cast.groups.values()
    )
    lines = [f"show: {show.title}", f"sets: {sets}", f"groups: {groups or 'none'}"]
    lines += [f"note [{n.kind}] {_where(n.anchor)}: {n.text}" for n in show.notes if n.visible]
    return "\n".join(lines)


def _where(anchor: Anchor) -> str:
    if anchor.set_id is not None:
        return f"set {anchor.set_id}"
    if anchor.tid is not None:
        return f"transition {format_tid(anchor.tid)}"
    if anchor.spots is not None:
        return f"spots {', '.join(anchor.spots)}"
    return anchor.kind


def _draft_ids(show: "Show", count: int) -> list[str]:
    taken = {n.id for n in show.notes}
    ids: list[str] = []
    number = 1
    while len(ids) < count:
        candidate = f"{DRAFT_ID_PREFIX}-{number}"
        if candidate not in taken:
            ids.append(candidate)
        number += 1
    return ids


def _checked_drafts(show: "Show", drafted: object) -> list[DraftedNote]:
    if not isinstance(drafted, list | tuple):
        raise SchemaError(
            f"drafter returned a {type(drafted).__name__}",
            hint="drafters must return a list of DraftedNote objects",
        )
    if len(drafted) > MAX_DRAFTED_NOTES:
        raise ValidationError(
            f"drafter returned {len(drafted)} notes",
            hint=f"drafters may return at most {MAX_DRAFTED_NOTES} notes",
            context={"count": len(drafted)},
        )
    for index, draft in enumerate(drafted):
        if not isinstance(draft, DraftedNote):
            raise SchemaError(
                f"draft {index} is a {type(draft).__name__}, not a DraftedNote",
                hint="drafters must return DraftedNote objects",
                context={"index": index},
            )
        _check_anchor(show, draft.anchor)
    return [d for d in drafted if isinstance(d, DraftedNote)]


def draft_notes(show: "Show", concept: str, drafter: NoteDrafter, at: datetime) -> list[Note]:
    """Asks a drafter for suggested set goals and ideas; returns them hidden, not in the show.

    Drafts get IDs `draft-1`, `draft-2`, ... (skipping IDs in use), author "agent", time
    stamp `at` and `visible=False`; accept_draft() adds one to the show. Errors raised by
    the drafter propagate; malformed drafts, unknown anchors or more than
    MAX_DRAFTED_NOTES drafts reject the whole batch.

    >>> from datetime import UTC, datetime
    >>> from tests.fixtures.shows import two_set_show
    >>> class Drafter:
    ...     def draft(self, show_summary: str, concept: str) -> list[DraftedNote]:
    ...         return [DraftedNote(text=concept, kind="goal", anchor=Anchor.on_set("1"))]
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> [(n.id, n.visible) for n in draft_notes(two_set_show(), "calm", Drafter(), at)]
    [('draft-1', False)]
    """
    drafted = _checked_drafts(show, drafter.draft(_summary(show), concept))
    ids = _draft_ids(show, len(drafted))
    return [
        _build_note(
            {
                **dict(draft),
                "id": note_id,
                "author": AGENT_AUTHOR,
                "created_at": at,
                "visible": False,
            }
        )
        for note_id, draft in zip(ids, drafted, strict=True)
    ]


def accept_draft(show: "Show", draft: Note) -> "Show":
    """Adds a drafted note to the show and makes it visible.

    >>> from datetime import UTC, datetime
    >>> from tests.fixtures.shows import two_set_show
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> draft = Note(id="draft-1", text="go", anchor=Anchor.on_set("1"), author="agent",
    ...              created_at=at, visible=False)
    >>> accept_draft(two_set_show(), draft).notes[0].visible
    True
    """
    return _insert(show, draft.model_copy(update={"visible": True}))
