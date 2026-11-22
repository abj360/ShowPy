#!/usr/bin/env python3

"""
test_set_transforms.py --- tests set transforms: selections, pins, facings and dispatch

Contains:
    test_set_transforms_dispatch_points_curves_and_sets: runs one transform on each input kind
"""

import numpy as np
import pytest

from showpy.choreo.set_transforms import mirror, rotate, set_interval, shift, symmetrize
from showpy.core.errors import GeometryError, LockedError, SchemaError
from showpy.core.util import approx_equal
from showpy.geo.curves import Segment
from showpy.geo.shapes import line
from tests.helpers import make_facing_set

TOL = 1e-9
SQUARE = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]])


def test_set_transforms_dispatch_points_curves_and_sets() -> None:
    assert shift(SQUARE, 1.0, -1.0)[0].tolist() == [1.0, -1.0]
    moved = shift(line((0, 0), (1, 0)), 0.0, 2.0)
    assert isinstance(moved, Segment)
    assert moved.a == (0.0, 2.0)
    s = shift(make_facing_set(("A", 0, 0, 0), ("B", 1, 0, 0)), 1.0, 0.0, sel=["B"])
    assert s.get("A").x == 0.0
    assert s.get("B").x == 2.0


def test_rotate_turns_set_facings_with_the_spots() -> None:
    s = rotate(make_facing_set(("A", 1, 0, 10.0), ("B", -1, 0, 0.0)), 90.0)
    assert approx_equal(s.get("A").point, (0.0, 1.0), abs_tol=TOL)
    assert approx_equal(s.get("A").facing, 100.0, abs_tol=1e-9)


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


def test_string_selector_must_be_all() -> None:
    with pytest.raises(SchemaError):
        shift(make_facing_set(("A", 0, 0, 0)), 1.0, 0.0, sel="A")


def test_set_interval_on_a_set_uses_selection_order() -> None:
    s = make_facing_set(("A", 0, 0, 0), ("B", 4, 0, 0), ("C", 2, 0, 0))
    out = set_interval(s, 1.0, sel=["A", "C", "B"])
    assert [out.get(i).x for i in ("A", "C", "B")] == [1.0, 2.0, 3.0]
    with pytest.raises(GeometryError):
        set_interval(np.zeros((3, 2)), 1.0)


def test_mirror_reflects_facings() -> None:
    s = mirror(make_facing_set(("A", 1, 0, 90.0)), "x")
    assert s.get("A").point == (-1.0, 0.0)
    assert approx_equal(s.get("A").facing, 270.0, abs_tol=1e-9)


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
    assert approx_equal(out.get("R2").facing, 330.0, abs_tol=1e-9)
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
