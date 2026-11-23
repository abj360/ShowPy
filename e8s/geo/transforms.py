"""
transforms.py --- moves, turns, resizes, mirrors and snaps points and curves

Every transform takes `Points` (N, 2) or a curve and returns the same kind; point-wise maps
(expand, snap, align) move a curve's defining points, or a dense polyline when the curve has
none. Affine maps come from `e8s.geo.affine`. `sel` and `force` are ignored; they remain so
that a `Set` still reaches `e8s.choreo.set_transforms`, which warns as deprecated. Grid
snapping, align and distribute live in `e8s.geo.snapping` and are re-exported here.

Contains:
    Anchor: names the box anchors move_to accepts
    MirrorAxis: names a mirror line
    shift(): translates
    move_to(): moves an anchor of the object onto a point
    rotate(): rotates counter-clockwise
    scale(): resizes about an anchor; negative factors raise
    expand(): moves every point d meters away from an anchor
    contract(): moves every point d meters toward an anchor
    set_interval(): respaces ordered points along their own polyline
    mirror(): reflects across a line
    skew(): shears about an anchor
    affine(): applies a 2x3 or 3x3 affine matrix
    interpolate_forms(): blends two equal-length point sets
    box_anchor_point(): returns a named anchor of an object's bounding box
    scale_about(): builds the matrix of a checked scale about a point
    expand_map(): builds the point map that pushes points away from an anchor
    contract_map(): builds the point map that pulls points toward an anchor
    respace_map(): builds the point map that respaces ordered points along their polyline
    vertical_mirror(): builds the matrix that reflects across a vertical line
    mirror_line(): builds the matrix of a named or two-point mirror line
    affine_matrix(): checks a 2x3 or 3x3 matrix and returns it as 3x3
    symmetrize(): forwards to e8s.choreo.set_transforms.symmetrize, deprecated
    snap(), snap_to_guides(), align(), distribute(): re-exported from e8s.geo.snapping
"""

import math
from collections.abc import Callable, Mapping
from typing import Literal, cast

import numpy as np
import numpy.typing as npt

from e8s.core.deprecation import deprecated_call
from e8s.core.errors import GeometryError, SpacingError
from e8s.core.types import Degrees, Meters, Point, Points
from e8s.core.util import as_points
from e8s.geo.affine import (
    Matrix,
    mirror_matrix,
    rotation_matrix,
    scale_matrix,
    skew_matrix,
    translation_matrix,
)
from e8s.geo.checks import pick
from e8s.geo.curves import CurveModel
from e8s.geo.measure import ALL, SET_TYPE, SetSelector, bounds, centroid, wrong_type_error
from e8s.geo.snapping import align, distribute, snap, snap_to_guides
from e8s.geo.transform_base import (
    About,
    PointMap,
    about_point,
    anchor_source,
    apply_matrix,
    apply_pointwise,
    is_set_input,
    warn_set_moved,
)

__all__ = [
    "Anchor",
    "MirrorAxis",
    "affine",
    "affine_matrix",
    "align",
    "box_anchor_point",
    "contract",
    "contract_map",
    "distribute",
    "expand",
    "expand_map",
    "interpolate_forms",
    "mirror",
    "mirror_line",
    "move_to",
    "respace_map",
    "rotate",
    "scale",
    "scale_about",
    "set_interval",
    "shift",
    "skew",
    "snap",
    "snap_to_guides",
    "symmetrize",
    "vertical_mirror",
]

type Anchor = Literal[
    "center", "centroid", "front-left", "front-center", "front-right", "back-left",
    "back-center", "back-right", "left-center", "right-center",
]  # fmt: skip
type MirrorAxis = Literal["center", "x", "y"] | tuple[Point, Point]

