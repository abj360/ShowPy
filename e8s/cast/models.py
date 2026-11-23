"""
models.py --- cast data classes: groups, spot footprints, spots, label rules and the cast

Contains:
    Shape: describes a spot's footprint, a circle or a facing-aligned rectangle
    Group: holds a designer-made set of spots with shared defaults
    Spot: describes one dot that persists through the whole show
    LabelScheme: holds the rules that turn group prefixes and numbers into labels
    Cast: holds every group and spot in the show
    Cast.spot(): returns one spot by ID
    RewriteReport: lists what a spot rename or delete dropped or left unresolved
    HexColor: a `#rrggbb` color string, in either case
    MAX_LABEL_PAD: bounds how wide a label number may be zero-padded
    spot_cap_error(): builds the error for a cast over MAX_SPOTS
    require_spot_count(): refuses a cast size over MAX_SPOTS before anything is allocated
"""

import math
from collections.abc import Mapping, Sized
from functools import cached_property
from typing import Annotated, Literal, get_args

from pydantic import Field, field_validator, model_validator

from e8s.choreo.path_models import FacingOverride
from e8s.core.caps import MAX_SPOTS
from e8s.core.deprecation import leave_field
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import GeometryError, SchemaError, UnknownRefError
from e8s.core.ids import RouteId, SelectionNameField, SpotId, SpotIdField, TransitionId
from e8s.core.readonly import ReadOnly, ReadOnlyMap
from e8s.core.types import Degrees, FrozenModel, Meters, PositiveFinite
from e8s.core.util import close_matches, natural_sort
from e8s.show._notes_document import Note, NotesDocument

DEFAULT_RADIUS_M = 0.3
DEFAULT_SYMBOL = "circle"
DEFAULT_COLOR = "#808080"
MAX_LABEL_PAD = 8
HEX_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"

type Claimable = Literal["performer", "crew", "none"]
type HexColor = Annotated[str, Field(pattern=HEX_COLOR_PATTERN)]
type SelectionKey = Annotated[str, *get_args(SelectionNameField)[1:]]
type SelectionSpots = tuple[SpotIdField, ...]


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
        if min(self.radius_m, self.width_m, self.depth_m) < 0:
            raise GeometryError(
                "footprint sizes cannot be negative",
                hint="give radius_m, width_m and depth_m of 0 or more",
                context={"kind": self.kind},
            )
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
        color: Hex color, `#rrggbb`.
        default_radius: Footprint radius for new spots, above 0.
        limits: Per-group limit overrides read by a consumer checker.
        moves: False for stationary groups such as a pit.
    """

    key: str
    name: str = ""
    label_prefix: str
    symbol: str = DEFAULT_SYMBOL
    color: HexColor = DEFAULT_COLOR
    default_radius: PositiveFinite = DEFAULT_RADIUS_M
    limits: Annotated[Mapping[str, PositiveFinite], ReadOnly()] = Field(
        default={}, validate_default=True
    )
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
        color: Hex color, `#rrggbb`; None inherits the group's.
        tags: Free tags such as `soloist`, deduplicated in first-seen order; a set is sorted.
        active: False excludes the spot from forms and checks.
        shape: Footprint on the sheet and for collisions.
        claimable: Who may claim the spot once the show is published.
    """

    id: SpotIdField
    group: str
    symbol: str | None = None
    color: HexColor | None = None
    tags: tuple[str, ...] = ()
    active: bool = True
    shape: Shape = Shape()
    claimable: Claimable = "performer"

    _ordered_tags = field_validator("tags", mode="before")(_sort_unordered)
    _unique_tags = field_validator("tags", mode="after")(_dedupe_in_order)


leave_field(Spot, "claimable", since="0.4.0", remove="0.7.0")


class LabelScheme(FrozenModel):
    """Holds labeling rules; a group missing from `prefixes` uses its own label_prefix.

    Attributes:
        prefixes: Group key to label prefix overrides; every key names a group of the cast.
        start: First number in each group.
        pad: Zero-pad numbers to this width, 0 to `MAX_LABEL_PAD`.
    """

    prefixes: Annotated[Mapping[str, str], ReadOnly()] = Field(default={}, validate_default=True)
    start: int = 1
    pad: Annotated[int, Field(ge=0, le=MAX_LABEL_PAD)] = 0

    def prefix_for(self, group: Group) -> str:
        """Returns the prefix for a group, preferring the scheme's override."""
        return self.prefixes.get(group.key, group.label_prefix)

    def label(self, prefix: str, number: int) -> str:
        """Returns the label for a number, zero-padded to `pad` digits."""
        return f"{prefix}{number:0{self.pad}d}"


def spot_cap_error(count: int, *, at_least: bool = False) -> SchemaError:
    """Builds the error for a cast over MAX_SPOTS, so every refusal of the cap reads the same.

    Args:
        count: Number of spots the cast would hold.
        at_least: Whether `count` is a lower bound, when the edit stopped before counting all.

    >>> spot_cap_error(5001, at_least=True).message
    'a cast of at least 5001 spots is over MAX_SPOTS (5,000)'
    """
    bound = "at least " if at_least else ""
    return SchemaError(
        f"a cast of {bound}{count} spots is over MAX_SPOTS ({MAX_SPOTS:,})",
        hint=f"keep at most {MAX_SPOTS:,} spots in one show; split a larger ensemble into"
        " several shows",
        context={"spots": count, "max_spots": MAX_SPOTS},
        code=ErrorCode.TOO_MANY_SPOTS,
    )


