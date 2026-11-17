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
    test_regression_similarity_fit_rejects_shape_mismatch(): rejects src and dst of different shape
    test_regression_similarity_fit_rejects_infinite_input(): rejects infinite coordinates
    test_regression_similarity_fit_rejects_one_point(): rejects a single matched point
    test_regression_similarity_fit_error_codes(): reports a stable code for each refusal
    test_similarity_fit_accepts_two_points_and_collinear_points(): fits input with no handedness
    test_regression_similarity_fit_collinear_mirror_is_not_refused(): does not call float noise a
        reflection
    test_regression_similarity_fit_rejects_ragged_input(): rejects ragged rows with BAD_SHAPE
    test_regression_similarity_fit_rejects_non_numeric_input(): rejects strings with BAD_SHAPE
    test_regression_similarity_fit_rejects_overflowing_coordinates(): refuses huge coordinates
        with NOT_FINITE instead of returning a non-finite matrix
    test_similarity_fit_handles_large_finite_coordinates(): still fits coordinates near 1e150
"""

import warnings

import numpy as np
import pytest

from showpy.core.errors import GeometryError
from showpy.sheet.replace import similarity_fit


def test_similarity_fit_needs_spread_points() -> None:
    with pytest.raises(GeometryError):
        similarity_fit(np.array([[1.0, 1.0], [1.0, 1.0]]), np.array([[0.0, 0.0], [2.0, 2.0]]))


def test_regression_similarity_fit_rejects_mirrored_landmarks() -> None:
    """Rejects a mirrored landmark set instead of fitting a wrong scale (audit 9.4; P0 #11)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0], [0.0, 3.0]])
    dst = np.array([[0.0, 0.0], [-4.0, 0.0], [0.0, 3.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


def test_regression_similarity_fit_rejects_coincident_targets() -> None:
    """Rejects coincident target landmarks instead of collapsing the show (audit 9.4)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0]])
    dst = np.array([[1.0, 1.0], [1.0, 1.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


def test_regression_similarity_fit_empty_input_raises_without_warning() -> None:
    """Raises GeometryError for no matches without a numpy RuntimeWarning (audit 9.4)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(GeometryError):
            similarity_fit(np.empty((0, 2)), np.empty((0, 2)))


def test_regression_similarity_fit_rejects_nan_input() -> None:
    """Raises GeometryError for NaN points instead of leaking LinAlgError (audit 9.4)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0]])
    dst = np.array([[np.nan, 0.0], [1.0, 0.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


def test_regression_similarity_fit_rejects_wrong_shape() -> None:
    """Raises GeometryError for non-2D points instead of leaking numpy errors (audit 9.4)."""
    src = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, src.copy())


def test_regression_similarity_fit_rejects_shape_mismatch() -> None:
    """Rejects src and dst of different shapes with BAD_SHAPE."""
    with pytest.raises(GeometryError) as info:
        similarity_fit(np.array([[0.0, 0.0], [4.0, 0.0]]), np.zeros((3, 2)))
    assert info.value.code == "BAD_SHAPE"


def test_regression_similarity_fit_rejects_infinite_input() -> None:
    """Rejects infinite coordinates with NOT_FINITE."""
    src = np.array([[0.0, 0.0], [4.0, 0.0]])
    with pytest.raises(GeometryError) as info:
        similarity_fit(src, np.array([[np.inf, 0.0], [1.0, 0.0]]))
    assert info.value.code == "NOT_FINITE"


def test_regression_similarity_fit_rejects_one_point() -> None:
    """Rejects a single matched point with TOO_FEW_POINTS."""
    with pytest.raises(GeometryError) as info:
        similarity_fit(np.array([[1.0, 2.0]]), np.array([[3.0, 4.0]]))
    assert info.value.code == "TOO_FEW_POINTS"


@pytest.mark.parametrize(
    ("src", "dst", "code"),
    [
        (np.zeros((2, 3)), np.zeros((2, 3)), "BAD_SHAPE"),
        (np.empty((0, 2)), np.empty((0, 2)), "TOO_FEW_POINTS"),
        ([[0.0, 0.0], [4.0, 0.0]], [[np.nan, 0.0], [1.0, 0.0]], "NOT_FINITE"),
        ([[0.0, 0.0], [4.0, 0.0]], [[1.0, 1.0], [1.0, 1.0]], "DEGENERATE"),
        ([[1.0, 1.0], [1.0, 1.0]], [[0.0, 0.0], [4.0, 0.0]], "DEGENERATE"),
        ([[0.0, 0.0], [4.0, 0.0], [0.0, 3.0]], [[0.0, 0.0], [-4.0, 0.0], [0.0, 3.0]], "REFLECTION"),
    ],
)
def test_regression_similarity_fit_error_codes(
    src: list[list[float]], dst: list[list[float]], code: str
) -> None:
    """Reports a stable code and a hint for each refusal."""
    with pytest.raises(GeometryError) as info:
        similarity_fit(np.asarray(src), np.asarray(dst))
    assert info.value.code == code
    assert info.value.hint


def test_similarity_fit_accepts_two_points_and_collinear_points() -> None:
    """Fits two-point and collinear input, which have no handedness."""
    two = similarity_fit(np.array([[0.0, 0.0], [1.0, 0.0]]), np.array([[0.0, 0.0], [0.0, 2.0]]))
    assert two[:2, :2].round(9).tolist() == [[0.0, -2.0], [2.0, 0.0]]
    line = np.array([[0.0, 0.0], [1.0, 0.0], [3.0, 0.0]])
    three = similarity_fit(line, line * 2.0)
    assert three[:2, :2].round(9).tolist() == [[2.0, 0.0], [0.0, 2.0]]


def test_regression_similarity_fit_collinear_mirror_is_not_refused() -> None:
    """Does not call float noise on collinear input a reflection."""
    line = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    fit = similarity_fit(line, np.array([[0.0, 0.0], [-1.0, 0.0], [-2.0, 0.0]]))
    assert np.isfinite(fit).all()


def test_regression_similarity_fit_rejects_ragged_input() -> None:
    """Rejects ragged rows with BAD_SHAPE instead of leaking a numpy ValueError."""
    with pytest.raises(GeometryError) as info:
        similarity_fit([[0.0, 0.0], [1.0]], [[0.0, 0.0], [1.0, 1.0]])  # type: ignore[arg-type]
    assert info.value.code == "BAD_SHAPE"


def test_regression_similarity_fit_rejects_non_numeric_input() -> None:
    """Rejects strings with BAD_SHAPE instead of leaking a numpy ValueError."""
    with pytest.raises(GeometryError) as info:
        similarity_fit("ab", "cd")  # type: ignore[arg-type]
    assert info.value.code == "BAD_SHAPE"


@pytest.mark.parametrize("magnitude", [1e160, 1e200])
def test_regression_similarity_fit_rejects_overflowing_coordinates(magnitude: float) -> None:
    """Refuses huge coordinates with NOT_FINITE instead of returning a non-finite matrix."""
    src = np.array([[0.0, 0.0], [magnitude, 0.0]])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(GeometryError) as info:
            similarity_fit(src, src)
    assert info.value.code == "NOT_FINITE"


def test_similarity_fit_handles_large_finite_coordinates() -> None:
    """Still fits coordinates near 1e150, whose squares stay finite."""
    src = np.array([[0.0, 0.0], [1e150, 0.0]])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        fit = similarity_fit(src, src)
    assert np.isfinite(fit).all()
