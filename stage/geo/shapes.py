#!/usr/bin/env python3

"""
shapes.py --- constructors that validate input and build curves, outlines, letters and logos

Contains:
    line(): builds a Segment
    polyline(): builds a Polyline through points
    arc(): builds an ArcCurve
    arc_through(): builds the arc through three points
    arc_from_chord(): bows a chord into an arc by a sagitta
    circle(): builds a CircleCurve
    ellipse(): builds an EllipseCurve
    curve_through(): builds a smooth SplineCurve through points
    bezier(): builds a BezierCurve
    spiral(): builds a SpiralCurve
    rect(): builds a closed rectangle outline
    wedge(): builds a V whose apex leads toward a facing
    regular_polygon(): builds a closed regular polygon
    star(): builds a closed star outline
    svg_outline(): fits SVG path data into a box as SvgCurves
    text_outline(): spells text in a stroke font as polylines
    join(): chains curves end to end
    offset(): builds a parallel curve
"""

import math
import re

import numpy as np
import shapely

from stage.core.errors import GeometryError
from stage.core.types import Box2, Degrees, Meters, Point
from stage.core.units import facing_vector
from stage.geo.curve_base import Curve
from stage.geo.curves import (
    AnyCurve,
    ArcCurve,
    BezierCurve,
    CircleCurve,
    CompositeCurve,
    EllipseCurve,
    Polyline,
    Segment,
    SpiralCurve,
    SplineCurve,
    SvgCurve,
)
from stage.geo.svg import Subpath, parse_path
from stage.geo.text import ADVANCE, GLYPH_HEIGHT, glyph_strokes

