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
"""

import copy

import pytest

from showpy.choreo.path_models import (
    MAX_EASING_STEPS,
    MAX_MOTION_CYCLES,
    MAX_SPIRAL_TURNS,
    FigureEightParams,
    SpiralParams,
    TransitionSpec,
    WeaveParams,
    ZigzagParams,
)
from showpy.core.errors import SchemaError
from showpy.core.json_types import JsonValue
from showpy.show.documents import SHEET_DOC, paths_doc
from showpy.show.migrate import ReportBuilder
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
