#!/usr/bin/env python3

"""
test_engine_facing.py --- tests facing blends at the edges of a move window

Contains:
    test_regression_facing_does_not_snap_at_window_end: blends facing into the next set instead of
        jumping at the window end
"""

from e8s.choreo.engine import PathEngine
from e8s.choreo.path_models import FacingMode, PathRow
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
