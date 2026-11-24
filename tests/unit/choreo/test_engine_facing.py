#!/usr/bin/env python3

"""
test_engine_facing.py --- tests facing blends at the edges of a move window

Contains:
    test_regression_facing_does_not_snap_at_window_end: blends facing into the next set instead of
        jumping at the window end
    test_plan_facings_equal_plan_facing_for_steady_modes: steps between the same facings that
        plan_facing returns one count at a time
    test_plan_facings_defer_gradual_turns: returns None for turn counts and travel-led modes
"""

import numpy as np
import pytest

from e8s.choreo.engine import PathEngine
from e8s.choreo.engine_facing import FacingContext, plan_facing, plan_facings
from e8s.choreo.path_models import FacingMode, FixedFacing, PathRow
from e8s.core.ids import SpotId
from e8s.core.units import angle_diff
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.helpers import defect

JUST_BEFORE_END = 8.0 - 1e-3
WINDOW_END = 8.0
MAX_JUMP_DEG = 1.0
TURN_COUNTS = 2.0


@defect("UP-9.2-33")
def test_regression_facing_does_not_snap_at_window_end() -> None:
    """Blends facing into the next set instead of jumping at the window end (audit 9.2)."""
    row = PathRow(entity=SpotId("T1"), facing_mode=FacingMode.TRAVEL, turn_counts=TURN_COUNTS)
    show = with_rows({"T1": row}, show=with_targets({"T1": {"x": 4.0, "y": 5.0}}))
    engine = PathEngine(show)
    before = engine.facing_at("T1", JUST_BEFORE_END, local=TID)
    after = engine.facing_at("T1", WINDOW_END, local=TID)
    assert abs(angle_diff(before, after)) <= MAX_JUMP_DEG


STEADY_ROWS = [
    PathRow(entity=SpotId("T1"), start=2.0, end=6.0),
    PathRow(entity=SpotId("T1"), start=1.0, facing_mode=FacingMode.AUDIENCE),
    PathRow(
        entity=SpotId("T1"),
        end=5.0,
        facing_mode=FacingMode.FIXED,
        facing_value=FixedFacing(degrees=135.0),
    ),
]
PROBE_COUNTS = np.linspace(0.0, 8.0, 33)


@pytest.mark.parametrize("row", STEADY_ROWS)
def test_plan_facings_equal_plan_facing_for_steady_modes(row: PathRow) -> None:
    show = with_rows({"T1": row}, show=with_targets({"T1": {"facing": 90.0}}))
    engine = PathEngine(show)
    plan = engine.compile(TID).plans[SpotId("T1")]
    context = FacingContext(lambda _e, _t: (0.0, 0.0), 0.0, WINDOW_END)
    stepped = plan_facings(plan, PROBE_COUNTS)
    assert stepped is not None
    one_by_one = [plan_facing(plan, tau, context) for tau in PROBE_COUNTS.tolist()]
    assert stepped.tolist() == one_by_one


@pytest.mark.parametrize(
    "row",
    [
        PathRow(entity=SpotId("T1"), turn_counts=TURN_COUNTS),
        PathRow(entity=SpotId("T1"), facing_mode=FacingMode.TRAVEL),
    ],
)
def test_plan_facings_defer_gradual_turns(row: PathRow) -> None:
    plan = PathEngine(with_rows({"T1": row})).compile(TID).plans[SpotId("T1")]
    assert plan_facings(plan, PROBE_COUNTS) is None
