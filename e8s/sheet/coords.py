"""
coords.py --- coordinate presets and generic position wording, plus its exact inverse

Wording is generic (packs add field language later). Long style reads
"3.50 m right of center, 2.00 m upstage of front edge"; short style reads "R3.50 U2.00".
Directions are taken as the audience sees the sheet from `audience_side`, except for the
STAGE kind, which uses the performer's stage left and right.

Contains:
    CoordStyle: names the wording styles
    coordinate_presets(): lists coordinate systems that suit a sheet
    set_coordinates(): returns a show with a new coordinate system; positions never move
    to_coords(): words a point in the show's coordinate system
    axis_offsets(): returns points' offsets from the origin, across and away from the audience
    axis_origin(): returns the system's origin in stage meters
    offsets_in_units(): converts offsets in meters to the system's units, unrounded
    rounded_offsets(): converts offsets to the system's units, rounded to its precision
    from_coords(): reads worded coordinates back into a point
"""

import math
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import numpy as np

from e8s.core.arrays import FloatArray
from e8s.core.caps import MAX_COORD_M
from e8s.core.deprecation import leaving_call
from e8s.core.errors import SchemaError, UnknownRefError
from e8s.core.types import Point, Points
from e8s.core.units import convert, round_to
from e8s.core.validation import require_finite
from e8s.sheet.bounds import _landmark, _landmark_anchor
from e8s.sheet.models import (
    CoordinateKind,
    CoordinateSystem,
    Sheet,
    SurfaceType,
    check_coordinate_system,
)

if TYPE_CHECKING:
    from e8s.show.models import Show

type CoordStyle = Literal["long", "short"]
type Axis = Literal["across", "depth"]

GENERIC_WORDS = {
    "right": "right",
    "left": "left",
    "up": "upstage",
    "down": "downstage",
    "right_short": "R",
    "left_short": "L",
    "up_short": "U",
    "down_short": "D",
}
# Stage left is the performer's left while facing the audience, which is the audience's right.
STAGE_WORDS = {
    **GENERIC_WORDS,
    "right": "stage left",
    "left": "stage right",
    "right_short": "SL",
    "left_short": "SR",
    "up_short": "US",
    "down_short": "DS",
}
DIRECTIONS: dict[str, tuple[Axis, int]] = {
    "right": ("across", 1),
    "left": ("across", -1),
    "up": ("depth", 1),
    "down": ("depth", -1),
}
# Audience's right-hand direction and viewing direction (away from the audience) per side.
FRAMES: dict[str, tuple[Point, Point]] = {
    "front": ((1.0, 0.0), (0.0, 1.0)),
    "back": ((-1.0, 0.0), (0.0, -1.0)),
    "left": ((0.0, -1.0), (1.0, 0.0)),
    "right": ((0.0, 1.0), (-1.0, 0.0)),
}
DEFAULT_REFS = ("center", "front edge")
GENERIC_REF = "origin"
CENTER_LANDMARKS = ("center", "center-stage", "center-court")
FEET_PRECISION = 0.5
LONG_CLAUSE = re.compile(r"^(?P<num>\d+(?:\.\d+)?)\s+(?P<unit>\S+)\s+(?P<rest>.+)$")
SHORT_TOKEN = re.compile(r"^(?P<code>[A-Za-z]+)(?P<num>\d+(?:\.\d+)?)$")
OF = " of "
ROUNDING_HINT = (
    "keep each offset under about 1e27 rounding steps: use a coarser precision or a nearer origin"
)
NUMBER_PREVIEW = 20
TEXT_PREVIEW = 40
POINT_BOUND_HINT = (
    f"use smaller numbers or a nearer origin: keep each axis between -{MAX_COORD_M:,.0f} and "
    f"{MAX_COORD_M:,.0f} m (MAX_COORD_M)"
)


