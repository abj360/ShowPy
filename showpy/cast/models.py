"""
models.py --- cast data classes: groups, spot footprints, spots, label rules and the cast

Contains:
    Shape: describes a spot's footprint, a circle or a facing-aligned rectangle
    Group: holds a designer-made set of spots with shared defaults
    Spot: describes one dot that persists through the whole show
    LabelScheme: holds the rules that turn group prefixes and numbers into labels
    Cast: holds every group and spot in the show
    Cast.spot(): returns one spot by ID
"""

import math
from typing import Literal

from pydantic import field_validator, model_validator

from showpy.core.errors import GeometryError, UnknownRefError
from showpy.core.ids import SpotId
from showpy.core.types import Degrees, FrozenModel, Meters
from showpy.core.util import close_matches, natural_sort

DEFAULT_RADIUS_M = 0.3
DEFAULT_SYMBOL = "circle"
DEFAULT_COLOR = "#808080"

type Claimable = Literal["performer", "crew", "none"]


def _sort_unordered(value: object) -> object:
    if isinstance(value, set | frozenset) and all(isinstance(item, str) for item in value):
        return natural_sort(value)
    return value


def _dedupe_in_order[T: str](items: tuple[T, ...]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(items))


class Shape(FrozenModel):
    """Describes a spot's footprint for display and collisions.

    Attributes:
        kind: `circle` or `rectangle`.
        radius_m: Circle radius.
        width_m: Rectangle size across the facing direction.
        depth_m: Rectangle size along the facing direction.
        rotation_deg: Extra rotation added to the spot's facing for rectangles.
    """

    kind: Literal["circle", "rectangle"] = "circle"
    radius_m: Meters = DEFAULT_RADIUS_M
    width_m: Meters = 2 * DEFAULT_RADIUS_M
    depth_m: Meters = 2 * DEFAULT_RADIUS_M
    rotation_deg: Degrees = 0.0

    @model_validator(mode="after")
    def _positive_size(self) -> "Shape":
        sizes = (self.radius_m,) if self.kind == "circle" else (self.width_m, self.depth_m)
        if not all(size > 0 for size in sizes):
            raise GeometryError(
                f"{self.kind} footprint needs positive size",
                hint="give radius_m (circle) or width_m and depth_m (rectangle) above 0",
                context={"kind": self.kind},
            )
        return self

    @property
    def extent_m(self) -> Meters:
        """Returns the radius of the smallest circle that holds the footprint."""
        if self.kind == "circle":
            return self.radius_m
        return math.hypot(self.width_m, self.depth_m) / 2


class Group(FrozenModel):
    """Holds a designer-made set of spots with shared defaults.

    Attributes:
        key: Stable group key, e.g. `trumpets`.
        name: Display name; defaults to the key in title case.
        label_prefix: Prefix for spot labels, e.g. `T` gives T1, T2.
        symbol: Sheet glyph.
        color: Hex color.
        default_radius: Footprint radius for new spots.
        limits: Per-group limit overrides read by a consumer checker.
        moves: False for stationary groups such as a pit.
    """

    key: str
    name: str = ""
    label_prefix: str
    symbol: str = DEFAULT_SYMBOL
    color: str = DEFAULT_COLOR
    default_radius: Meters = DEFAULT_RADIUS_M
    limits: dict[str, float] = {}
    moves: bool = True

    @model_validator(mode="before")
    @classmethod
    def _default_name(cls, values: object) -> object:
        if isinstance(values, dict) and not values.get("name") and "key" in values:
            words = str(values["key"]).replace("-", " ").replace("_", " ")
            values = {**values, "name": words.title()}
        return values


class Spot(FrozenModel):
    """Describes one dot that persists through the whole show.

    Attributes:
        id: Label shown on the sheet, stable all show.
        group: Key of the owning group.
        symbol: Sheet glyph; None inherits the group's.
        color: Hex color; None inherits the group's.
        tags: Free tags such as `soloist`, deduplicated in first-seen order; a set is sorted.
        active: False excludes the spot from forms and checks.
        shape: Footprint on the sheet and for collisions.
        claimable: Who may claim the spot once the show is published.
    """

    id: SpotId
    group: str
    symbol: str | None = None
    color: str | None = None
    tags: tuple[str, ...] = ()
    active: bool = True
    shape: Shape = Shape()
    claimable: Claimable = "performer"

    _ordered_tags = field_validator("tags", mode="before")(_sort_unordered)
    _unique_tags = field_validator("tags", mode="after")(_dedupe_in_order)


class LabelScheme(FrozenModel):
    """Holds labeling rules; a group missing from `prefixes` uses its own label_prefix.

    Attributes:
        prefixes: Group key to label prefix overrides.
        start: First number in each group.
        pad: Zero-pad numbers to this width.
    """

    prefixes: dict[str, str] = {}
    start: int = 1
    pad: int = 0

    def prefix_for(self, group: Group) -> str:
        """Returns the prefix for a group, preferring the scheme's override."""
        return self.prefixes.get(group.key, group.label_prefix)

    def label(self, prefix: str, number: int) -> str:
        """Returns the label for a number, zero-padded to `pad` digits."""
        return f"{prefix}{number:0{self.pad}d}"


class Cast(FrozenModel):
    """Holds every group and spot in the show.

    Attributes:
        groups: Group key to group.
        spots: Every spot, in creation order.
        selections: Named selections of spot IDs, deduplicated in first-seen order.
        label_scheme: Labeling rules.
    """

    groups: dict[str, Group] = {}
    spots: tuple[Spot, ...] = ()
    selections: dict[str, tuple[SpotId, ...]] = {}
    label_scheme: LabelScheme = LabelScheme()

    @field_validator("selections", mode="before")
    @classmethod
    def _order_selections(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {name: _sort_unordered(ids) for name, ids in value.items()}

    @field_validator("selections", mode="after")
    @classmethod
    def _dedupe_selections(
        cls, value: dict[str, tuple[SpotId, ...]]
    ) -> dict[str, tuple[SpotId, ...]]:
        return {name: _dedupe_in_order(ids) for name, ids in value.items()}

    @model_validator(mode="after")
    def _consistent(self) -> "Cast":
        seen: set[str] = set()
        for spot in self.spots:
            if spot.id in seen:
                raise UnknownRefError(
                    f"duplicate spot id {spot.id}", hint="give every spot a unique id"
                )
            if spot.group not in self.groups:
                raise UnknownRefError(
                    f"spot {spot.id} names unknown group {spot.group!r}",
                    hint=f"add the group first; known: {sorted(self.groups)}",
                    context={"spot": spot.id, "group": spot.group},
                )
            seen.add(spot.id)
        return self

    def spot(self, spot_id: str) -> Spot:
        """Returns one spot, raising UnknownRefError with close matches in the hint."""
        for spot in self.spots:
            if spot.id == spot_id:
                return spot
        close = close_matches(spot_id, self.ids())
        raise UnknownRefError(
            f"unknown spot {spot_id!r}",
            hint=f"did you mean {', '.join(close)}?" if close else "check the spot id",
            context={"spot": spot_id},
        )

    def ids(self) -> tuple[SpotId, ...]:
        """Returns every spot ID in creation order."""
        return tuple(spot.id for spot in self.spots)
