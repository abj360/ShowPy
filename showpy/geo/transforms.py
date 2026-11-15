"""
transforms.py --- moves, turns, resizes, mirrors and snaps points, curves and set selections

Every transform takes `Points` (N, 2), a curve or a `Set` and returns the same kind. On a
set only the selected on-field placements move; facings follow affine maps; pinned
placements raise LockedError unless force=True. `sel` and `force` are ignored for points
and curves. Affine maps come from `showpy.geo.affine`; point-wise maps (expand, snap, align)
move a curve's defining points, or a dense polyline when the curve has none. Grid and
guide snapping, align and distribute live in `showpy.geo.snapping` and are re-exported here.

Contains:
    Anchor: names the box anchors move_to accepts
    MirrorAxis: names a mirror line
    shift(): translates
    move_to(): moves an anchor of the object onto a point
    rotate(): rotates counter-clockwise; facings turn with it
    scale(): resizes about an anchor; negative factors raise
    expand(): moves every point d meters away from an anchor
    contract(): moves every point d meters toward an anchor
    set_interval(): respaces ordered spots along their own polyline
    mirror(): reflects across a line; facings reflect
    symmetrize(): copies one side of a set onto its mirror partners
    skew(): shears about an anchor
    affine(): applies a 2x3 or 3x3 affine matrix
    interpolate_forms(): blends two equal-length point sets
    snap(), snap_to_guides(), align(), distribute(): re-exported from showpy.geo.snapping
"""

import math
from collections.abc import Mapping, Sequence
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import linear_sum_assignment

from showpy.choreo.models import Set
from showpy.core.errors import GeometryError, SchemaError, SpacingError
from showpy.core.ids import SpotId
from showpy.core.types import Degrees, Meters, Point, Points
from showpy.core.util import as_points
from showpy.geo.affine import (
    Matrix,
    mirror_matrix,
    rotation_matrix,
    scale_matrix,
    skew_matrix,
    transform_facing,
    translation_matrix,
)
from showpy.geo.curves import CurveModel
from showpy.geo.measure import ALL, SetSelector, bounds, centroid, on_field_ids
from showpy.geo.snapping import align, distribute, snap, snap_to_guides
from showpy.geo.transform_base import (
    About,
    PointMap,
    about_point,
    anchor_source,
    apply_matrix,
    apply_pointwise,
    pick,
)

__all__ = [
    "Anchor",
    "MirrorAxis",
    "affine",
    "align",
    "contract",
    "distribute",
    "expand",
    "interpolate_forms",
    "mirror",
    "move_to",
    "rotate",
    "scale",
    "set_interval",
    "shift",
    "skew",
    "snap",
    "snap_to_guides",
    "symmetrize",
]

type Anchor = Literal[
    "center", "centroid", "front-left", "front-center", "front-right", "back-left",
    "back-center", "back-right", "left-center", "right-center",
]  # fmt: skip
type MirrorAxis = Literal["center", "x", "y"] | tuple[Point, Point]

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
SIDE_SIGN = {"left": -1.0, "right": 1.0}
INTERVAL_ANCHORS = {"start": 0.0, "center": 0.5, "end": 1.0}
MOVE_TOL = 1e-12
SINGULAR_TOL = 1e-12
AFFINE_ROW = (0.0, 0.0, 1.0)


def shift[T: (Points, CurveModel, Set)](
    obj: T, dx: Meters, dy: Meters, sel: SetSelector = ALL, force: bool = False
) -> T:
    """Translates by (dx, dy).

    >>> shift(np.array([[0.0, 0.0]]), 1.0, 2.0).tolist()
    [[1.0, 2.0]]
    """
    return apply_matrix(obj, translation_matrix(dx, dy), sel, force)


def move_to[T: (Points, CurveModel, Set)](
    obj: T, anchor: Anchor, to: Point, sel: SetSelector = ALL, force: bool = False
) -> T:
    """Moves an anchor of the object onto `to`; front is the lowest y, left the lowest x.

    >>> move_to(np.array([[0.0, 0.0], [2.0, 2.0]]), "front-left", (5.0, 5.0)).tolist()
    [[5.0, 5.0], [7.0, 7.0]]
    """
    source = anchor_source(obj, sel)
    if anchor == CENTROID:
        here = centroid(source)
    else:
        fx, fy = pick("anchor", anchor, BOX_ANCHORS)
        box = bounds(source)
        here = (box.min[0] + fx * box.size[0], box.min[1] + fy * box.size[1])
    return shift(obj, to[0] - here[0], to[1] - here[1], sel, force)