def _coordinate_presets(sheet: Sheet) -> list[CoordinateSystem]:
    """Lists coordinate systems that suit a sheet, its default first, without repeats.

    >>> from e8s.sheet.templates import _get_sheet
    >>> [p.kind.value for p in _coordinate_presets(_get_sheet("sheet:proscenium-stage"))][:2]
    ['stage', 'grid']
    """
    default = sheet.default_coordinates
    candidates = [
        default,
        CoordinateSystem(),
        CoordinateSystem(units="ft", precision=FEET_PRECISION),
    ]
    if sheet.surface_type == SurfaceType.STAGE:
        candidates.append(CoordinateSystem(kind=CoordinateKind.STAGE))
    if sheet.grid.unit_name != "m":
        candidates.append(default.replace(units=sheet.grid.unit_name))
    candidates += [
        default.replace(origin=mark.id)
        for mark in sheet.landmarks
        if mark.id in CENTER_LANDMARKS and mark.kind == "point"
    ]
    unique: list[CoordinateSystem] = []
    for system in candidates:
        if system not in unique:
            unique.append(system)
    return unique


@leaving_call(since="0.4.0", remove="0.7.0", replacement=("locate", "at_location", "Location"))
def coordinate_presets(sheet: Sheet) -> list[CoordinateSystem]:
    """Lists coordinate systems that suit a sheet, its default first, without repeats.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _coordinate_presets(sheet)


def _origin(sheet: Sheet, system: CoordinateSystem) -> Point:
    if isinstance(system.origin, str):
        return _landmark_anchor(_landmark(sheet, system.origin))
    return system.origin[0], system.origin[1]


def _frame(system: CoordinateSystem) -> tuple[Point, Point]:
    return FRAMES[system.audience_side]


def _set_coordinates(show: "Show", system: CoordinateSystem) -> "Show":
    """Returns the show with a new coordinate system; stored positions never move.

    >>> from e8s.examples import two_set_show
    >>> _set_coordinates(two_set_show(), CoordinateSystem(units="ft")).coordinates.units
    'ft'
    """
    check_coordinate_system(system)
    convert(1.0, system.units, "m")
    _origin(show.sheet, system)
    return show.replace(coordinates=system)


@leaving_call(since="0.4.0", remove="0.7.0", replacement=("locate", "at_location", "Location"))
def set_coordinates(show: "Show", system: CoordinateSystem) -> "Show":
    """Returns the show with a new coordinate system; stored positions never move.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _set_coordinates(show, system)


def _words(sheet: Sheet, system: CoordinateSystem) -> dict[str, str]:
    base = STAGE_WORDS if system.kind == CoordinateKind.STAGE else GENERIC_WORDS
    if isinstance(system.origin, str):
        name = _landmark(sheet, system.origin).name
        refs = (name, name)
    elif system.origin == (0.0, 0.0) and system.audience_side == "front":
        refs = DEFAULT_REFS
    else:
        refs = (GENERIC_REF, GENERIC_REF)
    return {**base, "across_ref": refs[0], "depth_ref": refs[1], **system.labels}


def _decimals(precision: float) -> int:
    exponent = Decimal(repr(precision)).normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def axis_offsets(
    sheet: Sheet, system: CoordinateSystem, points: Points
) -> tuple[FloatArray, FloatArray]:
    """Returns points' offsets from the system's origin, across and away from the audience.

    Across is positive toward the audience's right and depth positive away from the audience,
    both in meters, whichever edge of the sheet the audience sits at.

    Args:
        sheet: Sheet whose landmarks a named origin refers to.
        system: Coordinate system giving the origin and the audience side.
        points: Stage-plane points in meters, shape (N, 2).

    Returns:
        offsets: Across and depth offsets, each of shape (N,).

    >>> from e8s.examples import field_sheet
    >>> system = CoordinateSystem(origin=(1.0, 1.0), audience_side="back")
    >>> [a.tolist() for a in axis_offsets(field_sheet(), system, np.array([[3.5, 2.0]]))]
    [[-2.5], [-1.0]]
    """
    check_coordinate_system(system)
    ox, oy = _origin(sheet, system)
    right, ahead = _frame(system)
    # An offset past the float range becomes inf, as Python float arithmetic gives; every
    # caller refuses a non-finite offset, so numpy's overflow warning would only repeat it.
    with np.errstate(over="ignore", invalid="ignore"):
        dx, dy = points[:, 0] - ox, points[:, 1] - oy
        across: FloatArray = dx * right[0] + dy * right[1]
        depth: FloatArray = dx * ahead[0] + dy * ahead[1]
    return across, depth


