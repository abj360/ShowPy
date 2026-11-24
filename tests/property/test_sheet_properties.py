#!/usr/bin/env python3

"""
test_sheet_properties.py --- checks sheet fits on random inputs
"""

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from e8s.core.util import approx_equal
from e8s.geo.affine import rotation_matrix, scale_matrix, transform_points, translation_matrix
from e8s.sheet.replace import similarity_fit

FIT_TOL = 1e-6
coordinates = st.floats(min_value=-80.0, max_value=80.0, allow_nan=False)


@given(
    deg=st.floats(min_value=-179.0, max_value=179.0),
    scale=st.floats(min_value=0.5, max_value=2.0),
    dx=coordinates,
    dy=coordinates,
)
def test_similarity_fit_recovers_a_known_map(
    deg: float, scale: float, dx: float, dy: float
) -> None:
    src = np.array([[0.0, 0.0], [10.0, 0.0], [3.0, 7.0], [-4.0, 2.0]])
    truth = (
        translation_matrix(dx, dy)
        @ rotation_matrix(deg, (0, 0))
        @ scale_matrix(scale, scale, (0, 0))
    )
    fitted = similarity_fit(src, transform_points(src, truth))
    assert approx_equal(fitted, truth, abs_tol=FIT_TOL)
