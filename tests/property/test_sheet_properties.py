#!/usr/bin/env python3

"""
test_sheet_properties.py --- checks coordinate wording round-trips and sheet fits on random inputs
"""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from e8s.core.units import convert
from e8s.core.util import approx_equal
from e8s.geo.affine import rotation_matrix, scale_matrix, transform_points, translation_matrix
from e8s.sheet.coords import from_coords, set_coordinates, to_coords
from e8s.sheet.models import CoordinateKind, CoordinateSystem
from e8s.sheet.replace import similarity_fit
from tests.fixtures.shows import two_set_show

pytestmark = pytest.mark.filterwarnings("ignore::e8s.core.errors.DeprecatedWarning")

FIT_TOL = 1e-6
SLACK_M = 1e-9
coordinates = st.floats(min_value=-80.0, max_value=80.0, allow_nan=False)
systems = st.builds(
    CoordinateSystem,
    kind=st.sampled_from(list(CoordinateKind)),
    origin=st.sampled_from([(0.0, 0.0), (1.25, -3.0), "c"]),
    audience_side=st.sampled_from(["front", "back", "left", "right"]),
    units=st.sampled_from(["m", "ft", "yd", "cm"]),
    precision=st.sampled_from([0.01, 0.25, 0.5, 1.0]),
)


@given(x=coordinates, y=coordinates, system=systems, style=st.sampled_from(["long", "short"]))
def test_wording_round_trips_within_precision(
    x: float, y: float, system: CoordinateSystem, style: str
) -> None:
    show = set_coordinates(two_set_show(), system)
    text = to_coords(show, (x, y), style=style)  # type: ignore[arg-type]
    back = from_coords(show, text)
    tol = convert(system.precision, system.units, "m") / 2 + SLACK_M
    assert approx_equal(back, (x, y), abs_tol=tol * 2**0.5)
    assert to_coords(show, back, style=style) == text  # type: ignore[arg-type]


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
