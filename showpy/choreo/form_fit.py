"""
form_fit.py --- recognizes imported spots as a line, arc or circle form

A line is fitted by total least squares (SVD of the centered points). A circle is fitted
algebraically (Kåsa) and refined geometrically by Gauss-Newton. Arc versus circle is
decided by angular coverage: one angular gap much wider than the rest marks an arc.
Anything that fits neither within `tol` becomes a custom form.

Contains:
    fit_line(): fits a line by total least squares
    fit_circle(): fits a circle algebraically, then refines it geometrically
    detect_form(): turns imported spots into a line, arc, circle or custom form
"""

import math
from collections.abc import Sequence

import numpy as np

from showpy.choreo.form_models import Form
from showpy.choreo.forms import arc_form, circle_form, custom_form, line_form
from showpy.core.errors import SchemaError
from showpy.core.types import Meters, Point, Points
from showpy.core.util import as_points

MIN_CIRCLE_POINTS = 3
REFINE_STEPS = 50
REFINE_STOP = 1e-12
DEGENERATE_SPAN = 1e-9
ARC_GAP_FACTOR = 1.5
FULL_TURN = 360.0
DEFAULT_TOL = 0.1
ANGLE_DECIMALS = 9


def fit_line(points: object) -> tuple[Point, Point, float]:
    """Fits a line by total least squares.

    Returns:
        fit: The extreme points along the line, and the largest perpendicular residual.

    >>> fit_line([(0, 0), (1, 0), (3, 0)])
    ((0.0, 0.0), (3.0, 0.0), 0.0)
    """
    pts = as_points(points)
    if len(pts) == 0:
        raise SchemaError("a line fit needs 1 or more points", hint="pass at least 1 point")
    center = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - center)
    direction, normal = vt[0], vt[1]
    if direction[0] < 0 or (direction[0] == 0 and direction[1] < 0):
        direction = -direction
    along = (pts - center) @ direction
    residual = float(np.abs((pts - center) @ normal).max())
    a, b = center + along.min() * direction, center + along.max() * direction
    return (_pt(a), _pt(b), residual)


def _pt(row: Points) -> Point:
    return float(row[0]) + 0.0, float(row[1]) + 0.0


def _kasa(pts: Points) -> tuple[float, float, float]:
    design = np.column_stack([pts, np.ones(len(pts))])
    rhs = -(pts**2).sum(axis=1)
    (d, e, f), *_ = np.linalg.lstsq(design, rhs, rcond=None)
    cx, cy = -d / 2, -e / 2
    return float(cx), float(cy), float(math.sqrt(max(cx**2 + cy**2 - f, 0.0)))


def _refine(pts: Points, cx: float, cy: float, r: float) -> tuple[float, float, float]:
    params = np.array([cx, cy, r])
    for _ in range(REFINE_STEPS):
        offsets = pts - params[:2]
        dist = np.linalg.norm(offsets, axis=1)
        if np.any(dist < DEGENERATE_SPAN):
            break
        jacobian = np.column_stack([-offsets / dist[:, None], -np.ones(len(pts))])
        step, *_ = np.linalg.lstsq(jacobian, -(dist - params[2]), rcond=None)
        params = params + step
        if float(np.linalg.norm(step)) < REFINE_STOP:
            break
    return float(params[0]), float(params[1]), float(abs(params[2]))


def fit_circle(points: object) -> tuple[Point, Meters, float]:
    """Fits a circle: Kåsa's algebraic fit, refined by Gauss-Newton on true distances.

    Returns:
        fit: Center, radius and the largest radial residual.

    >>> center, radius, residual = fit_circle([(1, 0), (0, 1), (-1, 0), (0, -1)])
    >>> round(radius, 6), round(residual, 6)
    (1.0, 0.0)
    """
    pts = as_points(points)
    if len(pts) < MIN_CIRCLE_POINTS:
        raise SchemaError("a circle fit needs 3 or more points", hint="pass at least 3 points")
    cx, cy, r = _refine(pts, *_kasa(pts))
    residual = float(np.abs(np.linalg.norm(pts - [cx, cy], axis=1) - r).max())
    return (cx, cy), r, residual


def _ordered_members[T: str](members: Sequence[T], order: Sequence[int]) -> tuple[T, ...]:
    return tuple(members[i] for i in order) if members else ()


def _round_form(
    form_id: str, members: Sequence[str], pts: Points, center: Point, radius: Meters
) -> Form:
    raw = np.degrees(np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0]))
    angles = np.round(raw, ANGLE_DECIMALS) % FULL_TURN
    order = [int(i) for i in np.argsort(angles, kind="stable")]
    sorted_angles = angles[order]
    wrapped = np.concatenate([sorted_angles, [sorted_angles[0] + FULL_TURN]], dtype=np.float64)
    gaps = np.diff(wrapped)
    widest = int(np.argmax(gaps))
    if gaps[widest] <= ARC_GAP_FACTOR * float(np.median(gaps)):
        start = float(angles[order[0]])
        ordered = _ordered_members(members, order)
        return circle_form(form_id, ordered, center, radius, start, n=len(pts))
    order = order[widest + 1 :] + order[: widest + 1]
    sweep = FULL_TURN - float(gaps[widest])
    ordered = _ordered_members(members, order)
    start = float(angles[order[0]])
    return arc_form(form_id, ordered, center, radius, start, sweep, n=len(pts))


def detect_form(
    points: object,
    tol: Meters = DEFAULT_TOL,
    form_id: str = "detected",
    members: Sequence[str] = (),
) -> Form:
    """Turns imported spots into a line, arc or circle form, else a custom form.

    Members, when given, are listed in point order and reordered along the fitted shape.
    Fitted forms space their slots evenly, so unevenly spaced imports come back evened out.

    >>> detect_form([(0, 0), (2, 0), (1, 0.01)]).kind
    'line'
    >>> detect_form([(1, 0), (0, 1), (-1, 0), (0, -1)]).kind
    'circle'
    """
    pts = as_points(points)
    if members and len(members) != len(pts):
        raise SchemaError(
            f"{len(members)} members for {len(pts)} points", hint="give one member per point"
        )
    if len(pts) >= 2:
        a, b, residual = fit_line(pts)
        if residual <= tol and math.dist(a, b) > DEGENERATE_SPAN:
            direction = np.subtract(b, a)
            order = [int(i) for i in np.argsort((pts - a) @ direction, kind="stable")]
            return line_form(form_id, _ordered_members(members, order), a, b, n=len(pts))
    if len(pts) >= MIN_CIRCLE_POINTS:
        center, radius, residual = fit_circle(pts)
        if residual <= tol and radius > DEGENERATE_SPAN:
            return _round_form(form_id, members, pts, center, radius)
    return custom_form(form_id, tuple(members), pts)
