#!/usr/bin/env python3

"""
test_models.py --- tests Placement, Set editing and locks, forms specs and path rows
"""

import contextlib
import math

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
    Route,
    Set,
    SpacingSpec,
    TransitionSpec,
    Waypoint,
)
from showpy.core.errors import LockedError, PathError, SchemaError, UnknownRefError
from showpy.core.selection import Selection
from showpy.core.units import normalize_angle
from showpy.core.util import approx_equal
from tests.helpers import make_facing_set


def _set() -> Set:
    return make_facing_set(("T1", 0.0, 0.0, 0.0), ("T2", 2.0, 0.0, 0.0), pinned=("T2",))


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


TURNS_PAST_FULL = 720.0
NEGATIVE_COUNTS = -4.0
NEGATIVE_GAP = -1.0
NEIGHBOUR_COUNTS = 8.0
ANGLE_TOL = 1e-9


def _custom_form() -> Form:
    return Form(id="f", set="1", kind="custom", geometry={"n": 2}, members=("A",))


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


@pytest.mark.defect("UP-9.6-22")
@pytest.mark.xfail(strict=True, reason="UP-9.6-22")
def test_regression_frozen_model_replace_revalidates_negative_counts() -> None:
    """Rejects a negative count through FrozenModel.replace (audit 9.6)."""
    with pytest.raises(SchemaError):
        Set(id="1", counts=0.0).replace(counts=NEGATIVE_COUNTS)  # type: ignore[attr-defined]


@pytest.mark.defect("UP-9.6-23")
@pytest.mark.xfail(strict=True, reason="UP-9.6-23")
def test_regression_frozen_model_replace_revalidates_duplicate_placements() -> None:
    """Rejects duplicated placements through FrozenModel.replace (audit 9.6)."""
    placed = Set(id="1", counts=0.0, placements=(Placement(entity="A", x=0.0, y=0.0),))
    twice = placed.placements * 2
    with pytest.raises(SchemaError):
        placed.replace(placements=twice)  # type: ignore[attr-defined]


@pytest.mark.defect("UP-9.6-26")
@pytest.mark.xfail(strict=True, reason="UP-9.6-26")
def test_regression_transition_spec_checks_route_keys_against_route_ids() -> None:
    """Rejects a route stored under a key that differs from its ID (audit 9.6)."""
    route = Route(id="y", curve={}, members=(), offsets=(), travel=1.0)
    with pytest.raises(SchemaError):
        TransitionSpec(id=("1", "2"), counts=NEIGHBOUR_COUNTS, routes={"x": route})


@pytest.mark.defect("UP-9.6-33")
@pytest.mark.xfail(strict=True, reason="UP-9.6-33")
@pytest.mark.parametrize("counts", [math.nan, math.inf])
def test_regression_set_rejects_non_finite_counts(counts: float) -> None:
    """Rejects NaN and infinite Set.counts (audit 9.6)."""
    with pytest.raises(SchemaError):
        Set(id="1", counts=counts)


@pytest.mark.defect("UP-9.6-34")
@pytest.mark.xfail(strict=True, reason="UP-9.6-34")
def test_regression_path_row_rejects_nan_start() -> None:
    """Rejects a NaN PathRow.start (audit 9.6)."""
    with pytest.raises(PathError):
        PathRow(entity="A", start=math.nan)


@pytest.mark.defect("UP-9.6-36")
@pytest.mark.xfail(strict=True, reason="UP-9.6-36")
def test_regression_facing_spec_rejects_nan_point() -> None:
    """Rejects a toward_point facing whose point is NaN (audit 9.6)."""
    with pytest.raises(SchemaError):
        FacingSpec(mode="toward_point", value=(math.nan, 0.0))


@pytest.mark.defect("UP-9.6-38")
@pytest.mark.xfail(strict=True, reason="UP-9.6-38")
def test_regression_transition_spec_rejects_negative_counts() -> None:
    """Rejects negative TransitionSpec.counts (audit 9.6)."""
    with pytest.raises(SchemaError):
        TransitionSpec(id=("1", "2"), counts=NEGATIVE_COUNTS)


@pytest.mark.defect("UP-9.6-39")
@pytest.mark.xfail(strict=True, reason="UP-9.6-39")
def test_regression_spacing_spec_rejects_negative_gaps() -> None:
    """Rejects a negative extra gap in SpacingSpec.gaps (audit 9.6)."""
    with pytest.raises(SchemaError):
        SpacingSpec(gaps=((0, NEGATIVE_GAP),))


@pytest.mark.defect("UP-9.6-40")
@pytest.mark.xfail(strict=True, reason="UP-9.6-40")
def test_regression_placement_normalizes_facing() -> None:
    """Wraps a placement facing of 720 degrees into [0, 360) (audit 9.6)."""
    placement = Placement(entity="A", x=0.0, y=0.0, facing=TURNS_PAST_FULL)
    assert approx_equal(placement.facing, normalize_angle(TURNS_PAST_FULL), ANGLE_TOL)


@pytest.mark.defect("UP-9.6-41")
@pytest.mark.xfail(strict=True, reason="UP-9.6-41")
def test_regression_facing_spec_normalizes_fixed_value() -> None:
    """Wraps a fixed facing of 720 degrees into [0, 360) (audit 9.6)."""
    spec = FacingSpec(mode="fixed", value=TURNS_PAST_FULL)
    assert isinstance(spec.value, float)
    assert approx_equal(spec.value, normalize_angle(TURNS_PAST_FULL), ANGLE_TOL)
