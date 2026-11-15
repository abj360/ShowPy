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
    Sheet: holds the 2D design surface
"""

from enum import StrEnum
from typing import Literal

from pydantic import model_validator

from showpy.core.errors import GeometryError, TimeRangeError
from showpy.core.ids import LandmarkId, SheetRef, ZoneId
from showpy.core.selection import SelectorField
from showpy.core.types import Count, Degrees, FrozenModel, Meters, Point, Region, Ring

MIN_POINTS_BY_KIND = {"point": 1, "line": 2, "area": 3}


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


class GuideLine(FrozenModel):
    """Holds one line drawn on the sheet.

    Attributes:
        id: Guide ID.
        kind: yard, hash, sideline, center, edge, spike or custom.
        points: Vertices of the line.
        label: Text drawn with the line.
    """

    id: str
    kind: Literal["yard", "hash", "sideline", "center", "edge", "spike", "custom"]
    points: Ring
    label: str = ""


class Landmark(FrozenModel):
    """Holds a named reference such as "front hash" or "center stage".

    Attributes:
        id: Landmark ID.
        name: Display name.
        kind: point (1 point), line (2+ points) or area (3+ points, a ring).
        geometry: Points of the landmark.
        aliases: Other names that resolve to it.
    """

    id: LandmarkId
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

    id: ZoneId
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
    audience_side: Literal["front", "back", "left", "right"] = "front"
    units: str = "m"
    precision: float = 0.01
    labels: dict[str, str] = {}


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
