"""
notes.py --- adds, edits and finds design notes in the notes document kept beside a show

Notes leave the library: from v0.4.0 a show holds no notes, and these functions read and write
the notes document (`NotesDocument`, stored at `/notes/notes.json`) until the app keeps it. Each
public function warns `DeprecatedWarning` and is removed in 1.0.0. Anchors are checked against
the show the document belongs to.

Contains:
    add_note(): adds a note with an explicit ID and time stamp
    edit_note(): changes a note's fields
    remove_note(): removes a note
    notes_for(): returns the notes anchored to a set or a transition
    notes_near(): returns the notes anchored at or around a point
    accept_draft(): adds a drafted note to the notes document, visible
    rewrite_note_anchors(): points note anchors at renamed sets and spots, dropping orphans
"""

import math
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import shapely

from e8s.cast.rewrite import rewrite_note_spots
from e8s.cast.select import select
from e8s.core.deprecation import leaving_call
from e8s.core.errors import GeometryError, SchemaError, UnknownRefError, field_errors
from e8s.core.ids import SetId, SpotId
from e8s.core.types import Meters, Point
from e8s.core.util import close_matches
from e8s.show._notes_document import Note, NoteKind, NotesDocument, require_notes_of
from e8s.show.anchors import Anchor, renamed_anchor

if TYPE_CHECKING:
    from e8s.show.models import Show

type SetMapping = Mapping[SetId, str | None] | Mapping[str, str | None]
type SpotMapping = Mapping[SpotId, str | None] | Mapping[str, str | None]

SINCE: Final = "0.4.0"
REMOVE: Final = "1.0.0"


def _check_anchor(show: "Show", anchor: Anchor) -> None:
    if anchor.set_id is not None:
        show.set_index(anchor.set_id)
    if anchor.tid is not None:
        show.transition(anchor.tid)
    if anchor.spots is not None:
        select(show, list(anchor.spots))


def _note_index(notes: NotesDocument, note_id: str) -> int:
    for index, note in enumerate(notes.notes):
        if note.id == note_id:
            return index
    known = [n.id for n in notes.notes]
    close = close_matches(note_id, known)
    raise UnknownRefError(
        f"unknown note {note_id!r}",
        hint=f"did you mean {', '.join(close)}?" if close else "check the note id",
        context={"note": note_id},
    )


# Any: fields are arbitrary Note fields, validated by the model.
def _build_note(fields: dict[str, Any]) -> Note:
    try:
        note = Note._validated(fields)
    except SchemaError as error:
        raise SchemaError(
            f"bad note: {field_errors(error)[0]['msg']}",
            hint=f"give only these fields: {', '.join(Note._field_names())}",
            context={"fields": sorted(fields)},
        ) from error
    if not note.text.strip():
        raise SchemaError("note text is blank", hint="write some text", context={"note": note.id})
    return note


def _insert(notes: NotesDocument, show: "Show", note: Note) -> NotesDocument:
    if any(n.id.casefold() == note.id.casefold() for n in notes.notes):
        raise SchemaError(
            f"note {note.id!r} already exists",
            hint="pick a note id unique ignoring case, or use edit_note",
            context={"note": note.id},
        )
    _check_anchor(show, note.anchor)
    return notes.replace(notes=(*notes.notes, note))


def _add_note(
    notes: NotesDocument,
    show: "Show",
    text: str,
    anchor: Anchor,
    note_id: str,
    at: datetime,
    author: str,
    kind: NoteKind = "idea",
) -> NotesDocument:
    """Adds a note; the ID and time stamp are explicit, so the result is deterministic."""
    require_notes_of(notes, show.id)
    fields = {"id": note_id, "text": text, "kind": kind, "anchor": anchor}
    note = _build_note({**fields, "author": author, "created_at": at})
    return _insert(notes, show, note)


