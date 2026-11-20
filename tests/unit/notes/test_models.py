#!/usr/bin/env python3

"""
test_models.py --- tests the tagged Anchor and the Note model
"""

from datetime import UTC, datetime

import pytest

from showpy.core.errors import SchemaError
from showpy.core.ids import SetId
from showpy.notes.models import Anchor, Note

AT = datetime(2026, 11, 11, tzinfo=UTC)


def test_anchor_constructors_set_one_payload() -> None:
    assert Anchor.on_set("12").set_id == "12"
    assert Anchor.on_transition(("12", "13")).tid == ("12", "13")
    assert Anchor.at_point((1.0, 2.0)).point == (1.0, 2.0)
    assert Anchor.on_spots(["T1", "T2"]).spots == ("T1", "T2")


def test_anchor_rejects_missing_or_extra_payload() -> None:
    with pytest.raises(SchemaError):
        Anchor(kind="set")
    with pytest.raises(SchemaError):
        Anchor(kind="set", set_id=SetId("1"), point=(0.0, 0.0))


def test_note_round_trips_json() -> None:
    note = Note(
        id="n1", text="hit", kind="goal", anchor=Anchor.on_set("1"), author="me", created_at=AT
    )
    assert Note.model_validate_json(note.model_dump_json()) == note