JOIN_TOL = 1e-6
COLLINEAR_TOL = 1e-12
OFFSET_TOL = 0.001
SVG_FIT_TOL = 0.001
SVG_D_ATTRIBUTE = re.compile(r"\bd\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
MIN_POLYGON_SIDES = 3
MIN_STAR_POINTS = 2


def line(a: Point, b: Point) -> Segment:
    """Builds a straight line; the ends must differ.

    >>> line((0, 0), (3, 4)).length()
    5.0
    """
    if math.dist(a, b) == 0:
        raise GeometryError("line ends coincide", hint="give two different points")
    return Segment(a=a, b=b)


def polyline(*pts: Point, closed: bool = False) -> Polyline:
    """Builds connected straight pieces.

    >>> polyline((0, 0), (1, 0), (1, 1)).length()
    2.0
    """
    return Polyline(points=pts, closed=closed)


def arc(center: Point, radius: Meters, start_deg: Degrees, sweep_deg: Degrees) -> ArcCurve:
    """Builds a circular arc; angles count counter-clockwise from +x.

    >>> round(arc((0, 0), 1.0, 0.0, 180.0).length(), 4)
    3.1416
    """
    return ArcCurve(center=center, radius=radius, start_deg=start_deg, sweep_deg=sweep_deg)


def arc_through(a: Point, m: Point, b: Point) -> ArcCurve:
    """Builds the arc from `a` through `m` to `b`; raises on collinear points.

    >>> round(arc_through((-1, 0), (0, 1), (1, 0)).radius, 6)
    1.0
    """
    (ax, ay), (mx, my), (bx, by) = a, m, b
    det = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if abs(det) < COLLINEAR_TOL:
        raise GeometryError("arc points are collinear", hint="move the middle point off the line")
    sq_a, sq_m, sq_b = ax**2 + ay**2, mx**2 + my**2, bx**2 + by**2
    cx = (sq_a * (my - by) + sq_m * (by - ay) + sq_b * (ay - my)) / det
    cy = (sq_a * (bx - mx) + sq_m * (ax - bx) + sq_b * (mx - ax)) / det
    theta = [math.degrees(math.atan2(p[1] - cy, p[0] - cx)) for p in (a, m, b)]
    ccw_sweep = (theta[2] - theta[0]) % 360.0
    ccw_middle = (theta[1] - theta[0]) % 360.0
    sweep = ccw_sweep if ccw_middle < ccw_sweep else ccw_sweep - 360.0
    return arc((cx, cy), math.dist(a, (cx, cy)), theta[0], sweep)


def arc_from_chord(a: Point, b: Point, bulge: Meters) -> ArcCurve:
    """Bows the chord a→b by `bulge` meters; positive bows to the left of a→b.

    >>> arc_from_chord((-1, 0), (1, 0), 1.0).radius
    1.0
    """
    if bulge == 0:
        raise GeometryError("bulge is zero", hint="use line() for a straight chord")
    chord = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    length = float(np.linalg.norm(chord))
    if length == 0:
        raise GeometryError("chord ends coincide", hint="give two different points")
    left = np.array([-chord[1], chord[0]]) / length
    apex = (np.asarray(a) + np.asarray(b)) / 2 + left * bulge
    return arc_through(a, (float(apex[0]), float(apex[1])), b)


def circle(center: Point, radius: Meters, start_deg: Degrees = 0.0) -> CircleCurve:
    """Builds a counter-clockwise circle.

    >>> round(circle((0, 0), 1.0).length(), 4)
    6.2832
    """
    return CircleCurve(center=center, radius=radius, start_deg=start_deg)


def ellipse(center: Point, rx: Meters, ry: Meters, rotation_deg: Degrees = 0.0) -> EllipseCurve:
    """Builds an oval.

    >>> ellipse((0, 0), 2.0, 1.0).closed
    True
    """
    return EllipseCurve(center=center, rx=rx, ry=ry, rotation_deg=rotation_deg)


def curve_through(*pts: Point, closed: bool = False, alpha: float = 0.5) -> SplineCurve:
    """Builds a smooth centripetal Catmull–Rom curve through every point.

    >>> curve_through((0, 0), (1, 1), (2, 0)).length() > 2.8
    True
    """
    return SplineCurve(points=pts, closed=closed, alpha=alpha)


def bezier(*control: Point) -> BezierCurve:
    """Builds a Bézier curve from control points.

    >>> bezier((0, 0), (1, 0)).length()
    1.0
    """
    return BezierCurve(control=control)


def spiral(
    center: Point, r0: Meters, r1: Meters, turns: float, start_deg: Degrees = 0.0
) -> SpiralCurve:
    """Builds an Archimedean spiral from radius r0 to r1.

    >>> spiral((0, 0), 1.0, 2.0, 1.0).closed
    False
    """
    return SpiralCurve(center=center, r0=r0, r1=r1, turns=turns, start_deg=start_deg)


def _rotated(center: Point, local: list[tuple[float, float]], rotation_deg: Degrees) -> Polyline:
    rad = math.radians(rotation_deg)
    c, s = math.cos(rad), math.sin(rad)
    pts = tuple((center[0] + c * x - s * y, center[1] + s * x + c * y) for x, y in local)
    return Polyline(points=pts, closed=True)


def rect(center: Point, w: Meters, d: Meters, rotation_deg: Degrees = 0.0) -> Polyline:
    """Builds a closed rectangle, corners counter-clockwise from front-left.

    >>> rect((0, 0), 4, 2).length()
    12.0
    """
    if w <= 0 or d <= 0:
        raise GeometryError("rectangle sides must be above 0", hint="give positive w and d")
    half_w, half_d = w / 2, d / 2
    corners = [(-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d)]
    return _rotated(center, corners, rotation_deg)


def wedge(apex: Point, length: Meters, spread_deg: Degrees, facing_deg: Degrees = 0.0) -> Polyline:
    """Builds a V: the apex leads toward `facing_deg`, the arms trail behind it.

    >>> wedge((0, 0), 2.0, 90.0).points[1]
    (0.0, 0.0)
    """
    if length <= 0 or not 0 < spread_deg < 360:
        raise GeometryError("wedge needs length > 0 and 0 < spread < 360", hint="fix the wedge")
    arms = []
    for side in (-1, 1):
        back = facing_vector(facing_deg + 180.0 + side * spread_deg / 2)
        arms.append((apex[0] + back[0] * length, apex[1] + back[1] * length))
    return Polyline(points=(arms[0], (float(apex[0]), float(apex[1])), arms[1]))


def regular_polygon(center: Point, radius: Meters, n: int, rotation_deg: Degrees = 0.0) -> Polyline:
    """Builds a closed regular polygon whose first vertex points upstage (+y) before rotation.

    >>> len(regular_polygon((0, 0), 1.0, 5).points)
    5
    """
    if n < MIN_POLYGON_SIDES or radius <= 0:
        raise GeometryError("polygon needs n >= 3 and radius > 0", hint="fix n or radius")
    angles = [math.radians(90.0 + 360.0 * k / n) for k in range(n)]
    local = [(radius * math.cos(t), radius * math.sin(t)) for t in angles]
    return _rotated(center, local, rotation_deg)


def star(
    center: Point, r_outer: Meters, r_inner: Meters, n: int, rotation_deg: Degrees = 0.0
) -> Polyline:
    """Builds a closed star with `n` points, alternating outer and inner vertices.

    >>> len(star((0, 0), 2.0, 1.0, 5).points)
    10
    """
    if n < MIN_STAR_POINTS or not 0 < r_inner < r_outer:
        raise GeometryError("star needs n >= 2 and 0 < r_inner < r_outer", hint="fix the radii")
    local = []
    for k in range(2 * n):
        radius = r_outer if k % 2 == 0 else r_inner
        t = math.radians(90.0 + 180.0 * k / n)
        local.append((radius * math.cos(t), radius * math.sin(t)))
    return _rotated(center, local, rotation_deg)


def _svg_subpaths(svg: str) -> list[Subpath]:
    found = SVG_D_ATTRIBUTE.findall(svg)
    sources = [double or single for double, single in found] if found else [svg]
    return [subpath for d in sources for subpath in parse_path(d)]


def _fit_matrix(
    bounds: tuple[float, float, float, float], fit: Box2, keep_aspect: bool, flip_y: bool
) -> tuple[tuple[float, float, float], ...]:
    minx, miny, maxx, maxy = bounds
    width, depth = fit.size
    sx = width / (maxx - minx) if maxx > minx else 1.0
    sy = depth / (maxy - miny) if maxy > miny else 1.0
    if keep_aspect:
        sx = sy = min(sx, sy)
    used_w, used_d = (maxx - minx) * sx, (maxy - miny) * sy
    ox = fit.min[0] + (width - used_w) / 2 - minx * sx
    if flip_y:
        oy = fit.min[1] + (depth - used_d) / 2 + maxy * sy
        return ((sx, 0.0, ox), (0.0, -sy, oy), (0.0, 0.0, 1.0))
    oy = fit.min[1] + (depth - used_d) / 2 - miny * sy
    return ((sx, 0.0, ox), (0.0, sy, oy), (0.0, 0.0, 1.0))


def svg_outline(
    svg: str,
    fit: Box2,
    path_index: int | None = None,
    keep_aspect: bool = True,
    flip_y: bool = True,
) -> list[SvgCurve]:
    """Fits SVG path data (a `d` string or SVG text) into `fit`, one curve per subpath.

    `flip_y` maps SVG's downward y to upstage, so the picture reads correctly from the
    audience. Reading files is left to the caller.

    >>> len(svg_outline("M0 0 L1 0 M0 1 L1 1", Box2(min=(0, 0), max=(1, 1))))
    2
    """
    subpaths = _svg_subpaths(svg)
    if path_index is not None:
        subpaths = [subpaths[path_index]]
    raw = [SvgCurve(d=s.to_d(), closed=s.closed) for s in subpaths]
    pts = np.vstack([curve.to_polyline(SVG_FIT_TOL) for curve in raw])
    bounds = (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
    matrix = _fit_matrix(tuple(map(float, bounds)), fit, keep_aspect, flip_y)  # type: ignore[arg-type]
    return [curve.model_copy(update={"transform": matrix}) for curve in raw]


def text_outline(text: str, font: str, height: Meters, at: Point) -> list[Polyline]:
    """Spells `text` as stroke polylines; `at` is the baseline-left of the first letter.

    >>> len(text_outline("HI", "simplex", 2.0, (0, 0)))
    6
    """
    scale = height / GLYPH_HEIGHT
    curves = []
    for index, char in enumerate(text):
        left = at[0] + index * ADVANCE * scale
        for stroke in glyph_strokes(char, font):
            pts = tuple((left + x * scale, at[1] + y * scale) for x, y in stroke)
            curves.append(Polyline(points=pts))
    return curves


def join(*curves: AnyCurve, bridge: bool = False) -> CompositeCurve:
    """Chains curves end to end; gaps raise unless `bridge` adds straight connectors.

    >>> join(line((0, 0), (1, 0)), line((1, 0), (1, 1))).length()
    2.0
    """
    parts: list[AnyCurve] = []
    for curve in curves:
        if parts:
            end = parts[-1].point_at(parts[-1].length())
            start = curve.point_at(0.0)
            if float(np.linalg.norm(end - start)) > JOIN_TOL:
                if not bridge:
                    raise GeometryError(
                        "curve ends do not meet",
                        hint="pass bridge=True to connect them with straight pieces",
                        context={"end": end.tolist(), "start": start.tolist()},
                    )
                parts.append(Segment(a=(end[0], end[1]), b=(start[0], start[1])))
        parts.append(curve)
    return CompositeCurve(parts=tuple(parts))


def offset(curve: Curve, distance: Meters, side: str = "left") -> Curve:
    """Builds a parallel curve `distance` meters to the left or right of travel.

    Lines and arcs stay exact; other curves offset their polyline approximation.

    >>> offset(line((0, 0), (1, 0)), 1.0).point_at(0.0).tolist()
    [0.0, 1.0]
    """
    signed = distance if side == "left" else -distance
    if isinstance(curve, Segment):
        normal = curve.normal_at(0.0) * signed
        a, b = np.asarray(curve.a) + normal, np.asarray(curve.b) + normal
        return Segment(a=(float(a[0]), float(a[1])), b=(float(b[0]), float(b[1])))
    if isinstance(curve, ArcCurve | CircleCurve):
        direction = math.copysign(1.0, getattr(curve, "sweep_deg", 1.0))
        radius = curve.radius - signed * direction
        if radius <= 0:
            raise GeometryError(
                "offset passes through the arc center",
                hint="use a smaller offset distance",
                context={"radius": curve.radius, "distance": distance},
            )
        return curve.model_copy(update={"radius": radius})
    return _offset_polyline(curve, signed)


def _offset_polyline(curve: Curve, signed: Meters) -> Polyline:
    geometry = shapely.LineString(curve.to_polyline(OFFSET_TOL)).offset_curve(signed)
    if geometry.is_empty or not isinstance(geometry, shapely.LineString):
        raise GeometryError("offset collapses the curve", hint="use a smaller offset distance")
    coords = [(float(x), float(y)) for x, y in geometry.coords]
    if curve.closed and len(coords) > 2 and coords[0] == coords[-1]:
        return Polyline(points=tuple(coords[:-1]), closed=True)
    return Polyline(points=tuple(coords))
