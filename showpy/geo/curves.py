"""
curves.py --- concrete curve models: exact lines and arcs, sampled splines, joins and trims

Arc angles use the math convention: degrees counter-clockwise from +x; a positive sweep
turns counter-clockwise. Facings (0° = audience) are a separate convention in core.units.

Contains:
    CurveModel: adds substring() and reversed() to every concrete curve
    Segment: draws a straight line between two points
    Polyline: connects points with straight pieces, optionally closed
    ArcCurve: draws a circular arc; the sweep sign gives the direction
    CircleCurve: draws a full circle in either direction
    EllipseCurve: draws a rotated oval
    SplineCurve: passes a centripetal Catmull–Rom spline through points
    MAX_BEZIER_DEGREE: bounds the degree of one Bézier curve
    BezierCurve: draws a Bézier curve of any degree from control points
    SvgCurve: evaluates one SVG subpath under an affine transform
    SpiralCurve: draws an Archimedean spiral
    CompositeCurve: joins curves end to end
    TrimmedCurve: views a piece of another curve, reversed when s0 > s1
    AnyCurve: discriminates every curve model by its `kind`
    curve_from_construction(): rebuilds a curve from its JSON recipe
"""

import math
from functools import cached_property
from numbers import Real
from typing import Annotated, Any, Literal, cast, override

import numpy as np
from pydantic import Field, TypeAdapter, field_validator, model_validator
from scipy.special import comb

from showpy.core.caps import MAX_TABLE, require_size
from showpy.core.errors import GeometryError
from showpy.core.types import Degrees, Meters, Point, Points, PositiveFinite
from showpy.core.validation import validate_adapter
from showpy.geo.checks import require_finite_as, require_positive
from showpy.geo.curve_base import CurveBase, FloatArray, _unit
from showpy.geo.svg import Subpath, parse_path

SPLINE_SAMPLES_PER_SEGMENT = 256
SPIRAL_SAMPLES_PER_TURN = 1024
SVG_SAMPLES_PER_PIECE = 256
KNOT_FLOOR = 1e-9
# Parsing every stroke of every bundled font gives only line and cubic pieces, so the largest
# degree is 3 (measured 2026-11-21); the cap is 10 times that.
MAX_BEZIER_DEGREE = 30
# A closed composite's ends and trim bounds may differ by float error in arc-length tables.
CONTINUITY_TOL_M = 1e-6
TRANSFORM_ROWS = 3
IDENTITY_3X3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class CurveModel(CurveBase):
    """Adds trimming and reversal, which build TrimmedCurve views of the curve."""

    def substring(self, s0: Meters, s1: Meters) -> "TrimmedCurve":
        """Returns the piece between arc lengths s0 and s1, reversed when s0 > s1."""
        return TrimmedCurve(base=_as_any_curve(self), s0=s0, s1=s1)

    def reversed(self) -> "TrimmedCurve":
        """Returns the same curve travelled end to start."""
        return TrimmedCurve(base=_as_any_curve(self), s0=self.length(), s1=0.0)


class Segment(CurveModel):
    """Draws a straight line from `a` to `b`.

    Attributes:
        a: Start point.
        b: End point.
    """

    kind: Literal["segment"] = "segment"
    a: Point
    b: Point
    closed: Literal[False] = False

    def length(self) -> Meters:
        """Returns the distance from a to b."""
        return math.dist(self.a, self.b)

    def _points(self, s: FloatArray) -> Points:
        total = self.length()
        u = s / total if total > 0 else np.zeros_like(s)
        a = np.asarray(self.a)
        return a + np.outer(u, np.asarray(self.b) - a)

    def _tangents(self, s: FloatArray) -> Points:
        direction = np.asarray(self.b) - np.asarray(self.a)
        return _unit(np.tile(direction, (len(s), 1)))


