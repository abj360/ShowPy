"""
set_transforms.py --- moves, turns, resizes and mirrors points, curves and set selections

Every transform takes `Points` (N, 2), a curve or a `Set` and returns the same kind. On a
set only the selected on-field placements move; facings follow affine maps; pinned
placements raise LockedError unless force=True. `sel` and `force` are ignored for points
and curves, which `e8s.geo.transforms` handles.

Contains:
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
    anchor_source(): returns what anchors are measured on: points, a curve or set positions
    about_point(): resolves "center", "centroid" or a point to coordinates
    apply_matrix(): maps an object through an affine matrix; facings follow
    apply_pointwise(): maps an object's points through a function; facings stay
"""

from collections.abc import Mapping, Sequence
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import linear_sum_assignment

from e8s.choreo.models import PlacementPatch, Set
from e8s.choreo.set_measure import on_field_ids
from e8s.core.errors import SchemaError
from e8s.core.ids import SpotId
from e8s.core.types import Degrees, Meters, Point, Points
from e8s.geo import transform_base, transforms
from e8s.geo.affine import (
    Matrix,
    rotation_matrix,
    skew_matrix,
    transform_facing,
    transform_points,
    translation_matrix,
)
from e8s.geo.checks import pick
from e8s.geo.curves import CurveModel
from e8s.geo.measure import ALL, SetSelector, center, centroid
from e8s.geo.transform_base import About, PointMap
from e8s.geo.transforms import Anchor, MirrorAxis

SIDE_SIGN = {"left": -1.0, "right": 1.0}


def anchor_source(obj: Points | CurveModel | Set, sel: SetSelector) -> Points | CurveModel:
    """Returns what anchors are measured on: the points, the curve, or selected on-field spots.

    >>> anchor_source(np.array([[1.0, 2.0]]), "all").tolist()
    [[1.0, 2.0]]
    """
    if isinstance(obj, Set):
        return obj.positions(on_field_ids(obj, sel))
    return transform_base.anchor_source(obj)


def about_point(obj: Points | CurveModel | Set, about: About, sel: SetSelector) -> Point:
    """Resolves "center" (box center), "centroid" (mean) or a point to coordinates.

    >>> about_point(np.array([[0.0, 0.0], [2.0, 4.0]]), "center", "all")
    (1.0, 2.0)
    """
    if not isinstance(about, str):
        return float(about[0]), float(about[1])
    measure = pick("anchor", about, {"center": center, "centroid": centroid})
    return measure(anchor_source(obj, sel))


def _update_set(
    s: Set, ids: Sequence[SpotId], pts: Points, facings: Sequence[Degrees] | None, *, force: bool
) -> Set:
    updates: dict[SpotId, PlacementPatch] = {}
    for k, entity in enumerate(ids):
        x, y = float(pts[k, 0]), float(pts[k, 1])
        if facings is None:
            updates[entity] = PlacementPatch(x=x, y=y)
        else:
            updates[entity] = PlacementPatch(x=x, y=y, facing=facings[k])
    return s.with_placements(updates, force=force)


def apply_matrix[T: (Points, CurveModel, Set)](
    obj: T, matrix: Matrix, sel: SetSelector, *, force: bool
) -> T:
    """Maps an object through an affine matrix; set facings follow its linear part.

    >>> from e8s.geo.affine import translation_matrix
    >>> apply_matrix(np.array([[0.0, 0.0]]), translation_matrix(1, 0), "all", force=False).tolist()
    [[1.0, 0.0]]
    """
    if isinstance(obj, Set):
        ids = on_field_ids(obj, sel)
        pts = transform_points(obj.positions(ids), matrix)
        facings = [transform_facing(obj.get(i).facing, matrix) for i in ids]
        return _update_set(obj, ids, pts, facings, force=force)
    return transform_base.apply_matrix(obj, matrix)


def apply_pointwise[T: (Points, CurveModel, Set)](
    obj: T, fn: PointMap, sel: SetSelector, *, force: bool
) -> T:
    """Maps an object's points through `fn`; set facings stay as they are.

    >>> apply_pointwise(np.array([[1.0, 1.0]]), lambda p: p * 2, "all", force=False).tolist()
    [[2.0, 2.0]]
    """
    if isinstance(obj, Set):
        ids = on_field_ids(obj, sel)
        return _update_set(obj, ids, fn(obj.positions(ids)), None, force=force)
    return transform_base.apply_pointwise(obj, fn)


