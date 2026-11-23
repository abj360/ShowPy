"""
v1_to_v2_notes.py --- the notes sub-step of the v1 to v2 format migration

The tenth sub-step; it runs after ids. Format v2 keeps no notes in the show: they move from the
v1 document `/show/notes.json` to a notes document of their own at `/notes/notes.json`, with its
own header (`{"format": "e8.notes", "version": 1, "show": ..., "notes": [...]}`), written in the
same revision as the v2 show documents. Each note ID follows the rename rule of the ids step for
kind `note`, planned over the stored ID strings before any note is validated, so an empty or
otherwise broken v1 note ID is repaired, never refused; each anchor is pointed at the set and
spot IDs the ids step renamed, so anchors name IDs that exist. A v1 tree without notes gets no
notes document.

Contains:
    LEGACY_NOTES_DOC: names the v1 document that held the notes among the show's documents
    NOTES_DOC: names the notes document stored beside the show's documents
    move_notes(): takes the v1 notes out of the show's documents into the notes document
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from e8s.core.errors import SchemaError
from e8s.core.json_types import JsonValue
from e8s.show._notes_document import Note, NotesDocument
from e8s.show.anchors import renamed_anchor
from e8s.show.migrations.v1_to_v2_ids import renames_of
from e8s.show.models import Show

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder

LEGACY_NOTES_DOC: Final = "/show/notes.json"
NOTES_DOC: Final = "/notes/notes.json"
NOTE_KIND: Final = "note"
SET_KIND: Final = "set"
SPOT_KIND: Final = "spot"


def _stored_notes(objects: Mapping[str, JsonValue]) -> list[JsonValue]:
    """Reads the v1 notes array as parsed JSON, refusing a document that is not an array."""
    stored = objects.get(LEGACY_NOTES_DOC, [])
    if not isinstance(stored, list):
        raise SchemaError(
            f"{LEGACY_NOTES_DOC} must hold a JSON array, not {type(stored).__name__}",
            hint="write a JSON array of notes, as showpy 0.3 stored them",
            context={"path": LEGACY_NOTES_DOC},
        )
    return stored


def _stored_id(item: JsonValue) -> str | None:
    """Returns a stored note's ID text, or None when it holds none, for validation to refuse."""
    note_id = item.get("id") if isinstance(item, dict) else None
    return note_id if isinstance(note_id, str) else None


def move_notes(
    show: Show, objects: Mapping[str, JsonValue], report: "ReportBuilder"
) -> dict[str, JsonValue]:
    """Takes the v1 notes out of the show's documents into the notes document beside the show.

    Args:
        show: The migrated show, whose ID the notes document names.
        objects: The documents after the JSON sub-steps; `/show/notes.json` holds the notes.
        report: Holds the set and spot renames of the ids step; collects the note renames.

    Returns:
        beside: `{"/notes/notes.json": <notes document>}`, or empty when there are no notes.
            A note that does not fit raises SchemaError.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.migrate import ReportBuilder
    >>> note = {"id": "n-1", "text": "go", "anchor": {"kind": "set", "set_id": "1"},
    ...         "author": "pat", "created_at": "2026-11-11T09:00:00+00:00"}
    >>> moved = move_notes(two_set_show(), {LEGACY_NOTES_DOC: [note]}, ReportBuilder(1))
    >>> [n["id"] for n in moved[NOTES_DOC]["notes"]]
    ['n_1']
    """
    stored = _stored_notes(objects)
    if not stored:
        return {}
    ids = renames_of([i for i in map(_stored_id, stored) if i is not None], NOTE_KIND)
    for old, new in ids.items():
        report.rename(NOTE_KIND, old, new)
    renamed_ids = [
        {**item, "id": ids[note_id]} if isinstance(item, dict) and note_id in ids else item
        for item, note_id in zip(stored, map(_stored_id, stored), strict=True)
    ]
    notes = [Note.model_validate(item) for item in renamed_ids]
    sets, spots = report.renames(SET_KIND), report.renames(SPOT_KIND)
    moved = [
        {
            **note.model_dump(mode="json"),
            "anchor": renamed_anchor(note.anchor, sets, spots).model_dump(mode="json"),
        }
        for note in notes
    ]
    document = NotesDocument.model_validate({"show": show.id, "notes": moved})
    return {NOTES_DOC: document.model_dump(mode="json")}
