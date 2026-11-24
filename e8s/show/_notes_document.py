"""
_notes_document.py --- the private note models that read and write the notes document

From format v2 a show holds no notes, and the library has no notes functions: the host keeps
notes. Migrating a v1 show writes its notes, as plain JSON, to a notes document of their own,
stored at `/notes/notes.json` beside the show's documents; these models shape and check that
document, so the notes of a v1 show are never lost.

Contains:
    NOTES_FORMAT: names the notes document format, `e8.notes`
    NOTES_VERSION: the notes document version this release writes
    Note: holds one design note
    NotesDocument: holds the notes of one show, with its own format header
    require_notes_of(): refuses a notes document that belongs to another show
"""

from collections import Counter
from typing import Final, Literal

from pydantic import AwareDatetime, model_validator

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, VersionError
from e8s.core.ids import NoteIdField
from e8s.core.types import FrozenModel
from e8s.show import anchors

NOTES_FORMAT: Final = "e8.notes"
NOTES_VERSION: Final = 1

type NoteKind = Literal["goal", "idea", "todo", "warning"]


class Note(FrozenModel):
    """Holds one design note.

    Attributes:
        id: Note ID; it passes the ID check of kind `note`.
        text: Note body.
        kind: goal, idea, todo or warning.
        anchor: What the note is attached to.
        author: User name, or "agent".
        created_at: When the note was written, with a time zone; passed in, never read from
            the clock.
        visible: Whether the canvas shows it.
    """

    id: NoteIdField
    text: str
    kind: NoteKind = "idea"
    anchor: anchors.Anchor
    author: str
    created_at: AwareDatetime
    visible: bool = True


class NotesDocument(FrozenModel):
    """Holds the notes of one show; its JSON is the notes document stored beside the show.

    Attributes:
        format: Names the document format, always `e8.notes`.
        version: Document version, always 1.
        show: ID of the show the notes belong to.
        notes: The notes in order; IDs are unique ignoring case.

    >>> NotesDocument(show="show-1").to_json()
    {'format': 'e8.notes', 'version': 1, 'show': 'show-1', 'notes': []}
    """

    format: Literal["e8.notes"] = NOTES_FORMAT
    version: Literal[1] = NOTES_VERSION
    show: str
    notes: tuple[Note, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _readable_header(cls, data: object) -> object:
        """Refuses another format, and a newer version with VersionError, before the fields."""
        if not isinstance(data, dict):
            return data
        stored_format = data.get("format", NOTES_FORMAT)
        if stored_format != NOTES_FORMAT:
            raise SchemaError(
                f"notes document names format {stored_format!r}, not {NOTES_FORMAT!r}",
                hint=f'write "format": "{NOTES_FORMAT}" and "version": {NOTES_VERSION}',
                context={"format": repr(stored_format)},
                code=ErrorCode.UNKNOWN_FORMAT,
            )
        version = data.get("version", NOTES_VERSION)
        if isinstance(version, int) and not isinstance(version, bool) and version > NOTES_VERSION:
            raise VersionError(
                f"notes document is version {version}; this release reads versions up to"
                f" {NOTES_VERSION}",
                hint=f"upgrade e8s to a release that reads notes version {version}",
                context={"version": version, "newest": NOTES_VERSION},
            )
        return data

    @model_validator(mode="after")
    def _unique_ids(self) -> "NotesDocument":
        counts = Counter(note.id.casefold() for note in self.notes)
        repeated = [note.id for note in self.notes if counts[note.id.casefold()] > 1]
        if repeated:
            raise SchemaError(
                f"note ids repeat ignoring case: {', '.join(repeated)}",
                hint="give every note an id unique ignoring case",
                context={"repeated": repeated},
            )
        return self


def require_notes_of(notes: NotesDocument, show_id: str) -> None:
    """Refuses a notes document that names another show than `show_id`, with SchemaError.

    >>> require_notes_of(NotesDocument(show="show-1"), "show-1")
    """
    if notes.show != show_id:
        raise SchemaError(
            f"the notes document belongs to show {notes.show!r}, not {show_id!r}",
            hint=f'pass the notes document of show {show_id!r}, whose "show" is {show_id!r}',
            context={"notes_show": notes.show, "show": show_id},
        )
