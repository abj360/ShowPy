#!/usr/bin/env python3

"""
test_read_describe.py --- tests describe_transition, one segment kind at a time

Contains:
    test_describe_hold: describes a spot that stands still as one hold
    test_describe_straight: gives a straight move's direction, distance, counts and step size
    test_describe_straight_window_adds_holds: holds before and after a move's window
    test_describe_polyline_splits_at_corners: gives each straight leg its own direction
    test_describe_merges_adjacent_holds: joins a held window start and a waypoint wait in place
    test_regression_curve_style_without_waypoints_is_straight: names the kind from the curve
    test_regression_still_moments_inside_a_move_are_holds: splits a burst after a wait
    test_regression_out_and_back_splits_at_its_turn: splits a collinear reversal at the
        polyline corner, which heading comparison alone would join
    test_describe_eased_straight_stays_one_segment: keeps an eased straight move whole
    test_describe_curve: describes a spline move as one curve segment of its arc length
    test_describe_rotate: describes a rotation as one rotate segment of its arc length
    test_describe_follow: describes a route move as one follow segment
    test_describe_custom: describes a registered motion as one custom segment
    test_describe_pace_shortens_the_stride: halves meters per step at double time
    test_describe_groups_alike_spots: groups spots whose segments agree, in selection order
    test_describe_reuses_a_given_engine: equals the description read with a fresh engine
    test_regression_describe_rejects_an_unknown_transition: raises UnknownRefError with a hint
    test_regression_describe_rejects_an_unknown_spot: raises UnknownRefError
"""

import math

import pytest

from e8s.choreo.engine import PathEngine
from e8s.choreo.path_models import Easing, PathRow, PathStyle, Route, Waypoint
from e8s.choreo.read import describe_transition
from e8s.choreo.read_models import SegmentDescription, TransitionDescription
from e8s.core.errors import UnknownRefError
from e8s.core.ids import RouteId, SpotId
from e8s.core.util import approx_equal
from e8s.geo.curves import Segment
from e8s.show.models import Show
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.fixtures.shows import two_set_show

UPSTAGE = 180.0
MOVE_M = 4.0
COUNTS = 8.0
TOL = 1e-9


def _segments(show: Show, spot: str = "T1") -> tuple[SegmentDescription, ...]:
    return describe_transition(show, TID).moves[SpotId(spot)]


def _one(show: Show, spot: str = "T1") -> SegmentDescription:
    (only,) = _segments(show, spot)
    return only


def test_describe_hold() -> None:
    show = with_targets({"T1": {"y": 5.0}})
    assert _one(show) == SegmentDescription(
        kind="hold",
        start=0.0,
        counts=COUNTS,
        direction=None,
        distance=0.0,
        stride_m=None,
        pace=1.0,
    )


def test_describe_straight() -> None:
    move = _one(two_set_show())
    assert (move.kind, move.start, move.counts) == ("straight", 0.0, COUNTS)
    assert move.direction == UPSTAGE
    assert move.distance == MOVE_M
    assert move.stride_m == MOVE_M / COUNTS


def test_describe_straight_window_adds_holds() -> None:
    show = with_rows({"T1": PathRow(entity=SpotId("T1"), start=2.0, end=6.0)})
    segments = _segments(show)
    assert [(s.kind, s.start, s.counts) for s in segments] == [
        ("hold", 0.0, 2.0),
        ("straight", 2.0, 4.0),
        ("hold", 6.0, 2.0),
    ]


def test_describe_polyline_splits_at_corners() -> None:
    row = PathRow(entity=SpotId("T1"), style=PathStyle.POLYLINE, waypoints=(Waypoint(x=2, y=7),))
    segments = _segments(with_rows({"T1": row}))
    assert [(s.kind, s.direction) for s in segments] == [("straight", 135.0), ("straight", 225.0)]
    assert [s.start for s in segments] == [0.0, 4.0]
    for leg in segments:
        assert approx_equal(leg.distance, math.sqrt(8.0), abs_tol=TOL)


def test_describe_merges_adjacent_holds() -> None:
    row = PathRow(
        entity=SpotId("T1"),
        style=PathStyle.POLYLINE,
        start=1.0,
        waypoints=(Waypoint(x=0, y=5, at=3.0),),
    )
    segments = _segments(with_rows({"T1": row}))
    assert [(s.kind, s.start, s.counts) for s in segments] == [
        ("hold", 0.0, 3.0),
        ("straight", 3.0, 5.0),
    ]


