#!/usr/bin/env python3

"""
test_budgets.py --- one benchmark per performance budget row of docs/upgrade-plan.md section 5.3

Sizes come from --tier (default M). Each row records its budget in extra_info; the budgets are
targets, not gates. `nox -s bench` fails only on a >20% regression of the min time against
benchmarks/baseline-2026-11-13.json, and only at tier M. Heavy rows use fixed rounds
(HEAVY_ROUNDS) so the M run stays short.
"""

import subprocess
import sys
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from showpy.choreo.avoid import min_gaps
from showpy.choreo.engine import PathEngine
from showpy.choreo.match import match
from showpy.core.ids import TransitionId
from showpy.core.util import seeded_rng
from showpy.examples import two_set_show
from showpy.export.video import export_video
from showpy.show.collab import events_since
from showpy.show.manage import index
from showpy.show.models import Show
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import ROOT_REVISION, Workspace, load_show, open_workspace, save_show
from showpy.show.validate import validate_show
from showpy.time.clock import count_to_seconds
from tests.conftest import BenchTier
from tests.fixtures.generators import seeded_show

pytestmark = pytest.mark.bench

BENCH_SEED = 11
SHOW_ID = "bench"
AT = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
STEP = timedelta(seconds=1)
HEAVY_ROUNDS = {"S": 3, "M": 3, "L": 1, "XL": 1}
SAVE_ROUNDS = 5
FRAMES = 60
FRAMES_PER_S_BUDGET = 300
HISTORY_REVISIONS = 200
HEAD_REVISIONS = 1000
VIDEO_COUNTS = 8.0
VIDEO_FPS = 30.0
PX_PER_M_1080P = 17.5
VIDEO_BUDGET_PER_S = 60.0 / 120.0
MB = 2**20
IMPORT_SNIPPET = "import showpy; showpy.PathEngine"
BUDGET_S = {
    "min_gaps": 1.0,
    "compile_all": 2.0,
    "match": 0.2,
    "validate_show": 1.0,
    "one_spot_save": 0.05,
    "events_since": 1.0,
    "head": 0.001,
    "import": 0.3,
}
PEAK_BUDGET_MB = 500.0


@pytest.fixture(scope="module")
def show(tier: BenchTier) -> Show:
    """Builds the tier's seeded benchmark show."""
    return seeded_show(BENCH_SEED, tier.spots, tier.sets)