def require_spot_count(count: int) -> None:
    """Refuses a cast of more than MAX_SPOTS spots, before the caller allocates anything.

    Args:
        count: Number of spots the cast holds, or would hold after an edit.

    >>> require_spot_count(MAX_SPOTS)
    >>> require_spot_count(MAX_SPOTS + 1)
    Traceback (most recent call last):
        ...
    e8s.core.errors.SchemaError: TOO_MANY_SPOTS: a cast of 5001 spots is over MAX_SPOTS \
(5,000) (hint: keep at most 5,000 spots in one show; split a larger ensemble into several shows)
    """
    if count <= MAX_SPOTS:
        return
    raise spot_cap_error(count)


class Cast(FrozenModel):
    """Holds every group and spot in the show.

    Attributes:
        groups: Group key to group; each key equals its group's `key`.
        spots: Every spot, in creation order; at most MAX_SPOTS.
        selections: Named selections of spot IDs of this cast, deduplicated in first-seen order.
        label_scheme: Labeling rules; its prefixes name groups of this cast.
    """

    groups: Annotated[Mapping[str, Group], ReadOnly()] = Field(default={}, validate_default=True)
    spots: tuple[Spot, ...] = Field(default=(), json_schema_extra={"maxItems": MAX_SPOTS})
    selections: Annotated[Mapping[SelectionKey, SelectionSpots], ReadOnly()] = Field(
        default={}, validate_default=True
    )
    label_scheme: LabelScheme = LabelScheme()

    @field_validator("spots", mode="before")
    @classmethod
    def _spot_count_within_cap(cls, value: object) -> object:
        if isinstance(value, Sized):
            require_spot_count(len(value))
        return value

    @field_validator("spots", mode="after")
    @classmethod
    def _spots_within_cap(cls, value: tuple[Spot, ...]) -> tuple[Spot, ...]:
        require_spot_count(len(value))
        return value

    @field_validator("selections", mode="before")
    @classmethod
    def _order_selections(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {name: _sort_unordered(ids) for name, ids in value.items()}

    @field_validator("selections", mode="after")
    @classmethod
    def _dedupe_selections(
        cls, value: Mapping[str, SelectionSpots]
    ) -> Mapping[str, SelectionSpots]:
        return ReadOnlyMap({name: _dedupe_in_order(ids) for name, ids in value.items()})

    @model_validator(mode="after")
    def _consistent(self) -> "Cast":
        self._check_groups()
        self._check_spots()
        self._check_selections()
        self._check_prefixes()
        return self

    def _check_groups(self) -> None:
        for key, group in self.groups.items():
            if group.key != key:
                raise SchemaError(
                    f"group stored under {key!r} has key {group.key!r}",
                    hint="store each group under its own key",
                    context={"stored_key": key, "group_key": group.key},
                )

    def _check_spots(self) -> None:
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

    def _check_selections(self) -> None:
        known = set(self.ids())
        for name, ids in self.selections.items():
            missing = [spot_id for spot_id in ids if spot_id not in known]
            if missing:
                raise UnknownRefError(
                    f"selection {name!r} names spots the cast lacks: {', '.join(missing)}",
                    hint="name only spots of this cast, or delete the selection",
                    context={"selection": name, "missing": missing},
                )

    def _check_prefixes(self) -> None:
        unknown = sorted(set(self.label_scheme.prefixes) - set(self.groups))
        if unknown:
            raise UnknownRefError(
                f"label prefixes name unknown groups: {', '.join(unknown)}",
                hint=f"give prefixes only to groups of this cast; known: {sorted(self.groups)}",
                context={"groups": unknown},
            )

    @cached_property
    def _spot_index(self) -> Mapping[str, int]:
        return ReadOnlyMap({spot.id: position for position, spot in enumerate(self.spots)})

    def spot(self, spot_id: str) -> Spot:
        """Returns one spot, raising UnknownRefError with close matches in the hint."""
        position = self._spot_index.get(spot_id)
        if position is not None:
            return self.spots[position]
        close = close_matches(spot_id, self.ids())
        raise UnknownRefError(
            f"unknown spot {spot_id!r}",
            hint=f"did you mean {', '.join(close)}?" if close else "check the spot id",
            context={"spot": spot_id},
        )

    def ids(self) -> tuple[SpotId, ...]:
        """Returns every spot ID in creation order."""
        return tuple(spot.id for spot in self.spots)


class RewriteReport(FrozenModel):
    """Lists what rewriting spot IDs renamed, dropped or could not resolve.

    Attributes:
        renamed: Old ID to new ID for every spot whose ID changed.
        dropped_routes: Transition and route ID of each route left without members.
        dropped_facings: Each facing override removed because no spot was left on it or its
            target spot was dropped.
        orphaned_notes: Each note removed from the notes document passed in, because every spot
            it was anchored to was dropped.
        unresolved_selectors: Stored selectors that no longer resolve and were left as written.
        notes: The notes document passed in, with its spot anchors rewritten; None without one.
            It leaves the library in 1.0.0 with the notes, and an outside read warns.
    """

    renamed: Annotated[Mapping[SpotId, SpotId], ReadOnly()] = Field(
        default={}, validate_default=True
    )
    dropped_routes: tuple[tuple[TransitionId, RouteId], ...] = ()
    dropped_facings: tuple[FacingOverride, ...] = ()
    orphaned_notes: tuple[Note, ...] = ()
    notes: NotesDocument | None = None
    unresolved_selectors: tuple[str, ...] = ()


leave_field(RewriteReport, "notes", since="0.4.0", remove="1.0.0")
