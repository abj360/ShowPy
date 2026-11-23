#!/usr/bin/env python3

"""
test_models.py --- tests the Note model
"""

from datetime import UTC, datetime

from showpy.core.ids import NoteId
from showpy.notes.models import Note
from showpy.show.models import Anchor

AT = datetime(2026, 11, 11, tzinfo=UTC)


def test_note_round_trips_json() -> None:
    note = Note(
        id=NoteId("n1"),
        text="hit",
        kind="goal",
        anchor=Anchor.on_set("1"),
        author="me",
        created_at=AT,
    )
    assert Note.model_validate_json(note.model_dump_json()) == note
