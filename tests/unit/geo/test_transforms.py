#!/usr/bin/env python3

"""
test_transforms.py --- tests every transform on points, curves and sets, including pins and facings
"""

import math

import numpy as np
import pytest

from showpy.core.errors import GeometryError, LockedError, SchemaError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.geo.curves import ArcCurve, CompositeCurve, Polyline, Segment
from showpy.geo.shapes import arc, circle, join, line, polyline
from showpy.geo.transforms import (
    affine,
    align,
    contract,
    distribute,
    expand,
    interpolate_forms,
    mirror,
    move_to,
    rotate,
    scale,
    set_interval,
    shift,
    skew,
    snap,
    snap_to_guides,
    symmetrize,
)
from showpy.sheet.models import GuideLine
from tests.fixtures.shows import field_sheet
from tests.helpers import make_facing_set

TOL = 1e-9
SQUARE = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]])


def test_shift_points_curve_and_set() -> None:
    assert shift(SQUARE, 1.0, -1.0)[0].tolist() == [1.0, -1.0]
    moved = shift(line((0, 0), (1, 0)), 0.0, 2.0)
    assert isinstance(moved, Segment)
    assert moved.a == (0.0, 2.0)
    s = shift(make_facing_set(("A", 0, 0, 0), ("B", 1, 0, 0)), 1.0, 0.0, sel=["B"])
    assert s.get("A").x == 0.0
    assert s.get("B").x == 2.0


def test_set_transforms_skip_off_field_spots() -> None:
    s = shift(make_facing_set(("A", 0, 0, 0), ("B", 1, 0, 0), off=("B",)), 3.0, 0.0)
    assert s.get("A").x == 3.0
    assert s.get("B").x == 1.0


def test_pinned_spots_raise_unless_forced() -> None:
    s = make_facing_set(("A", 0, 0, 0), ("B", 1, 0, 0), pinned=("B",))
    with pytest.raises(LockedError):
        shift(s, 1.0, 0.0)
    assert shift(s, 1.0, 0.0, force=True).get("B").x == 2.0
    assert shift(s, 1.0, 0.0, sel=["A"]).get("A").x == 1.0


def test_inputs_are_not_mutated() -> None:
    pts = SQUARE.copy()
    rotate(pts, 30.0)
    assert np.array_equal(pts, SQUARE)


def test_string_selector_must_be_all() -> None:
    with pytest.raises(SchemaError):
        shift(make_facing_set(("A", 0, 0, 0)), 1.0, 0.0, sel="A")


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        ("center", (1.0, 1.0)),
        ("front-left", (0.0, 0.0)),
        ("front-center", (1.0, 0.0)),
        ("back-right", (2.0, 2.0)),
        ("left-center", (0.0, 1.0)),
        ("right-center", (2.0, 1.0)),
    ],
)
def test_move_to_puts_the_anchor_on_the_target(anchor: str, expected: tuple[float, float]) -> None:
    moved = move_to(SQUARE, anchor, (10.0, 10.0))  # type: ignore[arg-type]
    offset = np.array([10.0, 10.0]) - np.array(expected)
    assert approx_equal(moved, SQUARE + offset, tol=TOL)


def test_move_to_centroid_and_unknown_anchor() -> None:
    pts = np.array([[0.0, 0.0], [0.0, 0.0], [3.0, 0.0]])
    assert approx_equal(move_to(pts, "centroid", (0.0, 0.0))[2], (2.0, 0.0), tol=TOL)
    with pytest.raises(UnknownRefError) as caught:
        move_to(pts, "front-centre", (0.0, 0.0))  # type: ignore[arg-type]
    assert "front-center" in caught.value.hint


def test_rotate_about_center_point_and_facings() -> None:
    assert approx_equal(rotate(SQUARE, 90.0)[0], (2.0, 0.0), tol=TOL)
    assert approx_equal(rotate(SQUARE, 90.0, about=(0.0, 0.0))[1], (0.0, 2.0), tol=TOL)
    s = rotate(make_facing_set(("A", 1, 0, 10.0), ("B", -1, 0, 0.0)), 90.0)
    assert approx_equal(s.get("A").point, (0.0, 1.0), tol=TOL)
    assert approx_equal(s.get("A").facing, 100.0, tol=1e-9)


def test_rotate_keeps_arcs_exact() -> None:
    turned = rotate(arc((0, 0), 2.0, 0.0, 90.0), 90.0, about=(0.0, 0.0))
    assert isinstance(turned, ArcCurve)
    assert approx_equal(turned.start_deg, 90.0, tol=1e-9)


