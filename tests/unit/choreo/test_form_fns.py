#!/usr/bin/env python3

"""
test_form_fns.py --- tests the shape generator registry and the built-in generators
"""

from collections.abc import Mapping

import numpy as np
import pytest

from stage.choreo.form_fns import (
    DEFAULT_FORM_FNS,
    form_fns,
    register_form_fn,
    rose_points,
    sine_wave_points,
    spiral_points,
)
from stage.core.errors import SchemaError, UnknownRefError
from stage.core.types import Points
from stage.core.util import approx_equal

TOL = 1e-6


def _row(params: Mapping[str, float], n: int) -> Points:
    """Places n spots on the x axis, one meter apart."""
    return np.stack([np.arange(n, dtype=float), np.zeros(n)], axis=1)


def _wrong_shape(params: Mapping[str, float], n: int) -> Points:
    return np.zeros((n, 3))


def _needs_param(params: Mapping[str, float], n: int) -> Points:
    return np.full((n, 2), params["required"])


def test_register_returns_new_registry_and_keeps_default() -> None:
    extended = register_form_fn(DEFAULT_FORM_FNS, "row", _row)
    assert "row" in extended.fns
    assert "row" not in DEFAULT_FORM_FNS.fns


def test_register_probes_shape_and_defaults() -> None:
    with pytest.raises(SchemaError, match="shape"):
        register_form_fn(DEFAULT_FORM_FNS, "bad", _wrong_shape)
    with pytest.raises(SchemaError, match="default"):
        register_form_fn(DEFAULT_FORM_FNS, "needy", _needs_param)
    with pytest.raises(SchemaError, match="taken"):
        register_form_fn(DEFAULT_FORM_FNS, "spiral", _row)


def test_get_suggests_close_names() -> None:
    with pytest.raises(UnknownRefError) as caught:
        DEFAULT_FORM_FNS.get("rse")
    assert "rose" in caught.value.hint


def test_form_fns_lists_names_and_summaries() -> None:
    infos = form_fns(register_form_fn(DEFAULT_FORM_FNS, "row", _row))
    assert [i.name for i in infos] == ["rose", "row", "sine_wave", "spiral"]
    assert infos[1].summary == "Places n spots on the x axis, one meter apart."


def test_spiral_points_are_even_and_widen() -> None:
    pts = spiral_points({"center": (0, 0), "r0": 1.0, "r1": 3.0, "turns": 2.0}, 9)
    radii = np.linalg.norm(pts, axis=1)
    assert approx_equal(radii[0], 1.0, TOL)
    assert approx_equal(radii[-1], 3.0, TOL)
    assert np.all(np.diff(radii) > 0)


def test_sine_wave_spans_its_length_and_amplitude() -> None:
    pts = sine_wave_points({"start": (0, 0), "length": 8.0, "amplitude": 1.0}, 33)
    assert approx_equal(pts[0], [0, 0], TOL)
    assert approx_equal(pts[-1], [8, 0], 1e-3)
    assert np.abs(pts[:, 1]).max() <= 1.0 + TOL


def test_rose_points_never_coincide_or_reach_the_center() -> None:
    pts = rose_points({"center": (0, 0), "radius": 4.0, "petals": 4, "inner": 0.25}, 24)
    gaps = np.linalg.norm(pts[:, None] - pts[None], axis=2)[np.triu_indices(24, 1)]
    assert gaps.min() > 0.1
    assert np.linalg.norm(pts, axis=1).min() >= 1.0 - TOL
