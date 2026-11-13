#!/usr/bin/env python3

"""
test_golden_large.py --- replays a 240-spot seeded show and compares outputs to reviewed fixtures

The show is seeded_show(7, 240, 24) and uses every path style. Large outputs are stored as
exact counts, tolerance-compared sums and evenly spaced samples, so the fixtures stay small
and survive last-digit float noise across platforms. Set SHOWPY_UPDATE_GOLDEN=1 to rewrite
tests/golden/fixtures/large/; the diff then needs human review.
"""

import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from showpy.choreo.avoid import min_gaps
from showpy.choreo.engine import PathEngine
from showpy.choreo.engine_models import MotionSegment
from showpy.choreo.models import PathStyle
from showpy.export.data import export_frames_csv
from showpy.export.pdf import to_pdf
from showpy.export.print_sheets import coordinate_sheet
from showpy.export.video import render_frame, video_canvas
from showpy.show.models import Show
from tests.fixtures.generators import GROUPS, seeded_show

FIXTURES = Path(__file__).parent / "fixtures" / "large"
UPDATE_FLAG = "SHOWPY_UPDATE_GOLDEN"
SEED, SPOTS, SETS = 7, 240, 24
SEGMENT_SAMPLE_EVERY = 997
CSV_SAMPLE_EVERY = 499
GAP_SPOTS = 16
GAP_TRANSITIONS = (0, 11, 22)
RENDER_COUNTS = (0.0, 50.0, 150.0)
DIGITS = 9
ABS_TOL = 1e-6
REL_TOL = 1e-9
PDF_TEXT = re.compile(rb"\(((?:[^()\\]|\\.)*)\) Tj")

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
def test_large_show_uses_every_path_style(show: Show) -> None:
    styles = {row.style for spec in show.transitions for row in spec.rows.values()}
    assert styles == set(PathStyle)
    assert len(show.cast.spots) == SPOTS


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


def _frames_summary(text: str) -> Snapshot:
    rows = list(csv.reader(io.StringIO(text)))
    header, body = rows[0], rows[1:]
    numeric = [[float(v) for v in (r[0], r[1], r[3], r[4], r[5])] for r in body]
    return {
        "header": header,
        "rows": len(body),
        "on_field": sum(r[6] == "true" for r in body),
        "sums": [round(sum(column), DIGITS) for column in zip(*numeric, strict=True)],
        "sample": [[r[2], *n] for r, n in zip(body, numeric, strict=True)][::CSV_SAMPLE_EVERY],
    }


@pytest.mark.golden
def test_large_frames_csv_matches_fixture(show: Show) -> None:
    current = _frames_summary(export_frames_csv(show))
    _assert_close(current, _snapshot("frames", current), "frames")


def _pdf_text(pdf: bytes) -> list[str]:
    return [match.decode("latin-1") for match in PDF_TEXT.findall(pdf)]


@pytest.mark.golden
def test_large_pdf_text_matches_fixture(show: Show) -> None:
    firsts = [next(s.id for s in show.cast.spots if s.group == key) for key in GROUPS]
    current = {
        entity: [
            m.decode("latin-1") for m in PDF_TEXT.findall(to_pdf(coordinate_sheet(show, entity)))
        ]
        for entity in firsts
    }
    assert current == _snapshot("pdf_text", current)


@pytest.mark.golden
def test_large_render_hashes_match_fixture(show: Show) -> None:
    engine, canvas = PathEngine(show), video_canvas(show)
    current = {}
    for t in RENDER_COUNTS:
        image = render_frame(show, engine.sample(t), canvas)
        current[f"{t:g}"] = [*image.size, hashlib.sha256(image.tobytes()).hexdigest()]
    assert current == _snapshot("render", current)
