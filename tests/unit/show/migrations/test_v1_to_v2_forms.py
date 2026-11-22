#!/usr/bin/env python3

"""
test_v1_to_v2_forms.py --- tests the forms sub-step of the v1 to v2 format migration

Contains:
    test_v1_geometry_migrates_to_a_typed_recipe: turns one v1 form of every kind, from the
        fixtures where they hold one, into an equal typed recipe
    test_regression_forms_step_splits_a_v1_svg_composite_into_its_subpaths: stores each subpath
        of a v1 multi-subpath svg outline as its own curve
    test_regression_v1_svg_form_with_subpaths_keeps_its_v0_3_layout: lays a migrated v1 svg form
        with several subpaths and count or interval spacing out to the v0.3.0 slots and facings
    test_forms_step_passes_other_documents_and_fields_through: leaves other documents and form
        fields as they were, and never mutates its input
    test_regression_forms_step_refuses_what_v2_cannot_hold: refuses an unknown kind, a bad
        geometry, a reserved key, an unknown key, an out-of-range value (a negative count, a block
        of no rows), more members than slots and a curve its own model refuses (an arc of zero
        sweep), naming the document, the field and the value
    test_regression_forms_step_refuses_a_forms_document_that_is_not_a_list: refuses a forms
        document that holds no list
"""

import copy
import json
from pathlib import Path
from typing import get_args

import pytest

from showpy.choreo.form_models import Form, FormKind
from showpy.choreo.forms import curve_form, placements, polyline_form
from showpy.choreo.recipe_models import SvgRecipe
from showpy.core.errors import SchemaError, bounded_repr
from showpy.core.json_types import JsonValue
from showpy.core.types import Box2, Region
from showpy.core.util import approx_equal
from showpy.geo.curves import CompositeCurve
from showpy.geo.shapes import svg_outline
from showpy.show.migrate import FIRST_VERSION, ReportBuilder
from showpy.show.migrations.v1_to_v2_forms import migrate_forms

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "repos" / "v0.3.0" / "expected.json"
DOC = "/sets/2/forms.json"
SQUARE = Region(exterior=((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0))).model_dump(mode="json")
BOX = Box2(min=(0, 0), max=(4, 4))
POSE_TOL = 1e-9
ZERO_SWEEP_ARC: dict[str, JsonValue] = {
    "curve": {"kind": "arc", "center": [0, 0], "radius": 1.0, "start_deg": 0.0, "sweep_deg": 0.0}
}
SHARED_DEFAULTS: dict[str, JsonValue] = {"recipe_version": 1, "n": None, "transform": None}


def _fixture_forms() -> dict[str, dict[str, JsonValue]]:
    """Returns the first v1 form of each kind the v0.3.0 fixture holds."""
    forms = json.loads(FIXTURE.read_text())["show"]["forms"]
    by_kind: dict[str, dict[str, JsonValue]] = {}
    for form in forms.values():
        by_kind.setdefault(form["kind"], form)
    return by_kind


def _v1_form(kind: str, geometry: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"id": f"{kind}-1", "set": "1", "kind": kind, "geometry": geometry, "members": []}


def _built_forms() -> dict[str, dict[str, JsonValue]]:
    """Writes v1 forms of the kinds the fixtures lack, as a v0.3.0 constructor stored them."""
    curves = {
        "curve": curve_form("c", [], (0, 0), (2, 2), (4, 0), n=3),
        "polyline": polyline_form("p", [], (0, 0), (2, 0), (2, 2), n=3),
    }
    geometries: dict[str, dict[str, JsonValue]] = {
        kind: {"curve": form.recipe.model_dump(mode="json")["curve"], "n": 3}
        for kind, form in curves.items()
    }
    [outline] = svg_outline("M0 0 L1 0 L1 1", BOX)
    geometries |= {
        "svg": {"curve": outline.construction(), "n": 3},
        "fill": {"outline": SQUARE, "interval": 1.0, "pattern": "hex", "angle_deg": 15.0},
        "scatter": {"region": SQUARE, "min_gap": 1.0, "seed": 3, "n": 4},
        "custom": {"points": [[0.0, 0.0], [1.0, 0.0]], "facings": [0.0, 90.0], "n": 2},
        "generated": {"fn": "spiral", "params": {"turns": 2.0, "center": [0, 5]}},
    }
    return {kind: _v1_form(kind, geometry) for kind, geometry in geometries.items()}


