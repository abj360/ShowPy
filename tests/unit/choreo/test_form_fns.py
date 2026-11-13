#!/usr/bin/env python3

"""
test_form_fns.py --- tests the shape generator registry and the built-in generators
"""

import contextlib
from collections.abc import Mapping

import numpy as np
import pytest

from showpy.choreo.form_fns import (
    DEFAULT_FORM_FNS,
    form_fns,
    register_form_fn,
    rose_points,
    sine_wave_points,
    spiral_points,
)
from showpy.core.errors import SchemaError, UnknownRefError
from showpy.core.types import Points
from showpy.core.util import approx_equal

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


PROBE_SPOTS = 3
EVIL_NAME = "evil"


def _crashing_generator(params: Mapping[str, object], n: int) -> Points:
    raise RuntimeError("generator crashed")


@pytest.mark.defect("UP-9.6-20")
@pytest.mark.xfail(strict=True, reason="UP-9.6-20")
def test_regression_default_registry_cannot_be_mutated() -> None:
    """Leaves the global registry unchanged when a caller edits its fns (audit 9.6)."""
    try:
        with contextlib.suppress(TypeError):
            DEFAULT_FORM_FNS.fns[EVIL_NAME] = spiral_points
        assert EVIL_NAME not in DEFAULT_FORM_FNS.fns
    finally:
        DEFAULT_FORM_FNS.fns.pop(EVIL_NAME, None)


@pytest.mark.defect("UP-9.6-51")
@pytest.mark.xfail(strict=True, reason="UP-9.6-51")
def test_regression_spiral_rejects_text_turns_with_schema_error() -> None:
    """Raises SchemaError, not ValueError, for turns="x" (audit 9.6)."""
    with pytest.raises(SchemaError):
        spiral_points({"turns": "x"}, PROBE_SPOTS)


@pytest.mark.defect("UP-9.6-52")
@pytest.mark.xfail(strict=True, reason="UP-9.6-52")
def test_regression_spiral_rejects_none_turns_with_schema_error() -> None:
    """Raises SchemaError, not TypeError, for turns=None (audit 9.6)."""
    with pytest.raises(SchemaError):
        spiral_points({"turns": None}, PROBE_SPOTS)


@pytest.mark.defect("UP-9.6-53")
@pytest.mark.xfail(strict=True, reason="UP-9.6-53")
def test_regression_sine_wave_rejects_zero_wavelength() -> None:
    """Raises SchemaError for wavelength=0 instead of dividing by zero (audit 9.6)."""
    with pytest.raises(SchemaError):
        sine_wave_points({"wavelength": 0}, PROBE_SPOTS)


@pytest.mark.defect("UP-9.6-54")
@pytest.mark.xfail(strict=True, reason="UP-9.6-54")
def test_regression_register_form_fn_wraps_probe_crashes() -> None:
    """Raises SchemaError when the registration probe makes the generator crash (audit 9.6)."""
    with pytest.raises(SchemaError):
        register_form_fn(DEFAULT_FORM_FNS, "crashing", _crashing_generator)
