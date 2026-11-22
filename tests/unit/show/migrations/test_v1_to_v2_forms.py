#!/usr/bin/env python3

"""
test_v1_to_v2_forms.py --- tests the forms sub-step of the v1 to v2 format migration

Contains:
    test_v1_geometry_migrates_to_a_typed_recipe: turns one v1 form of every kind, from the
        fixtures where they hold one, into an equal typed recipe
    test_forms_step_passes_other_documents_and_fields_through: leaves other documents and form
        fields as they were, and never mutates its input
    test_regression_forms_step_refuses_what_v2_cannot_hold: refuses an unknown kind, a bad
        geometry, a reserved key, an unknown key and an out-of-range value, naming the
        document, the field and the value
    test_regression_forms_step_refuses_a_forms_document_that_is_not_a_list: refuses a forms
        document that holds no list
"""

import copy
import json
from pathlib import Path
from typing import get_args

import pytest

from showpy.choreo.form_models import Form, FormKind
from showpy.choreo.forms import curve_form, polyline_form, svg_form
from showpy.core.errors import SchemaError
from showpy.core.json_types import JsonValue
from showpy.core.types import Box2, Region
from showpy.show.migrate import FIRST_VERSION, ReportBuilder
from showpy.show.migrations.v1_to_v2_forms import migrate_forms

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "repos" / "v0.3.0" / "expected.json"
DOC = "/sets/2/forms.json"
SQUARE = Region(exterior=((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0))).model_dump(mode="json")
BOX = Box2(min=(0, 0), max=(4, 4))
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
        "svg": svg_form("s", [], "M0 0 L1 0 L1 1", BOX, n=3),
    }
    geometries: dict[str, dict[str, JsonValue]] = {
        kind: {"curve": form.recipe.model_dump(mode="json")["curve"], "n": 3}
        for kind, form in curves.items()
    }
    geometries |= {
        "fill": {"outline": SQUARE, "interval": 1.0, "pattern": "hex", "angle_deg": 15.0},
        "scatter": {"region": SQUARE, "min_gap": 1.0, "seed": 3, "n": 4},
        "custom": {"points": [[0.0, 0.0], [1.0, 0.0]], "facings": [0.0, 90.0], "n": 2},
        "generated": {"fn": "spiral", "params": {"turns": 2.0, "center": [0, 5]}},
    }
    return {kind: _v1_form(kind, geometry) for kind, geometry in geometries.items()}


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
        assert {key: recipe[key] for key in geometry} == geometry
        assert {key: recipe[key] for key in SHARED_DEFAULTS if key not in geometry} == {
            key: value for key, value in SHARED_DEFAULTS.items() if key not in geometry
        }
        form = Form.model_validate(migrated)
        assert form.kind == kind
        assert form.recipe.model_dump(mode="json") == recipe


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
        ("not a form", "[0]", "not a form"),
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