def _v2_fields(kind: str, geometry: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Returns the recipe fields a v1 geometry must migrate to; svg keeps a list of subpaths."""
    if kind != "svg":
        return geometry
    rest = {key: value for key, value in geometry.items() if key != "curve"}
    return {**rest, "curves": [geometry["curve"]]}


def _migrated(forms: list[JsonValue]) -> list[JsonValue]:
    result = migrate_forms({DOC: forms}, ReportBuilder(FIRST_VERSION))[DOC]
    assert isinstance(result, list)
    return result


def test_v1_geometry_migrates_to_a_typed_recipe() -> None:
    v1_forms = _fixture_forms() | _built_forms()
    assert sorted(v1_forms) == sorted(get_args(FormKind.__value__))
    for kind, v1 in v1_forms.items():
        [migrated] = _migrated([v1])
        assert isinstance(migrated, dict)
        assert "geometry" not in migrated
        assert "kind" not in migrated
        recipe = migrated["recipe"]
        assert isinstance(recipe, dict)
        geometry = v1["geometry"]
        assert isinstance(geometry, dict)
        assert recipe["kind"] == kind
        expected = _v2_fields(kind, geometry)
        assert {key: recipe[key] for key in expected} == expected
        assert {key: recipe[key] for key in SHARED_DEFAULTS if key not in geometry} == {
            key: value for key, value in SHARED_DEFAULTS.items() if key not in geometry
        }
        form = Form.model_validate(migrated)
        assert form.kind == kind
        assert form.recipe.model_dump(mode="json") == recipe


def test_regression_forms_step_splits_a_v1_svg_composite_into_its_subpaths() -> None:
    outline = svg_outline("M0 0 L1 0 M0 1 L3 1", BOX)
    parts: list[JsonValue] = [curve.construction() for curve in outline]
    composite: dict[str, JsonValue] = {"closed": False, "kind": "composite", "parts": parts}
    [migrated] = _migrated([_v1_form("svg", {"curve": composite, "n": 4})])
    assert isinstance(migrated, dict)
    recipe = migrated["recipe"]
    assert isinstance(recipe, dict)
    assert recipe["curves"] == parts
    assert "curve" not in recipe
    recipe_model = Form.model_validate(migrated).recipe
    assert isinstance(recipe_model, SvgRecipe)
    assert not any(isinstance(curve, CompositeCurve) for curve in recipe_model.curves)


def _corner_part(d: str) -> dict[str, JsonValue]:
    flip: list[JsonValue] = [[1.0, 0.0, 0.0], [0.0, -1.0, 2.0], [0.0, 0.0, 1.0]]
    return {"closed": False, "kind": "svg", "d": d, "transform": flip}


# `svg_form("f", ..., "M0 0 L2 0 M3 0 L3 2", Box2((0, 0), (3, 2)), ...)` as v0.3.0 stored it, and
# the poses (x, y, facing) v0.3.0 (68344ab) laid it out to.
V1_CORNER_CURVE: dict[str, JsonValue] = {
    "closed": False,
    "kind": "composite",
    "parts": [_corner_part("M0.0 0.0 L2.0 0.0"), _corner_part("M3.0 0.0 L3.0 2.0")],
}
V1_CORNER_LAYOUTS = [
    (
        {"mode": "count"},
        list("ABCDE"),
        [[0.0, 2.0, 90.0], [1.0, 2.0, 90.0], [3.0, 2.0, 0.0], [3.0, 1.0, 0.0], [3.0, 0.0, 0.0]],
    ),
    (
        {"mode": "interval", "interval": 0.75, "align": "center"},
        list("ABC"),
        [[1.25, 2.0, 90.0], [3.0, 2.0, 0.0], [3.0, 1.25, 0.0]],
    ),
]


@pytest.mark.parametrize(("spacing", "members", "poses"), V1_CORNER_LAYOUTS)
def test_regression_v1_svg_form_with_subpaths_keeps_its_v0_3_layout(
    spacing: dict[str, JsonValue], members: list[JsonValue], poses: list[list[float]]
) -> None:
    v1: dict[str, JsonValue] = {
        **_v1_form("svg", {"curve": V1_CORNER_CURVE}),
        "members": members,
        "spacing": spacing,
        "facing": {"mode": "along"},
    }
    [migrated] = _migrated([v1])
    form = Form.model_validate(migrated)
    laid = [[p.x, p.y, p.facing] for p in placements(form)]
    assert approx_equal(laid, poses, abs_tol=POSE_TOL)


def test_forms_step_passes_other_documents_and_fields_through() -> None:
    v1: dict[str, JsonValue] = {**_built_forms()["custom"], "locked": True, "meta": {"note": "x"}}
    docs: dict[str, JsonValue] = {DOC: [v1], "/sets/2/set.json": {"geometry": 1, "kind": "z"}}
    before = copy.deepcopy(docs)
    migrated = migrate_forms(docs, ReportBuilder(FIRST_VERSION))
    assert docs == before
    assert migrated["/sets/2/set.json"] == before["/sets/2/set.json"]
    [form] = _migrated([v1])
    assert isinstance(form, dict)
    assert {key: form[key] for key in ("id", "set", "members", "locked", "meta")} == {
        "id": "custom-1",
        "set": "1",
        "members": [],
        "locked": True,
        "meta": {"note": "x"},
    }


@pytest.mark.parametrize(
    ("forms", "field", "value"),
    [
        ({"id": "f"}, "[0].kind", "None"),
        (_v1_form("hexagon", {}), "[0].kind", "hexagon"),
        ({**_v1_form("custom", {}), "geometry": [1]}, "[0].geometry", "[1]"),
        (_v1_form("custom", {"points": [], "kind": "x"}), "[0].geometry.kind", "x"),
        (
            _v1_form("custom", {"points": [], "recipe_version": 2}),
            "[0].geometry.recipe_version",
            "2",
        ),
        (
            _v1_form(
                "block", {"origin": [0, 0], "rows": 1, "cols": 2, "interval": 1, "anchr": "x"}
            ),
            "[0].geometry.anchr",
            "x",
        ),
        (
            _v1_form(
                "block", {"origin": [0, 0], "rows": 1, "cols": 2, "interval": 1, "anchor": ""}
            ),
            "[0].geometry.anchor",
            "",
        ),
        (_v1_form("custom", {"points": [], "n": 2.5}), "[0].geometry.n", "2.5"),
        (_v1_form("custom", {"points": [], "n": -1}), "[0].geometry.n", "-1"),
        (
            _v1_form("block", {"origin": [0, 0], "rows": 0, "cols": 2, "interval": 1}),
            "[0].geometry.rows",
            "0",
        ),
        (
            _v1_form("block", {"origin": [0, 0], "rows": 2, "cols": -1, "interval": 1}),
            "[0].geometry.cols",
            "-1",
        ),
        (
            {**_v1_form("custom", {"points": [[0, 0]], "n": 1}), "members": ["A", "B"]},
            "[0].members",
            "['A', 'B']",
        ),
        ("not a form", "[0]", "not a form"),
        (_v1_form("arc", ZERO_SWEEP_ARC), "[0].geometry", bounded_repr(ZERO_SWEEP_ARC)),
    ],
)
def test_regression_forms_step_refuses_what_v2_cannot_hold(
    forms: JsonValue, field: str, value: str
) -> None:
    with pytest.raises(SchemaError) as caught:
        _migrated([forms])
    assert caught.value.context == {"path": DOC, "field": field, "value": value}


def test_regression_forms_step_refuses_a_forms_document_that_is_not_a_list() -> None:
    with pytest.raises(SchemaError) as caught:
        migrate_forms({DOC: {"f": 1}}, ReportBuilder(FIRST_VERSION))
    assert caught.value.context["path"] == DOC
