#!/usr/bin/env python3

"""
cases_time.py --- parity cases for the time area: the clock on seeded random timelines

Inputs come from `e8s.seeded_rng(SEED)` on the side under test, so the oracle and the working
tree build the same timelines and query counts.

Contains:
    random_timeline(): builds one valid timeline with tempo changes, ramps and holds
    query_counts(): picks the counts to convert: boundaries, nudges past them and random counts
    clock_on_200_seeded_timelines(): converts counts to seconds and back, and reads the tempo
    CASES: the time area's cases
"""

from collections.abc import Mapping
from types import ModuleType
from typing import Any

import numpy as np

from tests.parity.cases import Case, Output

SEED = 20261123
TIMELINES = 200
QUERIES = 24
EXTREME_SHARE = 0.1
SECONDS_MODE_SHARE = 0.02
RAMP_SHARE = 0.5
MAX_SECTIONS = 9
TEMPO_RANGE = (40.0, 240.0)
EXTREME_TEMPO_EXPONENTS = (-3.0, 6.0)
EXTREME_COUNT_EXPONENTS = (-6.0, 5.0)
COUNT_LENGTHS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
COUNT_SPLITS = (1.0, 0.5, 0.25, 1.0 / 3.0)
RAMP_SCALES = (0.5, 1.0, 3.0)
HOLD_SECONDS = (0.0, 0.5, 1.0, 2.0)
MAX_RANDOM_HOLD_S = 30.0
END_MARGIN = 1.3
END_PAD_COUNTS = 8.0
NUDGED_MARKS = 4


def _tempo(rng: np.random.Generator, *, extreme: bool) -> float:
    if extreme:
        return float(10 ** rng.uniform(*EXTREME_TEMPO_EXPONENTS))
    return float(np.round(rng.uniform(*TEMPO_RANGE), int(rng.integers(0, 4))))


def _count(rng: np.random.Generator, *, extreme: bool) -> float:
    if extreme:
        return float(10 ** rng.uniform(*EXTREME_COUNT_EXPONENTS))
    return float(rng.choice(COUNT_LENGTHS)) * float(rng.choice(COUNT_SPLITS))


# Any: the timeline class comes from the e8s module under test, which has no static type here.
def random_timeline(e8s: ModuleType, rng: np.random.Generator) -> Any:
    """Builds one valid timeline; a tenth use extreme tempos and lengths.

    Args:
        e8s: The e8s module under test.
        rng: The seeded generator both sides draw from in the same order.

    Returns:
        timeline: An e8s Timeline in counts mode, or rarely an empty seconds-mode one.
    """
    extreme = rng.random() < EXTREME_SHARE
    if rng.random() < SECONDS_MODE_SHARE:
        return e8s.Timeline(mode=e8s.TimelineMode.SECONDS)
    at, tempos = 0.0, []
    for _ in range(int(rng.integers(1, MAX_SECTIONS))):
        cpm = _tempo(rng, extreme=extreme)
        ramp_to = ramp_counts = None
        if rng.random() < RAMP_SHARE:
            ramp_to = _tempo(rng, extreme=extreme)
            ramp_counts = _count(rng, extreme=extreme) * float(rng.choice(RAMP_SCALES))
        tempos.append(
            e8s.TempoSection(
                at_count=at, counts_per_minute=cpm, ramp_to=ramp_to, ramp_counts=ramp_counts
            )
        )
        at += _count(rng, extreme=extreme) * float(rng.integers(1, 6))
    hold_at = sorted(
        {
            float(rng.choice([rng.uniform(0.0, at * 1.2), section.at_count]))
            for section in tempos
            for _ in range(int(rng.integers(0, 2)))
        }
    )
    holds = tuple(
        e8s.Hold(
            at_count=count,
            seconds=float(rng.choice([*HOLD_SECONDS, rng.uniform(0.0, MAX_RANDOM_HOLD_S)])),
        )
        for count in hold_at
    )
    return e8s.Timeline(mode=e8s.TimelineMode.COUNTS, tempos=tuple(tempos), holds=holds)


# Any: as above, the timeline is the e8s module's own model.
def query_counts(rng: np.random.Generator, timeline: Any) -> list[float]:
    """Returns 0, every boundary, nudges just past the first marks and random counts.

    Args:
        rng: The seeded generator both sides draw from in the same order.
        timeline: The timeline the counts are for.

    Returns:
        counts: At least QUERIES counts, all non-negative and finite.
    """
    marks = [0.0, *(section.at_count for section in timeline.tempos)]
    marks += [s.at_count + s.ramp_counts for s in timeline.tempos if s.ramp_counts]
    marks += [hold.at_count for hold in timeline.holds]
    end = max(marks) * END_MARGIN + END_PAD_COUNTS
    picks = [*marks, *(float(np.nextafter(mark, np.inf)) for mark in marks[:NUDGED_MARKS])]
    picks += [float(rng.uniform(0.0, end)) for _ in range(QUERIES - len(picks) % QUERIES)]
    return picks[: max(QUERIES, len(marks) + NUDGED_MARKS)]


def clock_on_200_seeded_timelines(e8s: ModuleType) -> Mapping[str, Output]:
    """Converts counts to seconds and back, and reads the tempo, on 200 seeded timelines.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: `val:` seconds, round-tripped counts and tempos, and the query count of each
            timeline.
    """
    rng = e8s.seeded_rng(SEED)
    seconds: list[float] = []
    counts_back: list[float] = []
    tempos: list[float] = []
    queries: list[int] = []
    for _ in range(TIMELINES):
        timeline = random_timeline(e8s, rng)
        counts = query_counts(rng, timeline)
        queries.append(len(counts))
        for count in counts:
            at = float(e8s.count_to_seconds(timeline, count))
            seconds.append(at)
            counts_back.append(float(e8s.seconds_to_count(timeline, at)))
            tempos.append(float(e8s.tempo_at(timeline, count)))
    return {
        "val:seconds": np.array(seconds, dtype=np.float64),
        "val:counts_back": np.array(counts_back, dtype=np.float64),
        "val:tempo": np.array(tempos, dtype=np.float64),
        "queries": np.array(queries, dtype=np.int64),
    }


CASES = (
    Case(
        area="time",
        name="clock_on_200_seeded_timelines",
        run=clock_on_200_seeded_timelines,
        timed=True,
    ),
)
