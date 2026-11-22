#!/usr/bin/env python3

"""
test_set_snapping.py --- tests snapping, guides, align and distribute on sets and points

Contains:
    test_snap_set_and_curve: snaps a set and a curve to a grid
    test_snap_to_guides_pulls_close_points_only: pulls points within tolerance onto guide lines
    test_align_and_distribute_move_set_selections: lines up and spreads on-field spots
"""

import numpy as np
import pytest

from showpy.choreo.set_snapping import align, distribute, snap, snap_to_guides
from showpy.core.errors import GeometryError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.geo.curves import Segment
from showpy.geo.shapes import line
from showpy.sheet.models import GuideLine
from tests.fixtures.shows import field_sheet
from tests.helpers import make_facing_set

TOL = 1e-9


def test_snap_set_and_curve() -> None:
    s, report = snap(make_facing_set(("A", 0.4, 0.6, 0.0)), 1.0)
    assert s.get("A").point == (0.0, 1.0)
    assert report.moved == 1
    seg, _ = snap(line((0.1, 0.1), (2.9, 0.0)), 1.0)
    assert isinstance(seg, Segment)
    assert seg.b == (3.0, 0.0)


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
    assert approx_equal(out, [[5.0, 2.0], [0.0, 6.0], [0.0, 2.0]], abs_tol=TOL)
    yards_only = snap_to_guides(pts, sheet, tol=0.5, kinds=["yard"])
    assert approx_equal(yards_only[1], (0.0, 5.8), abs_tol=TOL)
    with pytest.raises(UnknownRefError):
        snap_to_guides(pts, sheet, tol=0.5, kinds=["yards"])


def test_snap_to_guides_rejects_a_negative_tolerance() -> None:
    with pytest.raises(GeometryError):
        snap_to_guides(np.zeros((1, 2)), field_sheet(), tol=-1.0)


def test_align_and_distribute_move_set_selections() -> None:
    s = make_facing_set(("A", 0, 1, 0), ("B", 4, 3, 0), ("C", 1, 2, 0))
    lined = align(s, "y", "min")
    assert [lined.get(i).y for i in "ABC"] == [1.0, 1.0, 1.0]
    spread = distribute(s, "x", start=0.0, end=8.0)
    assert [spread.get(i).x for i in "ABC"] == [0.0, 8.0, 4.0]