@pytest.mark.parametrize(
    "easing",
    [Easing(kind="ease_in_out", strength=3.0), Easing(kind="steps", strength=4.0)],
)
def test_describe_eased_straight_stays_one_segment(easing: Easing) -> None:
    move = _one(with_rows({"T1": PathRow(entity=SpotId("T1"), easing=easing)}))
    assert (move.kind, move.counts, move.direction) == ("straight", COUNTS, UPSTAGE)
    assert approx_equal(move.distance, MOVE_M, abs_tol=TOL)


def test_describe_curve() -> None:
    row = PathRow(entity=SpotId("T1"), style=PathStyle.CURVE, waypoints=(Waypoint(x=1, y=7),))
    show = with_rows({"T1": row})
    move = _one(show)
    plan = PathEngine(show).compile(TID).plans[SpotId("T1")]
    assert plan.curve is not None
    assert (move.kind, move.direction) == ("curve", UPSTAGE)
    assert move.distance == plan.curve.length()


def test_describe_rotate() -> None:
    row = PathRow(entity=SpotId("T1"), style=PathStyle.ROTATE, pivot=(0.0, 7.0), sweep_deg=180.0)
    move = _one(with_rows({"T1": row}))
    assert move.kind == "rotate"
    assert approx_equal(move.distance, 2.0 * math.pi, abs_tol=1e-6)


def test_describe_follow() -> None:
    route = Route(
        id=RouteId("r"),
        curve=Segment(a=(0.0, 5.0), b=(0.0, 9.0)),
        members=(SpotId("T1"),),
        offsets=(0.0,),
        travel=MOVE_M,
    )
    row = PathRow(entity=SpotId("T1"), style=PathStyle.FOLLOW, route=RouteId("r"))
    move = _one(with_rows({"T1": row}, routes={"r": route}))
    assert (move.kind, move.direction, move.distance) == ("follow", UPSTAGE, MOVE_M)


def test_describe_custom() -> None:
    row = PathRow(entity=SpotId("T1"), style=PathStyle.CUSTOM, motion_fn="weave")
    move = _one(with_rows({"T1": row}))
    assert (move.kind, move.direction) == ("custom", UPSTAGE)
    assert move.distance > MOVE_M


def test_describe_pace_shortens_the_stride() -> None:
    plain = _one(two_set_show())
    doubled = _one(with_rows({"T1": PathRow(entity=SpotId("T1"), pace=2.0)}))
    assert plain.stride_m is not None
    assert doubled.stride_m == plain.stride_m / 2.0
    assert doubled.pace == 2.0


def test_regression_curve_style_without_waypoints_is_straight() -> None:
    move = _one(with_rows({"T1": PathRow(entity=SpotId("T1"), style=PathStyle.CURVE)}))
    assert (move.kind, move.direction, move.distance) == ("straight", UPSTAGE, MOVE_M)


def test_regression_still_moments_inside_a_move_are_holds() -> None:
    easing = Easing(kind="hold_then_burst", strength=2.0)
    segments = _segments(with_rows({"T1": PathRow(entity=SpotId("T1"), easing=easing)}))
    assert [(s.kind, s.start, s.counts) for s in segments] == [
        ("hold", 0.0, 4.0),
        ("straight", 4.0, 4.0),
    ]
    assert segments[1].distance == MOVE_M


def test_regression_out_and_back_splits_at_its_turn() -> None:
    row = PathRow(entity=SpotId("T1"), style=PathStyle.POLYLINE, waypoints=(Waypoint(x=0, y=12),))
    segments = _segments(with_rows({"T1": row}))
    assert [(s.kind, s.direction) for s in segments] == [("straight", 180.0), ("straight", 0.0)]
    assert [round(s.distance, 9) for s in segments] == [7.0, 3.0]


def test_describe_groups_alike_spots() -> None:
    show = with_rows({"T3": PathRow(entity=SpotId("T3"), start=2.0)})
    described = describe_transition(show, TID, "T4 + T3 + T2 + T1")
    assert described.groups == (("T4", "T2", "T1"), ("T3",))
    assert list(described.moves) == ["T4", "T3", "T2", "T1"]
    assert described.counts == COUNTS


def test_describe_reuses_a_given_engine() -> None:
    show = two_set_show()
    described = describe_transition(show, TID, engine=PathEngine(show))
    assert described == describe_transition(show, TID)
    assert isinstance(described, TransitionDescription)


@pytest.mark.parametrize("pair", [("1", "3"), ("2", "1")])
def test_regression_describe_rejects_an_unknown_transition(pair: tuple[str, str]) -> None:
    with pytest.raises(UnknownRefError) as caught:
        describe_transition(two_set_show(), pair)
    assert caught.value.hint


def test_regression_describe_rejects_an_unknown_spot() -> None:
    with pytest.raises(UnknownRefError):
        describe_transition(two_set_show(), TID, ["T9"])
