"""
engine_batch.py --- asks PathEngine about one spot at many counts in one pass

Every answer equals the engine's own single-count answer (`position`, `facing_at`) at that
count, bit for bit: counts are split by the transition that holds them exactly as
`PathEngine.locate` splits them, and each plan is evaluated once for all of its counts
unless its curve is not batch exact, which is evaluated one count at a time.

Contains:
    CountGroups: global counts split by the transition that holds them
    group_counts(): splits global counts by transition, as PathEngine.locate does
    track_positions(): returns one spot's positions at grouped counts
    track_facings(): returns one spot's facings at grouped counts
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from e8s.choreo.engine import PathEngine
from e8s.choreo.engine_compile import plan_positions
from e8s.choreo.engine_facing import plan_facings
from e8s.choreo.engine_models import MotionPlan
from e8s.core.arrays import FloatArray
from e8s.core.ids import TransitionId
from e8s.core.types import Points

type IndexArray = npt.NDArray[np.intp]


@dataclass(frozen=True)
class CountGroups:
    """Holds global counts split by the transition that holds them.

    Attributes:
        counts: The global counts, in the caller's order.
        static: Indices of counts outside every transition, where spots stand at a set.
        moving: Per transition, the indices of its counts and their local counts.
    """

    counts: FloatArray
    static: IndexArray
    moving: tuple[tuple[TransitionId, IndexArray, FloatArray], ...]


def group_counts(engine: PathEngine, counts: Sequence[float] | FloatArray) -> CountGroups:
    """Splits global counts by the transition that holds them, as PathEngine.locate does.

    Args:
        engine: Engine of the show the counts belong to.
        counts: Global counts.

    Returns:
        groups: The counts with their indices per transition and their local counts.

    >>> from e8s.examples import two_set_show
    >>> groups = group_counts(PathEngine(two_set_show()), [0.0, 4.0, 9.0])
    >>> groups.static.tolist(), groups.moving[0][2].tolist()
    ([2], [0.0, 4.0])
    """
    values = np.asarray(counts, dtype=float)
    static: list[int] = []
    moving: dict[TransitionId, tuple[list[int], list[float]]] = {}
    for index, t in enumerate(values.tolist()):
        tid, tau = engine.locate(t)
        if tid is None:
            static.append(index)
            continue
        indices, taus = moving.setdefault(tid, ([], []))
        indices.append(index)
        taus.append(tau)
    return CountGroups(
        counts=values,
        static=np.array(static, dtype=np.intp),
        moving=tuple(
            (tid, np.array(indices, dtype=np.intp), np.array(taus, dtype=float))
            for tid, (indices, taus) in moving.items()
        ),
    )


def _plan_points(plan: MotionPlan, taus: FloatArray) -> Points:
    if plan.curve is None or plan.curve.batch_exact:
        return plan_positions(plan, taus)
    return np.array([plan_positions(plan, np.array([tau]))[0] for tau in taus.tolist()])


def track_positions(engine: PathEngine, entity: str, groups: CountGroups) -> Points:
    """Returns one spot's positions at grouped counts, equal to `PathEngine.position` at each.

    Args:
        engine: Engine the counts were grouped with.
        entity: Spot ID.
        groups: Counts from `group_counts`.

    Returns:
        positions: Array of shape (counts, 2), in the order of `groups.counts`.

    >>> from e8s.examples import two_set_show
    >>> engine = PathEngine(two_set_show())
    >>> track_positions(engine, "T1", group_counts(engine, [0.0, 4.0])).tolist()
    [[0.0, 5.0], [0.0, 7.0]]
    """
    out = np.empty((len(groups.counts), 2))
    for index in groups.static.tolist():
        out[index] = engine.position(entity, float(groups.counts[index]))
    for tid, indices, taus in groups.moving:
        out[indices] = _plan_points(engine._plan(entity, tid), taus)
    return out


def track_facings(engine: PathEngine, entity: str, groups: CountGroups) -> FloatArray:
    """Returns one spot's facings at grouped counts, equal to `PathEngine.facing_at` at each.

    Facings that only step between steady values are read for all counts of a transition at
    once; gradual turns, and every count of a show with facing overrides, are asked one count
    at a time.

    Args:
        engine: Engine the counts were grouped with.
        entity: Spot ID.
        groups: Counts from `group_counts`.

    Returns:
        facings: Facings in degrees, in the order of `groups.counts`.

    >>> from e8s.examples import two_set_show
    >>> engine = PathEngine(two_set_show())
    >>> track_facings(engine, "T1", group_counts(engine, [0.0, 4.0])).tolist()
    [0.0, 0.0]
    """
    counts = groups.counts.tolist()
    if engine.show.facing_overrides:
        return np.array([engine.facing_at(entity, t) for t in counts], dtype=float)
    out = np.empty(len(counts))
    for index in groups.static.tolist():
        out[index] = engine.facing_at(entity, counts[index])
    for tid, indices, taus in groups.moving:
        stepped = plan_facings(engine._plan(entity, tid), taus)
        if stepped is None:
            stepped = np.array([engine.facing_at(entity, counts[i]) for i in indices.tolist()])
        out[indices] = stepped
    return out
