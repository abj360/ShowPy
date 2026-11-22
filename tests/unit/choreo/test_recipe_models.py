#!/usr/bin/env python3

"""
test_recipe_models.py --- tests the typed, versioned form recipes and the deprecated geometry

Contains:
    test_every_form_kind_round_trips_its_recipe: rebuilds an equal form of every kind from JSON,
        keeping the recipe's kind, version and hash
    test_recipe_union_covers_every_form_kind: names one recipe model per form kind
    test_regression_form_refuses_the_v1_kind_and_geometry_fields: refuses `kind` and `geometry`
        as construction fields
    test_regression_form_geometry_warns_and_returns_the_recipe_json: warns on the deprecated
        `geometry` and returns the recipe's JSON dump
    test_regression_recipe_rejects_a_non_affine_transform_shape: refuses a transform that is not
        3x3 finite numbers
    test_regression_custom_recipe_needs_one_facing_per_point: refuses facings that do not match
        the points
    test_regression_generated_params_are_read_only_and_reach_generators_as_plain_json: freezes
        the parameters and passes a user generator plain lists and dicts
    test_regression_curve_recipe_transform_maps_slots_and_facing_curve: applies a curve recipe's
        transform to its slots and to face_along
    test_regression_transform_form_composes_onto_a_stored_transform: maps a recipe that already
        holds a transform by composing, never by moving its points under it
    test_text_recipe_is_a_member_of_the_union: stores a text form's TextRecipe as its recipe
"""

import math
import warnings
from collections.abc import Mapping
from typing import get_args

import numpy as np
import pytest

from showpy.choreo.facing import face_along
from showpy.choreo.form_fns import DEFAULT_FORM_FNS, register_form_fn
from showpy.choreo.form_models import Form, FormKind, Recipe, TextRecipe
from showpy.choreo.form_ops import transform_form
from showpy.choreo.forms import (
    arc_form,
    block_form,
    circle_form,
    curve_form,
    custom_form,
    fill_form,
    generated_form,
    line_form,
    polyline_form,
    scatter_form,
    slots,
    svg_form,
    text_form,
    wedge_form,
)
from showpy.choreo.recipe_models import CustomRecipe, GeneratedRecipe, LineRecipe
from showpy.core.deprecation import DeprecatedWarning
from showpy.core.errors import SchemaError
from showpy.core.ids import FormId, SetId, SpotId
from showpy.core.json_types import JsonValue
from showpy.core.readonly import ReadOnlyMap
from showpy.core.types import Box2, Points
from showpy.core.util import approx_equal
from showpy.geo.affine import rotation_matrix, translation_matrix
from showpy.geo.shapes import line
from tests.fixtures.shows import two_set_show

TOL = 1e-9
SQUARE = [(0, 0), (4, 0), (4, 4), (0, 4)]
MEMBERS = ["A", "B", "C"]
SHIFT = [[1.0, 0.0, 2.0], [0.0, 1.0, 3.0], [0.0, 0.0, 1.0]]
TEXT_MEMBERS = [f"S{i}" for i in range(24)]
ORIGIN = (0.0, 0.0)


def _every_kind() -> list[Form]:
    return [
        line_form("line", MEMBERS, (0, 0), (4, 0)),
        arc_form("arc", MEMBERS, (0, 0), 2.0, 0.0, 180.0),
        curve_form("curve", MEMBERS, (0, 0), (2, 2), (4, 0)),
        circle_form("circle", MEMBERS, (0, 5), 3.0),
        block_form("block", MEMBERS, (0, 5), 1, 3, 1.0, anchor="front-left"),
        wedge_form("wedge", MEMBERS, (0, 5), 3.0, 90.0),
        polyline_form("polyline", MEMBERS, (0, 0), (2, 0), (2, 2)),
        svg_form("svg", MEMBERS, "M0 0 L1 0 L1 1", Box2(min=(0, 0), max=(4, 4))),
        fill_form("fill", MEMBERS, SQUARE, 1.0, pattern="hex", n=3),
        scatter_form("scatter", MEMBERS, SQUARE, 1.0, seed=7),
        custom_form("custom", MEMBERS, [(0, 0), (1, 0), (2, 0)], facings=[0.0, 90.0, 180.0]),
        generated_form("generated", MEMBERS, "spiral", turns=1.5, center=[0.0, 5.0]),
        text_form(
            "text", TEXT_MEMBERS, "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10), n=24
        ),
    ]


def test_every_form_kind_round_trips_its_recipe() -> None:
    forms = _every_kind()
    assert sorted(form.kind for form in forms) == sorted(get_args(FormKind.__value__))
    for form in forms:
        rebuilt = Form.model_validate_json(form.model_dump_json())
        assert rebuilt == form
        assert rebuilt.kind == form.recipe.kind == form.id
        assert rebuilt.recipe.recipe_version == 1
        assert hash(rebuilt.recipe) == hash(form.recipe)
        assert approx_equal(slots(rebuilt), slots(form), abs_tol=TOL)


