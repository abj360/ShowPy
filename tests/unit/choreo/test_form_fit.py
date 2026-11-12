#!/usr/bin/env python3

"""
test_form_fit.py --- tests line, circle and arc recognition of imported spots
"""

import math

import numpy as np
import pytest

from stage.choreo.form_fit import detect_form, fit_circle, fit_line
from stage.choreo.forms import slots
from stage.core.errors import SchemaError
from stage.core.types import Points
from stage.core.util import approx_equal, seeded_rng

TOL = 1e-6


def _ring(center: tuple[float, float], radius: float, degrees: list[float]) -> Points:
    theta = np.radians(degrees)
    return np.stack([center[0] + radius * np.cos(theta), center[1] + radius * np.sin(theta)], 1)


def test_fit_line_returns_extremes_and_residual() -> None:
    a, b, residual = fit_line([(0, 1), (2, 3), (4, 5)])
    assert approx_equal([a, b], [[0, 1], [4, 5]], TOL)
    assert residual < TOL


def test_fit_circle_recovers_noisy_circle() -> None:
    pts = _ring((3.0, 7.0), 4.0, list(range(0, 360, 30)))
    noisy = pts + seeded_rng(1).normal(0.0, 0.01, pts.shape)
    center, radius, residual = fit_circle(noisy)
    assert approx_equal(center, (3.0, 7.0), 0.02)
    assert approx_equal(radius, 4.0, 0.02)
    assert residual < 0.05


def test_fit_circle_is_a_geometric_least_squares_optimum_on_a_short_arc() -> None:
    pts = _ring((0.0, 0.0), 10.0, [80, 85, 90, 95, 100])
    pts = pts + np.array([[0.0, 0.05], [0.0, -0.05], [0.0, 0.05], [0.0, -0.05], [0.0, 0.05]])
    (cx, cy), radius, _ = fit_circle(pts)

    def sse(params: Points) -> float:
        dist = np.linalg.norm(pts - params[:2], axis=1)
        return float(((dist - params[2]) ** 2).sum())

    best = np.array([cx, cy, radius])
    for step in np.eye(3) * 1e-3:
        assert sse(best) <= sse(best + step) + 1e-12
        assert sse(best) <= sse(best - step) + 1e-12


def test_fit_circle_needs_three_points() -> None:
    with pytest.raises(SchemaError):
        fit_circle([(0, 0), (1, 1)])


def test_detects_line_and_orders_members_along_it() -> None:
    form = detect_form([(4, 0), (0, 0), (2, 0.02)], members=["C", "A", "B"])
    assert form.kind == "line"
    assert form.members == ("A", "B", "C")
    assert approx_equal(slots(form)[:, 0], [0, 2, 4], 0.05)


def test_detects_circle_with_ccw_member_order() -> None:
    pts = _ring((0.0, 5.0), 3.0, [0, 90, 180, 270])
    form = detect_form(pts[[2, 0, 3, 1]], members=["W", "E", "S", "N"])
    assert form.kind == "circle"
    assert form.members == ("E", "N", "W", "S")
    assert approx_equal(slots(form), pts, TOL)


def test_detects_arc_from_angular_coverage() -> None:
    pts = _ring((0.0, 0.0), 5.0, [30, 60, 90, 120])
    form = detect_form(pts)
    assert form.kind == "arc"
    curve = form.geometry["curve"]
    assert approx_equal(curve["sweep_deg"], 90.0, TOL)
    assert approx_equal(slots(form), pts, TOL)


def test_unfit_points_become_custom() -> None:
    pts = [(0, 0), (5, 0), (0, 3), (9, 9), (2, 7)]
    form = detect_form(pts, tol=0.1)
    assert form.kind == "custom"
    assert approx_equal(slots(form), pts)


def test_single_and_repeated_points_are_custom() -> None:
    assert detect_form([(1, 1)]).kind == "custom"
    assert detect_form([(1, 1), (1, 1)]).kind == "custom"


def test_member_count_must_match_points() -> None:
    with pytest.raises(SchemaError):
        detect_form([(0, 0), (1, 0)], members=["A"])


def test_loose_tolerance_accepts_rough_line() -> None:
    rough = [(0.0, 0.0), (1.0, 0.3), (2.0, -0.3), (3.0, 0.0)]
    assert detect_form(rough, tol=0.1).kind != "line"
    assert detect_form(rough, tol=0.5).kind == "line"
    assert math.isfinite(fit_line(rough)[2])