def axis_origin(sheet: Sheet, system: CoordinateSystem) -> Point:
    """Returns the system's origin in stage meters, resolving a named landmark.

    >>> from e8s.examples import field_sheet
    >>> axis_origin(field_sheet(), CoordinateSystem(origin=(1.0, 2.0)))
    (1.0, 2.0)
    """
    check_coordinate_system(system)
    return _origin(sheet, system)


def _local(sheet: Sheet, system: CoordinateSystem, p: Point) -> tuple[float, float]:
    across, depth = axis_offsets(sheet, system, np.array([p], dtype=float))
    return float(across[0]), float(depth[0])


def _rounded(value_m: float, system: CoordinateSystem) -> float:
    context = {"value": value_m, "precision": system.precision, "units": system.units}
    try:
        shown = round_to(convert(value_m, "m", system.units), system.precision)
    except SchemaError as error:
        raise SchemaError(
            f"cannot round {value_m} m to a step of {system.precision} {system.units}",
            hint=ROUNDING_HINT,
            context=context,
        ) from error
    if not math.isfinite(shown):
        raise SchemaError(
            f"{value_m} m is not a finite offset at a step of {system.precision} {system.units}",
            hint=ROUNDING_HINT,
            context=context,
        )
    return shown


def offsets_in_units(system: CoordinateSystem, offsets_m: FloatArray) -> FloatArray:
    """Returns offsets in meters converted to the system's units, unrounded.

    >>> offsets_in_units(CoordinateSystem(units="ft"), np.array([0.3048, -0.6096])).tolist()
    [1.0, -2.0]
    """
    check_coordinate_system(system)
    in_units: FloatArray = offsets_m * convert(1.0, "m", system.units)
    return in_units


def rounded_offsets(system: CoordinateSystem, offsets_m: FloatArray) -> FloatArray:
    """Returns offsets in meters in the system's units, rounded to its precision as worded.

    Each value equals the number `to_coords` writes for it, signed: half away from zero.

    Args:
        system: Coordinate system giving the units and the precision.
        offsets_m: Offsets in meters, any shape.

    Returns:
        rounded: The offsets in the system's units, rounded, in the same shape.

    Raises:
        SchemaError: An offset cannot be rounded to a finite value at the precision.

    >>> rounded_offsets(CoordinateSystem(precision=0.5), np.array([1.24, -1.25])).tolist()
    [1.0, -1.5]
    """
    check_coordinate_system(system)
    flat = [_rounded(value, system) for value in offsets_m.ravel().tolist()]
    return np.array(flat, dtype=float).reshape(offsets_m.shape)


def _part(
    value_m: float, system: CoordinateSystem, positive: str, negative: str
) -> tuple[str, str]:
    shown = _rounded(value_m, system)
    text = f"{abs(shown):.{_decimals(system.precision)}f}"
    return text, negative if shown < 0 else positive


def _to_coords(show: "Show", p: Point, style: CoordStyle = "long") -> str:
    """Words a point in the show's coordinate system, rounded to its precision.

    Args:
        show: Show whose sheet and coordinate system are used.
        p: Stage-plane point in meters.
        style: `long` for sentences, `short` for compact codes such as `R3.50 U2.00`.

    Returns:
        text: The worded position; from_coords() reads it back.

    >>> from e8s.examples import two_set_show
    >>> _to_coords(two_set_show(), (3.5, 2.0))
    '3.50 m right of center, 2.00 m upstage of front edge'
    """
    system = show.coordinates
    check_coordinate_system(system)
    require_finite(p[0], name="p.x")
    require_finite(p[1], name="p.y")
    words = _words(show.sheet, system)
    across, depth = _local(show.sheet, system, p)
    a_num, a_dir = _part(across, system, "right", "left")
    d_num, d_dir = _part(depth, system, "up", "down")
    if style == "short":
        return f"{words[a_dir + '_short']}{a_num} {words[d_dir + '_short']}{d_num}"
    unit = system.units
    across_text = f"{a_num} {unit} {words[a_dir]} of {words['across_ref']}"
    return f"{across_text}, {d_num} {unit} {words[d_dir]} of {words['depth_ref']}"


