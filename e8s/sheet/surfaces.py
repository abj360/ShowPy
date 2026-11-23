"""
surfaces.py --- builds the built-in surfaces: football fields and a gym floor

Every surface puts (0, 0) at the front-center (front sideline, y = 0), with x from -width/2
(audience left) to +width/2. Rule-book sizes stay in their own units here and go through
e8s.core.units.convert.

Contains:
    NFHS_HASH: hash distance from each sideline on a high-school field, (feet, inches)
    NCAA_HASH: hash distance from each sideline on a college field, (feet, inches)
    NFL_HASH: hash distance from each sideline on an NFL field, (feet, inches)
    rect_ring(): returns the corners of an axis-aligned rectangle
    template_ref(): returns the first-version reference of a built-in sheet
    football_field(): builds a 120 yd x 160 ft field with yard lines, hashes and sidelines
    gym_floor(): builds a 94 x 50 ft basketball court
"""

import math

from e8s.core.deprecation import leaving_call
from e8s.core.ids import SheetRef, format_ref
from e8s.core.types import Point, Region, Ring
from e8s.core.units import convert
from e8s.sheet.models import (
    Grid,
    GuideLine,
    Sheet,
    SurfaceType,
)

REF_KIND = "sheet"
FIRST_VERSION = "1"

FIELD_LENGTH_YD = 120.0
FIELD_WIDTH_FT = 160.0
GOAL_LINE_YD = 50
YARD_LINE_STEP_YD = 5
FIELD_GRID_MAJOR = 5
# Hash rows as measured from each sideline in the NFHS, NCAA and NFL rule books.
NFHS_HASH = (53.0, 4.0)
NCAA_HASH = (60.0, 0.0)
NFL_HASH = (70.0, 9.0)

# NBA court markings, in feet and inches.
COURT_LENGTH_FT = 94.0
COURT_WIDTH_FT = 50.0
CENTER_CIRCLE_RADIUS_FT = 6.0
LANE_WIDTH_FT = 16.0
FREE_THROW_FROM_BASELINE_FT = 19.0
CIRCLE_SEGMENTS = 48


def _ft(feet: float, inches: float = 0.0) -> float:
    return convert(feet, "ft", "m") + convert(inches, "in", "m")


def _closed(ring: Ring) -> Ring:
    return (*ring, ring[0])


def rect_ring(x0: float, y0: float, x1: float, y1: float) -> Ring:
    """Returns the corners of an axis-aligned rectangle, counter-clockwise.

    >>> rect_ring(0, 0, 2, 1)
    ((0, 0), (2, 0), (2, 1), (0, 1))
    """
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _template_ref(ref_id: str) -> SheetRef:
    """Returns the first-version reference of a built-in sheet.

    >>> _template_ref("gym-basketball")
    'sheet:gym-basketball@1'
    """
    return format_ref(REF_KIND, ref_id, FIRST_VERSION)


