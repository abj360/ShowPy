#!/usr/bin/env python3

"""
test_path_models.py --- tests PathRow, Waypoint, Easing and TransitionSpec validation and JSON

Contains:
    test_path_row_defaults_and_window: fills path row defaults and the window end
    test_path_row_rejects_bad_window: rejects a path row whose end precedes its start
    test_waypoint_and_easing_defaults: defaults a waypoint count to None and easing to linear
    test_transition_spec_row_lookup_defaults_to_straight: returns a straight row for a spot
        without one
    test_transition_spec_round_trips_json: round-trips a transition spec through JSON
    test_regression_transition_spec_checks_route_keys_against_route_ids: rejects a route stored
        under a key that differs from its ID
    test_regression_path_row_rejects_nan_start: rejects a NaN PathRow.start
    test_regression_transition_spec_rejects_negative_counts: rejects negative TransitionSpec.counts
    test_builtin_motion_params_reject_unknown_keys: refuses a key no built-in motion reads
    test_regression_motion_params_are_typed_by_motion_fn: validates each built-in's parameters
        into its own model and round-trips them through JSON
    test_regression_custom_motion_params_are_deeply_read_only: stores a custom motion's
        parameters as nested read-only JSON that still dumps plain
    test_regression_typed_params_must_match_the_motion: refuses one motion's typed parameters
        under another motion's name
    test_regression_wave_cycles_are_positive_half_turns_under_the_cap: refuses zero, negative,
        off-grid and over-cap cycles for weave and zigzag
    test_regression_spiral_turns_are_whole_and_capped: refuses negative, fractional and over-cap
        turns
    test_regression_easing_strength_is_at_least_one: refuses a strength below 1 for every kind
    test_regression_easing_steps_are_capped: accepts the step cap and refuses one step more
"""

import json
import math
from collections.abc import MutableMapping

import pytest

from showpy.choreo.path_models import (
    MAX_EASING_STEPS,
    MAX_MOTION_CYCLES,
    MAX_SPIRAL_TURNS,
    Easing,
    EasingKind,
    FacingMode,
    FigureEightParams,
    PathRow,
    PathStyle,
    Route,
    SpiralParams,
    TransitionSpec,
    Waypoint,
    WeaveParams,
    ZigzagParams,
)
from showpy.core.errors import PathError, SchemaError
from showpy.core.ids import RouteId, SpotId, make_tid
from showpy.core.readonly import ReadOnlyMap

NEGATIVE_COUNTS = -4.0
NEIGHBOUR_COUNTS = 8.0
BUILT_INS: dict[str, type[WeaveParams | ZigzagParams | FigureEightParams | SpiralParams]] = {
    "weave": WeaveParams,
    "zigzag": ZigzagParams,
    "figure_eight": FigureEightParams,
    "spiral": SpiralParams,
}
TYPED_PARAMS = [
    ("weave", {"amplitude": 0.5, "cycles": 1.5}),
    ("zigzag", {"cycles": 3}),
    ("figure_eight", {"amplitude": -2.0}),
    ("spiral", {"turns": 2, "clockwise": True}),
]
CUSTOM_PARAMS = {"bend": [1, {"deep": True}], "label": "x"}
ALL_KINDS: list[EasingKind] = [
    "linear", "ease_in", "ease_out", "ease_in_out", "hold_then_burst", "burst_then_settle",
    "steps", "custom",
]  # fmt: skip
CUSTOM_POINTS = ((0.0, 0.0), (1.0, 1.0))


def _custom_row(motion_fn: str | None, params: object) -> PathRow:
    return PathRow.model_validate(
        {"entity": "T1", "style": "custom", "motion_fn": motion_fn, "motion_params": params}
    )


def test_path_row_defaults_and_window() -> None:
    row = PathRow(entity=SpotId("T1"))
    assert row.style == PathStyle.STRAIGHT
    assert row.facing_mode == FacingMode.TO_TARGET
    assert row.window(8.0) == (0.0, 8.0)
    assert PathRow(entity=SpotId("T1"), start=2, end=6).window(8.0) == (2.0, 6.0)


def test_path_row_rejects_bad_window() -> None:
    with pytest.raises(PathError):
        PathRow(entity=SpotId("T1"), start=4, end=2)


def test_waypoint_and_easing_defaults() -> None:
    assert Waypoint(x=1, y=2).at is None
    assert Easing().kind == "linear"


def test_transition_spec_row_lookup_defaults_to_straight() -> None:
    spec = TransitionSpec(id=make_tid("1", "2"), counts=8.0)
    assert spec.row("T1") == PathRow(entity=SpotId("T1"))


def test_transition_spec_round_trips_json() -> None:
    spec = TransitionSpec(
        id=make_tid("1", "2"),
        counts=8.0,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), waypoints=(Waypoint(x=1, y=1),))},
    )
    assert TransitionSpec.model_validate_json(spec.model_dump_json()) == spec


