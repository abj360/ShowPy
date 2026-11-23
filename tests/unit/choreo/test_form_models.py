#!/usr/bin/env python3

"""
test_form_models.py --- tests SpacingSpec and FacingSpec validation and Form invariants

Contains:
    test_spacing_and_facing_spec_validation: rejects interval spacing without an interval
    test_form_requires_unique_members: rejects a form that lists a member twice
    test_regression_form_geometry_is_immutable: keeps a frozen form's recipe unchanged when the
        caller edits its geometry
    test_regression_form_is_hashable: hashes equal forms to equal values
    test_regression_form_meta_is_read_only_and_hashes_by_content: stores meta read-only, so it
        cannot change under a hash, whatever its key order
    test_regression_facing_spec_rejects_nan_point: rejects a toward_point facing whose point is NaN
    test_regression_spacing_spec_rejects_negative_gaps: rejects a negative extra gap in
        SpacingSpec.gaps
    test_regression_form_refuses_more_members_than_slots: rejects a form whose members outnumber
        its slot count, and accepts one that fills it
    test_regression_form_members_may_hold_open_slots: stores a slot with no spot as None, counts
        it toward the slot count and lists each spot once
    test_regression_facing_spec_normalizes_fixed_value: wraps a fixed facing of 720 degrees into [0,
        360)
"""

import contextlib
import math
from collections.abc import MutableMapping
from typing import cast

import pytest

from e8s.choreo.form_models import FacingSpec, Form, SpacingSpec
from e8s.choreo.recipe_models import CustomRecipe
from e8s.core.deprecation import DeprecatedWarning
from e8s.core.errors import SchemaError, SpacingError
from e8s.core.ids import FormId, SetId, SpotId
from e8s.core.units import normalize_angle
from e8s.core.util import approx_equal
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


def test_regression_form_geometry_is_immutable() -> None:
    """Keeps a frozen form's recipe unchanged when the caller edits its geometry (audit 9.6)."""
    form = _custom_form()
    with pytest.warns(DeprecatedWarning), contextlib.suppress(TypeError):
        form.geometry["n"] = 5
    with pytest.warns(DeprecatedWarning):
        assert form.geometry["n"] == 2


def test_regression_form_is_hashable() -> None:
    """Hashes equal forms to equal values (audit 9.6)."""
    assert hash(_custom_form()) == hash(_custom_form())


def test_regression_form_meta_is_read_only_and_hashes_by_content() -> None:
    """Stores meta read-only, so it cannot change under a hash, whatever its key order."""
    first = _custom_form().replace(meta={"a": "1", "b": "2"})
    second = _custom_form().replace(meta={"b": "2", "a": "1"})
    mutable = cast("MutableMapping[str, str]", first.meta)
    with pytest.raises(TypeError):
        mutable["a"] = "9"
    assert first == second
    assert hash(first) == hash(second)
    assert first.model_dump(mode="json")["meta"] == {"a": "1", "b": "2"}
    assert _custom_form().meta == {}


def test_regression_facing_spec_rejects_nan_point() -> None:
    """Rejects a toward_point facing whose point is NaN (audit 9.6)."""
    with pytest.raises(SchemaError):
        FacingSpec(mode="toward_point", value=(math.nan, 0.0))


def test_regression_spacing_spec_rejects_negative_gaps() -> None:
    """Rejects a negative extra gap in SpacingSpec.gaps (audit 9.6)."""
    with pytest.raises(SchemaError):
        SpacingSpec(gaps=((0, NEGATIVE_GAP),))


def test_regression_facing_spec_normalizes_fixed_value() -> None:
    """Wraps a fixed facing of 720 degrees into [0, 360) (audit 9.6)."""
    spec = FacingSpec(mode="fixed", value=TURNS_PAST_FULL)
    assert isinstance(spec.value, float)
    assert approx_equal(spec.value, normalize_angle(TURNS_PAST_FULL), abs_tol=ANGLE_TOL)


def test_regression_form_refuses_more_members_than_slots() -> None:
    members = (SpotId("A"), SpotId("B"), SpotId("C"))
    with pytest.raises(SpacingError) as caught:
        Form(id=FormId("f"), set=SetId("1"), recipe=CustomRecipe(points=(), n=2), members=members)
    assert caught.value.context == {"form": "f", "n": 2, "members": 3}
    full = Form(
        id=FormId("f"), set=SetId("1"), recipe=CustomRecipe(points=(), n=3), members=members
    )
    assert len(full.members) == full.recipe.n


def test_regression_form_members_may_hold_open_slots() -> None:
    recipe = CustomRecipe(points=(), n=4)
    gapped = Form(
        id=FormId("f"),
        set=SetId("1"),
        recipe=recipe,
        members=(SpotId("A"), None, None, SpotId("B")),
    )
    assert gapped.members == ("A", None, None, "B")
    assert Form.model_validate_json(gapped.model_dump_json()) == gapped
    with pytest.raises(SpacingError):
        Form(id=FormId("f"), set=SetId("1"), recipe=recipe, members=(None,) * 5)
    with pytest.raises(SchemaError):
        Form(
            id=FormId("f"), set=SetId("1"), recipe=recipe, members=(SpotId("A"), None, SpotId("A"))
        )