def test_recipe_union_covers_every_form_kind() -> None:
    union = get_args(get_args(Recipe.__value__)[0])
    kinds = [model.model_fields["kind"].default for model in union]
    assert sorted(kinds) == sorted(get_args(FormKind.__value__))


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "line", "recipe": LineRecipe(curve=line((0, 0), (1, 0)))},
        {"geometry": {"curve": line((0, 0), (1, 0)).construction()}, "kind": "line"},
    ],
)
def test_regression_form_refuses_the_v1_kind_and_geometry_fields(fields: dict[str, object]) -> None:
    with pytest.raises(SchemaError):
        Form.model_validate({"id": "f", "set": "1", "members": [], **fields})


def test_regression_form_geometry_warns_and_returns_the_recipe_json() -> None:
    form = line_form("f", MEMBERS, (0, 0), (4, 0), n=3)
    with pytest.warns(DeprecatedWarning, match="`geometry` renamed to `recipe`"):
        geometry = form.geometry
    assert geometry == form.recipe.model_dump(mode="json")
    assert geometry["n"] == 3


@pytest.mark.parametrize(
    "transform",
    [[[1, 0], [0, 1]], [[1, 0, 0], [0, 1, 0]], [[1, 0, math.nan], [0, 1, 0], [0, 0, 1]]],
)
def test_regression_recipe_rejects_a_non_affine_transform_shape(transform: object) -> None:
    with pytest.raises(SchemaError):
        LineRecipe.model_validate({"curve": line((0, 0), (1, 0)), "transform": transform})


def test_regression_custom_recipe_needs_one_facing_per_point() -> None:
    with pytest.raises(SchemaError, match="one facing per point"):
        CustomRecipe(points=((0.0, 0.0), (1.0, 0.0)), facings=(0.0,))


def _nested_reader(params: Mapping[str, object], n: int) -> Points:
    """Places n spots along x from a nested `start` list, insisting on plain JSON containers."""
    start = params.get("start", [[0.0, 0.0]])
    if not isinstance(params, dict) or not isinstance(start, list):
        raise TypeError(f"generator got {type(params).__name__} params")
    x0, y0 = start[0]
    return np.stack([x0 + np.arange(n, dtype=float), np.full(n, y0)], axis=1)


def test_regression_generated_params_are_read_only_and_reach_generators_as_plain_json() -> None:
    registry = register_form_fn(DEFAULT_FORM_FNS, "nested", _nested_reader)
    form = generated_form("g", MEMBERS, "nested", registry=registry, start=[[1.0, 2.0]])
    recipe = form.recipe
    assert isinstance(recipe, GeneratedRecipe)
    assert isinstance(recipe.params, ReadOnlyMap)
    assert recipe.params["start"] == ((1.0, 2.0),)
    assert slots(form, registry).tolist() == [[1.0, 2.0], [2.0, 2.0], [3.0, 2.0]]
    dumped: JsonValue = form.model_dump(mode="json")["recipe"]["params"]
    assert dumped == {"start": [[1.0, 2.0]]}


def test_regression_curve_recipe_transform_maps_slots_and_facing_curve() -> None:
    plain = line_form("f", ["T1", "T2"], (0, 5), (6, 5))
    turned = plain.replace(
        recipe=plain.recipe.replace(transform=rotation_matrix(90.0, ORIGIN).tolist())
    )
    assert approx_equal(slots(turned), [[-5.0, 0.0], [-5.0, 6.0]], abs_tol=TOL)
    show = two_set_show().model_copy(update={"forms": {FormId("f"): turned.replace(set="1")}})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        facing = face_along(show, "1", "f").get("T1").facing
    assert approx_equal(facing, 180.0, abs_tol=1e-6)


def test_regression_transform_form_composes_onto_a_stored_transform() -> None:
    custom = custom_form("c", ["A"], [(1.0, 0.0)])
    shifted = custom.replace(recipe=custom.recipe.replace(transform=SHIFT))
    turned = transform_form(shifted, rotation_matrix(90.0, ORIGIN))
    assert turned.recipe.model_dump(mode="json")["points"] == [[1.0, 0.0]]
    assert approx_equal(slots(turned), [[-3.0, 3.0]], abs_tol=TOL)
    moved = transform_form(line_form("l", ["A"], (0, 0), (2, 0)), translation_matrix(0, 1))
    assert moved.recipe.transform is None
    assert approx_equal(slots(moved), [[1.0, 1.0]], abs_tol=TOL)


def test_text_recipe_is_a_member_of_the_union() -> None:
    form = _every_kind()[-1]
    assert isinstance(form.recipe, TextRecipe)
    assert form.recipe.n == len(TEXT_MEMBERS)
    assert (
        Form(
            id=FormId("t"),
            set=SetId("1"),
            recipe=form.recipe,
            members=tuple(map(SpotId, TEXT_MEMBERS)),
        ).kind
        == "text"
    )
