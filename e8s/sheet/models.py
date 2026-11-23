"""
models.py --- sheet data classes: grid, guide lines, landmarks, zones, coordinates, the sheet

Contains:
    SurfaceType: names the kinds of surface a sheet represents
    ZoneKind: names the kinds of rule-bearing area
    CoordinateKind: names the coordinate wording families
    Grid: describes the background grid
    GuideLine: holds one line drawn on the sheet
    Landmark: holds a named reference point, line or area
    Landmark.point: returns the point of a point landmark
    Zone: holds an area with rules, optionally active only in count windows
    Zone.is_active(): reports whether the zone applies at a count
    CoordinateSystem: describes how positions are labeled; display only
    check_coordinate_system(): refuses a coordinate system whose parts have the wrong type or value
    normalized_name(): folds a landmark name to the form landmark lookup compares
    Sheet: holds the 2D design surface
"""

from collections import Counter
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, Field, ValidationInfo, model_validator

from e8s.core.errors import GeometryError, SchemaError, TimeRangeError
from e8s.core.ids import (
    LEGACY_IDS_KEY,
    LandmarkId,
    LandmarkIdField,
    SheetRef,
    ZoneIdField,
    check_sheet_id,
    parse_ref,
)
from e8s.core.readonly import ReadOnly, ReadOnlyMap
from e8s.core.selection import SelectorField
from e8s.core.types import (
    Count,
    Degrees,
    FrozenModel,
    Meters,
    Point,
    Region,
    Ring,
)
from e8s.core.validation import require_finite

MIN_POINTS_BY_KIND = {"point": 1, "line": 2, "area": 3}
MIN_OUTLINE_POINTS = 3
POINT_SIZE = 2
SHEET_REF_KIND = "sheet"
GUIDE_ID_KIND = "guide"
AudienceSide = Literal["front", "back", "left", "right"]
AUDIENCE_SIDES: tuple[str, ...] = get_args(AudienceSide)


class SurfaceType(StrEnum):
    """Names the kinds of surface a sheet represents."""

    FOOTBALL_FIELD = "football_field"
    STAGE = "stage"
    GYM_FLOOR = "gym_floor"
    ARENA = "arena"
    STREET = "street"
    BLANK = "blank"


class ZoneKind(StrEnum):
    """Names the kinds of rule-bearing area."""

    RESTRICTED = "restricted"
    PIT = "pit"
    WINGS = "wings"
    STAGING = "staging"
    AUDIENCE = "audience"
    SAFETY = "safety"
    CUSTOM = "custom"


class CoordinateKind(StrEnum):
    """Names the coordinate wording families."""

    MARCHING = "marching"
    STAGE = "stage"
    DANCE_FLOOR = "dance_floor"
    GRID = "grid"


class Grid(FrozenModel):
    """Describes the background grid.

    Attributes:
        origin: A grid intersection.
        spacing: Cell size along x and y.
        major_every: Every n-th line is drawn heavier.
        rotation_deg: Counter-clockwise grid rotation.
        unit_name: Unit the grid is labeled in.
    """

    origin: Point = (0.0, 0.0)
    spacing: tuple[Meters, Meters] = (1.0, 1.0)
    major_every: int = 5
    rotation_deg: Degrees = 0.0
    unit_name: str = "m"


def _check_guide_id(value: str, info: ValidationInfo) -> str:
    context = info.context
    if isinstance(context, Mapping) and context.get(LEGACY_IDS_KEY) is True:
        if not value:
            raise ValueError("empty guide id; a stored id needs at least one character")
        return value
    try:
        return check_sheet_id(value, kind=GUIDE_ID_KIND)
    except SchemaError as error:
        raise ValueError(f"{error.message}; {error.hint}") from error


GuideIdField = Annotated[str, AfterValidator(_check_guide_id)]


class GuideLine(FrozenModel):
    """Holds one line drawn on the sheet.

    Attributes:
        id: Guide ID.
        kind: yard, hash, sideline, center, edge, spike or custom.
        points: Vertices of the line.
        label: Text drawn with the line.
    """

    id: GuideIdField
    kind: Literal["yard", "hash", "sideline", "center", "edge", "spike", "custom"]
    points: Ring
    label: str = ""

    @model_validator(mode="after")
    def _has_points(self) -> "GuideLine":
        if not self.points:
            raise GeometryError(
                f"guide line {self.id} has no points",
                hint="give the line at least one point",
                context={"guide": self.id},
            )
        return self


