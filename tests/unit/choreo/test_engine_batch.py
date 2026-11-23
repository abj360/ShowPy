#!/usr/bin/env python3

"""
test_engine_batch.py --- tests that batched engine reads equal single-count engine reads

Contains:
    test_group_counts_splits_like_locate: puts each count in the transition locate names
    test_track_positions_equal_position_on_matrix_curves: matches position bit for bit on
        routes along a Bézier curve and a joined curve holding one, evaluated one count at a time
    test_track_facings_equal_facing_at_with_overrides: matches facing_at when facing overrides
        apply
    test_regression_track_positions_names_an_unknown_spot: raises UnknownRefError with a hint
"""

import numpy as np
import pytest

from e8s.choreo.engine import PathEngine
from e8s.choreo.engine_batch import group_counts, track_facings, track_positions
from e8s.choreo.path_models import (
    FacingMode,
    FacingOverride,
    FixedFacing,
    PathRow,
    PathStyle,
    Route,
)
from e8s.core.errors import UnknownRefError
from e8s.core.ids import RouteId, SpotId
from e8s.geo.curves import AnyCurve, BezierCurve, CompositeCurve, Segment
from e8s.show.models import Show
from tests.fixtures.motion_shows import with_rows
from tests.fixtures.shows import two_set_show

COUNTS = np.linspace(-1.0, 9.0, 41)
OVERRIDE_START = 2.0
OVERRIDE_END = 5.0
OVERRIDE_FACING = 270.0


BEZIER = BezierCurve(control=((0.0, 5.0), (3.0, 6.0), (-3.0, 8.0), (0.0, 9.0)))
JOINED = CompositeCurve(
    parts=(
        Segment(a=(0.0, 5.0), b=(0.0, 7.0)),
        BezierCurve(control=((0.0, 7.0), (2.0, 8.0), (-2.0, 8.5), (0.0, 9.0))),
    )
)


def _route_show(curve: AnyCurve) -> Show:
    route = Route(
        id=RouteId("r"), curve=curve, members=(SpotId("T1"),), offsets=(0.0,), travel=curve.length()
    )
    row = PathRow(entity=SpotId("T1"), style=PathStyle.FOLLOW, route=RouteId("r"))
    return with_rows({"T1": row}, routes={"r": route})


def test_group_counts_splits_like_locate() -> None:
    engine = PathEngine(two_set_show())
    counts = COUNTS.tolist()
    groups = group_counts(engine, counts)
    assert groups.static.tolist() == [
        i for i, t in enumerate(counts) if engine.locate(t)[0] is None
    ]
    for tid, indices, taus in groups.moving:
        assert [engine.locate(counts[i]) for i in indices.tolist()] == [
            (tid, tau) for tau in taus.tolist()
        ]


@pytest.mark.parametrize("curve", [BEZIER, JOINED])
def test_track_positions_equal_position_on_matrix_curves(curve: AnyCurve) -> None:
    engine = PathEngine(_route_show(curve))
    tracked = track_positions(engine, "T1", group_counts(engine, COUNTS))
    single = np.array([engine.position("T1", t) for t in COUNTS.tolist()])
    assert tracked.tolist() == single.tolist()


def test_track_facings_equal_facing_at_with_overrides() -> None:
    override = FacingOverride(
        entities="T1 + T2",
        mode=FacingMode.FIXED,
        value=FixedFacing(degrees=OVERRIDE_FACING),
        start=OVERRIDE_START,
        end=OVERRIDE_END,
    )
    engine = PathEngine(two_set_show().replace(facing_overrides=(override,)))
    groups = group_counts(engine, COUNTS)
    for spot in ("T1", "T3"):
        single = [engine.facing_at(spot, t) for t in COUNTS.tolist()]
        assert track_facings(engine, spot, groups).tolist() == single


def test_regression_track_positions_names_an_unknown_spot() -> None:
    engine = PathEngine(two_set_show())
    with pytest.raises(UnknownRefError, match="unknown spot") as caught:
        track_positions(engine, "T9", group_counts(engine, [4.0]))
    assert caught.value.hint
