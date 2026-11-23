#!/usr/bin/env python3

"""
test_models.py --- tests the Note model
"""

import json
from datetime import UTC, datetime

from e8s.core.ids import NoteId
from e8s.notes.models import Note
from e8s.show.models import Anchor

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
    assert Note.from_json(json.loads(json.dumps(note.to_json()))) == note