class Landmark(FrozenModel):
    """Holds a named reference such as "front hash" or "center stage".

    Attributes:
        id: Landmark ID.
        name: Display name.
        kind: point (1 point), line (2+ points) or area (3+ points, a ring).
        geometry: Points of the landmark.
        aliases: Other names that resolve to it.
    """

    id: LandmarkIdField
    name: str
    kind: Literal["point", "line", "area"]
    geometry: Ring
    aliases: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _geometry_matches_kind(self) -> "Landmark":
        needed = MIN_POINTS_BY_KIND[self.kind]
        count = len(self.geometry)
        if count < needed or (self.kind == "point" and count != 1):
            raise GeometryError(
                f"{self.kind} landmark {self.id} has {count} points",
                hint="point: 1 point, line: 2 or more, area: 3 or more",
                context={"landmark": self.id},
            )
        return self

    @property
    def point(self) -> Point:
        """Returns the first point; the point itself for point landmarks."""
        return self.geometry[0]


class Zone(FrozenModel):
    """Holds an area with rules.

    Attributes:
        id: Zone ID.
        kind: What the area is for.
        polygon: The area.
        applies_to: Spots the rules apply to.
        active: Global count windows when the zone applies; None means always.
        note: Designer note.
    """

    id: ZoneIdField
    kind: ZoneKind
    polygon: Region
    applies_to: SelectorField = "all"
    active: tuple[tuple[Count, Count], ...] | None = None
    note: str = ""

    @model_validator(mode="after")
    def _ordered_windows(self) -> "Zone":
        for start, end in self.active or ():
            if end <= start:
                raise TimeRangeError(
                    f"zone {self.id} window ends before it starts",
                    hint="use (start, end) with start < end",
                    context={"window": (start, end)},
                )
        return self

    def is_active(self, t: Count) -> bool:
        """Reports whether the zone applies at global count t."""
        return self.active is None or any(start <= t <= end for start, end in self.active)


class CoordinateSystem(FrozenModel):
    """Describes how positions are labeled and worded; it never moves stored positions.

    Attributes:
        kind: Wording family.
        origin: Point or landmark ID that reads as zero.
        audience_side: Edge of the sheet where the audience sits, as drawn.
        units: Unit positions are written in.
        precision: Rounding step in `units`.
        labels: Words used for directions, e.g. {"left": "stage right"}.
    """

    kind: CoordinateKind = CoordinateKind.GRID
    origin: Point | LandmarkId = (0.0, 0.0)
    audience_side: AudienceSide = "front"
    units: str = "m"
    precision: float = 0.01
    labels: Annotated[Mapping[str, str], ReadOnly()] = Field(
        default_factory=lambda: ReadOnlyMap({})
    )

    @model_validator(mode="after")
    def _parts_are_valid(self) -> "CoordinateSystem":
        check_coordinate_system(self)
        return self


def _bad_part(field: str, value: object, hint: str) -> SchemaError:
    return SchemaError(
        f"coordinate system {field} is {value!r}", hint=hint, context={field: repr(value)}
    )


def check_coordinate_system(system: CoordinateSystem) -> None:
    """Refuses a coordinate system whose parts have the wrong type or value.

    Models built through `model_construct` skip validation, so every function that reads a
    system calls this first; a bad part raises SchemaError, never a bare TypeError.

    Args:
        system: Coordinate system to check.

    >>> check_coordinate_system(CoordinateSystem(units="ft", precision=0.5))
    """
    if not isinstance(system.kind, CoordinateKind):
        raise _bad_part("kind", system.kind, f"use one of {', '.join(CoordinateKind)}")
    origin = system.origin
    if isinstance(origin, str):
        if not origin:
            raise _bad_part("origin", origin, "give a landmark ID or an (x, y) point")
    elif not isinstance(origin, tuple | list) or len(origin) != POINT_SIZE:
        raise _bad_part("origin", origin, "give the origin as (x, y) in meters or a landmark ID")
    else:
        require_finite(origin[0], name="origin.x")
        require_finite(origin[1], name="origin.y")
    if not isinstance(system.audience_side, str) or system.audience_side not in AUDIENCE_SIDES:
        raise _bad_part(
            "audience_side", system.audience_side, f"use one of {', '.join(AUDIENCE_SIDES)}"
        )
    if not isinstance(system.units, str) or not system.units:
        raise _bad_part("units", system.units, "name a unit such as 'm' or 'ft'")
    require_finite(system.precision, name="precision", low=0.0, low_open=True)
    labels = system.labels
    if not isinstance(labels, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
    ):
        raise _bad_part("labels", labels, "map direction words to text, e.g. {'left': 'SR'}")


