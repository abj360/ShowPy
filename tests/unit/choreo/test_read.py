#!/usr/bin/env python3

"""
test_read.py --- tests the floor readers: frames and coordinate tables

Contains:
    test_frames_equal_sample_at_every_frame: matches PathEngine.sample at each frame's count
    test_frames_fall_every_frame_interval: spaces frame times by 1/fps from the start count
    test_frames_keep_counts_and_spots_still_during_a_hold: repeats the hold's count and positions
    test_regression_frames_at_a_hold_a_subnormal_count_in: starts at count 0, equals sample at
        both edges of a hold at 5e-324 counts, and reads 5e-324 strictly inside it
    test_frames_follow_the_selection_order: orders columns as the selection names the spots
    test_frames_reuse_a_given_engine: equals the frames read with a fresh engine
    test_regression_frames_reject_a_range_outside_the_show: raises TimeRangeError with a hint
    test_regression_frames_reject_a_frame_rate_not_above_zero: raises SchemaError
    test_regression_frames_reject_an_engine_of_another_show: raises SchemaError
    test_regression_frames_cap_the_frame_values: raises SchemaError before allocating
    test_regression_frames_refuse_a_frame_count_past_the_float_range: raises a short, taught
        SchemaError, not OverflowError or a 300-digit count, when range times fps is huge
    test_regression_frames_model_rejects_mismatched_shapes: raises SchemaError
    test_coordinate_table_equals_positions_at_each_set: matches PathEngine.positions at every
        set's count
    test_coordinate_table_converts_to_a_unit: gives positions in the unit asked for, unrounded,
        each one the meter value converted
    test_coordinate_table_reads_every_spot_by_default: selects "all" when no spots are named
    test_coordinate_table_reads_named_sets_in_order: keeps the order the sets are named in
    test_coordinate_table_reports_who_is_on_the_field: copies each placement's field state
    test_regression_coordinate_table_rejects_an_unknown_unit: raises UnknownRefError naming the
        units it knows
    test_regression_coordinate_table_rejects_an_unknown_set: raises UnknownRefError
    test_regression_coordinate_table_rejects_no_sets: raises SchemaError
    test_regression_coordinate_table_model_rejects_mismatched_shapes: raises SchemaError
"""

import numpy as np
import pytest

from e8s.choreo.engine import PathEngine
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import FacingMode, PathRow
from e8s.choreo.read import coordinate_table, frames
from e8s.choreo.read_models import CoordinateTable, Frames
from e8s.core.errors import SchemaError, TimeRangeError, UnknownRefError
from e8s.core.ids import SpotId
from e8s.core.units import convert
from e8s.show.models import Show
from e8s.time.models import Hold
from tests.fixtures.motion_shows import with_rows, with_targets
from tests.fixtures.shows import two_set_show

HOLD_COUNT = 4.0
HOLD_SECONDS = 1.0
SUBNORMAL_COUNT = 5e-324
FPS = 8.0
SECONDS_PER_COUNT = 0.5
MESSAGE_LIMIT = 200


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


def test_regression_frames_at_a_hold_a_subnormal_count_in() -> None:
    show = two_set_show()
    hold = Hold(at_count=SUBNORMAL_COUNT, seconds=HOLD_SECONDS)
    show = show.replace(timeline=show.timeline.replace(holds=(hold,)))
    read = frames(show, 0.0, 8.0, fps=FPS)
    assert read.counts[0] == 0.0
    engine = PathEngine(show)
    edges = np.flatnonzero((read.seconds == 0.0) | (read.seconds == HOLD_SECONDS))
    assert len(edges) == 2
    for row in edges.tolist():
        states = {state.entity: state for state in engine.sample(float(read.counts[row])).states}
        for column, spot in enumerate(read.spots):
            assert (read.x[row, column], read.y[row, column]) == states[spot].position
            assert read.facing[row, column] == states[spot].facing
    inside = (read.seconds > 0.0) & (read.seconds < HOLD_SECONDS)
    assert inside.any()
    assert np.all(read.counts[inside] == SUBNORMAL_COUNT)


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
        frames(two_set_show(), 0.0, 8.0, fps=2e6)


@pytest.mark.parametrize("fps", [1e308, 1e300, 1e8])
def test_regression_frames_refuse_a_frame_count_past_the_float_range(fps: float) -> None:
    with pytest.raises(SchemaError, match="too many frames") as caught:
        frames(two_set_show(), 0.0, 8.0, fps=fps)
    assert "lower fps" in caught.value.hint
    assert len(caught.value.message) < MESSAGE_LIMIT


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
    Frames.from_json(FRAMES_FIELDS)
    with pytest.raises(SchemaError, match=r"shape|counts"):
        Frames.from_json({**FRAMES_FIELDS, **change})


def test_coordinate_table_equals_positions_at_each_set() -> None:
    show = _turning_show()
    table = coordinate_table(show, "all")
    engine = PathEngine(show)
    for row, count in enumerate(table.counts.tolist()):
        points = engine.positions(count, list(table.spots))
        assert table.x[row].tolist() == points[:, 0].tolist()
        assert table.y[row].tolist() == points[:, 1].tolist()


def test_coordinate_table_converts_to_a_unit() -> None:
    show = _turning_show()
    meters = coordinate_table(show, "T1 + T2")
    feet = coordinate_table(show, "T1 + T2", unit="ft")
    assert (meters.unit, feet.unit) == ("m", "ft")
    for in_meters, in_feet in ((meters.x, feet.x), (meters.y, feet.y)):
        expected = [[convert(v, "m", "ft") for v in row] for row in in_meters.tolist()]
        assert in_feet.tolist() == expected


def test_coordinate_table_reads_every_spot_by_default() -> None:
    assert coordinate_table(two_set_show()).spots == ("T1", "T2", "T3", "T4")


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


def test_regression_coordinate_table_rejects_an_unknown_unit() -> None:
    with pytest.raises(UnknownRefError, match="unknown unit") as caught:
        coordinate_table(two_set_show(), unit="furlong")
    assert "ft" in caught.value.hint


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
    fields = coordinate_table(two_set_show(), "T1").to_json()
    with pytest.raises(SchemaError, match=r"shape|counts"):
        CoordinateTable.from_json({**fields, **change})
