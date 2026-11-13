#!/usr/bin/env python3

"""
test_form_fit.py --- tests line, circle and arc recognition of imported spots

Contains:
    test_regression_detect_form_keeps_every_spot_within_tolerance: returns slots within tol of every
    test_regression_detect_form_does_not_call_a_2x2_block_a_circle: refuses to classify four block
    test_regression_detect_form_does_not_call_a_triangle_an_arc: refuses to classify three triangle
    test_regression_detect_form_tolerates_one_outlier_on_a_clean_circle: recognizes the circle of 12
    test_regression_fit_line_rejects_empty_input_with_schema_error: raises SchemaError for no
    test_regression_fit_circle_rejects_collinear_points: raises SchemaError for collinear points
"""

import math

import numpy as np
import pytest

from showpy.choreo.form_fit import detect_form, fit_circle, fit_line
from showpy.choreo.forms import slots
from showpy.core.errors import SchemaError
from showpy.core.types import Points
from showpy.core.util import approx_equal, seeded_rng

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


FIT_TOL = 0.1
UNEVEN_LINE = [(0.0, 0.0), (1.0, 0.0), (10.0, 0.0)]
SQUARE_BLOCK = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
TRIANGLE = [(0.0, 0.0), (4.0, 0.0), (0.0, 3.0)]
RING_SPOTS = 12
RING_RADIUS = 5.0
OUTLIER = (20.0, 20.0)
COLLINEAR = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]


@pytest.mark.defect("UP-9.6-27")
@pytest.mark.xfail(strict=True, reason="UP-9.6-27")
def test_regression_detect_form_keeps_every_spot_within_tolerance() -> None:
    """Returns slots within tol of every input spot, else a custom form (audit 9.6)."""
    form = detect_form(UNEVEN_LINE, tol=FIT_TOL)
    gaps = np.linalg.norm(np.asarray(UNEVEN_LINE)[:, None, :] - slots(form)[None, :, :], axis=2)
    assert float(gaps.min(axis=1).max()) <= FIT_TOL


@pytest.mark.defect("UP-9.6-28")
@pytest.mark.xfail(strict=True, reason="UP-9.6-28")
def test_regression_detect_form_does_not_call_a_2x2_block_a_circle() -> None:
    """Refuses to classify four block corners as a circle (audit 9.6)."""
    assert detect_form(SQUARE_BLOCK).kind != "circle"


@pytest.mark.defect("UP-9.6-29")
@pytest.mark.xfail(strict=True, reason="UP-9.6-29")
def test_regression_detect_form_does_not_call_a_triangle_an_arc() -> None:
    """Refuses to classify three triangle corners as an arc (audit 9.6)."""
    assert detect_form(TRIANGLE).kind != "arc"


@pytest.mark.defect("UP-9.6-30")
@pytest.mark.xfail(strict=True, reason="UP-9.6-30")
def test_regression_detect_form_tolerates_one_outlier_on_a_clean_circle() -> None:
    """Recognizes the circle of 12 ring spots despite one far outlier (audit 9.6)."""
    pts = _ring((0.0, 0.0), RING_RADIUS, [i * 360 / RING_SPOTS for i in range(RING_SPOTS)])
    form = detect_form(np.vstack([pts, OUTLIER]), tol=FIT_TOL)
    assert form.kind == "circle"
    curve = form.geometry["curve"]
    assert approx_equal(curve["center"], (0.0, 0.0), FIT_TOL)
    assert approx_equal(curve["radius"], RING_RADIUS, FIT_TOL)


@pytest.mark.defect("UP-9.6-31")
@pytest.mark.xfail(strict=True, reason="UP-9.6-31")
def test_regression_fit_line_rejects_empty_input_with_schema_error() -> None:
    """Raises SchemaError for no points, with no numpy warning or ValueError (audit 9.6)."""
    with pytest.raises(SchemaError):
        fit_line([])


@pytest.mark.defect("UP-9.6-32")
@pytest.mark.xfail(strict=True, reason="UP-9.6-32")
def test_regression_fit_circle_rejects_collinear_points() -> None:
    """Raises SchemaError for collinear points instead of a garbage circle (audit 9.6)."""
    with pytest.raises(SchemaError):
        fit_circle(COLLINEAR)