@leaving_call(since="0.4.0", remove="0.7.0", replacement="locate")
def to_coords(show: "Show", p: Point, style: CoordStyle = "long") -> str:
    """Words a point in the show's coordinate system, rounded to its precision.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _to_coords(show, p, style)


def _bad(token: str, text: str, hint: str) -> SchemaError:
    return SchemaError(
        f"cannot read {token!r} in {text!r}", hint=hint, context={"token": token, "text": text}
    )


def _meters(num: str, unit: str, text: str) -> float:
    try:
        meters = convert(float(num), unit, "m")
    except UnknownRefError as error:
        raise _bad(unit, text, error.hint) from error
    if not math.isfinite(meters):
        raise _bad(num[:NUMBER_PREVIEW] + "...", text, "write a number a float can hold")
    return meters


def _long_part(clause: str, text: str, words: dict[str, str]) -> tuple[Axis, float]:
    match = LONG_CLAUSE.match(clause)
    if match is None:
        raise _bad(clause, text, "write each part as '<number> <unit> <direction> of <origin>'")
    rest = match["rest"].casefold()
    for direction in sorted(DIRECTIONS, key=lambda d: -len(words[d])):
        prefix = words[direction].casefold() + OF
        if not rest.startswith(prefix):
            continue
        axis, sign = DIRECTIONS[direction]
        ref = match["rest"][len(prefix) :].strip()
        if ref.casefold() != words[f"{axis}_ref"].casefold():
            raise _bad(ref, text, f"measure {axis} from {words[f'{axis}_ref']!r}")
        return axis, sign * _meters(match["num"], match["unit"], text)
    token = match["rest"].split(OF)[0].strip()
    allowed = ", ".join(words[d] for d in DIRECTIONS)
    raise _bad(token, text, f"use one of: {allowed}")


def _short_part(token: str, text: str, words: dict[str, str], unit: str) -> tuple[Axis, float]:
    codes = {words[f"{d}_short"].casefold(): DIRECTIONS[d] for d in DIRECTIONS}
    match = SHORT_TOKEN.match(token)
    if match is None or match["code"].casefold() not in codes:
        allowed = ", ".join(words[f"{d}_short"] for d in DIRECTIONS)
        raise _bad(token, text, f"write a code ({allowed}) followed by a number")
    axis, sign = codes[match["code"].casefold()]
    return axis, sign * _meters(match["num"], unit, text)


def _parts(text: str, words: dict[str, str], unit: str) -> list[tuple[str, tuple[Axis, float]]]:
    if "," in text or OF in text.casefold():
        clauses = [c.strip() for c in text.split(",")]
        return [(c, _long_part(c, text, words)) for c in clauses]
    return [(t, _short_part(t, text, words, unit)) for t in text.split()]


def _from_coords(show: "Show", text: str) -> Point:
    """Reads worded coordinates (long or short style, either part first) into a point.

    Long style may name any registered unit; short style uses the system's unit.

    >>> from e8s.examples import two_set_show
    >>> _from_coords(two_set_show(), "L3.50 U2.00")
    (-3.5, 2.0)
    """
    system = show.coordinates
    check_coordinate_system(system)
    words = _words(show.sheet, system)
    values: dict[Axis, float] = {}
    for token, (axis, value) in _parts(text.strip(), words, system.units):
        if axis in values:
            raise _bad(token, text, f"give the {axis} part once")
        values[axis] = value
    missing = [axis for axis in ("across", "depth") if axis not in values]
    if missing:
        raise _bad(text, text, f"add the {missing[0]} part")
    (ox, oy), (right, ahead) = _origin(show.sheet, system), _frame(system)
    across, depth = values["across"], values["depth"]
    point = (
        ox + across * right[0] + depth * ahead[0] + 0.0,
        oy + across * right[1] + depth * ahead[1] + 0.0,
    )
    if not all(math.isfinite(axis) and abs(axis) <= MAX_COORD_M for axis in point):
        raise _bad(text[:TEXT_PREVIEW], text, POINT_BOUND_HINT)
    return point


@leaving_call(since="0.4.0", remove="0.7.0", replacement="at_location")
def from_coords(show: "Show", text: str) -> Point:
    """Reads worded coordinates (long or short style, either part first) into a point.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _from_coords(show, text)