def rotate[T: (Points, CurveModel, Set)](
    obj: T, deg: Degrees, about: About = "center", sel: SetSelector = ALL, force: bool = False
) -> T:
    """Rotates counter-clockwise by `deg` about an anchor; facings turn by the same angle.

    >>> rotate(np.array([[1.0, 0.0]]), 90.0, about=(0.0, 0.0)).round(6).tolist()
    [[0.0, 1.0]]
    """
    return apply_matrix(obj, rotation_matrix(deg, about_point(obj, about, sel)), sel, force)


def scale[T: (Points, CurveModel, Set)](
    obj: T,
    sx: float,
    sy: float | None = None,
    about: About = "center",
    sel: SetSelector = ALL,
    force: bool = False,
) -> T:
    """Resizes about an anchor; `sy` defaults to `sx`. Factors must be positive.

    >>> scale(np.array([[1.0, 1.0]]), 2.0, about=(0.0, 0.0)).tolist()
    [[2.0, 2.0]]
    """
    factor_y = sx if sy is None else sy
    if sx <= 0 or factor_y <= 0:
        raise GeometryError(
            f"scale factors must be above 0, got ({sx}, {factor_y})",
            hint="use mirror() to flip, then scale by a positive factor",
            context={"sx": sx, "sy": factor_y},
        )
    matrix = scale_matrix(sx, factor_y, about_point(obj, about, sel))
    return apply_matrix(obj, matrix, sel, force)


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


def expand[T: (Points, CurveModel, Set)](
    obj: T, d: Meters, about: About = "center", sel: SetSelector = ALL, force: bool = False
) -> T:
    """Moves every point d meters away from an anchor along its ray; points on it stay.

    >>> expand(np.array([[2.0, 0.0]]), 1.0, about=(0.0, 0.0)).tolist()
    [[3.0, 0.0]]
    """
    _require_non_negative("expand", d)
    return apply_pointwise(obj, _radial(about_point(obj, about, sel), d), sel, force)


def contract[T: (Points, CurveModel, Set)](
    obj: T, d: Meters, about: About = "center", sel: SetSelector = ALL, force: bool = False
) -> T:
    """Moves every point d meters toward an anchor; overshooting the anchor raises.

    >>> contract(np.array([[3.0, 0.0]]), 1.0, about=(0.0, 0.0)).tolist()
    [[2.0, 0.0]]
    """
    _require_non_negative("contract", d)
    return apply_pointwise(obj, _radial(about_point(obj, about, sel), -d), sel, force)


def _end_direction(pieces: Points, lengths: Points, from_end: bool) -> Points:
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
    out[before] = pts[0] + np.outer(s[before], _end_direction(pieces, lengths, False))
    out[after] = pts[-1] + np.outer(s[after] - total, _end_direction(pieces, lengths, True))
    return out


def _respace(interval: Meters, anchor: str) -> PointMap:
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


def set_interval[S: (Points, Set)](
    obj: S,
    interval: Meters,
    anchor: Literal["start", "center", "end"] = "center",
    sel: SetSelector = ALL,
    force: bool = False,
) -> S:
    """Respaces spots `interval` apart along the polyline through them, in order.

    The anchor (start, arc-length middle or end of that polyline) stays fixed; a longer
    line extends straight past its ends. Sets use selection order.

    >>> set_interval(np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]), 2.0).tolist()
    [[-1.0, 0.0], [1.0, 0.0], [3.0, 0.0]]
    """
    if interval <= 0:
        raise SpacingError(
            f"interval must be above 0, got {interval}", hint="pass a positive interval"
        )
    return apply_pointwise(obj, _respace(interval, anchor), sel, force)


def _vertical_mirror(axis_x: float) -> Matrix:
    return scale_matrix(-1.0, 1.0, (axis_x, 0.0))


def _mirror_line(obj: Points | CurveModel | Set, axis: MirrorAxis, sel: SetSelector) -> Matrix:
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
        return _vertical_mirror(0.0)
    cx, cy = about_point(obj, "center", sel)
    lines = {"center": _vertical_mirror(cx), "y": scale_matrix(1.0, -1.0, (0.0, cy))}
    return pick("mirror axis", axis, lines)


