"""
models.py --- sheet data classes: grid, guide lines, the sheet

Contains:
    SurfaceType: names the kinds of surface a sheet represents
    Grid: describes the background grid
    GuideLine: holds one line drawn on the sheet
    Sheet: holds the 2D design surface
"""

from collections import Counter
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, ValidationInfo, model_validator

from e8s.core.errors import GeometryError, SchemaError
from e8s.core.ids import LEGACY_IDS_KEY, SheetRef, check_sheet_id, parse_ref
from e8s.core.types import Degrees, FrozenModel, Meters, Point, Region, Ring
from e8s.core.validation import require_finite

MIN_OUTLINE_POINTS = 3
SHEET_REF_KIND = "sheet"
GUIDE_ID_KIND = "guide"


class SurfaceType(StrEnum):
    """Names the kinds of surface a sheet represents."""

    FOOTBALL_FIELD = "football_field"
    STAGE = "stage"
    GYM_FLOOR = "gym_floor"
    ARENA = "arena"
    STREET = "street"
    BLANK = "blank"


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


def _require_distinct(kind: str, ids: list[str]) -> None:
    repeated = sorted(key for key, count in Counter(ids).items() if count > 1)
    if repeated:
        raise SchemaError(
            f"sheet has two {kind}s with the id {repeated[0]!r}",
            hint=f"give every {kind} its own id",
            context={"kind": kind, "ids": repeated},
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
        grid: Background grid.
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
    grid: Grid = Grid()
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
        _require_distinct("guide", [guide.id for guide in self.guides])
        return self