class Polyline(CurveModel):
    """Connects points with straight pieces.

    Attributes:
        points: Vertices in order; a closed polyline does not repeat the first one.
        closed: Whether a final piece returns to the first vertex.
    """

    kind: Literal["polyline"] = "polyline"
    points: tuple[Point, ...]

    @model_validator(mode="after")
    def _needs_two_points(self) -> "Polyline":
        if len(self.points) < 2:
            raise GeometryError(
                "a polyline needs at least 2 points",
                hint="pass two or more points",
                context={"points": len(self.points)},
            )
        return self

    @cached_property
    def _vertices(self) -> Points:
        pts = np.asarray(self.points, dtype=float)
        return np.vstack([pts, pts[:1]]) if self.closed else pts

    @cached_property
    def _cumulative(self) -> FloatArray:
        pieces = np.linalg.norm(np.diff(self._vertices, axis=0), axis=1)
        return np.concatenate([[0.0], np.cumsum(pieces)])

    def length(self) -> Meters:
        """Returns the summed length of every piece."""
        return float(self._cumulative[-1])

    def _piece_index(self, s: FloatArray) -> np.ndarray[Any, np.dtype[np.intp]]:
        index = np.searchsorted(self._cumulative, s, side="right") - 1
        return np.clip(index, 0, len(self._cumulative) - 2)

    def _points(self, s: FloatArray) -> Points:
        x = np.interp(s, self._cumulative, self._vertices[:, 0])
        y = np.interp(s, self._cumulative, self._vertices[:, 1])
        return np.stack([x, y], axis=1)

    def _tangents(self, s: FloatArray) -> Points:
        index = self._piece_index(s)
        return _unit(self._vertices[index + 1] - self._vertices[index])

    def _breaks(self) -> FloatArray:
        return self._cumulative

    @override
    def to_polyline(self, tol: float = 0.01) -> Points:
        """Returns the vertices themselves, closing the loop when closed."""
        require_finite_as(GeometryError, tol, name="tol", low=0.0, low_open=True)
        return self._vertices.copy()


def _arc_points(center: Point, radius: float, theta_deg: FloatArray) -> Points:
    theta = np.radians(theta_deg)
    return np.stack([center[0] + radius * np.cos(theta), center[1] + radius * np.sin(theta)], 1)


def _arc_tangents(theta_deg: FloatArray, sign: float) -> Points:
    theta = np.radians(theta_deg)
    return np.stack([-np.sin(theta) * sign, np.cos(theta) * sign], axis=1)


class ArcCurve(CurveModel):
    """Draws a circular arc.

    Attributes:
        center: Circle center.
        radius: Radius in meters, above 0.
        start_deg: Start angle, counter-clockwise from +x.
        sweep_deg: Signed sweep; positive turns counter-clockwise.
    """

    kind: Literal["arc"] = "arc"
    center: Point
    radius: Meters
    start_deg: Degrees
    sweep_deg: Degrees
    closed: Literal[False] = False

    @model_validator(mode="after")
    def _non_degenerate(self) -> "ArcCurve":
        _require_positive("radius", self.radius)
        if self.sweep_deg == 0:
            raise GeometryError("arc sweep is zero", hint="give a non-zero sweep_deg")
        return self

    def length(self) -> Meters:
        """Returns radius times the absolute sweep in radians."""
        return self.radius * math.radians(abs(self.sweep_deg))

    def _theta(self, s: FloatArray) -> FloatArray:
        theta: FloatArray = self.start_deg + self.sweep_deg * s / self.length()
        return theta

    def _points(self, s: FloatArray) -> Points:
        return _arc_points(self.center, self.radius, self._theta(s))

    def _tangents(self, s: FloatArray) -> Points:
        return _arc_tangents(self._theta(s), math.copysign(1.0, self.sweep_deg))