@leaving_call(since=SINCE, remove=REMOVE)
def add_note(
    notes: NotesDocument,
    show: "Show",
    text: str,
    anchor: Anchor,
    note_id: str,
    at: datetime,
    author: str,
    kind: NoteKind = "idea",
) -> NotesDocument:
    """Adds a note to the notes document; the ID and time stamp are explicit.

    Deprecated: this name leaves the library in 1.0.0.

    Args:
        notes: The show's notes document.
        show: The show the notes belong to; anchors are checked against it.
        text: Note body; must not be blank.
        anchor: What the note is attached to; its set, transition or spots must exist.
        note_id: New note ID, unique ignoring case.
        at: When the note was written.
        author: User name, or "agent".
        kind: goal, idea, todo or warning.

    Returns:
        notes: The notes document with the note appended.

    >>> import warnings
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> show, at = two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)
    >>> empty = NotesDocument(show=show.id)
    >>> with warnings.catch_warnings(action="ignore"):
    ...     notes = add_note(empty, show, "go big", Anchor.on_set("2"), "n1", at, "pat")
    >>> notes.notes[0].text
    'go big'
    """
    return _add_note(notes, show, text, anchor, note_id, at, author, kind)


def _edit_note(
    notes: NotesDocument, show: "Show", note_id: str, /, **changes: object
) -> NotesDocument:
    """Changes a note's fields; the ID is fixed."""
    require_notes_of(notes, show.id)
    index = _note_index(notes, note_id)
    if "id" in changes:
        raise SchemaError(
            "a note id cannot change",
            hint="remove the note and add a new one",
            context={"note": note_id},
        )
    edited = _build_note({**dict(notes.notes[index]), **changes})
    _check_anchor(show, edited.anchor)
    return notes.replace(notes=(*notes.notes[:index], edited, *notes.notes[index + 1 :]))


@leaving_call(since=SINCE, remove=REMOVE)
def edit_note(
    notes: NotesDocument, show: "Show", note_id: str, /, **changes: object
) -> NotesDocument:
    """Changes a note's fields; the ID is fixed, and a new anchor is checked against the show.

    Deprecated: this name leaves the library in 1.0.0.

    Args:
        notes: The show's notes document.
        show: The show the notes belong to.
        note_id: ID of the note to change.
        changes: New values of Note fields other than `id`.

    Returns:
        notes: The notes document with the note changed in place.

    >>> import warnings
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> show, at = two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)
    >>> empty = NotesDocument(show=show.id)
    >>> with warnings.catch_warnings(action="ignore"):
    ...     notes = add_note(empty, show, "go", Anchor.on_set("2"), "n1", at, "pat")
    ...     edited = edit_note(notes, show, "n1", kind="todo")
    >>> edited.notes[0].kind
    'todo'
    """
    return _edit_note(notes, show, note_id, **changes)


def _remove_note(notes: NotesDocument, note_id: str) -> NotesDocument:
    """Removes a note."""
    index = _note_index(notes, note_id)
    return notes.replace(notes=(*notes.notes[:index], *notes.notes[index + 1 :]))


@leaving_call(since=SINCE, remove=REMOVE)
def remove_note(notes: NotesDocument, note_id: str) -> NotesDocument:
    """Removes a note from the notes document.

    Deprecated: this name leaves the library in 1.0.0.

    Args:
        notes: The show's notes document.
        note_id: ID of the note to remove; an unknown ID raises UnknownRefError.

    Returns:
        notes: The notes document without the note.

    >>> import warnings
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> show, at = two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)
    >>> empty = NotesDocument(show=show.id)
    >>> with warnings.catch_warnings(action="ignore"):
    ...     notes = add_note(empty, show, "go", Anchor.on_set("2"), "n1", at, "pat")
    ...     removed = remove_note(notes, "n1")
    >>> removed.notes
    ()
    """
    return _remove_note(notes, note_id)


def _notes_for(
    notes: NotesDocument,
    show: "Show",
    target: str | tuple[str, str],
    *,
    include_hidden: bool = False,
) -> list[Note]:
    """Returns the notes anchored to a set (an ID) or a transition (a pair), in document order."""
    require_notes_of(notes, show.id)
    if isinstance(target, str):
        show.set_index(target)
        anchor = Anchor.on_set(target)
    else:
        show.transition(target)
        anchor = Anchor.on_transition(target)
    return [n for n in notes.notes if n.anchor == anchor and (n.visible or include_hidden)]


