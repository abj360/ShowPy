"""
avoid.py --- advisory collision help: tightest gaps and fixes that open them

Everything here is a suggestion for scripts and the designer; the locked checker decides.
Gaps use exact closest approach on the engine's time-linear segments, minus each spot's
footprint extent (the radius of the smallest circle holding its shape).

Contains:
    closest_approach(): returns when and how close two piecewise-linear tracks come
    min_gaps(): lists the tightest pairs of a transition
    suggest_delays(): suggests delays that open a pair's gap
    suggest_waypoint(): suggests a bend that routes one spot around another
    suggest_reassignment(): re-matches spots to their own targets with a crossing penalty
    separate_in_time(): staggers starts along an order
    apply_fix(): applies a suggested fix to a transition's paths
"""

import itertools
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from showpy.cast.select import select
from showpy.choreo.engine import PathEngine
from showpy.choreo.engine_models import BendFix, DelayFix, Fix, GapInfo, StaggerFix
from showpy.choreo.match import match
from showpy.choreo.match_models import MatchOptions, MatchResult, MatchWeights
from showpy.choreo.path_models import TransitionSpec
from showpy.choreo.paths import add_waypoint, delay, stagger
from showpy.core.errors import PathError
from showpy.core.ids import SpotId
from showpy.core.types import Point, Points
from showpy.core.util import natural_key, noise_rank
from showpy.show.models import Show

type FloatArray = npt.NDArray[np.float64]

DELAY_STEP = 0.5
CROSSING_WEIGHT = 10.0
ZERO_TOL = 1e-12
ALL = "all"


def closest_approach(
    times_a: Sequence[float],
    points_a: Sequence[Point] | Points,
    times_b: Sequence[float],
    points_b: Sequence[Point] | Points,
) -> tuple[float, float]:
    """Returns (time, distance) of closest approach of two time-linear tracks.

    Between merged station times both tracks are linear, so the relative position is
    d(t) = d0 + w·s and the minimum sits at s* = clamp(-(d0·w)/(w·w), 0, 1).

    >>> closest_approach([0, 2], [(0, 0), (2, 0)], [0, 2], [(2, 0), (0, 0)])
    (1.0, 0.0)
    """
    if len(times_a) == 0 or len(times_b) == 0:
        raise PathError(
            "closest approach needs at least one station on each track",
            hint="pass the times and points of both tracks",
            context={"stations_a": len(times_a), "stations_b": len(times_b)},
        )
    t = np.unique(np.concatenate([times_a, times_b]))
    pa, pb = _track(times_a, points_a, t), _track(times_b, points_b, t)
    d0, w = (pa - pb)[:-1], np.diff(pa - pb, axis=0)
    ww = np.einsum("ij,ij->i", w, w)
    s = np.where(
        ww > ZERO_TOL, np.clip(-np.einsum("ij,ij->i", d0, w) / np.maximum(ww, ZERO_TOL), 0, 1), 0
    )
    dist = np.linalg.norm(d0 + w * s[:, None], axis=1)
    if len(dist) == 0:
        return float(t[0]), float(np.linalg.norm(pa[0] - pb[0]))
    best = int(np.argmin(dist))
    return float(t[best] + s[best] * (t[best + 1] - t[best])), float(dist[best])


def _track(times: Sequence[float], points: Sequence[Point] | Points, at: FloatArray) -> Points:
    pts = np.asarray(points, dtype=float)
    return np.stack([np.interp(at, times, pts[:, 0]), np.interp(at, times, pts[:, 1])], axis=1)


def _stations(
    engine: PathEngine, entity: str, tid: tuple[str, str]
) -> tuple[SpotId, list[float], Points]:
    segments = engine.entity_segments(entity, tid)
    times = [segments[0].t0, *(s.t1 for s in segments)]
    points = np.array([segments[0].p0, *(s.p1 for s in segments)], dtype=float)
    return segments[0].entity, times, points


def _extent(show: Show, entity: str) -> float:
    return show.cast.spot(entity).shape.extent_m if entity in show.cast.ids() else 0.0


