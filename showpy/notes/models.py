"""
models.py --- note data classes: the tagged Anchor and the Note

Contains:
    Anchor: tags what a note or comment is attached to, with exactly one payload
    Anchor.on_set(): anchors to a set
    Anchor.on_transition(): anchors to a transition
    Anchor.at_point(): anchors to a point on the sheet
    Anchor.on_area(): anchors to an area
    Anchor.on_spots(): anchors to spots
    Note: holds one design note
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import AwareDatetime, model_validator

from showpy.core.errors import SchemaError
from showpy.core.ids import SetId, SpotId, TransitionId
from showpy.core.types import FrozenModel, Point, Region

PAYLOAD_BY_KIND = {
    "set": "set_id",
    "transition": "tid",
    "point": "point",
    "area": "area",
    "spots": "spots",
}

type AnchorKind = Literal["set", "transition", "point", "area", "spots"]
type NoteKind = Literal["goal", "idea", "todo", "warning"]


class Anchor(FrozenModel):
    """Tags what a note or comment is attached to; exactly the field for `kind` is set.

    Attributes:
        kind: What the anchor points at.
        set_id: Set, for kind `set`.
        tid: Transition, for kind `transition`.
        point: Sheet point, for kind `point`.
        area: Sheet area, for kind `area`.
        spots: Spot IDs, for kind `spots`.
    """

    kind: AnchorKind
    set_id: SetId | None = None
    tid: TransitionId | None = None
    point: Point | None = None
    area: Region | None = None
    spots: tuple[SpotId, ...] | None = None

    @model_validator(mode="after")
    def _one_payload(self) -> "Anchor":
        filled = {name for name in PAYLOAD_BY_KIND.values() if getattr(self, name) is not None}
        if filled != {PAYLOAD_BY_KIND[self.kind]}:
            raise SchemaError(
                f"{self.kind} anchor needs only {PAYLOAD_BY_KIND[self.kind]}",
                hint="use the Anchor.on_* constructors",
                context={"filled": sorted(filled)},
            )
        return self

    @classmethod
    def on_set(cls, set_id: str) -> "Anchor":
        """Anchors to a set."""
        return cls.model_validate({"kind": "set", "set_id": set_id})

    @classmethod
    def on_transition(cls, tid: tuple[str, str]) -> "Anchor":
        """Anchors to a transition."""
        return cls.model_validate({"kind": "transition", "tid": tid})

    @classmethod
    def at_point(cls, point: Point) -> "Anchor":
        """Anchors to a point on the sheet."""
        return cls(kind="point", point=point)

    @classmethod
    def on_area(cls, area: Region) -> "Anchor":
        """Anchors to an area on the sheet."""
        return cls(kind="area", area=area)

    @classmethod
    def on_spots(cls, spots: Sequence[str]) -> "Anchor":
        """Anchors to spots, e.g. a sticky note on a soloist or a group."""
        return cls.model_validate({"kind": "spots", "spots": tuple(spots)})


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
    anchor: Anchor
    author: str
    created_at: AwareDatetime
    visible: bool = True
