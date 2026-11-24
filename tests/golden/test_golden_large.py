#!/usr/bin/env python3

"""
test_golden_large.py --- replays a 240-spot seeded show and compares outputs to reviewed fixtures

The show is seeded_show(7, 240, 24): every path style, plus spots exiting and re-entering.
Large outputs are stored as exact counts, tolerance-compared sums and evenly spaced samples,
so the fixtures stay small and survive last-digit float noise across platforms. Set
E8S_UPDATE_GOLDEN=1 to rewrite tests/golden/fixtures/large/; the diff needs human review.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from e8s.choreo.avoid import min_gaps
from e8s.choreo.engine import PathEngine
from e8s.choreo.engine_models import MotionSegment
from e8s.choreo.path_models import PathStyle
from e8s.show.models import Show
from tests.fixtures.generators import GROUPS, seeded_show

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parent / "fixtures" / "large"
UPDATE_FLAG = "E8S_UPDATE_GOLDEN"
SEED, SPOTS, SETS = 7, 240, 24
SEGMENT_SAMPLE_EVERY = 997
GAP_SPOTS = 16
GAP_TRANSITIONS = (0, 11, 22)
DIGITS = 9
ABS_TOL = 1e-6
REL_TOL = 1e-9
# Any: snapshots are JSON documents of mixed numbers, strings and lists.
type Snapshot = Any


@pytest.fixture(scope="module")
def show() -> Show:
    """Builds the large golden show."""
    return seeded_show(SEED, SPOTS, SETS)


def _snapshot(name: str, current: Snapshot) -> Snapshot:
    path = FIXTURES / f"{name}.json"
    if os.environ.get(UPDATE_FLAG) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    return json.loads(path.read_text())


def _segment_row(s: MotionSegment) -> list[float]:
    return [round(v, DIGITS) for v in (s.t0, s.t1, *s.p0, *s.p1)]


def _segments_summary(show: Show) -> Snapshot:
    engine = PathEngine(show)
    summary = {}
    for tid in show.transition_ids():
        segments = engine.segments(tid)
        rows = [_segment_row(s) for s in segments]
        summary["→".join(tid)] = {
            "count": len(rows),
            "sums": [round(sum(column), DIGITS) for column in zip(*rows, strict=True)],
            "sample": [[s.entity, *_segment_row(s)] for s in segments[::SEGMENT_SAMPLE_EVERY]],
        }
    return summary


def _assert_close(got: Snapshot, want: Snapshot, where: str) -> None:
    if isinstance(want, list):
        assert len(got) == len(want), where
        for index, (g, w) in enumerate(zip(got, want, strict=True)):
            _assert_close(g, w, f"{where}[{index}]")
    elif isinstance(want, dict):
        assert sorted(got) == sorted(want), where
        for key in want:
            _assert_close(got[key], want[key], f"{where}.{key}")
    elif isinstance(want, float):
        assert got == pytest.approx(want, rel=REL_TOL, abs=ABS_TOL), where
    else:
        assert got == want, where


@pytest.mark.golden
def test_large_show_uses_every_path_style_entrances_and_exits(show: Show) -> None:
    styles = {row.style for spec in show.transitions for row in spec.rows.values()}
    assert styles == set(PathStyle)
    assert len(show.cast.spots) == SPOTS
    changes = {
        (before.get(p.entity).on_field, p.on_field)
        for before, after in zip(show.sets, show.sets[1:], strict=False)
        for p in after.placements
    }
    assert {(True, False), (False, True)} <= changes


@pytest.mark.golden
def test_large_segments_match_fixture(show: Show) -> None:
    current = _segments_summary(show)
    _assert_close(current, _snapshot("segments", current), "segments")


@pytest.mark.golden
def test_large_min_gaps_match_fixture(show: Show) -> None:
    tids = show.transition_ids()
    first = {key: [s.id for s in show.cast.spots if s.group == key][:GAP_SPOTS] for key in GROUPS}
    current = {
        "→".join(tids[i]): [
            [g.a, g.b, round(g.tau, DIGITS), round(g.gap, DIGITS)]
            for g in min_gaps(show, tids[i], sel=first[list(GROUPS)[i % len(GROUPS)]])
        ]
        for i in GAP_TRANSITIONS
    }
    _assert_close(current, _snapshot("min_gaps", current), "min_gaps")