def shift[T: (Points, CurveModel, Set)](
    obj: T, dx: Meters, dy: Meters, sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Translates by (dx, dy).

    >>> from e8s.examples import two_set_show
    >>> first = two_set_show().set("1")
    >>> shift(first, 1.0, 0.0).get("T1").point
    (1.0, 5.0)
    """
    return apply_matrix(obj, translation_matrix(dx, dy), sel, force=force)


def move_to[T: (Points, CurveModel, Set)](
    obj: T, anchor: Anchor, to: Point, sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Moves an anchor of the object onto `to`; front is the lowest y, left the lowest x.

    >>> move_to(np.array([[0.0, 0.0], [2.0, 2.0]]), "front-left", (5.0, 5.0)).tolist()
    [[5.0, 5.0], [7.0, 7.0]]
    """
    here = transforms.box_anchor_point(anchor_source(obj, sel), anchor)
    return shift(obj, to[0] - here[0], to[1] - here[1], sel, force=force)


def rotate[T: (Points, CurveModel, Set)](
    obj: T, deg: Degrees, about: About = "center", sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Rotates counter-clockwise by `deg` about an anchor; facings turn by the same angle.

    >>> rotate(np.array([[1.0, 0.0]]), 90.0, about=(0.0, 0.0)).round(6).tolist()
    [[0.0, 1.0]]
    """
    return apply_matrix(obj, rotation_matrix(deg, about_point(obj, about, sel)), sel, force=force)


def scale[T: (Points, CurveModel, Set)](
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
    matrix = transforms.scale_about(sx, sy, about_point(obj, about, sel))
    return apply_matrix(obj, matrix, sel, force=force)


def expand[T: (Points, CurveModel, Set)](
    obj: T, d: Meters, about: About = "center", sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Moves every point d meters away from an anchor along its ray; points on it stay.

    >>> expand(np.array([[2.0, 0.0]]), 1.0, about=(0.0, 0.0)).tolist()
    [[3.0, 0.0]]
    """
    push = transforms.expand_map(about_point(obj, about, sel), d)
    return apply_pointwise(obj, push, sel, force=force)


def contract[T: (Points, CurveModel, Set)](
    obj: T, d: Meters, about: About = "center", sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Moves every point d meters toward an anchor; overshooting the anchor raises.

    >>> contract(np.array([[3.0, 0.0]]), 1.0, about=(0.0, 0.0)).tolist()
    [[2.0, 0.0]]
    """
    pull = transforms.contract_map(about_point(obj, about, sel), d)
    return apply_pointwise(obj, pull, sel, force=force)


def set_interval[S: (Points, Set)](
    obj: S,
    interval: Meters,
    anchor: Literal["start", "center", "end"] = "center",
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> S:
    """Respaces spots `interval` apart along the polyline through them, in order.

    The anchor (start, arc-length middle or end of that polyline) stays fixed; a longer
    line extends straight past its ends. Sets use selection order.

    >>> set_interval(np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]), 2.0).tolist()
    [[-1.0, 0.0], [1.0, 0.0], [3.0, 0.0]]
    """
    return apply_pointwise(obj, transforms.respace_map(interval, anchor), sel, force=force)


def mirror[T: (Points, CurveModel, Set)](
    obj: T, axis: MirrorAxis, sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Reflects across a line; facings reflect too.

    `center` is the vertical line through the box center, `x` the sheet center line x = 0,
    `y` the horizontal line through the box center; two points name any line.

    >>> mirror(np.array([[1.0, 2.0]]), "x").tolist()
    [[-1.0, 2.0]]
    """
    matrix = transforms.mirror_line(axis, lambda: about_point(obj, "center", sel))
    return apply_matrix(obj, matrix, sel, force=force)


def _pair_by_assignment(
    s: Set, sources: Sequence[SpotId], partners: Sequence[SpotId], axis_x: float
) -> dict[str, str]:
    if not sources or not partners:
        return {}
    mirrored = s.positions(sources) * np.array([-1.0, 1.0]) + np.array([2 * axis_x, 0.0])
    cost = np.linalg.norm(mirrored[:, None, :] - s.positions(partners)[None, :, :], axis=2)
    rows, cols = linear_sum_assignment(cost)
    return {sources[int(r)]: partners[int(c)] for r, c in zip(rows, cols, strict=True)}


def _check_pairs(s: Set, pairs: Mapping[str, str]) -> list[tuple[SpotId, SpotId]]:
    typed = [(s.get(source).entity, s.get(partner).entity) for source, partner in pairs.items()]
    clash = set(pairs) & set(pairs.values())
    if clash or len(set(pairs.values())) != len(pairs):
        raise SchemaError(
            "each spot may appear once, as a source or as a partner",
            hint="pass pairs like {'L1': 'R1', 'L2': 'R2'} with distinct spots",
            context={"repeated": sorted(clash)},
        )
    return typed


def symmetrize(
    s: Set,
    sel: SetSelector = ALL,
    source_side: Literal["left", "right"] = "left",
    pairs: Mapping[str, str] | None = None,
    axis_x: float = 0.0,
    *,
    force: bool = False,
) -> Set:
    """Copies each source-side spot's mirror image (position and facing) onto its partner.

    Without `pairs`, selected on-field spots on the source side of x = axis_x are matched to
    those on the other side by an optimal assignment on mirrored positions; unmatched spots
    and spots on the axis stay put.

    >>> from e8s.choreo.models import Placement
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
    matrix = transforms.vertical_mirror(axis_x)
    updates: dict[SpotId, PlacementPatch] = {}
    for source, partner in _check_pairs(s, pairs):
        origin = s.get(source)
        updates[partner] = PlacementPatch(
            x=2 * axis_x - origin.x,
            y=origin.y,
            facing=transform_facing(origin.facing, matrix),
        )
    return s.with_placements(updates, force=force)


def skew[T: (Points, CurveModel, Set)](
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
    matrix = skew_matrix(deg_x, deg_y, about_point(obj, about, sel))
    return apply_matrix(obj, matrix, sel, force=force)


def affine[T: (Points, CurveModel, Set)](
    obj: T, matrix: npt.ArrayLike, sel: SetSelector = ALL, *, force: bool = False
) -> T:
    """Applies a 2x3 or 3x3 affine matrix; facings follow its linear part.

    >>> affine(np.array([[1.0, 1.0]]), [[2.0, 0.0, 1.0], [0.0, 1.0, 0.0]]).tolist()
    [[3.0, 1.0]]
    """
    return apply_matrix(obj, transforms.affine_matrix(matrix), sel, force=force)