@leaving_call(since=SINCE, remove=REMOVE)
def notes_for(
    notes: NotesDocument,
    show: "Show",
    target: str | tuple[str, str],
    *,
    include_hidden: bool = False,
) -> list[Note]:
    """Returns the notes anchored to a set (an ID) or a transition (a pair), in document order.

    Deprecated: this name leaves the library in 1.0.0.

    Args:
        notes: The show's notes document.
        show: The show the notes belong to; the set or transition must exist in it.
        target: A set ID or a pair of set IDs.
        include_hidden: Also returns hidden notes, such as unaccepted drafts.

    Returns:
        found: The matching notes.

    >>> import warnings
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> show, at = two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)
    >>> empty = NotesDocument(show=show.id)
    >>> with warnings.catch_warnings(action="ignore"):
    ...     notes = add_note(empty, show, "go", Anchor.on_set("2"), "n1", at, "pat")
    ...     found = notes_for(notes, show, "2")
    >>> [n.id for n in found]
    ['n1']
    """
    return _notes_for(notes, show, target, include_hidden=include_hidden)


def _spot_points(show: "Show", note: Note, set_id: str | None) -> list[Point]:
    sets = show.sets if set_id is None else (show.set(set_id),)
    spots = set(note.anchor.spots or ())
    return [p.point for s in sets for p in s.placements if p.on_field and p.entity in spots]


def _is_near(show: "Show", note: Note, p: Point, radius: Meters, set_id: str | None) -> bool:
    anchor = note.anchor
    if anchor.point is not None:
        return math.dist(anchor.point, p) <= radius
    if anchor.area is not None:
        return bool(anchor.area.to_shapely().distance(shapely.Point(p)) <= radius)
    if anchor.spots is not None:
        return any(math.dist(q, p) <= radius for q in _spot_points(show, note, set_id))
    return False


def _notes_near(
    notes: NotesDocument,
    show: "Show",
    p: Point,
    radius: Meters,
    set_id: str | None = None,
    *,
    include_hidden: bool = False,
) -> list[Note]:
    """Returns notes anchored within `radius` of a point, in document order."""
    require_notes_of(notes, show.id)
    if radius < 0:
        raise GeometryError(
            "radius must not be negative", hint="pass radius >= 0", context={"radius": radius}
        )
    if set_id is not None:
        show.set_index(set_id)
    return [
        n
        for n in notes.notes
        if (n.visible or include_hidden) and _is_near(show, n, p, radius, set_id)
    ]


@leaving_call(since=SINCE, remove=REMOVE)
def notes_near(
    notes: NotesDocument,
    show: "Show",
    p: Point,
    radius: Meters,
    set_id: str | None = None,
    *,
    include_hidden: bool = False,
) -> list[Note]:
    """Returns the notes anchored within `radius` of a point, in document order.

    Point anchors match by distance, area anchors by distance to the area, and spot anchors
    when any anchored spot's on-field placement in any set (or only in `set_id`) is close.
    Deprecated: this name leaves the library in 1.0.0.

    Args:
        notes: The show's notes document.
        show: The show the notes belong to; spot positions come from it.
        p: Point on the sheet.
        radius: Distance in meters; a negative one raises GeometryError.
        set_id: Set whose placements spot anchors are measured in; None means every set.
        include_hidden: Also returns hidden notes.

    Returns:
        found: The matching notes.

    >>> import warnings
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> show, at = two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)
    >>> empty = NotesDocument(show=show.id)
    >>> with warnings.catch_warnings(action="ignore"):
    ...     notes = add_note(empty, show, "lead", Anchor.on_spots(["T1"]), "n1", at, "pat")
    ...     found = notes_near(notes, show, (0.0, 5.5), 1.0)
    >>> [n.id for n in found]
    ['n1']
    """
    return _notes_near(notes, show, p, radius, set_id, include_hidden=include_hidden)


def _accept_draft(notes: NotesDocument, show: "Show", draft: Note) -> NotesDocument:
    """Adds a drafted note to the notes document and makes it visible."""
    require_notes_of(notes, show.id)
    accepted = _build_note({**dict(draft), "visible": True})
    held = next((n for n in notes.notes if n.id == accepted.id and not n.visible), None)
    if held is None:
        return _insert(notes, show, accepted)
    _check_anchor(show, accepted.anchor)
    return notes.replace(notes=tuple(accepted if n is held else n for n in notes.notes))