@pytest.fixture(scope="module")
def middle(show: Show) -> TransitionId:
    """Returns the transition in the middle of the show."""
    tids = show.transition_ids()
    return tids[len(tids) // 2]


def _record(benchmark: BenchmarkFixture, tier: BenchTier, **budget: float) -> None:
    benchmark.extra_info.update({"tier": tier.name, "spots": tier.spots, "sets": tier.sets})
    benchmark.extra_info.update(budget)


# Any: pytest-benchmark leaves pedantic() untyped and returns whatever the target returns.
def _pedantic(benchmark: BenchmarkFixture) -> Callable[..., Any]:
    pedantic: Callable[..., Any] = benchmark.pedantic
    return pedantic


# Any: the result is whatever `fn` returns.
def _heavy(benchmark: BenchmarkFixture, tier: BenchTier, fn: Callable[[], Any]) -> Any:
    return _pedantic(benchmark)(fn, rounds=HEAVY_ROUNDS[tier.name], iterations=1)


def _one_spot_edit(show: Show, x: float) -> Show:
    entity = show.cast.ids()[0]
    edited = show.sets[1].with_placements({entity: {"x": x}}, force=True)
    return show.model_copy(update={"sets": (show.sets[0], edited, *show.sets[2:])})


def _history(base: Show, revisions: int) -> Workspace:
    repo = InMemoryRepository()
    save_show(open_workspace(repo, SHOW_ID, "designer"), base, AT)
    for n in range(revisions):
        ws = open_workspace(repo, SHOW_ID, "designer")
        save_show(ws, _one_spot_edit(load_show(ws), float(n % 2)), AT + STEP * (n + 1))
    return open_workspace(repo, SHOW_ID, "designer")


def test_min_gaps_one_transition(
    benchmark: BenchmarkFixture, tier: BenchTier, show: Show, middle: TransitionId
) -> None:
    _record(benchmark, tier, budget_s=BUDGET_S["min_gaps"])
    gaps = _heavy(benchmark, tier, lambda: min_gaps(show, middle))
    assert gaps


def test_sample_frames(benchmark: BenchmarkFixture, tier: BenchTier, show: Show) -> None:
    engine = PathEngine(show)
    engine.compile_all()
    times = np.linspace(0.0, index(show).total_counts, FRAMES).tolist()
    _record(benchmark, tier, budget_s=FRAMES / FRAMES_PER_S_BUDGET, frames=FRAMES)
    frames = benchmark(lambda: [engine.sample(t) for t in times])
    assert len(frames) == FRAMES


def test_compile_all(benchmark: BenchmarkFixture, tier: BenchTier, show: Show) -> None:
    _record(benchmark, tier, budget_s=BUDGET_S["compile_all"])
    compiled = _heavy(benchmark, tier, lambda: PathEngine(show).compile_all())
    assert len(compiled) == len(show.sets) - 1


def test_match_default_objective(benchmark: BenchmarkFixture, tier: BenchTier, show: Show) -> None:
    starts = show.sets[0].positions()
    slots = seeded_rng(BENCH_SEED).permutation(show.sets[1].positions())
    _record(benchmark, tier, budget_s=BUDGET_S["match"])
    result = benchmark(match, starts, slots)
    assert len(result.assignment) == tier.spots


def test_validate_show(benchmark: BenchmarkFixture, tier: BenchTier, show: Show) -> None:
    _record(benchmark, tier, budget_s=BUDGET_S["validate_show"])
    report = _heavy(benchmark, tier, lambda: validate_show(show))
    assert report.ok


def test_one_spot_edit_save(benchmark: BenchmarkFixture, tier: BenchTier, show: Show) -> None:
    def fresh() -> tuple[tuple[Workspace, Show, datetime], dict[str, Any]]:
        ws = open_workspace(InMemoryRepository(), SHOW_ID, "designer")
        save_show(ws, show, AT)
        loaded = load_show(open_workspace(ws.repo, SHOW_ID, "designer"))
        return (ws, _one_spot_edit(loaded, -1.0), AT + STEP), {}

    _record(benchmark, tier, budget_s=BUDGET_S["one_spot_save"])
    revision = _pedantic(benchmark)(save_show, setup=fresh, rounds=SAVE_ROUNDS, iterations=1)
    assert revision.id


def test_events_since(benchmark: BenchmarkFixture, tier: BenchTier, show: Show) -> None:
    ws = _history(show, HISTORY_REVISIONS)
    _record(benchmark, tier, budget_s=BUDGET_S["events_since"], revisions=HISTORY_REVISIONS)
    events = _heavy(benchmark, tier, lambda: events_since(ws, ROOT_REVISION))
    assert len(events) >= HISTORY_REVISIONS


def test_head(benchmark: BenchmarkFixture, tier: BenchTier) -> None:
    ws = _history(two_set_show(), HEAD_REVISIONS)
    _record(benchmark, tier, budget_s=BUDGET_S["head"], revisions=HEAD_REVISIONS)
    head = benchmark(ws.repo.head, SHOW_ID)
    assert head == ws.revision


def test_video(benchmark: BenchmarkFixture, tier: BenchTier, show: Show) -> None:
    seconds = count_to_seconds(show.timeline, VIDEO_COUNTS)
    _record(benchmark, tier, budget_s=seconds * VIDEO_BUDGET_PER_S, video_s=seconds)
    video = _heavy(
        benchmark,
        tier,
        lambda: export_video(show, (0.0, VIDEO_COUNTS), VIDEO_FPS, px_per_m=PX_PER_M_1080P),
    )
    assert video[4:8] == b"ftyp"


def test_import_and_first_name(benchmark: BenchmarkFixture, tier: BenchTier) -> None:
    command = [sys.executable, "-c", IMPORT_SNIPPET]
    _record(benchmark, tier, budget_s=BUDGET_S["import"])
    done = _heavy(benchmark, tier, lambda: subprocess.run(command, check=True))
    assert done.returncode == 0


def test_peak_memory(benchmark: BenchmarkFixture, tier: BenchTier, show: Show) -> None:
    def workload() -> float:
        tracemalloc.start()
        try:
            engine = PathEngine(show)
            engine.compile_all()
            for t in np.linspace(0.0, index(show).total_counts, FRAMES).tolist():
                engine.sample(t)
            return tracemalloc.get_traced_memory()[1] / MB
        finally:
            tracemalloc.stop()

    peak_mb = _heavy(benchmark, tier, workload)
    _record(benchmark, tier, budget_mb=PEAK_BUDGET_MB, peak_mb=peak_mb)
    assert peak_mb > 0