@pytest.mark.defect("UP-9.6-26")
@pytest.mark.xfail(strict=True, reason="UP-9.6-26")
def test_regression_transition_spec_checks_route_keys_against_route_ids() -> None:
    """Rejects a route stored under a key that differs from its ID (audit 9.6)."""
    route = Route(id=RouteId("y"), curve={}, members=(), offsets=(), travel=1.0)
    with pytest.raises(SchemaError):
        TransitionSpec(id=make_tid("1", "2"), counts=NEIGHBOUR_COUNTS, routes={RouteId("x"): route})


@pytest.mark.defect("UP-9.6-34")
@pytest.mark.xfail(strict=True, reason="UP-9.6-34")
def test_regression_path_row_rejects_nan_start() -> None:
    """Rejects a NaN PathRow.start (audit 9.6)."""
    with pytest.raises(PathError):
        PathRow(entity=SpotId("A"), start=math.nan)


@pytest.mark.defect("UP-9.6-38")
@pytest.mark.xfail(strict=True, reason="UP-9.6-38")
def test_regression_transition_spec_rejects_negative_counts() -> None:
    """Rejects negative TransitionSpec.counts (audit 9.6)."""
    with pytest.raises(SchemaError):
        TransitionSpec(id=make_tid("1", "2"), counts=NEGATIVE_COUNTS)


@pytest.mark.parametrize("name", sorted(BUILT_INS))
def test_builtin_motion_params_reject_unknown_keys(name: str) -> None:
    """Refuses a key no built-in motion reads, instead of ignoring it silently."""
    with pytest.raises(SchemaError):
        _custom_row(name, {"wobble": 1.0})


@pytest.mark.parametrize(("name", "params"), TYPED_PARAMS)
def test_regression_motion_params_are_typed_by_motion_fn(
    name: str, params: dict[str, object]
) -> None:
    """Validates each built-in's parameters into its own model and round-trips them via JSON."""
    row = _custom_row(name, params)
    assert row.motion_params == BUILT_INS[name].model_validate(params)
    assert PathRow.model_validate_json(row.model_dump_json()) == row


def test_regression_custom_motion_params_are_deeply_read_only() -> None:
    """Stores a custom motion's parameters as nested read-only JSON that still dumps plain."""
    params = _custom_row("probe", CUSTOM_PARAMS).motion_params
    assert isinstance(params, ReadOnlyMap)
    assert not isinstance(params, MutableMapping)
    bend = params["bend"]
    assert isinstance(bend, tuple)
    assert isinstance(bend[1], ReadOnlyMap)
    assert json.loads(_custom_row("probe", CUSTOM_PARAMS).model_dump_json())["motion_params"] == (
        CUSTOM_PARAMS
    )
    assert isinstance(PathRow(entity=SpotId("T1")).motion_params, ReadOnlyMap)


@pytest.mark.parametrize(
    ("motion_fn", "params"),
    [("zigzag", WeaveParams()), ("probe", WeaveParams()), (None, SpiralParams())],
)
def test_regression_typed_params_must_match_the_motion(
    motion_fn: str | None, params: WeaveParams | SpiralParams
) -> None:
    """Refuses one motion's typed parameters under another motion's name."""
    with pytest.raises(SchemaError):
        _custom_row(motion_fn, params)


@pytest.mark.parametrize("model", [WeaveParams, ZigzagParams])
@pytest.mark.parametrize("cycles", [0.0, -1.0, 0.3, MAX_MOTION_CYCLES + 0.5])
def test_regression_wave_cycles_are_positive_half_turns_under_the_cap(
    model: type[WeaveParams | ZigzagParams], cycles: float
) -> None:
    """Refuses zero, negative, off-grid and over-cap cycles for weave and zigzag."""
    assert model(cycles=MAX_MOTION_CYCLES).cycles == MAX_MOTION_CYCLES
    with pytest.raises(SchemaError):
        model(cycles=cycles)


@pytest.mark.parametrize("turns", [-1, 1.5, MAX_SPIRAL_TURNS + 1])
def test_regression_spiral_turns_are_whole_and_capped(turns: float) -> None:
    """Refuses negative, fractional and over-cap spiral turns."""
    assert SpiralParams(turns=MAX_SPIRAL_TURNS).turns == MAX_SPIRAL_TURNS
    with pytest.raises(SchemaError):
        SpiralParams.model_validate({"turns": turns})


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_regression_easing_strength_is_at_least_one(kind: EasingKind) -> None:
    """Refuses a strength below 1 for every kind, since v1 clamped it to 1 silently."""
    points = CUSTOM_POINTS if kind == "custom" else ()
    assert Easing(kind=kind, strength=1.0, points=points).strength == 1.0
    with pytest.raises(SchemaError):
        Easing(kind=kind, strength=0.99, points=points)


def test_regression_easing_steps_are_capped() -> None:
    """Accepts the step cap and refuses one step more."""
    assert Easing(kind="steps", strength=MAX_EASING_STEPS).strength == MAX_EASING_STEPS
    with pytest.raises(SchemaError):
        Easing(kind="steps", strength=MAX_EASING_STEPS + 1)
