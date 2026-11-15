#!/usr/bin/env python3

"""
test_replace.py --- tests the similarity fit between matched sheet points

Contains:
    test_similarity_fit_needs_spread_points(): rejects matched points with no spread
    test_regression_similarity_fit_rejects_mirrored_landmarks(): rejects a mirrored landmark set
        instead of fitting a wrong scale
    test_regression_similarity_fit_rejects_coincident_targets(): rejects coincident target landmarks
        instead of collapsing the show
    test_regression_similarity_fit_empty_input_raises_without_warning(): raises GeometryError for no
        matches without a numpy RuntimeWarning
    test_regression_similarity_fit_rejects_nan_input(): raises GeometryError for NaN points instead
        of leaking LinAlgError
    test_regression_similarity_fit_rejects_wrong_shape(): raises GeometryError for non-2D points
        instead of leaking numpy errors
"""

import warnings

import numpy as np
import pytest

from showpy.core.errors import GeometryError
from showpy.sheet.replace import similarity_fit


def test_similarity_fit_needs_spread_points() -> None:
    with pytest.raises(GeometryError):
        similarity_fit(np.array([[1.0, 1.0], [1.0, 1.0]]), np.array([[0.0, 0.0], [2.0, 2.0]]))


@pytest.mark.defect("UP-9.4-02")
@pytest.mark.xfail(strict=True, reason="UP-9.4-02")
def test_regression_similarity_fit_rejects_mirrored_landmarks() -> None:
    """Rejects a mirrored landmark set instead of fitting a wrong scale (audit 9.4; P0 #11)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0], [0.0, 3.0]])
    dst = np.array([[0.0, 0.0], [-4.0, 0.0], [0.0, 3.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


@pytest.mark.defect("UP-9.4-03")
@pytest.mark.xfail(strict=True, reason="UP-9.4-03")
def test_regression_similarity_fit_rejects_coincident_targets() -> None:
    """Rejects coincident target landmarks instead of collapsing the show (audit 9.4)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0]])
    dst = np.array([[1.0, 1.0], [1.0, 1.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


@pytest.mark.defect("UP-9.4-04")
@pytest.mark.xfail(strict=True, reason="UP-9.4-04")
def test_regression_similarity_fit_empty_input_raises_without_warning() -> None:
    """Raises GeometryError for no matches without a numpy RuntimeWarning (audit 9.4)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(GeometryError):
            similarity_fit(np.empty((0, 2)), np.empty((0, 2)))


@pytest.mark.defect("UP-9.4-05")
@pytest.mark.xfail(strict=True, reason="UP-9.4-05")
def test_regression_similarity_fit_rejects_nan_input() -> None:
    """Raises GeometryError for NaN points instead of leaking LinAlgError (audit 9.4)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0]])
    dst = np.array([[np.nan, 0.0], [1.0, 0.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


@pytest.mark.defect("UP-9.4-06")
@pytest.mark.xfail(strict=True, reason="UP-9.4-06")
def test_regression_similarity_fit_rejects_wrong_shape() -> None:
    """Raises GeometryError for non-2D points instead of leaking numpy errors (audit 9.4)."""
    src = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, src.copy())
