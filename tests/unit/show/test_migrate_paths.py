#!/usr/bin/env python3

"""
test_migrate_paths.py --- tests the paths sub-step of the v1 to v2 format migration

Contains:
    test_regression_v1_builtin_params_migrate_into_the_typed_model(): types each built-in
        motion's stored parameters with no repair, leaving the input unchanged
    test_regression_unknown_v1_param_is_dropped_with_a_repair(): drops a key v1's motion never
        read and records the repair
    test_regression_value_v2_cannot_hold_is_refused(): refuses off-grid, zero, fractional and
        over-cap parameters naming the document, the field and the value
    test_regression_easing_strength_below_one_is_raised_with_a_repair(): stores the strength 1 that
        v1 used in place of a lower one
    test_regression_steps_over_the_cap_are_refused(): refuses a step count v2 cannot hold
    test_custom_motion_params_and_other_documents_pass_through(): leaves a custom
        motion's parameters and every non-paths document unchanged
    test_regression_unhashable_motion_fn_raises_schema_error(): refuses a list or object
        motion_fn with SchemaError naming the document, the field and the value
    test_regression_v1_route_curve_recipe_becomes_a_typed_curve(): stores the curve model of each
        recipe kind and keeps the route's other fields
    test_regression_route_curve_v2_cannot_hold_is_refused(): refuses a recipe that is no curve
        naming the document, the field and the value
    test_regression_v1_facing_values_are_tagged_by_mode(): tags a number, a pair and a string by
        the facing mode that reads them, in rows and in show.json overrides
    test_regression_tagged_facing_value_passes_through_the_step(): keeps a value that is already
        tagged for its mode, without a repair
    test_regression_v1_fixed_facing_is_wrapped_with_a_repair(): wraps a fixed heading into
        [0, 360) and records the repair
    test_regression_unread_v1_facing_value_is_dropped_with_a_repair(): drops a value the mode
        never read and records the repair
    test_regression_facing_value_that_does_not_fit_its_mode_is_refused(): refuses a missing,
        wrongly shaped or non-finite value naming the document, the field and the value
"""

import copy
import math

import pytest

from showpy.choreo.path_models import (
    MAX_EASING_STEPS,
    MAX_MOTION_CYCLES,
    MAX_SPIRAL_TURNS,
    EntityFacing,
    FigureEightParams,
    FixedFacing,
    PointFacing,
    SpiralParams,
    TransitionSpec,
    WeaveParams,
    ZigzagParams,
)
from showpy.core.errors import SchemaError
from showpy.core.ids import RouteId, SpotId
from showpy.core.json_types import JsonValue
from showpy.geo.curves import ArcCurve, CurveModel, Polyline, Segment
from showpy.show.documents import SHEET_DOC, paths_doc
from showpy.show.migrate import SHOW_DOC, ReportBuilder
from showpy.show.migrations.v1_to_v2_paths import migrate_paths

PATHS = paths_doc(("1", "2"))
TYPED = [
    ("weave", {"amplitude": 0.5, "cycles": 2.0}, WeaveParams(amplitude=0.5, cycles=2.0)),
    ("zigzag", {"cycles": 1}, ZigzagParams(cycles=1.0)),
    ("figure_eight", {}, FigureEightParams()),
    ("spiral", {"turns": 2, "clockwise": True}, SpiralParams(turns=2, clockwise=True)),
]
REFUSED = [
    ("weave", "cycles", 0.3),
    ("zigzag", "cycles", 0),
    ("weave", "cycles", MAX_MOTION_CYCLES + 1),
    ("spiral", "turns", 1.5),
    ("spiral", "turns", MAX_SPIRAL_TURNS + 1),
]
LOW_STRENGTH = 0.5
UNHASHABLE_MOTION_FNS: list[JsonValue] = [["weave"], {"name": "weave"}]


def _v1_docs(row: dict[str, JsonValue]) -> dict[str, JsonValue]:
    spec: dict[str, JsonValue] = {"id": ["1", "2"], "counts": 8.0, "rows": {"T1": row}}
    return {PATHS: spec, SHEET_DOC: {"motion_fn": "weave", "motion_params": {"wobble": 1}}}


def _custom_row(motion_fn: str, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"entity": "T1", "style": "custom", "motion_fn": motion_fn, "motion_params": params}


def _migrated(docs: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], ReportBuilder]:
    before = copy.deepcopy(docs)
    report = ReportBuilder(1)
    out = migrate_paths(docs, report)
    assert docs == before
    return out, report


def _row(out: dict[str, JsonValue]) -> object:
    spec = TransitionSpec.model_validate(out[PATHS])
    return spec.row("T1").motion_params


