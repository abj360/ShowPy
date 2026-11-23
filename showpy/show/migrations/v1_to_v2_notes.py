"""
v1_to_v2_notes.py --- the notes sub-step of the v1 to v2 format migration

The tenth sub-step; it runs after ids. Format v2 keeps no notes in the show: they move from the
v1 document `/show/notes.json` to a notes document of their own at `/notes/notes.json`, with its
own header (`{"format": "e8.notes", "version": 1, "show": ..., "notes": [...]}`), written in the
same revision as the v2 show documents. Each note ID follows the rename rule of the ids step for
kind `note`, and each anchor is pointed at the set and spot IDs the ids step renamed, so anchors
name IDs that exist. A v1 tree without notes gets no notes document.

Contains:
    LEGACY_NOTES_DOC: names the v1 document that held the notes among the show's documents
    NOTES_DOC: names the notes document stored beside the show's documents
    renamed_anchor(): returns an anchor whose sets and spots follow a migration's renames
    move_notes(): takes the v1 notes out of the show's documents into the notes document
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from showpy.core.errors import SchemaError
from showpy.core.ids import LEGACY_IDS_KEY, SetId, SpotId, TransitionId
from showpy.core.json_types import JsonValue
from showpy.notes.models import Note, NotesDocument
from showpy.show.anchors import Anchor
from showpy.show.migrations.v1_to_v2_ids import renames_of
from showpy.show.models import Show

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder

LEGACY_NOTES_DOC: Final = "/show/notes.json"
NOTES_DOC: Final = "/notes/notes.json"
LEGACY_CONTEXT: Final = {LEGACY_IDS_KEY: True}
NOTE_KIND: Final = "note"
SET_KIND: Final = "set"
SPOT_KIND: Final = "spot"


def renamed_anchor(anchor: Anchor, sets: Mapping[str, str], spots: Mapping[str, str]) -> Anchor:
    """Returns an anchor whose set, transition or spots follow a migration's renames.

    Args:
        anchor: The stored anchor.
        sets: Old set ID to new set ID.
        spots: Old spot ID to new spot ID.

    Returns:
        anchor: The anchor naming the renamed IDs; point and area anchors come back unchanged.

    >>> renamed_anchor(Anchor.on_set("con"), {"con": "con_"}, {}).set_id
    'con_'
    """
    if anchor.set_id is not None:
        return anchor.replace(set_id=SetId(sets.get(anchor.set_id, anchor.set_id)))
    if anchor.tid is not None:
        a, b = anchor.tid
        return anchor.replace(tid=TransitionId(SetId(sets.get(a, a)), SetId(sets.get(b, b))))
    if anchor.spots is not None:
        return anchor.replace(spots=tuple(SpotId(spots.get(s, s)) for s in anchor.spots))
    return anchor


def _stored_notes(objects: Mapping[str, JsonValue]) -> list[Note]:
    """Reads the v1 notes array, accepting any non-empty v1 note ID."""
    stored = objects.get(LEGACY_NOTES_DOC, [])
    if not isinstance(stored, list):
        raise SchemaError(
            f"{LEGACY_NOTES_DOC} must hold a JSON array, not {type(stored).__name__}",
            hint="write a JSON array of notes, as showpy 0.3 stored them",
            context={"path": LEGACY_NOTES_DOC},
        )
    return [Note.model_validate(item, context=LEGACY_CONTEXT) for item in stored]


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

    >>> from showpy.examples import two_set_show
    >>> from showpy.show.migrate import ReportBuilder
    >>> note = {"id": "n-1", "text": "go", "anchor": {"kind": "set", "set_id": "1"},
    ...         "author": "pat", "created_at": "2026-11-11T09:00:00+00:00"}
    >>> moved = move_notes(two_set_show(), {LEGACY_NOTES_DOC: [note]}, ReportBuilder(1))
    >>> [n["id"] for n in moved[NOTES_DOC]["notes"]]
    ['n_1']
    """
    notes = _stored_notes(objects)
    if not notes:
        return {}
    ids = renames_of([note.id for note in notes], NOTE_KIND)
    for old, new in ids.items():
        report.rename(NOTE_KIND, old, new)
    sets, spots = report.renames(SET_KIND), report.renames(SPOT_KIND)
    moved = [
        {
            **note.model_dump(mode="json"),
            "id": ids.get(note.id, note.id),
            "anchor": renamed_anchor(note.anchor, sets, spots).model_dump(mode="json"),
        }
        for note in notes
    ]
    document = NotesDocument.model_validate({"show": show.id, "notes": moved})
    return {NOTES_DOC: document.model_dump(mode="json")}