@leaving_call(since=SINCE, remove=REMOVE)
def accept_draft(notes: NotesDocument, show: "Show", draft: Note) -> NotesDocument:
    """Adds a drafted note to the notes document, visible.

    A hidden note with the draft's ID already in the document is replaced in place; the text
    must not be blank. Deprecated: this name leaves the library in 1.0.0.

    Args:
        notes: The show's notes document.
        show: The show the notes belong to; the anchor is checked against it.
        draft: The drafted note.

    Returns:
        notes: The notes document holding the draft, visible.

    >>> import warnings
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> show, at = two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)
    >>> empty = NotesDocument(show=show.id)
    >>> draft = Note(id="draft_1", text="go", anchor=Anchor.on_set("1"), author="agent",
    ...              created_at=at, visible=False)
    >>> with warnings.catch_warnings(action="ignore"):
    ...     accepted = accept_draft(empty, show, draft)
    >>> accepted.notes[0].visible
    True
    """
    return _accept_draft(notes, show, draft)


def _names_a_set(anchor: Anchor, sets: frozenset[str]) -> bool:
    """Tells whether an anchor names one of `sets`, alone or as an end of its transition."""
    if anchor.set_id is not None:
        return anchor.set_id in sets
    return anchor.tid is not None and bool(set(anchor.tid) & sets)


def _rewrite_note_anchors(
    notes: NotesDocument,
    show: "Show",
    *,
    sets: SetMapping | None = None,
    spots: SpotMapping | None = None,
) -> tuple[NotesDocument, tuple[Note, ...]]:
    """Points note anchors at renamed sets and spots; drops notes on removed sets or spots."""
    require_notes_of(notes, show.id)
    set_map: dict[str, str | None] = {} if sets is None else {str(k): v for k, v in sets.items()}
    removed = frozenset(old for old, new in set_map.items() if new is None)
    renames = {old: new for old, new in set_map.items() if new is not None}
    on_removed = tuple(n for n in notes.notes if _names_a_set(n.anchor, removed))
    kept = [
        n.replace(anchor=renamed_anchor(n.anchor, renames, {}))
        for n in notes.notes
        if n not in on_removed
    ]
    spot_map: dict[str, str | None] = {} if spots is None else dict(spots.items())
    moved, on_dropped = rewrite_note_spots(kept, lambda spot_id: spot_map.get(spot_id, spot_id))
    orphan_ids = {n.id for n in (*on_removed, *on_dropped)}
    orphaned = tuple(n for n in notes.notes if n.id in orphan_ids)
    return notes.replace(notes=moved), orphaned


@leaving_call(since=SINCE, remove=REMOVE)
def rewrite_note_anchors(
    notes: NotesDocument,
    show: "Show",
    *,
    sets: SetMapping | None = None,
    spots: SpotMapping | None = None,
) -> tuple[NotesDocument, tuple[Note, ...]]:
    """Points note anchors at renamed sets and spots, dropping notes on removed ones.

    The show's own edits (renumber, remove_set, split_section, join_sections, delete_spots)
    leave the notes document alone; call this with their mapping to keep it in step.
    Deprecated: this name leaves the library in 1.0.0.

    Args:
        notes: The show's notes document.
        show: The show after the edit; the notes document must belong to it.
        sets: Old set ID to new set ID, or to None for a removed set; a note on a removed set
            or on a transition into or out of one is dropped.
        spots: Old spot ID to new spot ID, or to None for a deleted spot; a note whose every
            spot was deleted is dropped.

    Returns:
        notes: The notes document with anchors rewritten.
        orphaned: Each dropped note, in document order, as `RewriteReport.orphaned_notes` holds.

    >>> import warnings
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> show, at = two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)
    >>> empty = NotesDocument(show=show.id)
    >>> with warnings.catch_warnings(action="ignore"):
    ...     notes = add_note(empty, show, "go", Anchor.on_set("2"), "n1", at, "pat")
    ...     followed, orphaned = rewrite_note_anchors(notes, show, sets={"2": None})
    >>> followed.notes, [n.id for n in orphaned]
    ((), ['n1'])
    """
    return _rewrite_note_anchors(notes, show, sets=sets, spots=spots)