@pytest.mark.parametrize(("name", "params", "typed"), TYPED)
def test_regression_v1_builtin_params_migrate_into_the_typed_model(
    name: str, params: dict[str, JsonValue], typed: object
) -> None:
    """Types each built-in motion's stored parameters with no repair, leaving the input as is."""
    out, report = _migrated(_v1_docs(_custom_row(name, params)))
    assert _row(out) == typed
    assert report.build().repairs == ()


def test_regression_unknown_v1_param_is_dropped_with_a_repair() -> None:
    """Drops a key v1's weave never read and records the repair, naming document, spot and key."""
    out, report = _migrated(_v1_docs(_custom_row("weave", {"cycles": 1, "turns": 3})))
    assert _row(out) == WeaveParams(cycles=1.0)
    (repair,) = report.build().repairs
    assert PATHS in repair
    assert "T1" in repair
    assert "'turns'" in repair


@pytest.mark.parametrize(("name", "key", "value"), REFUSED)
def test_regression_value_v2_cannot_hold_is_refused(name: str, key: str, value: float) -> None:
    """Refuses a parameter v2 cannot hold, naming the document, the field and the value."""
    docs = _v1_docs(_custom_row(name, {key: value}))
    with pytest.raises(SchemaError) as info:
        _migrated(docs)
    field = f"rows.T1.motion_params.{key}"
    assert info.value.context == {"path": PATHS, "field": field, "value": repr(value)}
    assert PATHS in info.value.message
    assert field in info.value.message


def test_regression_easing_strength_below_one_is_raised_with_a_repair() -> None:
    """Stores the strength 1 that v1 used in place of a lower one, and records the repair."""
    row: dict[str, JsonValue] = {
        "entity": "T1",
        "easing": {"kind": "ease_in", "strength": LOW_STRENGTH},
    }
    out, report = _migrated(_v1_docs(row))
    assert TransitionSpec.model_validate(out[PATHS]).row("T1").easing.strength == 1.0
    (repair,) = report.build().repairs
    assert "rows.T1.easing.strength" in repair


def test_regression_steps_over_the_cap_are_refused() -> None:
    """Refuses a step count v2 cannot hold, naming the document, the field and the value."""
    over = float(MAX_EASING_STEPS + 1)
    row: dict[str, JsonValue] = {"entity": "T1", "easing": {"kind": "steps", "strength": over}}
    with pytest.raises(SchemaError) as info:
        _migrated(_v1_docs(row))
    field = "rows.T1.easing.strength"
    assert info.value.context == {"path": PATHS, "field": field, "value": repr(over)}


def test_custom_motion_params_and_other_documents_pass_through() -> None:
    """Leaves a custom motion's parameters and every non-paths document unchanged."""
    docs = _v1_docs(_custom_row("probe", {"wobble": [1, {"deep": True}]}))
    out, report = _migrated(docs)
    assert out == docs
    assert report.build().repairs == ()


@pytest.mark.parametrize("motion_fn", UNHASHABLE_MOTION_FNS)
def test_regression_unhashable_motion_fn_raises_schema_error(motion_fn: JsonValue) -> None:
    """Refuses a list or object motion_fn with SchemaError naming document, field and value."""
    row: dict[str, JsonValue] = {"entity": "T1", "style": "custom", "motion_fn": motion_fn}
    with pytest.raises(SchemaError) as info:
        _migrated(_v1_docs(row))
    field = "rows.T1.motion_fn"
    assert info.value.context == {"path": PATHS, "field": field, "value": repr(motion_fn)}
    assert PATHS in info.value.message
    assert "a string or null" in info.value.hint


def _facing_row(mode: str, value: JsonValue) -> dict[str, JsonValue]:
    return {"entity": "T1", "facing_mode": mode, "facing_value": value}


def _facing_of(out: dict[str, JsonValue]) -> object:
    return TransitionSpec.model_validate(out[PATHS]).row("T1").facing_value


def test_regression_v1_facing_values_are_tagged_by_mode() -> None:
    """Tags a number, a pair and a string by the facing mode that reads them."""
    cases: list[tuple[str, JsonValue, object]] = [
        ("fixed", 90, FixedFacing(degrees=90.0)),
        ("toward_point", [1.0, 2.0], PointFacing(point=(1.0, 2.0))),
        ("toward_entity", "T2", EntityFacing(entity=SpotId("T2"))),
    ]
    for mode, value, typed in cases:
        out, report = _migrated(_v1_docs(_facing_row(mode, value)))
        assert _facing_of(out) == typed
        assert report.build().repairs == ()
    docs: dict[str, JsonValue] = {
        SHOW_DOC: {"facing_overrides": [{"mode": "toward_entity", "value": "T2", "start": 0}]}
    }
    out, _ = _migrated(docs)
    assert out[SHOW_DOC] == {
        "facing_overrides": [
            {"mode": "toward_entity", "value": {"kind": "entity", "entity": "T2"}, "start": 0}
        ]
    }


