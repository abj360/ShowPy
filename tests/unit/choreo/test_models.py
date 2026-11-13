#!/usr/bin/env python3

"""
test_models.py --- tests Placement, Set editing and locks, forms specs and path rows
"""

import pytest

from showpy.choreo.models import (
    ApprovalState,
    Easing,
    FacingMode,
    FacingSpec,
    Form,
    PathRow,
    PathStyle,
    Placement,
    Set,
    SpacingSpec,
    TransitionSpec,
    Waypoint,
)
from showpy.core.errors import LockedError, PathError, SchemaError, UnknownRefError
from showpy.core.selection import Selection
from showpy.core.util import approx_equal


def _set() -> Set:
    return Set(
        id="1",
        counts=0.0,
        placements=(
            Placement(entity="T1", x=0.0, y=0.0),
            Placement(entity="T2", x=2.0, y=0.0, pinned=True),
        ),
    )


def test_set_defaults() -> None:
    assert _set().state == ApprovalState.DRAFT


def test_set_rejects_duplicate_entities() -> None:
    with pytest.raises(SchemaError):
        Set(id="1", counts=0, placements=(Placement(entity="A", x=0, y=0),) * 2)


def test_set_rejects_negative_counts() -> None:
    with pytest.raises(SchemaError):
        Set(id="1", counts=-1, placements=())


def test_placement_rejects_non_finite() -> None:
    with pytest.raises(SchemaError):
        Placement(entity="A", x=float("nan"), y=0.0)


def test_get_and_unknown() -> None:
    assert _set().get("T2").x == 2.0
    with pytest.raises(UnknownRefError):
        _set().get("Z9")


def test_positions_in_selection_order() -> None:
    s = _set()
    assert approx_equal(s.positions(["T2", "T1"]), [[2.0, 0.0], [0.0, 0.0]])
    assert approx_equal(s.positions(Selection(ids=("T1",))), [[0.0, 0.0]])
    assert s.positions().shape == (2, 2)


def test_with_placements_updates_without_mutating() -> None:
    s = _set()
    moved = s.with_placements({"T1": {"x": 5.0}})
    assert moved.get("T1").x == 5.0
    assert s.get("T1").x == 0.0


def test_with_placements_respects_pins() -> None:
    with pytest.raises(LockedError):
        _set().with_placements({"T2": {"x": 9.0}})
    assert _set().with_placements({"T2": {"x": 9.0}}, force=True).get("T2").x == 9.0


def test_with_placements_accepts_placement_objects() -> None:
    new = Placement(entity="T1", x=1.0, y=1.0, facing=90.0)
    assert _set().with_placements({"T1": new}).get("T1").facing == 90.0


def test_with_placements_rejects_entity_mismatch() -> None:
    with pytest.raises(SchemaError):
        _set().with_placements({"T1": Placement(entity="T9", x=0, y=0)})


def test_table_columns() -> None:
    table = _set().table()
    assert list(table.columns) == [
        "entity", "x", "y", "facing", "form", "order", "pinned", "on_field", "note",
    ]  # fmt: skip
    assert len(table) == 2


def test_spacing_and_facing_spec_validation() -> None:
    assert SpacingSpec().mode == "count"
    with pytest.raises(SchemaError):
        SpacingSpec(mode="interval")
    with pytest.raises(SchemaError):
        FacingSpec(mode="toward_point")


def test_form_requires_unique_members() -> None:
    with pytest.raises(SchemaError):
        Form(id="f", set="1", kind="line", geometry={}, members=("A", "A"))


def test_path_row_defaults_and_window() -> None:
    row = PathRow(entity="T1")
    assert row.style == PathStyle.STRAIGHT
    assert row.facing_mode == FacingMode.TO_TARGET
    assert row.window(8.0) == (0.0, 8.0)
    assert PathRow(entity="T1", start=2, end=6).window(8.0) == (2.0, 6.0)


def test_path_row_rejects_bad_window() -> None:
    with pytest.raises(PathError):
        PathRow(entity="T1", start=4, end=2)


def test_waypoint_and_easing_defaults() -> None:
    assert Waypoint(x=1, y=2).at is None
    assert Easing().kind == "linear"


def test_transition_spec_row_lookup_defaults_to_straight() -> None:
    spec = TransitionSpec(id=("1", "2"), counts=8.0)
    assert spec.row("T1") == PathRow(entity="T1")


def test_transition_spec_round_trips_json() -> None:
    spec = TransitionSpec(
        id=("1", "2"),
        counts=8.0,
        rows={"T1": PathRow(entity="T1", waypoints=(Waypoint(x=1, y=1),))},
    )
    assert TransitionSpec.model_validate_json(spec.model_dump_json()) == spec