def test_scale_and_negative_scale() -> None:
    assert approx_equal(scale(SQUARE, 2.0)[2], (3.0, 3.0), tol=TOL)
    assert approx_equal(scale(SQUARE, 2.0, 1.0, about=(0.0, 0.0))[2], (4.0, 2.0), tol=TOL)
    with pytest.raises(GeometryError) as caught:
        scale(SQUARE, -1.0)
    assert "mirror" in caught.value.hint


def test_expand_and_contract_move_points_radially() -> None:
    pts = np.array([[0.0, 0.0], [3.0, 0.0], [0.0, -4.0]])
    grown = expand(pts, 1.0, about=(0.0, 0.0))
    assert approx_equal(grown, [[0.0, 0.0], [4.0, 0.0], [0.0, -5.0]], tol=TOL)
    shrunk = contract(pts, 2.0, about=(0.0, 0.0))
    assert approx_equal(shrunk, [[0.0, 0.0], [1.0, 0.0], [0.0, -2.0]], tol=TOL)
    with pytest.raises(GeometryError) as caught:
        contract(pts, 3.5, about=(0.0, 0.0))
    assert caught.value.context["max_d"] == 3.0


def test_expand_curve_maps_its_points() -> None:
    grown = expand(polyline((-1, 0), (1, 0)), 1.0)
    assert isinstance(grown, Polyline)
    assert grown.points == ((-2.0, 0.0), (2.0, 0.0))
    ring = expand(circle((0, 0), 1.0), 1.0, about=(0.0, 0.0))
    assert approx_equal(np.linalg.norm(ring.point_at_u(0.3)), 2.0, tol=1e-2)


def test_set_interval_respaces_about_the_center() -> None:
    pts = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])
    wide = set_interval(pts, 2.0)
    assert approx_equal(wide[:, 0], [-1.5, 0.5, 2.5, 4.5], tol=TOL)
    start = set_interval(pts, 0.5, anchor="start")
    assert approx_equal(start[:, 0], [0.0, 0.5, 1.0, 1.5], tol=TOL)


def test_set_interval_follows_a_bend() -> None:
    pts = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]])
    tight = set_interval(pts, 1.0, anchor="start")
    assert approx_equal(tight, [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]], tol=TOL)


def test_set_interval_on_a_set_uses_selection_order() -> None:
    s = make_facing_set(("A", 0, 0, 0), ("B", 4, 0, 0), ("C", 2, 0, 0))
    out = set_interval(s, 1.0, sel=["A", "C", "B"])
    assert [out.get(i).x for i in ("A", "C", "B")] == [1.0, 2.0, 3.0]
    with pytest.raises(GeometryError):
        set_interval(np.zeros((3, 2)), 1.0)


@pytest.mark.parametrize(
    ("axis", "expected_first"),
    [("center", (4.0, 0.0)), ("x", (-1.0, 0.0)), ("y", (1.0, 2.0)), (((0, 0), (1, 1)), (0.0, 1.0))],
)
def test_mirror_axes(axis: object, expected_first: tuple[float, float]) -> None:
    pts = np.array([[1.0, 0.0], [3.0, 1.0], [4.0, 2.0]])
    assert approx_equal(mirror(pts, axis)[0], expected_first, tol=TOL)  # type: ignore[arg-type]


def test_mirror_reflects_facings() -> None:
    s = mirror(make_facing_set(("A", 1, 0, 90.0)), "x")
    assert s.get("A").point == (-1.0, 0.0)
    assert approx_equal(s.get("A").facing, 270.0, tol=1e-9)


def test_symmetrize_pairs_by_assignment() -> None:
    s = make_facing_set(
        ("L1", -1, 0, 30.0),
        ("L2", -3, 1, 0.0),
        ("R1", 3.2, 1.1, 0.0),
        ("R2", 0.9, 0.2, 0.0),
        ("M", 0, 5, 0.0),
    )
    out = symmetrize(s)
    assert out.get("R2").point == (1.0, 0.0)
    assert approx_equal(out.get("R2").facing, 330.0, tol=1e-9)
    assert out.get("R1").point == (3.0, 1.0)
    assert out.get("M").point == (0.0, 5.0)
    assert out.get("L1") == s.get("L1")


def test_symmetrize_with_explicit_pairs_and_right_source() -> None:
    s = make_facing_set(("L", -1, 0, 0.0), ("R", 4, 4, 0.0))
    assert symmetrize(s, pairs={"L": "R"}).get("R").point == (1.0, 0.0)
    assert symmetrize(s, source_side="right").get("L").point == (-4.0, 4.0)
    with pytest.raises(SchemaError):
        symmetrize(s, pairs={"L": "L"})


def test_symmetrize_respects_pins() -> None:
    s = make_facing_set(("L", -1, 0, 0.0), ("R", 4, 4, 0.0), pinned=("R",))
    with pytest.raises(LockedError):
        symmetrize(s)


