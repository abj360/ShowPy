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
    from_coords(): reads worded coordinates back into a point
"""

import re
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from showpy.core.errors import SchemaError, UnknownRefError
from showpy.core.types import Point
from showpy.core.units import convert, round_to
from showpy.sheet.bounds import landmark, landmark_anchor
from showpy.sheet.models import CoordinateKind, CoordinateSystem, Sheet, SurfaceType

if TYPE_CHECKING:
    from showpy.show.models import Show

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


def coordinate_presets(sheet: Sheet) -> list[CoordinateSystem]:
    """Lists coordinate systems that suit a sheet, its default first, without repeats.

    >>> from showpy.sheet.templates import get_sheet
    >>> [p.kind.value for p in coordinate_presets(get_sheet("sheet:proscenium-stage"))][:2]
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


def _origin(sheet: Sheet, system: CoordinateSystem) -> Point:
    if isinstance(system.origin, str):
        return landmark_anchor(landmark(sheet, system.origin))
    return system.origin


def set_coordinates(show: "Show", system: CoordinateSystem) -> "Show":
    """Returns the show with a new coordinate system; stored positions never move.

    >>> from showpy.examples import two_set_show
    >>> set_coordinates(two_set_show(), CoordinateSystem(units="ft")).coordinates.units
    'ft'
    """
    if not system.precision > 0:
        raise SchemaError(
            f"precision must be above 0, got {system.precision}",
            hint="use a rounding step such as 0.01",
            context={"precision": system.precision},
        )
    convert(1.0, system.units, "m")
    _origin(show.sheet, system)
    return show.replace(coordinates=system)


def _words(sheet: Sheet, system: CoordinateSystem) -> dict[str, str]:
    base = STAGE_WORDS if system.kind == CoordinateKind.STAGE else GENERIC_WORDS
    if isinstance(system.origin, str):
        name = landmark(sheet, system.origin).name
        refs = (name, name)
    elif system.origin == (0.0, 0.0) and system.audience_side == "front":
        refs = DEFAULT_REFS
    else:
        refs = (GENERIC_REF, GENERIC_REF)
    return {**base, "across_ref": refs[0], "depth_ref": refs[1], **system.labels}


def _decimals(precision: float) -> int:
    exponent = Decimal(repr(precision)).normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _local(sheet: Sheet, system: CoordinateSystem, p: Point) -> tuple[float, float]:
    ox, oy = _origin(sheet, system)
    right, ahead = FRAMES[system.audience_side]
    dx, dy = p[0] - ox, p[1] - oy
    return dx * right[0] + dy * right[1], dx * ahead[0] + dy * ahead[1]


def _part(
    value_m: float, system: CoordinateSystem, positive: str, negative: str
) -> tuple[str, str]:
    shown = round_to(convert(value_m, "m", system.units), system.precision)
    text = f"{abs(shown):.{_decimals(system.precision)}f}"
    return text, negative if shown < 0 else positive


def to_coords(show: "Show", p: Point, style: CoordStyle = "long") -> str:
    """Words a point in the show's coordinate system, rounded to its precision.

    Args:
        show: Show whose sheet and coordinate system are used.
        p: Stage-plane point in meters.
        style: `long` for sentences, `short` for compact codes such as `R3.50 U2.00`.

    Returns:
        text: The worded position; from_coords() reads it back.

    >>> from showpy.examples import two_set_show
    >>> to_coords(two_set_show(), (3.5, 2.0))
    '3.50 m right of center, 2.00 m upstage of front edge'
    """
    system = show.coordinates
    words = _words(show.sheet, system)
    across, depth = _local(show.sheet, system, p)
    a_num, a_dir = _part(across, system, "right", "left")
    d_num, d_dir = _part(depth, system, "up", "down")
    if style == "short":
        return f"{words[a_dir + '_short']}{a_num} {words[d_dir + '_short']}{d_num}"
    unit = system.units
    across_text = f"{a_num} {unit} {words[a_dir]} of {words['across_ref']}"
    return f"{across_text}, {d_num} {unit} {words[d_dir]} of {words['depth_ref']}"


def _bad(token: str, text: str, hint: str) -> SchemaError:
    return SchemaError(
        f"cannot read {token!r} in {text!r}", hint=hint, context={"token": token, "text": text}
    )


def _meters(num: str, unit: str, text: str) -> float:
    try:
        return convert(float(num), unit, "m")
    except UnknownRefError as error:
        raise _bad(unit, text, error.hint) from error


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


def from_coords(show: "Show", text: str) -> Point:
    """Reads worded coordinates (long or short style, either part first) into a point.

    Long style may name any registered unit; short style uses the system's unit.

    >>> from showpy.examples import two_set_show
    >>> from_coords(two_set_show(), "L3.50 U2.00")
    (-3.5, 2.0)
    """
    system = show.coordinates
    words = _words(show.sheet, system)
    values: dict[Axis, float] = {}
    for token, (axis, value) in _parts(text.strip(), words, system.units):
        if axis in values:
            raise _bad(token, text, f"give the {axis} part once")
        values[axis] = value
    missing = [axis for axis in ("across", "depth") if axis not in values]
    if missing:
        raise _bad(text, text, f"add the {missing[0]} part")
    (ox, oy), (right, ahead) = _origin(show.sheet, system), FRAMES[system.audience_side]
    across, depth = values["across"], values["depth"]
    return (
        ox + across * right[0] + depth * ahead[0] + 0.0,
        oy + across * right[1] + depth * ahead[1] + 0.0,
    )
