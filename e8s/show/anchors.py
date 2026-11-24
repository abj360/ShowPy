"""
anchors.py --- the tagged Anchor that points a comment, a highlight or a note at the show

The class is public as `e8s.show.anchors.Anchor`; it lives in this module so the note model,
which the cast's rewrite report holds, can use it without importing the show model.

Contains:
    Anchor: tags what a comment, highlight or note is attached to, with exactly one payload
    Anchor.on_set(): anchors to a set
    Anchor.on_transition(): anchors to a transition
    Anchor.at_point(): anchors to a point on the sheet
    Anchor.on_area(): anchors to an area
    Anchor.on_spots(): anchors to spots
    renamed_anchor(): returns an anchor whose set, transition or spots follow renamed IDs
"""

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import model_validator

from e8s.core.errors import SchemaError
from e8s.core.ids import SetId, SpotId, TransitionId
from e8s.core.types import FrozenModel, Point, Region

PAYLOAD_BY_KIND = {
    "set": "set_id",
    "transition": "tid",
    "point": "point",
    "area": "area",
    "spots": "spots",
}

type AnchorKind = Literal["set", "transition", "point", "area", "spots"]


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
        return cls._validated({"kind": "set", "set_id": set_id})

    @classmethod
    def on_transition(cls, tid: tuple[str, str]) -> "Anchor":
        """Anchors to a transition."""
        return cls._validated({"kind": "transition", "tid": tid})

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
        return cls._validated({"kind": "spots", "spots": tuple(spots)})


def renamed_anchor(anchor: Anchor, sets: Mapping[str, str], spots: Mapping[str, str]) -> Anchor:
    """Returns an anchor whose set, transition or spots follow renamed IDs.

    Args:
        anchor: The anchor to rename.
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
