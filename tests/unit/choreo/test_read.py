#!/usr/bin/env python3

"""
test_read.py --- tests the floor readers: frames and coordinate tables

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
    test_regression_frames_refuse_a_frame_count_past_the_float_range: raises a taught
        SchemaError, not OverflowError, when range times fps overflows
    test_regression_frames_model_rejects_mismatched_shapes: raises SchemaError
    test_coordinate_table_equals_positions_at_each_set: matches PathEngine.positions at every
        set's count
    test_coordinate_table_counts_steps_from_the_origin: counts 8-to-5 steps along the axes of
        the show's coordinate system and rounds them half away from zero
    test_coordinate_table_reads_named_sets_in_order: keeps the order the sets are named in
    test_coordinate_table_reports_who_is_on_the_field: copies each placement's field state
    test_regression_coordinate_table_rejects_an_unknown_resolution: raises SchemaError
    test_regression_coordinate_table_rejects_an_unknown_set: raises UnknownRefError
    test_regression_coordinate_table_rejects_no_sets: raises SchemaError
    test_regression_coordinate_table_model_rejects_mismatched_shapes: raises SchemaError
"""

import numpy as np
import pytest

from e8s.choreo.engine import PathEngine
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import FacingMode, PathRow
from e8s.choreo.read import STEP_M, coordinate_table, frames
from e8s.choreo.read_models import CoordinateTable, Frames
from e8s.core.errors import SchemaError, TimeRangeError, UnknownRefError
from e8s.core.ids import LandmarkId, SpotId
from e8s.core.units import round_to
from e8s.sheet.models import CoordinateSystem
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


def test_regression_frames_refuse_a_frame_count_past_the_float_range() -> None:
    with pytest.raises(SchemaError, match="too many frames") as caught:
        frames(two_set_show(), 0.0, 8.0, fps=1e308)
    assert "lower fps" in caught.value.hint


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


def test_coordinate_table_equals_positions_at_each_set() -> None:
    show = _turning_show()
    table = coordinate_table(show, "all")
    engine = PathEngine(show)
    for row, count in enumerate(table.counts.tolist()):
        points = engine.positions(count, list(table.spots))
        assert table.x[row].tolist() == points[:, 0].tolist()
        assert table.y[row].tolist() == points[:, 1].tolist()


def test_coordinate_table_counts_steps_from_the_origin() -> None:
    system = CoordinateSystem(origin=LandmarkId("c"), audience_side="back")
    show = two_set_show().replace(coordinates=system)
    table = coordinate_table(show, "T2 + T4", resolution=0.125)
    assert table.origin == (0.0, 10.0)
    assert table.origin_landmark == "c"
    assert table.audience_side == "back"
    assert table.across_exact[0].tolist() == [-2.0 / STEP_M, -6.0 / STEP_M]
    assert table.depth_exact[:, 0].tolist() == [5.0 / STEP_M, 1.0 / STEP_M]
    expected = [[round_to(v, 0.125) for v in row] for row in table.depth_exact.tolist()]
    assert table.depth.tolist() == expected
    assert table.step == STEP_M
    assert table.resolution == 0.125


def test_coordinate_table_reads_named_sets_in_order() -> None:
    table = coordinate_table(two_set_show(), ["T1"], sets=["2", "1"])
    assert table.sets == ("2", "1")
    assert table.counts.tolist() == [8.0, 0.0]
    assert table.y[:, 0].tolist() == [9.0, 5.0]


def test_coordinate_table_reports_who_is_on_the_field() -> None:
    base = two_set_show()
    second = base.sets[1].with_placements({"T3": PlacementPatch(on_field=False)})
    table = coordinate_table(base.replace(sets=(base.sets[0], second)), "T2 + T3")
    assert table.on_field.tolist() == [[True, True], [True, False]]


@pytest.mark.parametrize("resolution", [0.3, 2.0, 0.0])
def test_regression_coordinate_table_rejects_an_unknown_resolution(resolution: float) -> None:
    with pytest.raises(SchemaError, match="resolution") as caught:
        coordinate_table(two_set_show(), "all", resolution=resolution)
    assert "0.125" in caught.value.hint


def test_regression_coordinate_table_rejects_an_unknown_set() -> None:
    with pytest.raises(UnknownRefError, match="unknown set"):
        coordinate_table(two_set_show(), "all", sets=["9"])


def test_regression_coordinate_table_rejects_no_sets() -> None:
    with pytest.raises(SchemaError, match="no sets"):
        coordinate_table(two_set_show(), "all", sets=[])


@pytest.mark.parametrize("change", [{"x": [[0.0]]}, {"counts": [0.0, 8.0, 9.0]}])
def test_regression_coordinate_table_model_rejects_mismatched_shapes(
    change: dict[str, object],
) -> None:
    fields = coordinate_table(two_set_show(), "T1").model_dump()
    with pytest.raises(SchemaError, match=r"shape|counts"):
        CoordinateTable.model_validate({**fields, **change})
