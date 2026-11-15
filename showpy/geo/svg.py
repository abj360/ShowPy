"""
svg.py --- parses SVG path data into evaluable line, quadratic, cubic and elliptical pieces

Contains:
    LinePiece: evaluates a straight piece
    QuadPiece: evaluates a quadratic Bézier piece
    CubicPiece: evaluates a cubic Bézier piece
    ArcPiece: evaluates an elliptical arc piece in center parameterization
    Subpath: holds the pieces of one M…(Z) run and writes them back as absolute path data
    parse_path(): parses path data into subpaths, supporting M L H V C S Q T A Z
    _unpack_arc_flags(): splits packed arc flags into single digits
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from showpy.core.errors import SchemaError
from showpy.core.types import Point, Points

type FloatArray = npt.NDArray[np.float64]

TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")
COMMAND_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
SEPARATORS = re.compile(r"[\s,]+")
ARC_FLAG_SLOTS = (3, 4)


@dataclass(frozen=True)
class LinePiece:
    """Evaluates a straight piece.

    Attributes:
        start: First point.
        end: Last point.
    """

    start: Point
    end: Point

    def evaluate(self, u: FloatArray) -> Points:
        """Returns points at parameters 0..1."""
        a = np.asarray(self.start)
        return a + np.outer(u, np.asarray(self.end) - a)

    def to_d(self) -> str:
        """Returns the piece as an absolute L command."""
        return f"L{_fmt(self.end)}"


@dataclass(frozen=True)
class QuadPiece:
    """Evaluates a quadratic Bézier piece.

    Attributes:
        start: First point.
        control: Control point.
        end: Last point.
    """

    start: Point
    control: Point
    end: Point

    def evaluate(self, u: FloatArray) -> Points:
        """Returns points at parameters 0..1."""
        w = u[:, None]
        p0, p1, p2 = (np.asarray(p) for p in (self.start, self.control, self.end))
        result: Points = (1 - w) ** 2 * p0 + 2 * (1 - w) * w * p1 + w**2 * p2
        return result

    def to_d(self) -> str:
        """Returns the piece as an absolute Q command."""
        return f"Q{_fmt(self.control)} {_fmt(self.end)}"


@dataclass(frozen=True)
class CubicPiece:
    """Evaluates a cubic Bézier piece.

    Attributes:
        start: First point.
        c1: First control point.
        c2: Second control point.
        end: Last point.
    """

    start: Point
    c1: Point
    c2: Point
    end: Point

    def evaluate(self, u: FloatArray) -> Points:
        """Returns points at parameters 0..1."""
        w = u[:, None]
        p0, p1, p2, p3 = (np.asarray(p) for p in (self.start, self.c1, self.c2, self.end))
        result: Points = (
            (1 - w) ** 3 * p0 + 3 * (1 - w) ** 2 * w * p1 + 3 * (1 - w) * w**2 * p2 + w**3 * p3
        )
        return result

    def to_d(self) -> str:
        """Returns the piece as an absolute C command."""
        return f"C{_fmt(self.c1)} {_fmt(self.c2)} {_fmt(self.end)}"


@dataclass(frozen=True)
class ArcPiece:
    """Evaluates an elliptical arc in center parameterization (SVG spec F.6.5).

    Attributes:
        start: First point.
        end: Last point.
        center: Ellipse center.
        rx: Radius along the rotated x axis.
        ry: Radius along the rotated y axis.
        phi_deg: Rotation of the ellipse axes.
        theta1_deg: Start angle on the unit ellipse.
        dtheta_deg: Signed sweep on the unit ellipse.
    """

    start: Point
    end: Point
    center: Point
    rx: float
    ry: float
    phi_deg: float
    theta1_deg: float
    dtheta_deg: float

    def evaluate(self, u: FloatArray) -> Points:
        """Returns points at parameters 0..1."""
        theta = np.radians(self.theta1_deg + self.dtheta_deg * u)
        phi = math.radians(self.phi_deg)
        x = self.rx * np.cos(theta)
        y = self.ry * np.sin(theta)
        return np.stack(
            [
                self.center[0] + math.cos(phi) * x - math.sin(phi) * y,
                self.center[1] + math.sin(phi) * x + math.cos(phi) * y,
            ],
            axis=1,
        )

    def to_d(self) -> str:
        """Returns the piece as an absolute A command."""
        large = int(abs(self.dtheta_deg) > 180)
        sweep = int(self.dtheta_deg > 0)
        return f"A{self.rx!r} {self.ry!r} {self.phi_deg!r} {large} {sweep} {_fmt(self.end)}"


type Piece = LinePiece | QuadPiece | CubicPiece | ArcPiece


@dataclass(frozen=True)
class Subpath:
    """Holds the pieces of one subpath.

    Attributes:
        start: Point of the opening M command.
        pieces: Drawn pieces in order.
        closed: Whether the subpath ended with Z.
    """

    start: Point
    pieces: tuple[Piece, ...]
    closed: bool

    def to_d(self) -> str:
        """Returns the subpath as absolute path data."""
        body = " ".join(piece.to_d() for piece in self.pieces)
        return f"M{_fmt(self.start)} {body}{' Z' if self.closed else ''}"


def _fmt(p: Point) -> str:
    return f"{p[0]!r} {p[1]!r}"


def _tokenize(d: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    for match in TOKEN.finditer(d):
        gap = d[position : match.start()]
        if gap and not SEPARATORS.fullmatch(gap):
            raise SchemaError(
                f"bad path data near {gap.strip()!r}",
                hint="use SVG commands M L H V C S Q T A Z",
                context={"position": position},
            )
        tokens.append(match.group(0))
        position = match.end()
    if d[position:].strip(" \t\n\r,"):
        raise SchemaError(
            f"bad path data near {d[position:].strip()!r}",
            hint="use SVG commands M L H V C S Q T A Z",
            context={"position": position},
        )
    return tokens


class _PathBuilder:
    """Accumulates pieces while walking path commands."""

    def __init__(self) -> None:
        self.subpaths: list[Subpath] = []
        self.pieces: list[Piece] = []
        self.start: Point = (0.0, 0.0)
        self.current: Point = (0.0, 0.0)
        self.last_control: Point | None = None
        self.last_command = ""

    def flush(self, closed: bool) -> None:
        if self.pieces:
            self.subpaths.append(Subpath(self.start, tuple(self.pieces), closed))
        self.pieces = []

    def add(self, piece: Piece, control: Point | None = None) -> None:
        self.pieces.append(piece)
        self.current = piece.end
        self.last_control = control

    def apply(self, command: str, args: list[float]) -> None:
        upper = command.upper()
        relative = command.islower()
        ox, oy = self.current if relative else (0.0, 0.0)
        handler = HANDLERS[upper]
        handler(self, args, ox, oy)
        self.last_command = upper


def _move(b: _PathBuilder, a: list[float], ox: float, oy: float) -> None:
    b.flush(closed=False)
    b.start = b.current = (a[0] + ox, a[1] + oy)
    b.last_control = None


def _line(b: _PathBuilder, a: list[float], ox: float, oy: float) -> None:
    b.add(LinePiece(b.current, (a[0] + ox, a[1] + oy)))


def _horizontal(b: _PathBuilder, a: list[float], ox: float, _oy: float) -> None:
    b.add(LinePiece(b.current, (a[0] + ox, b.current[1])))


def _vertical(b: _PathBuilder, a: list[float], _ox: float, oy: float) -> None:
    b.add(LinePiece(b.current, (b.current[0], a[0] + oy)))


def _reflect(b: _PathBuilder, kinds: str) -> Point:
    if b.last_control is None or b.last_command not in kinds:
        return b.current
    return 2 * b.current[0] - b.last_control[0], 2 * b.current[1] - b.last_control[1]


def _cubic(b: _PathBuilder, a: list[float], ox: float, oy: float) -> None:
    c2 = (a[2] + ox, a[3] + oy)
    b.add(CubicPiece(b.current, (a[0] + ox, a[1] + oy), c2, (a[4] + ox, a[5] + oy)), c2)


def _smooth_cubic(b: _PathBuilder, a: list[float], ox: float, oy: float) -> None:
    c2 = (a[0] + ox, a[1] + oy)
    b.add(CubicPiece(b.current, _reflect(b, "CS"), c2, (a[2] + ox, a[3] + oy)), c2)


def _quad(b: _PathBuilder, a: list[float], ox: float, oy: float) -> None:
    control = (a[0] + ox, a[1] + oy)
    b.add(QuadPiece(b.current, control, (a[2] + ox, a[3] + oy)), control)


def _smooth_quad(b: _PathBuilder, a: list[float], ox: float, oy: float) -> None:
    control = _reflect(b, "QT")
    b.add(QuadPiece(b.current, control, (a[0] + ox, a[1] + oy)), control)


def _arc(b: _PathBuilder, a: list[float], ox: float, oy: float) -> None:
    end = (a[5] + ox, a[6] + oy)
    if math.dist(b.current, end) == 0:
        return
    if a[0] == 0 or a[1] == 0:
        b.add(LinePiece(b.current, end))
        return
    b.add(_arc_piece(b.current, end, abs(a[0]), abs(a[1]), a[2], bool(a[3]), bool(a[4])))


def _close(b: _PathBuilder, _a: list[float], _ox: float, _oy: float) -> None:
    if math.dist(b.current, b.start) > 0:
        b.add(LinePiece(b.current, b.start))
    b.flush(closed=True)
    b.current = b.start


HANDLERS: dict[str, Callable[[_PathBuilder, list[float], float, float], None]] = {
    "M": _move,
    "L": _line,
    "H": _horizontal,
    "V": _vertical,
    "C": _cubic,
    "S": _smooth_cubic,
    "Q": _quad,
    "T": _smooth_quad,
    "A": _arc,
    "Z": _close,
}


def _arc_piece(
    p1: Point, p2: Point, rx: float, ry: float, phi_deg: float, large: bool, sweep: bool
) -> ArcPiece:
    phi = math.radians(phi_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx, dy = (p1[0] - p2[0]) / 2, (p1[1] - p2[1]) / 2
    x1 = cos_phi * dx + sin_phi * dy
    y1 = -sin_phi * dx + cos_phi * dy
    scale = x1**2 / rx**2 + y1**2 / ry**2
    if scale > 1:
        rx, ry = rx * math.sqrt(scale), ry * math.sqrt(scale)
    numerator = max(rx**2 * ry**2 - rx**2 * y1**2 - ry**2 * x1**2, 0.0)
    factor = math.sqrt(numerator / (rx**2 * y1**2 + ry**2 * x1**2))
    factor = -factor if large == sweep else factor
    cx1, cy1 = factor * rx * y1 / ry, -factor * ry * x1 / rx
    center = (
        cos_phi * cx1 - sin_phi * cy1 + (p1[0] + p2[0]) / 2,
        sin_phi * cx1 + cos_phi * cy1 + (p1[1] + p2[1]) / 2,
    )
    theta1 = _angle((1, 0), ((x1 - cx1) / rx, (y1 - cy1) / ry))
    dtheta = _angle(((x1 - cx1) / rx, (y1 - cy1) / ry), ((-x1 - cx1) / rx, (-y1 - cy1) / ry))
    if not sweep and dtheta > 0:
        dtheta -= 360.0
    elif sweep and dtheta < 0:
        dtheta += 360.0
    return ArcPiece(p1, p2, center, rx, ry, phi_deg, theta1, dtheta)


def _angle(u: tuple[float, float], v: tuple[float, float]) -> float:
    return math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]))


def _unpack_arc_flags(tokens: list[str], index: int) -> list[str]:
    """Splits packed arc flags such as `011` into `0`, `1`, `1`; SVG flags are single digits."""
    out = list(tokens)
    for slot in ARC_FLAG_SLOTS:
        position = index + slot
        if position >= len(out):
            return out
        token = out[position]
        if len(token) > 1 and token[0] in "01" and token[1] not in ".eE":
            out[position : position + 1] = [token[0], token[1:]]
    return out


def parse_path(d: str) -> list[Subpath]:
    """Parses SVG path data into subpaths of evaluable pieces.

    >>> [len(s.pieces) for s in parse_path("M0 0 L1 0 L1 1 Z")]
    [3]
    """
    builder = _PathBuilder()
    tokens = _tokenize(d)
    index = 0
    command = ""
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        elif not command:
            raise SchemaError("path data must start with M", hint="begin with M x y")
        arity = COMMAND_ARITY[command.upper()]
        if command in "Aa":
            tokens = _unpack_arc_flags(tokens, index)
        args = [float(t) for t in tokens[index : index + arity]]
        if len(args) < arity or any(t.isalpha() for t in tokens[index : index + arity]):
            raise SchemaError(
                f"{command} needs {arity} numbers",
                hint="check the path data argument counts",
                context={"command": command},
            )
        builder.apply(command, args)
        index += arity
        if command in "Mm":
            command = "l" if command == "m" else "L"
        if arity == 0:
            command = ""
    builder.flush(closed=False)
    return builder.subpaths