@leaving_call(
    since="0.4.0",
    remove="0.7.0",
    replacement=("sheets", "sheet", "field", "floor", "Marking", "on_sheet"),
)
def template_ref(ref_id: str) -> SheetRef:
    """Returns the first-version reference of a built-in sheet.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _template_ref(ref_id)


def _field_edges(half: float, depth: float) -> tuple[GuideLine, ...]:
    return (
        GuideLine(id="sideline-front", kind="sideline", points=((-half, 0.0), (half, 0.0))),
        GuideLine(id="sideline-back", kind="sideline", points=((-half, depth), (half, depth))),
        GuideLine(id="end-line-left", kind="edge", points=((-half, 0.0), (-half, depth))),
        GuideLine(id="end-line-right", kind="edge", points=((half, 0.0), (half, depth))),
    )


def _yard_key(offset_yd: int) -> str:
    if offset_yd == 0:
        return "50"
    side = "left" if offset_yd < 0 else "right"
    return f"{side}-{GOAL_LINE_YD - abs(offset_yd)}"


def _yard_offsets() -> range:
    return range(-GOAL_LINE_YD, GOAL_LINE_YD + 1, YARD_LINE_STEP_YD)


def _yard_lines(depth: float) -> tuple[GuideLine, ...]:
    guides = []
    for offset in _yard_offsets():
        key = _yard_key(offset)
        number = GOAL_LINE_YD - abs(offset)
        x = convert(offset, "yd", "m")
        label = "G" if number == 0 else str(number)
        points = ((x, 0.0), (x, depth))
        guides.append(GuideLine(id=f"yard-{key}", kind="yard", points=points, label=label))
    return tuple(guides)


def _football_field(ref_id: str, name: str, hash_ft_in: tuple[float, float]) -> Sheet:
    """Builds a 120 yd x 160 ft field: yard lines every 5 yd, hashes, sidelines, end lines.

    Args:
        ref_id: Template ID, e.g. `hs-football`.
        name: Display name.
        hash_ft_in: Distance of each hash row from its sideline, as (feet, inches).

    >>> _football_field("f", "Field", NFL_HASH).guides[0].id
    'sideline-front'
    """
    length = convert(FIELD_LENGTH_YD, "yd", "m")
    depth = convert(FIELD_WIDTH_FT, "ft", "m")
    half = length / 2
    from_sideline = _ft(*hash_ft_in)
    hashes = (from_sideline, depth - from_sideline)
    goal = convert(GOAL_LINE_YD, "yd", "m")
    hash_guides = tuple(
        GuideLine(id=f"hash-{side}", kind="hash", points=((-goal, y), (goal, y)))
        for side, y in zip(("front", "back"), hashes, strict=True)
    )
    ring = rect_ring(-half, 0.0, half, depth)
    yard = convert(1.0, "yd", "m")
    return Sheet(
        ref=_template_ref(ref_id),
        name=name,
        surface_type=SurfaceType.FOOTBALL_FIELD,
        size_m=(length, depth),
        outline=ring,
        walkable=Region(exterior=ring),
        guides=(*_field_edges(half, depth), *_yard_lines(depth), *hash_guides),
        grid=Grid(spacing=(yard, yard), major_every=FIELD_GRID_MAJOR, unit_name="yd"),
    )


@leaving_call(since="0.4.0", remove="0.7.0", replacement="field")
def football_field(ref_id: str, name: str, hash_ft_in: tuple[float, float]) -> Sheet:
    """Builds a 120 yd x 160 ft field: yard lines every 5 yd, hashes, sidelines, end lines.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _football_field(ref_id, name, hash_ft_in)


def _circle_ring(center: Point, radius: float) -> Ring:
    step = 2 * math.pi / CIRCLE_SEGMENTS
    return tuple(
        (center[0] + radius * math.cos(i * step), center[1] + radius * math.sin(i * step))
        for i in range(CIRCLE_SEGMENTS)
    )


def _court_end(side: str, sign: float, half: float, mid: float) -> tuple[GuideLine, ...]:
    lane_half = _ft(LANE_WIDTH_FT) / 2
    baseline = sign * half
    free_throw = baseline - sign * _ft(FREE_THROW_FROM_BASELINE_FT)
    x0, x1 = sorted((baseline, free_throw))
    lane = _closed(rect_ring(x0, mid - lane_half, x1, mid + lane_half))
    return (GuideLine(id=f"lane-{side}", kind="custom", points=lane),)


def _gym_floor() -> Sheet:
    """Builds a 94 x 50 ft basketball court: lines, center circle, lanes.

    >>> round(_gym_floor().size_m[0], 4)
    28.6512
    """
    length, depth = _ft(COURT_LENGTH_FT), _ft(COURT_WIDTH_FT)
    half, mid = length / 2, depth / 2
    ring = rect_ring(-half, 0.0, half, depth)
    circle = _closed(_circle_ring((0.0, mid), _ft(CENTER_CIRCLE_RADIUS_FT)))
    ends = (("left", -1.0), ("right", 1.0))
    return Sheet(
        ref=_template_ref("gym-basketball"),
        name="Gym floor (basketball court 94 x 50 ft)",
        surface_type=SurfaceType.GYM_FLOOR,
        size_m=(length, depth),
        outline=ring,
        walkable=Region(exterior=ring),
        guides=(
            GuideLine(id="sideline-front", kind="sideline", points=((-half, 0.0), (half, 0.0))),
            GuideLine(id="sideline-back", kind="sideline", points=((-half, depth), (half, depth))),
            GuideLine(id="baseline-left", kind="edge", points=((-half, 0.0), (-half, depth))),
            GuideLine(id="baseline-right", kind="edge", points=((half, 0.0), (half, depth))),
            GuideLine(id="center-line", kind="center", points=((0.0, 0.0), (0.0, depth))),
            GuideLine(id="center-circle", kind="custom", points=circle),
            *(g for side, sign in ends for g in _court_end(side, sign, half, mid)),
        ),
    )


@leaving_call(since="0.4.0", remove="0.7.0", replacement="floor")
def gym_floor() -> Sheet:
    """Builds a 94 x 50 ft basketball court: lines, center circle, lanes.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _gym_floor()
