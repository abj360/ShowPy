"""
surfaces.py --- builds the built-in surfaces: football fields, a proscenium stage, a gym floor

Every surface puts (0, 0) at the front-center (front sideline or downstage edge, y = 0), with
x from -width/2 (audience left) to +width/2. Rule-book sizes stay in their own units here and
go through e8s.core.units.convert.

Contains:
    NFHS_HASH: hash distance from each sideline on a high-school field, (feet, inches)
    NCAA_HASH: hash distance from each sideline on a college field, (feet, inches)
    NFL_HASH: hash distance from each sideline on an NFL field, (feet, inches)
    rect_ring(): returns the corners of an axis-aligned rectangle
    point_landmark(): builds a point landmark
    line_landmark(): builds a straight line landmark
    template_ref(): returns the first-version reference of a built-in sheet
    football_field(): builds a 120 yd x 160 ft field with yard lines, hashes and landmarks
    proscenium_stage(): builds a 12 x 9 m stage with wings, a center line and spike marks
    gym_floor(): builds a 94 x 50 ft basketball court
"""

import math

from e8s.core.deprecation import leaving_call
from e8s.core.ids import SheetRef, format_ref
from e8s.core.types import Point, Region, Ring
from e8s.core.units import convert
from e8s.sheet.models import (
    CoordinateKind,
    CoordinateSystem,
    Grid,
    GuideLine,
    Landmark,
    Sheet,
    SurfaceType,
    Zone,
    ZoneKind,
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

STAGE_WIDTH_M = 12.0
STAGE_DEPTH_M = 9.0
WING_WIDTH_M = 1.5
SPIKE_LENGTH_M = 0.3
STAGE_THIRD_X_M = 3.0
DOWNSTAGE_Y_M, CENTER_Y_M, UPSTAGE_Y_M = 1.5, 4.5, 7.5
# Stage right is the performer's right while facing the audience: audience left, -x.
STAGE_AREAS = (
    ("downstage-right", "downstage right", -1, DOWNSTAGE_Y_M, ("dsr",)),
    ("downstage-center", "downstage center", 0, DOWNSTAGE_Y_M, ("dsc",)),
    ("downstage-left", "downstage left", 1, DOWNSTAGE_Y_M, ("dsl",)),
    ("stage-right", "center stage right", -1, CENTER_Y_M, ("csr", "stage right")),
    ("center-stage", "center stage", 0, CENTER_Y_M, ("cs", "center")),
    ("stage-left", "center stage left", 1, CENTER_Y_M, ("csl", "stage left")),
    ("upstage-right", "upstage right", -1, UPSTAGE_Y_M, ("usr",)),
    ("upstage-center", "upstage center", 0, UPSTAGE_Y_M, ("usc",)),
    ("upstage-left", "upstage left", 1, UPSTAGE_Y_M, ("usl",)),
)

# NBA court markings, in feet and inches.
COURT_LENGTH_FT = 94.0
COURT_WIDTH_FT = 50.0
CENTER_CIRCLE_RADIUS_FT = 6.0
LANE_WIDTH_FT = 16.0
FREE_THROW_FROM_BASELINE_FT = 19.0
BASKET_FROM_BASELINE_IN = 63.0
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


def _point_landmark(mark_id: str, name: str, p: Point, *aliases: str) -> Landmark:
    """Builds a point landmark.

    >>> _point_landmark("c", "center", (0.0, 0.0), "middle").aliases
    ('middle',)
    """
    return Landmark.model_validate(
        {"id": mark_id, "name": name, "kind": "point", "geometry": (p,), "aliases": aliases}
    )


@leaving_call(since="0.4.0", remove="0.7.0", replacement=("Marking", "markings_near"))
def point_landmark(mark_id: str, name: str, p: Point, *aliases: str) -> Landmark:
    """Builds a point landmark.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _point_landmark(mark_id, name, p, *aliases)


def _line_landmark(mark_id: str, name: str, a: Point, b: Point, *aliases: str) -> Landmark:
    """Builds a straight line landmark from `a` to `b`.

    >>> _line_landmark("edge", "front edge", (-1.0, 0.0), (1.0, 0.0)).kind
    'line'
    """
    return Landmark.model_validate(
        {"id": mark_id, "name": name, "kind": "line", "geometry": (a, b), "aliases": aliases}
    )


@leaving_call(since="0.4.0", remove="0.7.0", replacement=("Marking", "markings_near"))
def line_landmark(mark_id: str, name: str, a: Point, b: Point, *aliases: str) -> Landmark:
    """Builds a straight line landmark from `a` to `b`.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _line_landmark(mark_id, name, a, b, *aliases)


def _area_landmark(mark_id: str, name: str, ring: Ring) -> Landmark:
    return Landmark.model_validate({"id": mark_id, "name": name, "kind": "area", "geometry": ring})


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


def _yard_names(offset_yd: int) -> tuple[str, str, str]:
    number = GOAL_LINE_YD - abs(offset_yd)
    if offset_yd == 0:
        return "50", "50", "50 yard line"
    side = "left" if offset_yd < 0 else "right"
    line_name = f"{side} goal line" if number == 0 else f"{side} {number} yard line"
    return f"{side}-{number}", f"{side} {number}", line_name


def _yard_offsets() -> range:
    return range(-GOAL_LINE_YD, GOAL_LINE_YD + 1, YARD_LINE_STEP_YD)


def _yard_lines(depth: float) -> tuple[GuideLine, ...]:
    guides = []
    for offset in _yard_offsets():
        key, _, _ = _yard_names(offset)
        number = GOAL_LINE_YD - abs(offset)
        x = convert(offset, "yd", "m")
        label = "G" if number == 0 else str(number)
        points = ((x, 0.0), (x, depth))
        guides.append(GuideLine(id=f"yard-{key}", kind="yard", points=points, label=label))
    return tuple(guides)


def _yard_landmarks(depth: float, hashes: tuple[float, float]) -> tuple[Landmark, ...]:
    marks = []
    for offset in _yard_offsets():
        key, short, line_name = _yard_names(offset)
        x = convert(offset, "yd", "m")
        marks.append(_line_landmark(f"yard-{key}", line_name, (x, 0.0), (x, depth), short))
        for side, home_word, y in (("front", "home", hashes[0]), ("back", "visitor", hashes[1])):
            name, alias = f"{short} {side} hash", f"{short} {home_word} hash"
            marks.append(_point_landmark(f"{side}-hash-{key}", name, (x, y), alias))
    return tuple(marks)


def _field_landmarks(
    half: float, depth: float, hashes: tuple[float, float]
) -> tuple[Landmark, ...]:
    goal = convert(GOAL_LINE_YD, "yd", "m")
    front, back = hashes
    return (
        _point_landmark("center", "center", (0.0, 0.0), "front center", "50 front sideline"),
        _point_landmark("back-center", "back center", (0.0, depth), "50 back sideline"),
        _line_landmark("front-sideline", "front sideline", (-half, 0), (half, 0), "home sideline"),
        _line_landmark(
            "back-sideline", "back sideline", (-half, depth), (half, depth), "visitor sideline"
        ),
        _line_landmark("front-hash", "front hash", (-goal, front), (goal, front), "home hash"),
        _line_landmark("back-hash", "back hash", (-goal, back), (goal, back), "visitor hash"),
        _area_landmark("end-zone-left", "left end zone", rect_ring(-half, 0.0, -goal, depth)),
        _area_landmark("end-zone-right", "right end zone", rect_ring(goal, 0.0, half, depth)),
    )


def _football_field(ref_id: str, name: str, hash_ft_in: tuple[float, float]) -> Sheet:
    """Builds a 120 yd x 160 ft field: yard lines every 5 yd, hashes, sidelines, end lines.

    Landmarks: the center (50 on the front sideline), every yard line, and the front (home)
    and back (visitor) hash point on every yard line.

    Args:
        ref_id: Template ID, e.g. `hs-football`.
        name: Display name.
        hash_ft_in: Distance of each hash row from its sideline, as (feet, inches).

    >>> _football_field("f", "Field", NFL_HASH).landmarks[0].name
    'center'
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
        landmarks=(*_field_landmarks(half, depth, hashes), *_yard_landmarks(depth, hashes)),
        grid=Grid(spacing=(yard, yard), major_every=FIELD_GRID_MAJOR, unit_name="yd"),
        default_coordinates=CoordinateSystem(kind=CoordinateKind.MARCHING),
    )


@leaving_call(since="0.4.0", remove="0.7.0", replacement="field")
def football_field(ref_id: str, name: str, hash_ft_in: tuple[float, float]) -> Sheet:
    """Builds a 120 yd x 160 ft field: yard lines every 5 yd, hashes, sidelines, end lines.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _football_field(ref_id, name, hash_ft_in)


def _spikes(play_half: float) -> tuple[GuideLine, ...]:
    spikes = []
    for n in range(-math.floor(play_half), math.floor(play_half) + 1):
        side = "sr-" if n < 0 else "sl-" if n > 0 else ""
        points = ((float(n), 0.0), (float(n), SPIKE_LENGTH_M))
        spikes.append(
            GuideLine(id=f"spike-{side}{abs(n)}", kind="spike", points=points, label=str(abs(n)))
        )
    return tuple(spikes)


def _stage_guides(half: float, play_half: float) -> tuple[GuideLine, ...]:
    depth = STAGE_DEPTH_M
    return (
        GuideLine(id="edge-downstage", kind="edge", points=((-half, 0.0), (half, 0.0))),
        GuideLine(id="edge-upstage", kind="edge", points=((-half, depth), (half, depth))),
        GuideLine(id="center-line", kind="center", points=((0.0, 0.0), (0.0, depth))),
        GuideLine(id="proscenium-sr", kind="edge", points=((-play_half, 0), (-play_half, depth))),
        GuideLine(id="proscenium-sl", kind="edge", points=((play_half, 0), (play_half, depth))),
        *_spikes(play_half),
    )


def _wing(zone_id: str, x0: float, x1: float, note: str) -> Zone:
    ring = rect_ring(x0, 0.0, x1, STAGE_DEPTH_M)
    return Zone.model_validate(
        {"id": zone_id, "kind": ZoneKind.WINGS, "polygon": Region(exterior=ring), "note": note}
    )


def _proscenium_stage() -> Sheet:
    """Builds a 12 x 9 m stage: 1.5 m wings, a center line, spike marks, nine stage areas.

    >>> _proscenium_stage().default_coordinates.kind
    <CoordinateKind.STAGE: 'stage'>
    """
    half = STAGE_WIDTH_M / 2
    play_half = half - WING_WIDTH_M
    ring = rect_ring(-half, 0.0, half, STAGE_DEPTH_M)
    areas = tuple(
        _point_landmark(mark_id, name, (column * STAGE_THIRD_X_M, y), *aliases)
        for mark_id, name, column, y, aliases in STAGE_AREAS
    )
    edge = _line_landmark(
        "downstage-edge", "downstage edge", (-half, 0.0), (half, 0.0), "front edge", "apron"
    )
    return Sheet(
        ref=_template_ref("proscenium-stage"),
        name="Proscenium stage 12 x 9 m",
        surface_type=SurfaceType.STAGE,
        size_m=(STAGE_WIDTH_M, STAGE_DEPTH_M),
        outline=ring,
        walkable=Region(exterior=ring),
        guides=_stage_guides(half, play_half),
        landmarks=(_point_landmark("front-center", "front center", (0.0, 0.0)), edge, *areas),
        zones=(
            _wing("wing-sr", -half, -play_half, "stage right wing"),
            _wing("wing-sl", play_half, half, "stage left wing"),
        ),
        default_coordinates=CoordinateSystem(kind=CoordinateKind.STAGE),
    )


@leaving_call(since="0.4.0", remove="0.7.0", replacement="floor")
def proscenium_stage() -> Sheet:
    """Builds a 12 x 9 m stage: 1.5 m wings, a center line, spike marks, nine stage areas.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _proscenium_stage()


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


def _court_marks(side: str, sign: float, half: float, mid: float) -> tuple[Landmark, ...]:
    baseline = sign * half
    free_throw = baseline - sign * _ft(FREE_THROW_FROM_BASELINE_FT)
    basket = baseline - sign * convert(BASKET_FROM_BASELINE_IN, "in", "m")
    return (
        _point_landmark(f"free-throw-{side}", f"{side} free throw line", (free_throw, mid)),
        _point_landmark(f"basket-{side}", f"{side} basket", (basket, mid), f"{side} hoop"),
    )


def _gym_floor() -> Sheet:
    """Builds a 94 x 50 ft basketball court: lines, center circle, lanes, baskets.

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
        landmarks=(
            _point_landmark("center-court", "center court", (0.0, mid), "center", "center circle"),
            _point_landmark("front-center", "front center", (0.0, 0.0)),
            *(m for side, sign in ends for m in _court_marks(side, sign, half, mid)),
        ),
        default_coordinates=CoordinateSystem(kind=CoordinateKind.DANCE_FLOOR),
    )


@leaving_call(since="0.4.0", remove="0.7.0", replacement="floor")
def gym_floor() -> Sheet:
    """Builds a 94 x 50 ft basketball court: lines, center circle, lanes, baskets.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _gym_floor()