def _pair_gap(engine: PathEngine, tid: tuple[str, str], a: str, b: str) -> GapInfo:
    id_a, ta, pa = _stations(engine, a, tid)
    id_b, tb, pb = _stations(engine, b, tid)
    tau, dist = closest_approach(ta, pa, tb, pb)
    first, second = sorted((id_a, id_b))
    gap = dist - _extent(engine.show, a) - _extent(engine.show, b)
    return GapInfo(a=first, b=second, tau=tau, gap=gap)


def _on_field_ids(
    engine: PathEngine, tid: tuple[str, str], sel: str | Sequence[str]
) -> list[SpotId]:
    plans = engine.compile(tid).plans
    ids = list(plans) if sel == ALL else list(select(engine.show, sel).ids)
    return [e for e in ids if plans[e].start.on_field or plans[e].end.on_field]


def min_gaps(
    show: Show, tid: tuple[str, str], sel: str | Sequence[str] = ALL, top: int = 10
) -> list[GapInfo]:
    """Lists the `top` tightest pairs of on-field spots in a transition, tightest first.

    Gaps are ranked rounded to 9 decimals (`noise_rank`), so pairs that tie to within float noise
    are listed in natural spot ID order (T2 before T10). A bounding-box broadphase skips pairs
    that cannot beat the current top list.

    >>> from showpy.examples import two_set_show
    >>> round(min_gaps(two_set_show(), ("1", "2"), top=1)[0].gap, 6)
    1.4
    """
    if top < 0:
        raise PathError(
            f"top must be 0 or more, got {top}",
            hint="pass how many of the tightest pairs to list",
            context={"top": top},
        )
    if top == 0:
        return []
    engine = PathEngine(show)
    ids = _on_field_ids(engine, tid, sel)
    boxes = {e: _box(engine, e, tid) for e in ids}
    found: list[GapInfo] = []
    for a, b in itertools.combinations(ids, 2):
        reach = _extent(show, a) + _extent(show, b)
        lower = noise_rank(_box_distance(boxes[a], boxes[b]) - reach)
        if len(found) >= top and lower > noise_rank(found[-1].gap):
            continue
        found.append(_pair_gap(engine, tid, a, b))
        found.sort(key=lambda g: (noise_rank(g.gap), natural_key(g.a), natural_key(g.b)))
        del found[top:]
    return found


def _box(engine: PathEngine, entity: SpotId, tid: tuple[str, str]) -> tuple[Points, Points]:
    _, _, pts = _stations(engine, entity, tid)
    return pts.min(axis=0), pts.max(axis=0)


def _box_distance(a: tuple[Points, Points], b: tuple[Points, Points]) -> float:
    gap = np.maximum(0.0, np.maximum(a[0] - b[1], b[0] - a[1]))
    return float(np.linalg.norm(gap))


def _evaluate(
    show: Show,
    tid: tuple[str, str],
    changed: TransitionSpec,
    pair: tuple[str, str],
    moved: str,
) -> tuple[float, float]:
    others = tuple(t for t in show.transitions if t.id != tid)
    trial = show.replace(transitions=(*others, changed))
    engine = PathEngine(trial)
    return _pair_gap(engine, tid, *pair).gap, engine.speed(moved, tid).max_instant


def suggest_delays(
    show: Show, tid: tuple[str, str], pair: tuple[str, str], max_delay: float = 4.0
) -> list[Fix]:
    """Suggests delaying either spot by 0.5-count steps up to max_delay, best gap first.

    Only delays that widen the gap are returned; ties prefer the shorter delay.

    >>> from showpy.examples import crossing_show
    >>> suggest_delays(crossing_show(), ("1", "2"), ("T1", "T2"))[0].new_gap > 0
    True
    """
    base_gap = _pair_gap(PathEngine(show), tid, *pair).gap
    spec = show.transition(tid)
    found: list[tuple[DelayFix, Fix]] = []
    for entity in pair:
        for counts in np.arange(DELAY_STEP, max_delay + ZERO_TOL, DELAY_STEP):
            try:
                changed = delay(spec, [entity], float(counts))
            except PathError:
                break
            gap, fastest = _evaluate(show, tid, changed, pair, entity)
            if gap > base_gap:
                params = DelayFix(entity=SpotId(entity), counts=float(counts))
                fix = Fix(kind="delay", params=params, new_gap=gap, max_speed_after=fastest)
                found.append((params, fix))
    found.sort(key=lambda item: (-item[1].new_gap, item[0].counts, item[0].entity))
    return [fix for _, fix in found]