def test_skew_shears_about_the_anchor() -> None:
    out = skew(SQUARE, deg_x=45.0, about=(0.0, 0.0))
    assert approx_equal(out[2], (4.0, 2.0), tol=TOL)


def test_affine_accepts_2x3_and_3x3() -> None:
    m = [[1.0, 0.0, 5.0], [0.0, 1.0, 0.0]]
    assert affine(SQUARE, m)[0].tolist() == [5.0, 0.0]
    assert affine(SQUARE, [*m, [0.0, 0.0, 1.0]])[0].tolist() == [5.0, 0.0]
    with pytest.raises(GeometryError):
        affine(SQUARE, [[1.0, 0.0], [0.0, 1.0]])
    with pytest.raises(GeometryError):
        affine(SQUARE, [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])


def test_snap_modes_and_report() -> None:
    pts = np.array([[0.24, 0.26], [1.0, 1.0], [-0.25, 0.0]])
    out, report = snap(pts, 0.5)
    assert approx_equal(out, [[0.0, 0.5], [1.0, 1.0], [-0.5, 0.0]], tol=TOL)
    assert report.moved == 2
    assert approx_equal(report.max_shift, math.hypot(0.24, 0.24), tol=TOL)
    floored, _ = snap(pts, 0.5, mode="floor", axes="x")
    assert approx_equal(floored[0], (0.0, 0.26), tol=TOL)
    ceiled, _ = snap(np.array([[0.3, 0.0]]), 0.1, mode="ceil")
    assert approx_equal(ceiled[0], (0.3, 0.0), tol=TOL)


def test_snap_set_and_curve() -> None:
    s, report = snap(make_facing_set(("A", 0.4, 0.6, 0.0)), 1.0)
    assert s.get("A").point == (0.0, 1.0)
    assert report.moved == 1
    seg, _ = snap(line((0.1, 0.1), (2.9, 0.0)), 1.0)
    assert isinstance(seg, Segment)
    assert seg.b == (3.0, 0.0)


def test_snap_rejects_bad_precision() -> None:
    with pytest.raises(GeometryError):
        snap(SQUARE, 0.0)


def test_snap_to_guides_pulls_close_points_only() -> None:
    sheet = field_sheet().model_copy(
        update={
            "guides": (
                GuideLine(id="y5", kind="yard", points=((5.0, 0.0), (5.0, 20.0))),
                GuideLine(id="h", kind="hash", points=((-20.0, 6.0), (20.0, 6.0))),
            )
        }
    )
    pts = np.array([[4.7, 2.0], [0.0, 5.8], [0.0, 2.0]])
    out = snap_to_guides(pts, sheet, tol=0.5)
    assert approx_equal(out, [[5.0, 2.0], [0.0, 6.0], [0.0, 2.0]], tol=TOL)
    yards_only = snap_to_guides(pts, sheet, tol=0.5, kinds=["yard"])
    assert approx_equal(yards_only[1], (0.0, 5.8), tol=TOL)
    with pytest.raises(UnknownRefError):
        snap_to_guides(pts, sheet, tol=0.5, kinds=["yards"])


def test_align_values() -> None:
    pts = np.array([[0.0, 1.0], [2.0, 3.0]])
    assert align(pts, "y", "mean")[:, 1].tolist() == [2.0, 2.0]
    assert align(pts, "x", "max")[:, 0].tolist() == [2.0, 2.0]
    assert align(pts, "x", 7.0)[:, 0].tolist() == [7.0, 7.0]


def test_distribute_keeps_order_along_the_axis() -> None:
    pts = np.array([[5.0, 0.0], [0.0, 1.0], [1.0, 2.0], [10.0, 3.0]])
    out = distribute(pts, "x")
    assert approx_equal(out[:, 0], [20 / 3, 0.0, 10 / 3, 10.0], tol=TOL)
    assert out[:, 1].tolist() == [0.0, 1.0, 2.0, 3.0]
    spread = distribute(pts, "x", start=0.0, end=30.0)
    assert approx_equal(spread[:, 0], [20.0, 0.0, 10.0, 30.0], tol=TOL)


def test_interpolate_forms() -> None:
    a = np.array([[0.0, 0.0], [2.0, 0.0]])
    b = np.array([[0.0, 4.0], [2.0, 8.0]])
    assert interpolate_forms(a, b, 0.25).tolist() == [[0.0, 1.0], [2.0, 2.0]]
    with pytest.raises(GeometryError):
        interpolate_forms(a, b[:1], 0.5)
    with pytest.raises(GeometryError):
        interpolate_forms(a, b, 1.5)


def test_composite_curves_keep_their_parts() -> None:
    curve = join(line((0, 0), (1, 0)), line((1, 0), (1, 1)))
    grown = expand(curve, 1.0, about=(0.0, 0.0))
    assert isinstance(grown, CompositeCurve)
    assert len(grown.parts) == 2