class CircleCurve(CurveModel):
    """Draws a full circle, counter-clockwise unless `ccw` is False.

    Attributes:
        center: Circle center.
        radius: Radius in meters, above 0.
        start_deg: Angle of the start point, counter-clockwise from +x.
        ccw: Travel direction; mirroring a circle flips it.
    """

    kind: Literal["circle"] = "circle"
    center: Point
    radius: Meters
    start_deg: Degrees = 0.0
    ccw: bool = True
    closed: Literal[True] = True

    @model_validator(mode="after")
    def _positive_radius(self) -> "CircleCurve":
        _require_positive("radius", self.radius)
        return self

    def length(self) -> Meters:
        """Returns the circumference."""
        return 2 * math.pi * self.radius

    def _sign(self) -> float:
        return 1.0 if self.ccw else -1.0

    def _theta(self, s: FloatArray) -> FloatArray:
        theta: FloatArray = self.start_deg + self._sign() * 360.0 * s / self.length()
        return theta

    def _points(self, s: FloatArray) -> Points:
        return _arc_points(self.center, self.radius, self._theta(s))

    def _tangents(self, s: FloatArray) -> Points:
        return _arc_tangents(self._theta(s), self._sign())


class EllipseCurve(CurveModel):
    """Draws a counter-clockwise oval.

    Attributes:
        center: Ellipse center.
        rx: Semi-axis along the rotated x direction.
        ry: Semi-axis along the rotated y direction.
        rotation_deg: Counter-clockwise rotation of the axes.
    """

    kind: Literal["ellipse"] = "ellipse"
    center: Point
    rx: Meters
    ry: Meters
    rotation_deg: Degrees = 0.0
    closed: Literal[True] = True

    @model_validator(mode="after")
    def _positive_axes(self) -> "EllipseCurve":
        _require_positive("rx", self.rx)
        _require_positive("ry", self.ry)
        return self

    def _native(self, t: FloatArray) -> Points:
        angle = 2 * np.pi * t
        local = np.stack([self.rx * np.cos(angle), self.ry * np.sin(angle)], axis=1)
        result: Points = _rotate(local, self.rotation_deg) + np.asarray(self.center)
        return result


class SplineCurve(CurveModel):
    """Passes a centripetal Catmull–Rom spline through every point.

    Attributes:
        points: Points the curve passes through, in order.
        alpha: Knot exponent; 0.5 (centripetal) avoids cusps and self-loops.
        closed: Whether the spline loops back to the first point.
    """

    kind: Literal["spline"] = "spline"
    points: tuple[Point, ...]
    alpha: float = 0.5

    @model_validator(mode="after")
    def _needs_two_points(self) -> "SplineCurve":
        if len(self.points) < 2:
            raise GeometryError("a spline needs at least 2 points", hint="pass two or more")
        return self

    @cached_property
    def _controls(self) -> Points:
        pts = np.asarray(self.points, dtype=float)
        if self.closed:
            return np.vstack([pts[-1:], pts, pts[:2]])
        return np.vstack([2 * pts[0] - pts[1], pts, 2 * pts[-1] - pts[-2]])

    def _segments(self) -> int:
        return len(self.points) if self.closed else len(self.points) - 1

    def _table_size(self) -> int:
        return SPLINE_SAMPLES_PER_SEGMENT * self._segments() + 1

    def _native(self, t: FloatArray) -> Points:
        n = self._segments()
        index = np.minimum((t * n).astype(int), n - 1)
        u = t * n - index
        out = np.empty((len(t), 2))
        for i in np.unique(index):
            mask = index == i
            out[mask] = _catmull_rom(self._controls[i : i + 4], u[mask], self.alpha)
        return out

    def _breaks(self) -> FloatArray:
        t, s = self._table
        return np.interp(np.linspace(0.0, 1.0, self._segments() + 1), t, s)