def suggest_waypoint(
    show: Show, tid: tuple[str, str], entity: str, around: str, clearance: float = 0.8
) -> list[Fix]:
    """Suggests a timed bend that takes `entity` around `around` at their closest moment.

    Two candidates sit `clearance` beyond both footprints, on either side of `around`.

    >>> from showpy.examples import crossing_show
    >>> suggest_waypoint(crossing_show(), ("1", "2"), "T1", "T2")[0].kind
    'waypoint'
    """
    engine = PathEngine(show)
    info = _pair_gap(engine, tid, entity, around)
    tau = min(max(info.tau, DELAY_STEP), engine.compile(tid).counts - DELAY_STEP)
    here, other = engine.position(entity, tau, local=tid), engine.position(around, tau, local=tid)
    away = _away(here, other, engine.velocity_at(entity, tau, local=tid))
    reach = clearance + _extent(show, entity) + _extent(show, around)
    fixes = []
    for side in (1.0, -1.0):
        point = other + side * away * reach
        changed = add_waypoint(
            show.transition(tid), entity, (float(point[0]), float(point[1])), at=tau
        )
        gap, fastest = _evaluate(show, tid, changed, (entity, around), entity)
        params = BendFix(entity=SpotId(entity), point=(float(point[0]), float(point[1])), at=tau)
        fixes.append(Fix(kind="waypoint", params=params, new_gap=gap, max_speed_after=fastest))
    return sorted(fixes, key=lambda f: -f.new_gap)


def _away(here: Points, other: Points, velocity: Points) -> Points:
    offset = here - other
    if float(np.linalg.norm(offset)) > ZERO_TOL:
        unit: Points = offset / np.linalg.norm(offset)
        return unit
    if float(np.linalg.norm(velocity)) > ZERO_TOL:
        left = np.array([-velocity[1], velocity[0]])
        side: Points = left / np.linalg.norm(left)
        return side
    return np.array([1.0, 0.0])


def suggest_reassignment(
    show: Show,
    tid: tuple[str, str],
    sel: Sequence[str],
    crossing_weight: float = CROSSING_WEIGHT,
) -> MatchResult:
    """Re-matches the selected spots to their own next-set targets, penalizing crossings.

    Apply the result with showpy.choreo.match_ops.apply_match.

    >>> from showpy.examples import crossing_show
    >>> suggest_reassignment(crossing_show(), ("1", "2"), ["T1", "T2"]).crossings
    0
    """
    ids = list(sel)
    targets = show.set(tid[1]).positions(ids)
    options = MatchOptions(keep_groups=False, weights=MatchWeights(crossing=crossing_weight))
    return match(show.set(tid[0]), targets, entities=ids, options=options)


def separate_in_time(
    spec: TransitionSpec, ordered_sel: Sequence[str], gap_counts: float = 1.0
) -> TransitionSpec:
    """Staggers starts `gap_counts` apart along an order.

    >>> from showpy.choreo.path_models import TransitionSpec
    >>> separate_in_time(TransitionSpec(id=("1", "2"), counts=8.0), ["A", "B"]).row("B").start
    1.0
    """
    return stagger(spec, ordered_sel, step=gap_counts)


def apply_fix(spec: TransitionSpec, fix: Fix) -> TransitionSpec:
    """Applies a delay, waypoint or stagger fix; a reassignment must go through apply_match.

    >>> from showpy.choreo.path_models import TransitionSpec
    >>> params = DelayFix(entity="A", counts=2.0)
    >>> fix = Fix(kind="delay", params=params, new_gap=1.0, max_speed_after=1.0)
    >>> apply_fix(TransitionSpec(id=("1", "2"), counts=8.0), fix).row("A").start
    2.0
    """
    params = fix.params
    if isinstance(params, DelayFix):
        return delay(spec, [params.entity], params.counts)
    if isinstance(params, BendFix):
        return add_waypoint(spec, params.entity, params.point, at=params.at)
    if isinstance(params, StaggerFix):
        return stagger(spec, params.order, step=params.step)
    raise PathError(
        "a reassignment changes the next set, not the paths",
        hint="apply it with showpy.choreo.match_ops.apply_match(show, tid, result)",
    )