def mirror[T: (Points, CurveModel, Set)](
    obj: T, axis: MirrorAxis, sel: SetSelector = ALL, force: bool = False
) -> T:
    """Reflects across a line; facings reflect too.

    `center` is the vertical line through the box center, `x` the sheet center line x = 0,
    `y` the horizontal line through the box center; two points name any line.

    >>> mirror(np.array([[1.0, 2.0]]), "x").tolist()
    [[-1.0, 2.0]]
    """
    return apply_matrix(obj, _mirror_line(obj, axis, sel), sel, force)


def _pair_by_assignment(
    s: Set, sources: Sequence[SpotId], partners: Sequence[SpotId], axis_x: float
) -> dict[SpotId, SpotId]:
    if not sources or not partners:
        return {}
    mirrored = s.positions(sources) * np.array([-1.0, 1.0]) + np.array([2 * axis_x, 0.0])
    cost = np.linalg.norm(mirrored[:, None, :] - s.positions(partners)[None, :, :], axis=2)
    rows, cols = linear_sum_assignment(cost)
    return {sources[int(r)]: partners[int(c)] for r, c in zip(rows, cols, strict=True)}


def _check_pairs(s: Set, pairs: Mapping[SpotId, SpotId]) -> None:
    for source, partner in pairs.items():
        s.get(source)
        s.get(partner)
    clash = set(pairs) & set(pairs.values())
    if clash or len(set(pairs.values())) != len(pairs):
        raise SchemaError(
            "each spot may appear once, as a source or as a partner",
            hint="pass pairs like {'L1': 'R1', 'L2': 'R2'} with distinct spots",
            context={"repeated": sorted(clash)},
        )


def symmetrize(
    s: Set,
    sel: SetSelector = ALL,
    source_side: Literal["left", "right"] = "left",
    pairs: Mapping[SpotId, SpotId] | None = None,
    axis_x: float = 0.0,
    force: bool = False,
) -> Set:
    """Copies each source-side spot's mirror image (position and facing) onto its partner.

    Without `pairs`, selected on-field spots on the source side of x = axis_x are matched to
    those on the other side by an optimal assignment on mirrored positions; unmatched spots
    and spots on the axis stay put.

    >>> from showpy.choreo.models import Placement
    >>> rows = (Placement(entity="L", x=-1, y=2), Placement(entity="R", x=3, y=0))
    >>> symmetrize(Set(id="1", counts=0, placements=rows)).get("R").point
    (1.0, 2.0)
    """
    sign = pick("source side", source_side, SIDE_SIGN)
    ids = on_field_ids(s, sel)
    side = {i: sign * (s.get(i).x - axis_x) for i in ids}
    if pairs is None:
        sources = [i for i in ids if side[i] > 0]
        partners = [i for i in ids if side[i] < 0]
        pairs = _pair_by_assignment(s, sources, partners, axis_x)
    _check_pairs(s, pairs)
    matrix = _vertical_mirror(axis_x)
    updates: dict[SpotId, dict[str, float]] = {}
    for source, partner in pairs.items():
        origin = s.get(source)
        updates[partner] = {
            "x": 2 * axis_x - origin.x,
            "y": origin.y,
            "facing": transform_facing(origin.facing, matrix),
        }
    return s.with_placements(updates, force=force)


def skew[T: (Points, CurveModel, Set)](
    obj: T,
    deg_x: Degrees = 0.0,
    deg_y: Degrees = 0.0,
    about: About = "center",
    sel: SetSelector = ALL,
    force: bool = False,
) -> T:
    """Shears about an anchor: x shifts by tan(deg_x)·y, y by tan(deg_y)·x.

    >>> skew(np.array([[0.0, 1.0]]), deg_x=45.0, about=(0.0, 0.0)).round(6).tolist()
    [[1.0, 1.0]]
    """
    matrix = skew_matrix(deg_x, deg_y, about_point(obj, about, sel))
    return apply_matrix(obj, matrix, sel, force)


def _as_matrix(matrix: npt.ArrayLike) -> Matrix:
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


def affine[T: (Points, CurveModel, Set)](
    obj: T, matrix: npt.ArrayLike, sel: SetSelector = ALL, force: bool = False
) -> T:
    """Applies a 2x3 or 3x3 affine matrix; facings follow its linear part.

    >>> affine(np.array([[1.0, 1.0]]), [[2.0, 0.0, 1.0], [0.0, 1.0, 0.0]]).tolist()
    [[3.0, 1.0]]
    """
    return apply_matrix(obj, _as_matrix(matrix), sel, force)


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