def test_regression_tagged_facing_value_passes_through_the_step() -> None:
    """Keeps a value that is already tagged for its mode, without a repair."""
    tagged: dict[str, JsonValue] = {"kind": "fixed", "degrees": 270.0}
    out, report = _migrated(_v1_docs(_facing_row("fixed", tagged)))
    assert _facing_of(out) == FixedFacing(degrees=270.0)
    assert report.build().repairs == ()


def test_regression_v1_fixed_facing_is_wrapped_with_a_repair() -> None:
    """Wraps a fixed heading into [0, 360), in rows and overrides, and records the repair."""
    out, report = _migrated(_v1_docs(_facing_row("fixed", -90.0)))
    assert _facing_of(out) == FixedFacing(degrees=270.0)
    (repair,) = report.build().repairs
    assert PATHS in repair
    assert "rows.T1.facing_value" in repair
    docs: dict[str, JsonValue] = {
        SHOW_DOC: {"facing_overrides": [{"mode": "fixed", "value": 450.0}]}
    }
    out, report = _migrated(docs)
    assert out[SHOW_DOC] == {
        "facing_overrides": [{"mode": "fixed", "value": {"kind": "fixed", "degrees": 90.0}}]
    }
    (repair,) = report.build().repairs
    assert "facing_overrides[0].value" in repair


def test_regression_unread_v1_facing_value_is_dropped_with_a_repair() -> None:
    """Drops a value a facing mode never read and records the repair."""
    out, report = _migrated(_v1_docs(_facing_row("travel", 45.0)))
    assert _facing_of(out) is None
    (repair,) = report.build().repairs
    assert "rows.T1.facing_value" in repair
    assert "travel" in repair
    out, report = _migrated(_v1_docs(_facing_row("travel", None)))
    assert report.build().repairs == ()


@pytest.mark.parametrize(
    ("mode", "value"),
    [
        ("fixed", None),
        ("fixed", "T2"),
        ("fixed", True),
        ("fixed", math.nan),
        ("toward_point", 5.0),
        ("toward_point", [1.0]),
        ("toward_point", [True, 2.0]),
        ("toward_entity", [1.0, 2.0]),
        ("toward_entity", None),
    ],
)
def test_regression_facing_value_that_does_not_fit_its_mode_is_refused(
    mode: str, value: JsonValue
) -> None:
    """Refuses a missing, wrongly shaped or non-finite value, naming document, field and value."""
    with pytest.raises(SchemaError) as info:
        _migrated(_v1_docs(_facing_row(mode, value)))
    field = "rows.T1.facing_value"
    assert info.value.context == {"path": PATHS, "field": field, "value": repr(value)}
    assert PATHS in info.value.message
    assert field in info.value.message


def _route_doc(curve: JsonValue) -> dict[str, JsonValue]:
    route: dict[str, JsonValue] = {
        "id": "r",
        "curve": curve,
        "members": ["T1"],
        "offsets": [0.0],
        "travel": 4.0,
    }
    spec: dict[str, JsonValue] = {
        "id": ["1", "2"],
        "counts": 8.0,
        "rows": {},
        "routes": {"r": route},
    }
    return {PATHS: spec}


ROUTE_CURVES = [
    Segment(a=(0.0, 0.0), b=(10.0, 0.0)),
    Polyline(points=((0.0, 0.0), (5.0, 0.0), (5.0, 5.0))),
    ArcCurve(center=(0.0, 0.0), radius=3.0, start_deg=0.0, sweep_deg=90.0),
]


@pytest.mark.parametrize("curve", ROUTE_CURVES)
def test_regression_v1_route_curve_recipe_becomes_a_typed_curve(curve: CurveModel) -> None:
    """Stores the curve model of each recipe kind and keeps the route's other fields."""
    out, report = _migrated(_route_doc(curve.construction()))
    route = TransitionSpec.model_validate(out[PATHS]).routes[RouteId("r")]
    assert route.curve == curve
    assert (route.members, route.travel) == ((SpotId("T1"),), 4.0)
    assert report.build().repairs == ()


ROUTE_REFUSED: list[JsonValue] = [
    {},
    {"kind": "nowhere"},
    {"kind": "segment", "a": [0.0, 0.0]},
    {"kind": "polyline", "points": [[0.0, 0.0]]},
    "segment",
    None,
]


@pytest.mark.parametrize("recipe", ROUTE_REFUSED)
def test_regression_route_curve_v2_cannot_hold_is_refused(recipe: JsonValue) -> None:
    """Refuses a recipe that is no curve, naming the document, the field and the value."""
    with pytest.raises(SchemaError) as info:
        _migrated(_route_doc(recipe))
    field = "routes.r.curve"
    assert info.value.context == {"path": PATHS, "field": field, "value": repr(recipe)}
    assert PATHS in info.value.message
    assert field in info.value.message