def normalized_name(text: str) -> str:
    """Folds a landmark name or alias to the form landmark lookup compares.

    >>> normalized_name("  Center   STAGE ")
    'center stage'
    """
    return " ".join(text.casefold().split())


def _require_distinct(kind: str, ids: list[str]) -> None:
    repeated = sorted(key for key, count in Counter(ids).items() if count > 1)
    if repeated:
        raise SchemaError(
            f"sheet has two {kind}s with the id {repeated[0]!r}",
            hint=f"give every {kind} its own id",
            context={"kind": kind, "ids": repeated},
        )


def _require_distinct_landmark_names(landmarks: tuple["Landmark", ...]) -> None:
    owners: dict[str, str] = {}
    for mark in landmarks:
        keys = {normalized_name(name) for name in (mark.id, mark.name, *mark.aliases)}
        for key in sorted(keys):
            other = owners.setdefault(key, mark.id)
            if other != mark.id:
                raise SchemaError(
                    f"landmarks {other!r} and {mark.id!r} both answer to {key!r}",
                    hint="rename one; lookup ignores case and repeated spaces",
                    context={"name": key, "landmarks": [other, mark.id]},
                )


def _require_sheet_ref(field: str, value: str) -> None:
    kind, _, version = parse_ref(value)
    if kind != SHEET_REF_KIND or version is None:
        raise SchemaError(
            f"{field} {value!r} is not a sheet reference",
            hint="write sheet:<name>@<version>, e.g. sheet:gym@1",
            context={"field": field, "ref": value},
        )


class Sheet(FrozenModel):
    """Holds the 2D design surface; (0, 0) is the front-center of the sheet.

    Attributes:
        ref: Sheet reference, `sheet:<id>@<version>`.
        name: Display name.
        surface_type: Surface family.
        size_m: Width and depth in meters.
        outline: Sheet edge ring.
        walkable: Area performers may use.
        guides: Lines drawn on the sheet.
        landmarks: Named references.
        zones: Rule-bearing areas.
        grid: Background grid.
        default_coordinates: Coordinate system new shows start with.
        based_on: Template this sheet was customized from.
        owner: Who saved the sheet.
        thumbnail: Asset URI of a preview image.
    """

    ref: SheetRef
    name: str
    surface_type: SurfaceType
    size_m: tuple[Meters, Meters]
    outline: Ring
    walkable: Region
    guides: tuple[GuideLine, ...] = ()
    landmarks: tuple[Landmark, ...] = ()
    zones: tuple[Zone, ...] = ()
    grid: Grid = Grid()
    default_coordinates: CoordinateSystem = CoordinateSystem()
    based_on: SheetRef | None = None
    owner: str = ""
    thumbnail: str = ""

    @model_validator(mode="after")
    def _is_consistent(self) -> "Sheet":
        _require_sheet_ref("ref", self.ref)
        if self.based_on is not None:
            _require_sheet_ref("based_on", self.based_on)
        for axis, length in zip(("width", "depth"), self.size_m, strict=True):
            require_finite(length, name=f"size_m {axis}", low=0.0, low_open=True)
        if len(self.outline) < MIN_OUTLINE_POINTS:
            raise GeometryError(
                f"outline has {len(self.outline)} points",
                hint=f"give the outline at least {MIN_OUTLINE_POINTS} points",
                context={"ref": self.ref},
            )
        Region(exterior=self.outline)
        _require_distinct("landmark", [normalized_name(mark.id) for mark in self.landmarks])
        _require_distinct_landmark_names(self.landmarks)
        _require_distinct("zone", [zone.id for zone in self.zones])
        _require_distinct("guide", [guide.id for guide in self.guides])
        return self
