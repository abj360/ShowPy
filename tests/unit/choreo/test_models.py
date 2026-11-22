#!/usr/bin/env python3

"""
test_models.py --- tests Placement, Set editing and locks, and the moved model names

Contains:
    test_moved_choreo_model_names_warn: warns and resolves each name that moved out of this module
    test_moved_model_name_count: pins the number of moved names at 26
    test_unknown_choreo_model_name_raises: raises AttributeError for a name never in the module
    test_regression_frozen_model_replace_revalidates_negative_counts: rejects a negative count
        through FrozenModel.replace
    test_regression_frozen_model_replace_revalidates_duplicate_placements: rejects duplicated
        placements through FrozenModel.replace
    test_regression_set_rejects_non_finite_counts: rejects NaN and infinite Set.counts
    test_regression_placement_normalizes_facing: wraps a placement facing of 720 degrees into [0,
        360)
"""

import importlib
import math

import pytest

from showpy.choreo import models as choreo_models
from showpy.choreo.models import ApprovalState, Placement, Set
from showpy.core.deprecation import DeprecatedWarning
from showpy.core.errors import LockedError, SchemaError, UnknownRefError
from showpy.core.ids import SetId, SpotId
from showpy.core.selection import Selection
from showpy.core.units import normalize_angle
from showpy.core.util import approx_equal
from tests.helpers import make_facing_set

TURNS_PAST_FULL = 720.0
NEGATIVE_COUNTS = -4.0
ANGLE_TOL = 1e-9


def _set() -> Set:
    return make_facing_set(("T1", 0.0, 0.0, 0.0), ("T2", 2.0, 0.0, 0.0), pinned=("T2",))


def test_set_defaults() -> None:
    assert _set().state == ApprovalState.DRAFT


def test_set_rejects_duplicate_entities() -> None:
    with pytest.raises(SchemaError):
        Set(id=SetId("1"), counts=0, placements=(Placement(entity=SpotId("A"), x=0, y=0),) * 2)


def test_set_rejects_negative_counts() -> None:
    with pytest.raises(SchemaError):
        Set(id=SetId("1"), counts=-1, placements=())


def test_placement_rejects_non_finite() -> None:
    with pytest.raises(SchemaError):
        Placement(entity=SpotId("A"), x=float("nan"), y=0.0)


def test_get_and_unknown() -> None:
    assert _set().get("T2").x == 2.0
    with pytest.raises(UnknownRefError):
        _set().get("Z9")


def test_positions_in_selection_order() -> None:
    s = _set()
    assert approx_equal(s.positions(["T2", "T1"]), [[2.0, 0.0], [0.0, 0.0]])
    assert approx_equal(s.positions(Selection(ids=(SpotId("T1"),))), [[0.0, 0.0]])
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
    new = Placement(entity=SpotId("T1"), x=1.0, y=1.0, facing=90.0)
    assert _set().with_placements({"T1": new}).get("T1").facing == 90.0


def test_with_placements_rejects_entity_mismatch() -> None:
    with pytest.raises(SchemaError):
        _set().with_placements({"T1": Placement(entity=SpotId("T9"), x=0, y=0)})


def test_table_columns() -> None:
    table = _set().table()
    assert list(table.columns) == [
        "entity", "x", "y", "facing", "form", "order", "pinned", "on_field", "note",
    ]  # fmt: skip
    assert len(table) == 2


def test_regression_frozen_model_replace_revalidates_negative_counts() -> None:
    """Rejects a negative count through FrozenModel.replace (audit 9.6)."""
    with pytest.raises(SchemaError):
        Set(id=SetId("1"), counts=0.0).replace(counts=NEGATIVE_COUNTS)


def test_regression_frozen_model_replace_revalidates_duplicate_placements() -> None:
    """Rejects duplicated placements through FrozenModel.replace (audit 9.6)."""
    placed = Set(
        id=SetId("1"), counts=0.0, placements=(Placement(entity=SpotId("A"), x=0.0, y=0.0),)
    )
    twice = placed.placements * 2
    with pytest.raises(SchemaError):
        placed.replace(placements=twice)


@pytest.mark.parametrize("counts", [math.nan, math.inf])
def test_regression_set_rejects_non_finite_counts(counts: float) -> None:
    """Rejects NaN and infinite Set.counts (audit 9.6)."""
    with pytest.raises(SchemaError):
        Set(id=SetId("1"), counts=counts)


def test_regression_placement_normalizes_facing() -> None:
    """Wraps a placement facing of 720 degrees into [0, 360) (audit 9.6)."""
    placement = Placement(entity=SpotId("A"), x=0.0, y=0.0, facing=TURNS_PAST_FULL)
    assert approx_equal(placement.facing, normalize_angle(TURNS_PAST_FULL), abs_tol=ANGLE_TOL)


MOVED_MODEL_NAMES = {
    "form_models": (
        "SpacingSpec FacingSpec TextRecipe FormKind CrowdedStroke TextReport Form FormFnInfo "
        "FONT_URI_PATTERN MAX_TEXT_FONTS FontUri"
    ),
    "path_models": (
        "PathStyle FacingMode TurnDirection FacingOverride Waypoint Easing PathRow Route "
        "TransitionSpec DEFAULT_EASING_STRENGTH"
    ),
    "match_models": "MatchObjective CostFn MatchOptions MatchResult MATCH_WEIGHT_KEYS",
}
UNKNOWN_NAME = "NoSuchName"
MOVED_CASES = [
    (module, name) for module, names in MOVED_MODEL_NAMES.items() for name in names.split()
]


def test_moved_model_name_count() -> None:
    assert len(MOVED_CASES) == 26


@pytest.mark.parametrize(("module", "name"), MOVED_CASES)
def test_moved_choreo_model_names_warn(module: str, name: str) -> None:
    new_module = importlib.import_module(f"showpy.choreo.{module}")
    with pytest.warns(DeprecatedWarning, match=f"`{name}` moved to"):
        old = getattr(choreo_models, name)
    assert old is getattr(new_module, name)


def test_unknown_choreo_model_name_raises() -> None:
    with pytest.raises(AttributeError):
        getattr(choreo_models, UNKNOWN_NAME)
