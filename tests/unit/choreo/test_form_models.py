#!/usr/bin/env python3

"""
test_form_models.py --- tests SpacingSpec and FacingSpec validation and Form invariants

Contains:
    test_spacing_and_facing_spec_validation: rejects interval spacing without an interval
    test_form_requires_unique_members: rejects a form that lists a member twice
    test_regression_form_geometry_is_immutable: keeps a frozen form's recipe unchanged when the
        caller edits its geometry
    test_regression_form_is_hashable: hashes equal forms to equal values
    test_regression_facing_spec_rejects_nan_point: rejects a toward_point facing whose point is NaN
    test_regression_spacing_spec_rejects_negative_gaps: rejects a negative extra gap in
        SpacingSpec.gaps
    test_regression_facing_spec_normalizes_fixed_value: wraps a fixed facing of 720 degrees into [0,
        360)
"""

import contextlib
import math

import pytest

from showpy.choreo.form_models import FacingSpec, Form, SpacingSpec
from showpy.choreo.recipe_models import CustomRecipe
from showpy.core.errors import SchemaError
from showpy.core.ids import FormId, SetId, SpotId
from showpy.core.units import normalize_angle
from showpy.core.util import approx_equal
from tests.fixtures.recipes import LINE_RECIPE

TURNS_PAST_FULL = 720.0
NEGATIVE_GAP = -1.0
ANGLE_TOL = 1e-9


def test_spacing_and_facing_spec_validation() -> None:
    assert SpacingSpec().mode == "count"
    with pytest.raises(SchemaError):
        SpacingSpec(mode="interval")
    with pytest.raises(SchemaError):
        FacingSpec(mode="toward_point")


def test_form_requires_unique_members() -> None:
    with pytest.raises(SchemaError):
        Form(
            id=FormId("f"),
            set=SetId("1"),
            recipe=LINE_RECIPE,
            members=(SpotId("A"), SpotId("A")),
        )


def _custom_form() -> Form:
    return Form(
        id=FormId("f"),
        set=SetId("1"),
        recipe=CustomRecipe(points=(), n=2),
        members=(SpotId("A"),),
    )


@pytest.mark.defect("UP-9.6-19")
@pytest.mark.xfail(strict=True, reason="UP-9.6-19")
def test_regression_form_geometry_is_immutable() -> None:
    """Keeps a frozen form's recipe unchanged when the caller edits its geometry (audit 9.6)."""
    form = _custom_form()
    with contextlib.suppress(TypeError):
        form.geometry["n"] = 5
    assert form.geometry["n"] == 2


@pytest.mark.defect("UP-9.6-21")
@pytest.mark.xfail(strict=True, reason="UP-9.6-21")
def test_regression_form_is_hashable() -> None:
    """Hashes equal forms to equal values (audit 9.6)."""
    assert hash(_custom_form()) == hash(_custom_form())


def test_regression_facing_spec_rejects_nan_point() -> None:
    """Rejects a toward_point facing whose point is NaN (audit 9.6)."""
    with pytest.raises(SchemaError):
        FacingSpec(mode="toward_point", value=(math.nan, 0.0))


@pytest.mark.defect("UP-9.6-39")
@pytest.mark.xfail(strict=True, reason="UP-9.6-39")
def test_regression_spacing_spec_rejects_negative_gaps() -> None:
    """Rejects a negative extra gap in SpacingSpec.gaps (audit 9.6)."""
    with pytest.raises(SchemaError):
        SpacingSpec(gaps=((0, NEGATIVE_GAP),))


@pytest.mark.defect("UP-9.6-41")
@pytest.mark.xfail(strict=True, reason="UP-9.6-41")
def test_regression_facing_spec_normalizes_fixed_value() -> None:
    """Wraps a fixed facing of 720 degrees into [0, 360) (audit 9.6)."""
    spec = FacingSpec(mode="fixed", value=TURNS_PAST_FULL)
    assert isinstance(spec.value, float)
    assert approx_equal(spec.value, normalize_angle(TURNS_PAST_FULL), abs_tol=ANGLE_TOL)