GEO_MODULE = "e8s.geo.transforms"
CHOREO_MODULE = "e8s.choreo.set_transforms"
BOX_ANCHORS: dict[str, tuple[float, float]] = {
    "center": (0.5, 0.5),
    "front-left": (0.0, 0.0),
    "front-center": (0.5, 0.0),
    "front-right": (1.0, 0.0),
    "back-left": (0.0, 1.0),
    "back-center": (0.5, 1.0),
    "back-right": (1.0, 1.0),
    "left-center": (0.0, 0.5),
    "right-center": (1.0, 0.5),
}
CENTROID = "centroid"
INTERVAL_ANCHORS = {"start": 0.0, "center": 0.5, "end": 1.0}
MOVE_TOL = 1e-12
SINGULAR_TOL = 1e-12
AFFINE_ROW = (0.0, 0.0, 1.0)


def shift[T: (Points, CurveModel)](
    obj: T, dx: Meters, dy: Meters, sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Translates by (dx, dy).

    >>> shift(np.array([[0.0, 0.0]]), 1.0, 2.0).tolist()
    [[1.0, 2.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import shift as twin

        warn_set_moved(GEO_MODULE, "shift", CHOREO_MODULE, obj, Set)
        return twin(obj, dx, dy, sel, force=force)
    return apply_matrix(obj, translation_matrix(dx, dy))


def box_anchor_point(source: Points | CurveModel, anchor: Anchor) -> Point:
    """Returns a named anchor of the object's bounding box, or its centroid.

    Front is the lowest y and left the lowest x.

    >>> box_anchor_point(np.array([[0.0, 0.0], [2.0, 4.0]]), "back-right")
    (2.0, 4.0)
    """
    if anchor == CENTROID:
        return centroid(source)
    fx, fy = pick("anchor", anchor, BOX_ANCHORS)
    box = bounds(source)
    return box.min[0] + fx * box.size[0], box.min[1] + fy * box.size[1]


def move_to[T: (Points, CurveModel)](
    obj: T, anchor: Anchor, to: Point, sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Moves an anchor of the object onto `to`; front is the lowest y, left the lowest x.

    >>> move_to(np.array([[0.0, 0.0], [2.0, 2.0]]), "front-left", (5.0, 5.0)).tolist()
    [[5.0, 5.0], [7.0, 7.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import move_to as twin

        warn_set_moved(GEO_MODULE, "move_to", CHOREO_MODULE, obj, Set)
        return twin(obj, anchor, to, sel, force=force)
    here = box_anchor_point(anchor_source(obj), anchor)
    return shift(obj, to[0] - here[0], to[1] - here[1])


def rotate[T: (Points, CurveModel)](
    obj: T, deg: Degrees, about: About = "center", sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Rotates counter-clockwise by `deg` about an anchor.

    >>> rotate(np.array([[1.0, 0.0]]), 90.0, about=(0.0, 0.0)).round(6).tolist()
    [[0.0, 1.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import rotate as twin

        warn_set_moved(GEO_MODULE, "rotate", CHOREO_MODULE, obj, Set)
        return twin(obj, deg, about, sel, force=force)
    return apply_matrix(obj, rotation_matrix(deg, about_point(obj, about)))


def scale_about(sx: float, sy: float | None, about: Point) -> Matrix:
    """Builds the matrix that resizes about a point; `sy` defaults to `sx`, factors must be > 0.

    >>> scale_about(2.0, None, (0.0, 0.0))[0].tolist()
    [2.0, 0.0, 0.0]
    """
    factor_y = sx if sy is None else sy
    if sx <= 0 or factor_y <= 0:
        raise GeometryError(
            f"scale factors must be above 0, got ({sx}, {factor_y})",
            hint="use mirror() to flip, then scale by a positive factor",
            context={"sx": sx, "sy": factor_y},
        )
    return scale_matrix(sx, factor_y, about)


def scale[T: (Points, CurveModel)](
    obj: T,
    sx: float,
    sy: float | None = None,
    about: About = "center",
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> T:
    """Resizes about an anchor; `sy` defaults to `sx`. Factors must be positive.

    >>> scale(np.array([[1.0, 1.0]]), 2.0, about=(0.0, 0.0)).tolist()
    [[2.0, 2.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import scale as twin

        warn_set_moved(GEO_MODULE, "scale", CHOREO_MODULE, obj, Set)
        return twin(obj, sx, sy, about, sel, force=force)
    return apply_matrix(obj, scale_about(sx, sy, about_point(obj, about)))


def _radial(anchor: Point, d: Meters) -> PointMap:
    origin = np.asarray(anchor, dtype=float)

    def push(pts: Points) -> Points:
        offsets = pts - origin
        radii = np.linalg.norm(offsets, axis=1)
        away = radii > MOVE_TOL
        if d < 0 and np.any(away) and -d > radii[away].min():
            raise GeometryError(
                f"cannot contract by {-d} m: a point is only {radii[away].min():.3f} m away",
                hint="contract by at most the distance of the closest point",
                context={"max_d": float(radii[away].min()), "d": -d},
            )
        moved = pts.copy()
        moved[away] += offsets[away] / radii[away, None] * d
        return moved

    return push


def _require_non_negative(name: str, d: Meters) -> None:
    if d < 0:
        raise GeometryError(
            f"{name} distance must be >= 0, got {d}",
            hint=f"pass d >= 0; use the opposite of {name} to go the other way",
            context={"d": d},
        )


def expand_map(anchor: Point, d: Meters) -> PointMap:
    """Builds the point map that moves every point `d` meters away from `anchor`.

    >>> expand_map((0.0, 0.0), 1.0)(np.array([[2.0, 0.0]])).tolist()
    [[3.0, 0.0]]
    """
    _require_non_negative("expand", d)
    return _radial(anchor, d)


def contract_map(anchor: Point, d: Meters) -> PointMap:
    """Builds the point map that moves every point `d` meters toward `anchor`.

    >>> contract_map((0.0, 0.0), 1.0)(np.array([[3.0, 0.0]])).tolist()
    [[2.0, 0.0]]
    """
    _require_non_negative("contract", d)
    return _radial(anchor, -d)


def expand[T: (Points, CurveModel)](
    obj: T, d: Meters, about: About = "center", sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Moves every point d meters away from an anchor along its ray; points on it stay.

    >>> expand(np.array([[2.0, 0.0]]), 1.0, about=(0.0, 0.0)).tolist()
    [[3.0, 0.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import expand as twin

        warn_set_moved(GEO_MODULE, "expand", CHOREO_MODULE, obj, Set)
        return twin(obj, d, about, sel, force=force)
    return apply_pointwise(obj, expand_map(about_point(obj, about), d))


def contract[T: (Points, CurveModel)](
    obj: T, d: Meters, about: About = "center", sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Moves every point d meters toward an anchor; overshooting the anchor raises.

    >>> contract(np.array([[3.0, 0.0]]), 1.0, about=(0.0, 0.0)).tolist()
    [[2.0, 0.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import contract as twin

        warn_set_moved(GEO_MODULE, "contract", CHOREO_MODULE, obj, Set)
        return twin(obj, d, about, sel, force=force)
    return apply_pointwise(obj, contract_map(about_point(obj, about), d))


def _end_direction(pieces: Points, lengths: Points, *, from_end: bool) -> Points:
    order = range(len(pieces) - 1, -1, -1) if from_end else range(len(pieces))
    index = next(i for i in order if lengths[i] > MOVE_TOL)
    direction: Points = pieces[index] / lengths[index]
    return direction


def _along_extended(pts: Points, s: Points) -> Points:
    pieces = np.diff(pts, axis=0)
    lengths = np.linalg.norm(pieces, axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
    total = float(cumulative[-1])
    out = np.stack([np.interp(s, cumulative, pts[:, k]) for k in (0, 1)], axis=1)
    before, after = s < 0, s > total
    out[before] = pts[0] + np.outer(s[before], _end_direction(pieces, lengths, from_end=False))
    out[after] = pts[-1] + np.outer(
        s[after] - total, _end_direction(pieces, lengths, from_end=True)
    )
    return out


def respace_map(interval: Meters, anchor: Literal["start", "center", "end"]) -> PointMap:
    """Builds the point map that respaces ordered points `interval` apart along their polyline.

    >>> respace_map(2.0, "center")(np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])).tolist()
    [[-1.0, 0.0], [1.0, 0.0], [3.0, 0.0]]
    """
    if interval <= 0:
        raise SpacingError(
            f"interval must be above 0, got {interval}", hint="pass a positive interval"
        )
    share = pick("interval anchor", anchor, INTERVAL_ANCHORS)

    def respace(pts: Points) -> Points:
        if len(pts) < 2:
            return pts
        total = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
        if total <= MOVE_TOL:
            raise GeometryError(
                "spots all stand on one point, so they have no line to respace along",
                hint="spread the spots out first, e.g. with distribute()",
                context={"points": len(pts)},
            )
        span = interval * (len(pts) - 1)
        stations = np.asarray(share * (total - span) + interval * np.arange(len(pts)), np.float64)
        return _along_extended(pts, stations)

    return respace


def set_interval(
    obj: Points,
    interval: Meters,
    anchor: Literal["start", "center", "end"] = "center",
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> Points:
    """Respaces points `interval` apart along the polyline through them, in order.

    The anchor (start, arc-length middle or end of that polyline) stays fixed; a longer
    line extends straight past its ends.

    >>> set_interval(np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]), 2.0).tolist()
    [[-1.0, 0.0], [1.0, 0.0], [3.0, 0.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import set_interval as twin

        warn_set_moved(GEO_MODULE, "set_interval", CHOREO_MODULE, obj, Set)
        return twin(obj, interval, anchor, sel, force=force)
    return apply_pointwise(obj, respace_map(interval, anchor))


def vertical_mirror(axis_x: float) -> Matrix:
    """Builds the matrix that reflects across the vertical line x = `axis_x`.

    >>> vertical_mirror(1.0)[0].tolist()
    [-1.0, 0.0, 2.0]
    """
    return scale_matrix(-1.0, 1.0, (axis_x, 0.0))


def mirror_line(axis: MirrorAxis, center: Callable[[], Point]) -> Matrix:
    """Builds the matrix of a mirror line: `center`, `x`, `y` or two distinct points.

    Args:
        axis: The line to reflect across.
        center: Returns the box center of the object; read only when the line needs it.

    Returns:
        matrix: The 3x3 reflection matrix.

    >>> mirror_line("x", lambda: (0.0, 0.0))[0].tolist()
    [-1.0, 0.0, 0.0]
    """
    if not isinstance(axis, str):
        a, b = axis
        if math.dist(a, b) <= MOVE_TOL:
            raise GeometryError(
                "mirror line needs two distinct points",
                hint="pass two different points, or 'center', 'x' or 'y'",
                context={"axis": axis},
            )
        return mirror_matrix(a, b)
    if axis == "x":
        return vertical_mirror(0.0)
    cx, cy = center()
    lines = {"center": vertical_mirror(cx), "y": scale_matrix(1.0, -1.0, (0.0, cy))}
    return pick("mirror axis", axis, lines)


def mirror[T: (Points, CurveModel)](
    obj: T, axis: MirrorAxis, sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Reflects across a line.

    `center` is the vertical line through the box center, `x` the sheet center line x = 0,
    `y` the horizontal line through the box center; two points name any line.

    >>> mirror(np.array([[1.0, 2.0]]), "x").tolist()
    [[-1.0, 2.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import mirror as twin

        warn_set_moved(GEO_MODULE, "mirror", CHOREO_MODULE, obj, Set)
        return twin(obj, axis, sel, force=force)
    return apply_matrix(obj, mirror_line(axis, lambda: about_point(obj, "center")))


@deprecated_call("e8s.choreo.set_transforms.symmetrize", since="0.4.0", remove="0.6.0")
def symmetrize[S](
    s: S,
    sel: SetSelector = ALL,
    source_side: Literal["left", "right"] = "left",
    pairs: Mapping[str, str] | None = None,
    axis_x: float = 0.0,
    *,
    force: bool = False,
) -> S:
    """Copies one side of a set onto its mirror partners; moved to `e8s.choreo`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_transforms import symmetrize as twin

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.transforms.symmetrize", "s", s, SET_TYPE)
    return cast(S, twin(s, sel, source_side, pairs, axis_x, force=force))


def skew[T: (Points, CurveModel)](
    obj: T,
    deg_x: Degrees = 0.0,
    deg_y: Degrees = 0.0,
    about: About = "center",
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> T:
    """Shears about an anchor: x shifts by tan(deg_x)·y, y by tan(deg_y)·x.

    >>> skew(np.array([[0.0, 1.0]]), deg_x=45.0, about=(0.0, 0.0)).round(6).tolist()
    [[1.0, 1.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import skew as twin

        warn_set_moved(GEO_MODULE, "skew", CHOREO_MODULE, obj, Set)
        return twin(obj, deg_x, deg_y, about, sel, force=force)
    return apply_matrix(obj, skew_matrix(deg_x, deg_y, about_point(obj, about)))


def affine_matrix(matrix: npt.ArrayLike) -> Matrix:
    """Checks a 2x3 or 3x3 affine matrix and returns it as 3x3.

    >>> affine_matrix([[1.0, 0.0, 2.0], [0.0, 1.0, 0.0]])[0].tolist()
    [1.0, 0.0, 2.0]
    """
    m = np.asarray(matrix, dtype=float)
    if m.shape == (2, 3):
        m = np.vstack([m, AFFINE_ROW])
    if m.shape != (3, 3) or not np.allclose(m[2], AFFINE_ROW):
        raise GeometryError(
            f"affine matrix must be 2x3 or 3x3 with last row (0, 0, 1), got {m.shape}",
            hint="pass [[a, b, tx], [c, d, ty]]",
            context={"shape": m.shape},
        )
    if abs(np.linalg.det(m[:2, :2])) < SINGULAR_TOL:
        raise GeometryError(
            "affine matrix collapses the plane", hint="pass an invertible linear part"
        )
    return m


def affine[T: (Points, CurveModel)](
    obj: T, matrix: npt.ArrayLike, sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Applies a 2x3 or 3x3 affine matrix.

    >>> affine(np.array([[1.0, 1.0]]), [[2.0, 0.0, 1.0], [0.0, 1.0, 0.0]]).tolist()
    [[3.0, 1.0]]
    """
    if is_set_input(obj):
        from e8s.choreo.models import Set
        from e8s.choreo.set_transforms import affine as twin

        warn_set_moved(GEO_MODULE, "affine", CHOREO_MODULE, obj, Set)
        return twin(obj, matrix, sel, force=force)
    return apply_matrix(obj, affine_matrix(matrix))


def interpolate_forms(a: Points, b: Points, u: float) -> Points:
    """Blends two equal-length point sets: u = 0 gives `a`, u = 1 gives `b`.

    >>> interpolate_forms(np.array([[0.0, 0.0]]), np.array([[4.0, 2.0]]), 0.5).tolist()
    [[2.0, 1.0]]
    """
    start, stop = as_points(a), as_points(b)
    if start.shape != stop.shape:
        raise GeometryError(
            f"forms differ in size: {len(start)} and {len(stop)} points",
            hint="pass point sets of equal length, matched member by member",
            context={"a": len(start), "b": len(stop)},
        )
    if not 0.0 <= u <= 1.0:
        raise GeometryError(f"u must be in [0, 1], got {u}", hint="pass 0 <= u <= 1")
    blended: Points = (1.0 - u) * start + u * stop
    return blended
