#!/usr/bin/env python3

"""
test_read.py --- tests the floor readers: frames

Contains:
    test_frames_equal_sample_at_every_frame: matches PathEngine.sample at each frame's count
    test_frames_fall_every_frame_interval: spaces frame times by 1/fps from the start count
    test_frames_keep_counts_and_spots_still_during_a_hold: repeats the hold's count and positions
    test_frames_follow_the_selection_order: orders columns as the selection names the spots
    test_frames_reuse_a_given_engine: equals the frames read with a fresh engine
    test_regression_frames_reject_a_range_outside_the_show: raises TimeRangeError with a hint
    test_regression_frames_reject_a_frame_rate_not_above_zero: raises SchemaError
    test_regression_frames_reject_an_engine_of_another_show: raises SchemaError
    test_regression_frames_cap_the_frame_values: raises SchemaError before allocating
    test_regression_frames_model_rejects_mismatched_shapes: raises SchemaError
"""

import numpy as np
import pytest

from e8s.choreo.engine import PathEngine
from e8s.choreo.path_models import FacingMode, PathRow
from e8s.choreo.read import frames
from e8s.choreo.read_models import Frames
from e8s.core.errors import SchemaError, TimeRangeError
from e8s.core.ids import SpotId
from e8s.show.models import Show
from e8s.time.models import Hold
from tests.fixtures.motion_shows import with_rows, with_targets
from tests.fixtures.shows import two_set_show

HOLD_COUNT = 4.0
HOLD_SECONDS = 1.0
FPS = 8.0
SECONDS_PER_COUNT = 0.5


def _held_show() -> Show:
    show = two_set_show()
    hold = Hold(at_count=HOLD_COUNT, seconds=HOLD_SECONDS)
    return show.replace(timeline=show.timeline.replace(holds=(hold,)))


def _turning_show() -> Show:
    rows = {
        "T1": PathRow(entity=SpotId("T1"), facing_mode=FacingMode.TRAVEL, turn_counts=2.0),
        "T2": PathRow(entity=SpotId("T2"), start=2.0, end=6.0),
    }
    return with_rows(rows, show=with_targets({"T1": {"x": 3.0, "facing": 90.0}}))


def test_frames_equal_sample_at_every_frame() -> None:
    show = _turning_show()
    read = frames(show, 0.0, 8.0, fps=FPS)
    engine = PathEngine(show)
    for row, count in enumerate(read.counts.tolist()):
        states = {state.entity: state for state in engine.sample(count).states}
        for column, spot in enumerate(read.spots):
            state = states[spot]
            assert (read.x[row, column], read.y[row, column]) == state.position
            assert read.facing[row, column] == state.facing


def test_frames_fall_every_frame_interval() -> None:
    read = frames(two_set_show(), 2.0, 6.0, fps=FPS)
    assert len(read.seconds) == int((6.0 - 2.0) * SECONDS_PER_COUNT * FPS) + 1
    assert np.allclose(np.diff(read.seconds), 1.0 / FPS, rtol=0.0, atol=1e-12)
    assert read.counts[0] == 2.0
    assert read.counts[-1] == 6.0


def test_frames_keep_counts_and_spots_still_during_a_hold() -> None:
    read = frames(_held_show(), 0.0, 8.0, fps=FPS)
    held = np.flatnonzero(read.counts == HOLD_COUNT)
    assert len(held) == HOLD_SECONDS * FPS + 1
    assert np.all(read.y[held] == read.y[held[0]])
    assert np.all(read.x[held] == read.x[held[0]])


def test_frames_follow_the_selection_order() -> None:
    read = frames(two_set_show(), 0.0, 8.0, who="T3 + T1")
    assert read.spots == ("T3", "T1")
    assert read.x[0].tolist() == [4.0, 0.0]


def test_frames_reuse_a_given_engine() -> None:
    show = _turning_show()
    assert frames(show, 1.0, 7.0, engine=PathEngine(show)) == frames(show, 1.0, 7.0)


@pytest.mark.parametrize(("start", "end"), [(-1.0, 4.0), (2.0, 9.0), (6.0, 2.0)])
def test_regression_frames_reject_a_range_outside_the_show(start: float, end: float) -> None:
    with pytest.raises(TimeRangeError, match="not inside") as caught:
        frames(two_set_show(), start, end)
    assert "start <= end" in caught.value.hint


@pytest.mark.parametrize("fps", [0.0, -30.0, float("nan")])
def test_regression_frames_reject_a_frame_rate_not_above_zero(fps: float) -> None:
    with pytest.raises(SchemaError, match="fps"):
        frames(two_set_show(), 0.0, 8.0, fps=fps)


def test_regression_frames_reject_an_engine_of_another_show() -> None:
    with pytest.raises(SchemaError, match="another show"):
        frames(two_set_show(), 0.0, 8.0, engine=PathEngine(two_set_show()))


def test_regression_frames_cap_the_frame_values() -> None:
    with pytest.raises(SchemaError, match="frame values"):
        frames(two_set_show(), 0.0, 8.0, fps=1e9)


FRAMES_FIELDS = {
    "spots": ["T1"],
    "fps": 1.0,
    "seconds": [0.0, 1.0],
    "counts": [0.0, 2.0],
    "x": [[0.0], [0.0]],
    "y": [[0.0], [1.0]],
    "facing": [[0.0], [0.0]],
}


@pytest.mark.parametrize("change", [{"x": [[0.0]]}, {"counts": [0.0]}])
def test_regression_frames_model_rejects_mismatched_shapes(change: dict[str, object]) -> None:
    Frames.model_validate(FRAMES_FIELDS)
    with pytest.raises(SchemaError, match=r"shape|counts"):
        Frames.model_validate({**FRAMES_FIELDS, **change})
