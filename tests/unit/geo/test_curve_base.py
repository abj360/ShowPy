#!/usr/bin/env python3

"""
test_curve_base.py --- encodes the audited CurveBase defects as strict xfails

Contains:
    test_regression_to_polyline_rejects_nonpositive_tol: rejects tol <= 0 instead of hanging
    test_regression_to_polyline_rejects_nan_tol: rejects a NaN tol instead of hanging
    test_regression_to_polyline_caps_its_output: rejects a polyline over the point cap
    test_regression_polyline_to_polyline_rejects_bad_tol: validates tol on the exact override
    test_to_polyline_matches_recursive_subdivision: keeps the stations of the recursive version
    test_regression_to_polyline_keeps_periodic_curves: keeps the length of a 32-turn arc
    test_regression_project_finds_global_minimum_on_spiral: finds the closest point on a spiral
    test_regression_project_finds_global_minimum_on_zigzag: finds the closest point on a zigzag
"""

import itertools
import sys

import numpy as np
import pytest

from showpy.core.errors import GeometryError
from showpy.geo import curve_base
from showpy.geo.curve_base import INITIAL_SPLITS, MAX_SUBDIVISION_DEPTH, Curve, CurveBase
from showpy.geo.shapes import arc, circle, polyline, spiral
from tests.helpers import fails_after

SIGNAL_TIMEOUT_S = 5
POLYLINE_TEST_CAP = 200
TURNS = 32
FULL_TURN_DEG = 360.0
EXPECTED_ARC_LENGTH_M = 2 * np.pi * TURNS
LENGTH_TOL_M = 1.0
SPIRAL_TURNS = 40
SPIRAL_PROBE = (10.05, 3.3)
ZIGZAG_VERTICES = 2000
ZIGZAG_STEP_M = 0.01
ZIGZAG_AMPLITUDE_M = 0.05
ZIGZAG_PROBE = (15.2, 0.0)
DENSE_SAMPLES = 400_001
PROJECT_TOL_M = 1e-3

timeout_on_hang = fails_after(SIGNAL_TIMEOUT_S)
skip_without_signals = pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")


def _closest_distance(curve: Curve, probe: tuple[float, float]) -> tuple[float, float]:
    """Returns the brute-force minimum distance and the distance of curve.project's answer."""
    dense = curve.point_at(np.linspace(0.0, curve.length(), DENSE_SAMPLES))
    target = np.asarray(probe)
    truth = float(np.min(np.linalg.norm(dense - target, axis=1)))
    found = curve.point_at(curve.project(probe))
    return truth, float(np.linalg.norm(found - target))


@timeout_on_hang
@skip_without_signals
def test_regression_to_polyline_rejects_nonpositive_tol() -> None:
    """Rejects a non-positive tolerance instead of recursing forever (audit 9.1; P0 #7)."""
    with pytest.raises(GeometryError):
        circle((0.0, 0.0), 1.0).to_polyline(tol=0.0)


@timeout_on_hang
@skip_without_signals
def test_regression_to_polyline_rejects_nan_tol() -> None:
    """Rejects a NaN tolerance instead of recursing forever (audit 9.1; P0 #7)."""
    with pytest.raises(GeometryError):
        circle((0.0, 0.0), 1.0).to_polyline(tol=float("nan"))


def test_regression_to_polyline_caps_its_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stops subdividing once the point count would pass the cap."""
    monkeypatch.setattr(curve_base, "MAX_POINTS", POLYLINE_TEST_CAP)
    curve = circle((0.0, 0.0), 1.0)
    assert len(curve.to_polyline(tol=0.01)) <= POLYLINE_TEST_CAP
    with pytest.raises(GeometryError, match="polyline points"):
        curve.to_polyline(tol=1e-9)


def _recursive_stations(
    curve: CurveBase, lo: float, hi: float, tol: float, depth: int
) -> list[float]:
    """Returns the stations of the original recursive subdivision, as a reference."""
    if depth >= MAX_SUBDIVISION_DEPTH or curve._chord_error(lo, hi) <= tol:
        return [hi]
    mid = (lo + hi) / 2
    return _recursive_stations(curve, lo, mid, tol, depth + 1) + _recursive_stations(
        curve, mid, hi, tol, depth + 1
    )


@pytest.mark.parametrize("tol", [0.1, 0.01, 1e-4])
@pytest.mark.parametrize(
    "curve",
    [circle((1.0, 2.0), 5.0), arc((0.0, 0.0), 2.0, 0.0, 270.0), spiral((0.0, 0.0), 1.0, 4.0, 3.0)],
)
def test_to_polyline_matches_recursive_subdivision(curve: CurveBase, tol: float) -> None:
    """Keeps the same stations in the same order as the recursive version."""
    breaks = np.unique(np.concatenate([curve._breaks(), [0.0, curve.length()]]))
    stations = [float(breaks[0])]
    for a, b in itertools.pairwise(breaks):
        edges = np.linspace(a, b, INITIAL_SPLITS + 1)
        for lo, hi in itertools.pairwise(edges):
            stations.extend(_recursive_stations(curve, float(lo), float(hi), tol, 0))
    assert np.array_equal(curve.to_polyline(tol), curve._points(np.array(stations)))


@pytest.mark.parametrize("bad", [0.0, -0.1, float("nan"), float("inf")])
def test_regression_polyline_to_polyline_rejects_bad_tol(bad: float) -> None:
    """Rejects a bad tolerance even where the override ignores it."""
    with pytest.raises(GeometryError):
        polyline((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)).to_polyline(tol=bad)


@pytest.mark.defect("UP-9.1-10")
@pytest.mark.xfail(strict=True, reason="UP-9.1-10")
def test_regression_to_polyline_keeps_periodic_curves() -> None:
    """Keeps a 32-turn arc's length instead of collapsing it to 9 identical points (audit 9.1)."""
    curve = arc((0.0, 0.0), 1.0, 0.0, TURNS * FULL_TURN_DEG)
    pts = curve.to_polyline()
    length = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
    assert abs(length - EXPECTED_ARC_LENGTH_M) < LENGTH_TOL_M


@pytest.mark.defect("UP-9.1-11")
@pytest.mark.xfail(strict=True, reason="UP-9.1-11")
def test_regression_project_finds_global_minimum_on_spiral() -> None:
    """Projects onto the closest point of a 40-turn spiral, not a local minimum (audit 9.1)."""
    truth, found = _closest_distance(spiral((0.0, 0.0), 1.0, 20.0, SPIRAL_TURNS), SPIRAL_PROBE)
    assert found <= truth + PROJECT_TOL_M


@pytest.mark.defect("UP-9.1-12")
@pytest.mark.xfail(strict=True, reason="UP-9.1-12")
def test_regression_project_finds_global_minimum_on_zigzag() -> None:
    """Projects onto the closest point of a 2,000-vertex zigzag, not a local minimum (audit 9.1)."""
    pts = [
        (i * ZIGZAG_STEP_M, ZIGZAG_AMPLITUDE_M if i % 2 else -ZIGZAG_AMPLITUDE_M)
        for i in range(ZIGZAG_VERTICES)
    ]
    truth, found = _closest_distance(polyline(*pts), ZIGZAG_PROBE)
    assert found <= truth + PROJECT_TOL_M