def _catmull_rom(p: Points, u: FloatArray, alpha: float) -> Points:
    gaps = np.maximum(np.linalg.norm(np.diff(p, axis=0), axis=1) ** alpha, KNOT_FLOOR)
    t0, t1, t2, t3 = 0.0, gaps[0], gaps[0] + gaps[1], gaps[0] + gaps[1] + gaps[2]
    t = (t1 + u * (t2 - t1))[:, None]
    a1 = (t1 - t) / (t1 - t0) * p[0] + (t - t0) / (t1 - t0) * p[1]
    a2 = (t2 - t) / (t2 - t1) * p[1] + (t - t1) / (t2 - t1) * p[2]
    a3 = (t3 - t) / (t3 - t2) * p[2] + (t - t2) / (t3 - t2) * p[3]
    b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
    b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
    result: Points = (t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2
    return result


class BezierCurve(CurveModel):
    """Draws a Bézier curve of degree len(control) - 1.

    Attributes:
        control: Control points; the curve starts at the first and ends at the last.
    """

    kind: Literal["bezier"] = "bezier"
    control: tuple[Point, ...]
    closed: Literal[False] = False

    @model_validator(mode="after")
    def _needs_two_points(self) -> "BezierCurve":
        if len(self.control) < 2:
            raise GeometryError("a Bézier curve needs 2+ control points", hint="add points")
        require_size(
            len(self.control) - 1, MAX_BEZIER_DEGREE, what="Bézier degree", error=GeometryError
        )
        return self

    def _native(self, t: FloatArray) -> Points:
        degree = len(self.control) - 1
        k = np.arange(degree + 1)
        basis = comb(degree, k) * t[:, None] ** k * (1 - t[:, None]) ** (degree - k)
        result: Points = basis @ np.asarray(self.control, dtype=float)
        return result


class SvgCurve(CurveModel):
    """Evaluates one SVG subpath, mapped onto the stage plane by an affine transform.

    Attributes:
        d: Path data for exactly one subpath, in absolute commands.
        transform: 3x3 affine matrix from SVG units to stage meters.
        closed: Whether the subpath ends with Z.
    """

    kind: Literal["svg"] = "svg"
    d: str
    transform: tuple[tuple[float, float, float], ...] = IDENTITY_3X3

    @model_validator(mode="after")
    def _consistent(self) -> "SvgCurve":
        if len(self.transform) != TRANSFORM_ROWS:
            raise GeometryError(
                f"transform needs {TRANSFORM_ROWS} rows, got {len(self.transform)}",
                hint="pass a 3x3 affine matrix",
                context={"rows": len(self.transform)},
            )
        if self.closed != self._subpath.closed:
            raise GeometryError(
                f"closed={self.closed} contradicts the path, which "
                f"{'ends' if self._subpath.closed else 'does not end'} with Z",
                hint=f"pass closed={self._subpath.closed}",
                context={"closed": self.closed, "path_closed": self._subpath.closed},
            )
        return self

    @cached_property
    def _subpath(self) -> Subpath:
        subpaths = parse_path(self.d)
        if len(subpaths) != 1:
            raise GeometryError(
                "an SvgCurve holds exactly one subpath",
                hint="split the path with svg_outline",
                context={"subpaths": len(subpaths)},
            )
        return subpaths[0]

    def _table_size(self) -> int:
        return SVG_SAMPLES_PER_PIECE * len(self._subpath.pieces) + 1

    def _native(self, t: FloatArray) -> Points:
        pieces = self._subpath.pieces
        n = len(pieces)
        index = np.minimum((t * n).astype(int), n - 1)
        u = t * n - index
        out = np.empty((len(t), 2))
        order = np.argsort(index, kind="stable")
        present, starts = np.unique(index[order], return_index=True)
        stops = [*starts[1:].tolist(), len(t)]
        for i, start, stop in zip(present.tolist(), starts.tolist(), stops, strict=True):
            rows = order[start:stop]
            out[rows] = pieces[i].evaluate(u[rows])
        matrix = np.asarray(self.transform)
        result: Points = out @ matrix[:2, :2].T + matrix[:2, 2]
        return result

    def _breaks(self) -> FloatArray:
        t, s = self._table
        return np.interp(np.linspace(0.0, 1.0, len(self._subpath.pieces) + 1), t, s)


class SpiralCurve(CurveModel):
    """Draws an Archimedean spiral whose radius changes linearly with angle.

    Attributes:
        center: Spiral center.
        r0: Start radius.
        r1: End radius.
        turns: Number of counter-clockwise turns, above 0.
        start_deg: Start angle, counter-clockwise from +x.
    """

    kind: Literal["spiral"] = "spiral"
    center: Point
    r0: Meters
    r1: Meters
    turns: PositiveFinite
    start_deg: Degrees = 0.0
    closed: Literal[False] = False

    @field_validator("turns", mode="before")
    @classmethod
    def _turns_are_finite(cls, value: Any) -> Any:  # Any: raw input before pydantic coerces it
        if isinstance(value, Real) and not isinstance(value, bool):
            _require_turns(value)
        return value

    @model_validator(mode="after")
    def _valid(self) -> "SpiralCurve":
        require_size(
            self._table_size(), MAX_TABLE, what="spiral table samples", error=GeometryError
        )
        if min(self.r0, self.r1) < 0 or max(self.r0, self.r1) == 0:
            raise GeometryError("spiral radii must be >= 0 and not both 0", hint="fix r0/r1")
        return self

    def _table_size(self) -> int:
        return int(SPIRAL_SAMPLES_PER_TURN * math.ceil(self.turns)) + 1

    def _native(self, t: FloatArray) -> Points:
        theta = np.radians(self.start_deg + 360.0 * self.turns * t)
        radius = self.r0 + (self.r1 - self.r0) * t
        offsets = np.stack([radius * np.cos(theta), radius * np.sin(theta)], axis=1)
        return np.asarray(self.center) + offsets


class CompositeCurve(CurveModel):
    """Joins curves end to end into one curve.

    Attributes:
        parts: Curves in travel order.
        closed: Whether the last part ends on the first part's start.
    """

    kind: Literal["composite"] = "composite"
    parts: tuple["AnyCurve", ...]

    @model_validator(mode="after")
    def _needs_parts(self) -> "CompositeCurve":
        if not self.parts:
            raise GeometryError("a composite curve needs parts", hint="pass one or more curves")
        if self.closed:
            _require_meeting(
                _end_of(self.parts[-1]), _start_of(self.parts[0]), "last and first part"
            )
        return self

    @cached_property
    def _offsets(self) -> FloatArray:
        return np.concatenate([[0.0], np.cumsum([part.length() for part in self.parts])])

    def length(self) -> Meters:
        """Returns the summed part lengths."""
        return float(self._offsets[-1])

    def _dispatch(self, s: FloatArray, fn: str) -> Points:
        index = np.clip(np.searchsorted(self._offsets, s, side="right") - 1, 0, len(self.parts) - 1)
        out = np.empty((len(s), 2))
        for i in np.unique(index):
            mask = index == i
            local = s[mask] - self._offsets[i]
            out[mask] = getattr(self.parts[i], fn)(self.parts[i]._wrap(local))
        return out

    def _points(self, s: FloatArray) -> Points:
        return self._dispatch(s, "_points")

    def _tangents(self, s: FloatArray) -> Points:
        return self._dispatch(s, "_tangents")

    def _breaks(self) -> FloatArray:
        inner = [
            part._breaks() + offset for part, offset in zip(self.parts, self._offsets, strict=False)
        ]
        return np.unique(np.concatenate([self._offsets, *inner]))


def _start_of(curve: "CurveModel") -> Point:
    start = curve.point_at(0.0)
    return (float(start[0]), float(start[1]))


def _end_of(curve: "CurveModel") -> Point:
    end = curve.point_at(curve.length())
    return (float(end[0]), float(end[1]))


def _require_meeting(end: Point, start: Point, where: str) -> None:
    gap = math.dist(end, start)
    if gap > CONTINUITY_TOL_M:
        raise GeometryError(
            f"{where} do not meet: {gap} m apart",
            hint="start each part where the previous one ends, or drop closed=True",
            context={"gap_m": gap, "end": end, "start": start},
        )


class TrimmedCurve(CurveModel):
    """Views the piece of `base` between arc lengths s0 and s1, reversed when s0 > s1.

    Attributes:
        base: Curve being trimmed.
        s0: Arc length on `base` where this curve starts.
        s1: Arc length on `base` where this curve ends.
    """

    kind: Literal["trimmed"] = "trimmed"
    base: "AnyCurve"
    s0: Meters
    s1: Meters
    closed: Literal[False] = False

    @model_validator(mode="after")
    def _inside_open_base(self) -> "TrimmedCurve":
        if self.base.closed:
            return self
        total = self.base.length()
        for name, value in (("s0", self.s0), ("s1", self.s1)):
            if not -CONTINUITY_TOL_M <= value <= total + CONTINUITY_TOL_M:
                raise GeometryError(
                    f"{name}={value} is outside the base curve [0, {total}]",
                    hint=f"give {name} between 0 and the base length",
                    context={name: value, "length": total},
                )
        return self

    def length(self) -> Meters:
        """Returns |s1 - s0|."""
        return abs(self.s1 - self.s0)

    def _sign(self) -> float:
        return 1.0 if self.s1 >= self.s0 else -1.0

    def _points(self, s: FloatArray) -> Points:
        return self.base._points(self.base._wrap(self.s0 + self._sign() * s))

    def _tangents(self, s: FloatArray) -> Points:
        return self._sign() * self.base._tangents(self.base._wrap(self.s0 + self._sign() * s))

    def _breaks(self) -> FloatArray:
        lo, hi = sorted((self.s0, self.s1))
        inner = self.base._breaks()
        inner = inner[(inner > lo) & (inner < hi)]
        return np.sort(np.abs(np.concatenate([[self.s0, self.s1], inner]) - self.s0))


type AnyCurve = Annotated[
    Segment
    | Polyline
    | ArcCurve
    | CircleCurve
    | EllipseCurve
    | SplineCurve
    | BezierCurve
    | SvgCurve
    | SpiralCurve
    | CompositeCurve
    | TrimmedCurve,
    Field(discriminator="kind"),
]

CURVE_TYPES = (
    Segment,
    Polyline,
    ArcCurve,
    CircleCurve,
    EllipseCurve,
    SplineCurve,
    BezierCurve,
    SvgCurve,
    SpiralCurve,
    CompositeCurve,
    TrimmedCurve,
)

CompositeCurve.model_rebuild()
TrimmedCurve.model_rebuild()
CURVE_ADAPTER: TypeAdapter[AnyCurve] = TypeAdapter(AnyCurve)


def _as_any_curve(curve: CurveModel) -> AnyCurve:
    if not isinstance(curve, CURVE_TYPES):
        raise GeometryError(
            f"{type(curve).__name__} is not a known curve kind",
            hint=f"use one of {', '.join(t.__name__ for t in CURVE_TYPES)}",
            context={"kind": getattr(curve, "kind", None)},
        )
    return curve


# Any: recipes are JSON objects whose fields depend on the curve kind.
def curve_from_construction(recipe: dict[str, Any]) -> AnyCurve:
    """Rebuilds a curve from the recipe returned by `construction()`.

    >>> curve_from_construction(Segment(a=(0, 0), b=(1, 0)).construction()).length()
    1.0
    """
    return validate_adapter(CURVE_ADAPTER, recipe, name="Curve")


def _require_turns(value: Real) -> None:
    # cast: Real is not float to mypy; require_finite_as accepts any real number, even huge ints.
    require_finite_as(GeometryError, cast("float", value), name="turns", low=0.0, low_open=True)


def _require_positive(name: str, value: float) -> None:
    require_positive(name, value)


def _rotate(points: Points, degrees: float) -> Points:
    rad = math.radians(degrees)
    c, s = math.cos(rad), math.sin(rad)
    result: Points = points @ np.array([[c, s], [-s, c]])
    return result
