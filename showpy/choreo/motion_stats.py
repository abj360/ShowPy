#!/usr/bin/env python3

"""
motion_stats.py --- measurements over time, all computed by the PathEngine

These live in showpy.choreo, not showpy.geo, because they need motion; showpy.geo stays static
geometry (ADR 0002 addendum).

Contains:
    position_at(): returns a spot's position at a global count
    speed(): returns a spot's movement demand in a transition
    speeds(): ranks every spot of a transition by demand, hardest first
    direction_of_travel(): returns the facing of a spot's travel, or None when still
    motion_contrast(): summarizes how varied a transition's movement is
    energy_curve(): returns mean on-field speed through the show
    phrase_profile(): returns change, energy and density per musical phrase
"""

import itertools
import math

import numpy as np
import pandas as pd

from showpy.choreo.engine import PathEngine
from showpy.choreo.engine_models import MotionStats
from showpy.core.errors import TimeRangeError
from showpy.core.ids import SpotId, TransitionId
from showpy.core.types import Count, Degrees, Points
from showpy.core.units import vector_facing
from showpy.geo.models import SpeedStats
from showpy.show.models import Show

STILL_TOL = 1e-9
SPEED_COLUMNS = ["entity", "distance", "counts", "m_per_count", "m_per_s", "max_instant"]


def position_at(show: Show, entity: SpotId, t: Count) -> Points:
    """Returns a spot's position at a global count.

    >>> from showpy.examples import two_set_show
    >>> position_at(two_set_show(), "T1", 4.0).tolist()
    [0.0, 7.0]
    """
    return PathEngine(show).position(entity, t)


def speed(
    show: Show, entity: SpotId, tid: TransitionId, window: tuple[float, float] | None = None
) -> SpeedStats:
    """Returns a spot's movement demand over a local window of a transition.

    >>> from showpy.examples import two_set_show
    >>> speed(two_set_show(), "T1", ("1", "2")).m_per_count
    0.5
    """
    return PathEngine(show).speed(entity, tid, window)


def speeds(show: Show, tid: TransitionId) -> pd.DataFrame:
    """Ranks every spot of a transition by peak then mean speed, hardest first.

    >>> from showpy.examples import two_set_show
    >>> len(speeds(two_set_show(), ("1", "2")))
    4
    """
    engine = PathEngine(show)
    rows = [{"entity": e, **engine.speed(e, tid).model_dump()} for e in engine.compile(tid).plans]
    table = pd.DataFrame(rows, columns=SPEED_COLUMNS)
    ranked = table.sort_values(
        ["max_instant", "m_per_count", "entity"], ascending=[False, False, True]
    )
    return ranked.reset_index(drop=True)


def direction_of_travel(
    show: Show, entity: SpotId, tid: TransitionId, tau: float
) -> Degrees | None:
    """Returns the facing a spot's travel points at local count tau, or None when still.

    >>> from showpy.examples import two_set_show
    >>> direction_of_travel(two_set_show(), "T1", ("1", "2"), 4.0)
    180.0
    """
    v = PathEngine(show).velocity_at(entity, tau, local=tid)
    if float(np.linalg.norm(v)) <= STILL_TOL:
        return None
    return vector_facing((float(v[0]), float(v[1])))


def motion_contrast(show: Show, tid: TransitionId) -> MotionStats:
    """Summarizes moving share, speed spread and direction variety of a transition.

    >>> from showpy.examples import two_set_show
    >>> motion_contrast(two_set_show(), ("1", "2")).moving_share
    1.0
    """
    engine = PathEngine(show)
    plans = engine.compile(tid).plans
    on_field = [p for p in plans.values() if p.start.on_field or p.end.on_field]
    moving = [p for p in on_field if p.moves]
    if not moving:
        return MotionStats(moving_share=0.0, speed_spread=0.0, direction_variety=0.0)
    rates = np.array([engine.speed(p.entity, tid).m_per_count for p in moving])
    net = np.array([np.subtract(p.end.point, p.start.point) for p in moving], dtype=float)
    norms = np.linalg.norm(net, axis=1)
    units = net[norms > STILL_TOL] / norms[norms > STILL_TOL, None]
    variety = 1.0 - float(np.linalg.norm(units.mean(axis=0))) if len(units) else 0.0
    return MotionStats(
        moving_share=len(moving) / len(on_field),
        speed_spread=float(rates.std() / rates.mean()) if rates.mean() > 0 else 0.0,
        direction_variety=max(variety, 0.0),
    )


def _mean_speed(engine: PathEngine, t: Count) -> float:
    states = [s for s in engine.sample(t).states if s.on_field]
    return float(np.mean([math.hypot(*s.velocity) for s in states])) if states else 0.0


def energy_curve(show: Show, per_count: int = 1) -> pd.Series:
    """Returns mean on-field speed (m/count) through the show, indexed by global count.

    >>> from showpy.examples import two_set_show
    >>> round(float(energy_curve(two_set_show()).loc[4.0]), 6)
    0.5
    """
    engine = PathEngine(show)
    total = sum(s.counts for s in show.sets[1:])
    times = np.linspace(0.0, total, round(total * per_count) + 1)
    return pd.Series([_mean_speed(engine, float(t)) for t in times], index=times, name="energy")


def _density(engine: PathEngine, t: Count) -> float:
    pts = np.array([s.position for s in engine.sample(t).states if s.on_field])
    if len(pts) < 2:
        return float("inf")
    d = np.linalg.norm(pts[:, None] - pts[None, :], axis=2)
    np.fill_diagonal(d, np.inf)
    return float(d.min(axis=1).mean())


def phrase_profile(show: Show, t0: Count, t1: Count) -> pd.DataFrame:
    """Returns change, energy and density for each phrase between t0 and t1.

    Phrases split at timeline markers inside the range. Change is mean displacement over the
    phrase, energy is mean speed, density is mean nearest-neighbour distance at its middle.

    >>> from showpy.examples import two_set_show
    >>> phrase_profile(two_set_show(), 0.0, 8.0)["change"].tolist()
    [4.0]
    """
    if t1 <= t0:
        raise TimeRangeError(f"phrase range {t0}–{t1} is empty", hint="use t0 < t1")
    engine = PathEngine(show)
    cuts = sorted({t0, t1, *(m.at_count for m in show.timeline.markers if t0 < m.at_count < t1)})
    rows = []
    for a, b in itertools.pairwise(cuts):
        moved = engine.positions(b) - engine.positions(a)
        rows.append(
            {
                "start": a,
                "end": b,
                "change": float(np.linalg.norm(moved, axis=1).mean()),
                "energy": _mean_speed(engine, (a + b) / 2),
                "density": _density(engine, (a + b) / 2),
            }
        )
    return pd.DataFrame(rows, columns=["start", "end", "change", "energy", "density"])
