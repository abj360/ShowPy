#!/usr/bin/env python3

"""
test_svg_text.py --- tests SVG path import and the built-in stroke font
"""

import math

import numpy as np
import pytest

from showpy.core.errors import SchemaError, UnknownRefError
from showpy.core.types import Box2
from showpy.core.util import approx_equal
from showpy.geo.shapes import svg_outline, text_outline
from showpy.geo.svg import parse_path

BOX = Box2(min=(0.0, 0.0), max=(10.0, 10.0))


def test_parse_path_absolute_and_relative_lines() -> None:
    subpaths = parse_path("M0 0 L10 0 l0 10 h-10 z")
    assert len(subpaths) == 1
    assert subpaths[0].closed
    assert approx_equal(subpaths[0].pieces[0].end, (10.0, 0.0))


def test_parse_path_splits_subpaths() -> None:
    assert len(parse_path("M0 0 L1 0 M5 5 L6 5")) == 2


def test_parse_path_rejects_unknown_command() -> None:
    with pytest.raises(SchemaError):
        parse_path("M0 0 X4 4")


def test_svg_outline_fits_box_and_keeps_aspect() -> None:
    curves = svg_outline("M0 0 L20 0 L20 10 Z", BOX)
    pts = np.vstack([c.to_polyline(0.01) for c in curves])
    assert approx_equal(pts[:, 0].min(), 0.0)
    assert approx_equal(pts[:, 0].max(), 10.0)
    assert approx_equal(pts[:, 1].max() - pts[:, 1].min(), 5.0)


def test_svg_outline_flips_y_so_top_is_upstage() -> None:
    curve = svg_outline("M0 0 L0 10", BOX)[0]
    assert curve.point_at(0.0)[1] > curve.point_at(curve.length())[1]


def test_svg_cubic_and_arc_commands() -> None:
    cubic = svg_outline("M0 0 C0 10 10 10 10 0", BOX, keep_aspect=False)[0]
    assert cubic.length() > 10.0
    wide = Box2(min=(0.0, 0.0), max=(10.0, 5.0))
    half = svg_outline("M0 0 A5 5 0 0 1 10 0", wide, flip_y=False)[0]
    assert approx_equal(half.length(), math.pi * 5, tol=1e-3)


def test_svg_outline_selects_path_index() -> None:
    assert len(svg_outline("M0 0 L1 0 M0 1 L1 1", BOX, path_index=1)) == 1


def test_text_outline_height_and_position() -> None:
    curves = text_outline("HI", "simplex", 2.0, (0.0, 0.0))
    pts = np.vstack([c.to_polyline(0.01) for c in curves])
    assert approx_equal(pts[:, 1].max() - pts[:, 1].min(), 2.0)
    assert approx_equal(pts[:, 0].min(), 0.0)


def test_text_outline_unknown_font_or_glyph() -> None:
    with pytest.raises(UnknownRefError):
        text_outline("A", "comic", 1.0, (0, 0))
    with pytest.raises(UnknownRefError):
        text_outline("~", "simplex", 1.0, (0, 0))


def test_regression_parse_path_accepts_packed_arc_flags() -> None:
    packed = parse_path("M0 0 a1 1 0 011 1")
    spaced = parse_path("M0 0 a1 1 0 0 1 1 1")
    assert len(packed[0].pieces) == 1
    assert packed[0].pieces[0] == spaced[0].pieces[0]
    assert approx_equal(packed[0].pieces[0].end, (1.0, 1.0))
