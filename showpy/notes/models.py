"""
models.py --- the Note data class; serves Anchor from its old path with a warning

Contains:
    Note: holds one design note
    __getattr__(): returns Anchor from showpy.show.models and warns that it moved
"""

from typing import Literal

from pydantic import AwareDatetime

from showpy.core.deprecation import moved
from showpy.core.types import FrozenModel
from showpy.show import anchors

type NoteKind = Literal["goal", "idea", "todo", "warning"]


class Note(FrozenModel):
    """Holds one design note.

    Attributes:
        id: Note ID.
        text: Note body.
        kind: goal, idea, todo or warning.
        anchor: What the note is attached to.
        author: User name, or "agent".
        created_at: When the note was written, with a time zone; passed in, never read from
            the clock.
        visible: Whether the canvas shows it.
    """

    id: str
    text: str
    kind: NoteKind = "idea"
    anchor: anchors.Anchor
    author: str
    created_at: AwareDatetime
    visible: bool = True


__getattr__ = moved(
    __name__, {"Anchor": "showpy.show.models.Anchor"}, since="0.4.0", remove="0.6.0"
)
