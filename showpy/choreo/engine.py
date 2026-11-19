"""
engine.py --- PathEngine, the only code that turns path instructions into positions over time

The canvas, checker, exports and analysis all ask this engine, so they never disagree.

Contains:
    PathEngine: compiles a show's transitions and answers where, how and how fast spots move
    PathEngine.compile(): builds every plan of one transition
    PathEngine.compile_entity(): builds one spot's plan, uncached
    PathEngine.compile_all(): builds every transition of the show
    PathEngine.locate(): maps a global count to (transition, local count)
    PathEngine.position(): returns a spot's position at a count
    PathEngine.positions(): returns many spots' positions at a count
    PathEngine.facing_at(): returns a spot's facing at a count
    PathEngine.velocity_at(): returns a spot's velocity in meters per count
    PathEngine.on_field_at(): reports whether a spot is on the field at a count
    PathEngine.state_at(): returns a spot's full state at a count
    PathEngine.sample(): returns every spot's state at a count
    PathEngine.frames(): yields frames between two counts, per count or per second
    PathEngine.segments(): returns time-linear segments for exact collision checks
    PathEngine.entity_segments(): returns one spot's time-linear segments
    PathEngine.speed(): returns a spot's movement demand in a transition
    PathEngine.path_geometry(): returns a spot's drawn path for the canvas
    PathEngine.invalidate(): clears cached compiles
"""

import bisect
import hashlib
import itertools
import math
from collections.abc import Iterable, Iterator, Sequence

import numpy as np

from showpy.cast.select import select
from showpy.choreo.engine_compile import (
    compile_plan,
    curvature_bound,
    easing_chord_error,
    plan_breaks,
    plan_positions,
    plan_progress,
)
from showpy.choreo.engine_facing import FacingContext, plan_facing, validate_facing
from showpy.choreo.engine_models import (
    CompiledTransition,
    EntityState,
    Frame,
    MotionPlan,
    MotionSegment,
)
from showpy.choreo.models import FacingOverride
from showpy.choreo.motion import DEFAULT_MOTIONS, MotionRegistry, ease, turn
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import PathError, UnknownRefError
from showpy.core.ids import SetId, SpotId, TransitionId
from showpy.core.selection import Selection
from showpy.core.types import Count, Degrees, Point, Points
from showpy.core.util import close_matches, seeded_rng
from showpy.core.validation import require_finite
from showpy.geo.curves import Polyline
from showpy.geo.models import SpeedStats
from showpy.show.models import Show
from showpy.time.clock import count_to_seconds, seconds_to_count

DEFAULT_CURVE_TOL = 0.01
DEFAULT_SEGMENT_TOL = 0.02
VELOCITY_STEP = 1e-4
MOVING_SPEED = 1e-9
MAX_SEGMENT_DEPTH = 20
PROBES_PER_SEGMENT = 9
PROBE_TOL_SHARE = 0.5
SEED_BYTES = 8
ALL = "all"


class PathEngine:
    """Compiles a show's transitions and answers where, how and how fast spots move.

    Attributes:
        show: The show being animated; frozen, so caches never go stale.
        curve_tol: Chord tolerance for drawn paths.
        motions: Registry that resolves custom motion names.
    """

    def __init__(
        self,
        show: Show,
        curve_tol: float = DEFAULT_CURVE_TOL,
        *,
        cache: bool = True,
        motions: MotionRegistry = DEFAULT_MOTIONS,
    ) -> None:
        self.show = show
        self.curve_tol = curve_tol
        self.motions = motions
        self._use_cache = cache
        self._cache: dict[TransitionId, CompiledTransition] = {}
        self._override_cache: dict[int, tuple[SpotId, ...]] = {}
        self._starts = list(itertools.accumulate((s.counts for s in show.sets[1:]), initial=0.0))
        self._ids = show.cast.ids() or (show.sets[0].entity_ids() if show.sets else ())

    def _set_start(self, set_id: SetId) -> Count:
        return self._starts[self.show.set_index(set_id)]

    def compile(self, tid: TransitionId) -> CompiledTransition:
        """Builds every plan of one transition; raises ContinuityError on a missed target."""
        if self._use_cache and tid in self._cache:
            return self._cache[tid]
        plans = {entity: self.compile_entity(entity, tid) for entity in self._ids}
        compiled = CompiledTransition(
            id=tid,
            counts=self.show.transition(tid).counts,
            t_global0=self._set_start(tid[0]),
            plans=plans,
        )
        if self._use_cache:
            self._cache[tid] = compiled
        return compiled

    def compile_entity(self, entity: SpotId, tid: TransitionId) -> MotionPlan:
        """Builds one spot's plan without caching, so callers can report errors per spot."""
        spec = self.show.transition(tid)
        a, b = self.show.set(tid[0]).get(entity), self.show.set(tid[1]).get(entity)
        plan = compile_plan(spec.row(entity), spec, a, b, self.motions)
        validate_facing(plan)
        return plan

    def compile_all(self) -> dict[TransitionId, CompiledTransition]:
        """Builds every transition of the show, in show order."""
        return {tid: self.compile(tid) for tid in self.show.transition_ids()}

    def locate(self, t: Count) -> tuple[TransitionId | None, float]:
        """Maps a global count to (transition, local count); None before or after the show."""
        tids = self.show.transition_ids()
        if not tids or t < 0 or t > self._starts[-1]:
            return None, 0.0
        index = max(bisect.bisect_left(self._starts, t), 1)
        return tids[index - 1], t - self._starts[index - 1]

    def _plan(self, entity: SpotId, tid: TransitionId) -> MotionPlan:
        plans = self.compile(tid).plans
        if entity not in plans:
            close = close_matches(entity, plans)
            raise UnknownRefError(
                f"unknown spot {entity!r}",
                hint=f"did you mean {', '.join(close)}?" if close else "check the spot id",
                context={"entity": entity},
            )
        return plans[entity]

    def _resolve(self, t: Count, local: TransitionId | None) -> tuple[TransitionId | None, float]:
        return (local, t) if local is not None else self.locate(t)

    def _static_set(self, t: Count) -> int:
        return 0 if t <= 0 else len(self.show.sets) - 1

    def _static_point(self, entity: SpotId, t: Count) -> Point:
        return self.show.sets[self._static_set(t)].get(entity).point

    def position(self, entity: SpotId, t: Count, local: TransitionId | None = None) -> Points:
        """Returns a spot's position (shape (2,)) at a global count, or a local one with `local`."""
        tid, tau = self._resolve(t, local)
        if tid is None:
            return np.asarray(self._static_point(entity, t), dtype=float)
        point: Points = plan_positions(self._plan(entity, tid), np.array([tau]))[0]
        return point

    def positions(self, t: Count, sel: str | Sequence[SpotId] | Selection = ALL) -> Points:
        """Returns positions (N, 2) at a global count, in selection order.

        `sel` is "all", a selection expression, a list of IDs or a Selection.
        """
        ids = self._ids if sel == ALL else select(self.show, sel).ids
        return np.array([self.position(e, t) for e in ids]).reshape(-1, 2)

    def _point_at(self, entity: str, t: Count) -> Point:
        p = self.position(entity, t)
        return float(p[0]), float(p[1])

    def _path_facing(self, entity: SpotId, tid: TransitionId, tau: float) -> Degrees:
        compiled = self.compile(tid)
        ctx = FacingContext(self._point_at, compiled.t_global0, compiled.counts)
        return plan_facing(self._plan(entity, tid), tau, ctx)

    def facing_at(self, entity: SpotId, t: Count, local: TransitionId | None = None) -> Degrees:
        """Returns a spot's facing; facing overrides win inside their count ranges."""
        tid, tau = self._resolve(t, local)
        if tid is None:
            return self.show.sets[self._static_set(t)].get(entity).facing
        base = self._path_facing(entity, tid, tau)
        t_global = self.compile(tid).t_global0 + tau
        for index, override in enumerate(self.show.facing_overrides):
            if entity in self._override_ids(index, override):
                base = self._apply_override(override, entity, t_global, base)
        return base

    def _override_ids(self, index: int, override: FacingOverride) -> tuple[SpotId, ...]:
        if index not in self._override_cache:
            entities = override.entities
            ids = self._ids if entities == ALL else select(self.show, entities).ids
            self._override_cache[index] = ids
        return self._override_cache[index]

    def _apply_override(
        self, override: FacingOverride, entity: SpotId, t: Count, base: Degrees
    ) -> Degrees:
        span = override.turn_counts
        if t < override.start or t > override.end + span:
            return base
        forced = self._override_facing(override, entity, min(t, override.end), base)
        if t > override.end:
            return turn(forced, base, (t - override.end) / span) if span > 0 else base
        if span > 0 and t - override.start < span:
            return turn(base, forced, (t - override.start) / span)
        return forced

    def _override_facing(
        self, override: FacingOverride, entity: SpotId, t: Count, base: Degrees
    ) -> Degrees:
        tid, tau = self.locate(t)
        if tid is None:
            return base
        plan = self._plan(entity, tid).replace(
            facing_mode=override.mode, facing_value=override.value
        )
        compiled = self.compile(tid)
        validate_facing(plan)
        ctx = FacingContext(self._point_at, compiled.t_global0, compiled.counts)
        start, end = plan.window
        inside = min(max(tau, start + VELOCITY_STEP), end - VELOCITY_STEP)
        from_mode = plan.replace(turn_counts=0.0)
        return plan_facing(from_mode, inside, ctx) if plan.curve is not None else base

    def velocity_at(self, entity: SpotId, t: Count, local: TransitionId | None = None) -> Points:
        """Returns velocity in meters per count by a central difference of the plan's positions."""
        tid, tau = self._resolve(t, local)
        if tid is None:
            return np.zeros(2)
        counts = self.compile(tid).counts
        lo, hi = max(tau - VELOCITY_STEP, 0.0), min(tau + VELOCITY_STEP, counts)
        if hi <= lo:
            return np.zeros(2)
        pts = plan_positions(self._plan(entity, tid), np.array([lo, hi]))
        velocity: Points = (pts[1] - pts[0]) / (hi - lo)
        return velocity

    def on_field_at(self, entity: SpotId, t: Count, local: TransitionId | None = None) -> bool:
        """Reports whether a spot is on the field: previous set, either while moving, next set."""
        tid, tau = self._resolve(t, local)
        if tid is None:
            return self.show.sets[self._static_set(t)].get(entity).on_field
        plan = self._plan(entity, tid)
        start, end = plan.window
        if tau <= start:
            return plan.start.on_field
        if tau >= end:
            return plan.end.on_field
        return plan.start.on_field or plan.end.on_field

    def state_at(self, entity: SpotId, t: Count, local: TransitionId | None = None) -> EntityState:
        """Returns a spot's position, facing, velocity and field state."""
        p = self.position(entity, t, local)
        v = self.velocity_at(entity, t, local)
        return EntityState(
            entity=entity,
            position=(float(p[0]), float(p[1])),
            facing=self.facing_at(entity, t, local),
            velocity=(float(v[0]), float(v[1])),
            moving=bool(np.linalg.norm(v) > MOVING_SPEED),
            on_field=self.on_field_at(entity, t, local),
        )

    def sample(self, t: Count) -> Frame:
        """Returns every spot's state at a global count."""
        tid, tau = self.locate(t)
        return Frame(t=t, tid=tid, tau=tau, states=tuple(self.state_at(e, t) for e in self._ids))

    def frames(
        self, t0: Count, t1: Count, per_count: int = 8, fps: float | None = None
    ) -> Iterator[Frame]:
        """Yields frames from t0 to t1 inclusive, per count or, with fps, per second of music."""
        if fps is None:
            steps = max(round((t1 - t0) * per_count), 0)
            times = np.linspace(t0, t1, steps + 1)
        else:
            s0, s1 = (
                count_to_seconds(self.show.timeline, t0),
                count_to_seconds(self.show.timeline, t1),
            )
            seconds = np.linspace(s0, s1, max(round((s1 - s0) * fps), 0) + 1)
            times = np.array([seconds_to_count(self.show.timeline, float(s)) for s in seconds])
        for t in times:
            yield self.sample(float(t))

    def segments(self, tid: TransitionId, tol: float = DEFAULT_SEGMENT_TOL) -> list[MotionSegment]:
        """Returns contiguous time-linear segments per spot, accurate to `tol` meters.

        Raises PathError code SEGMENT_BOUND when a spot's motion cannot be brought within
        `tol` before the depth cap.
        """
        require_finite(tol, name="tol", low=0.0, low_open=True)
        return [seg for e in self.compile(tid).plans for seg in self.entity_segments(e, tid, tol)]

    def entity_segments(
        self, entity: SpotId, tid: TransitionId, tol: float = DEFAULT_SEGMENT_TOL
    ) -> list[MotionSegment]:
        """Returns one spot's contiguous time-linear segments in a transition."""
        tol = require_finite(tol, name="tol", low=0.0, low_open=True)
        plan = self._plan(entity, tid)
        stations = _SegmentFit(plan, tid, tol).stations(self.compile(tid).counts)
        pts = plan_positions(plan, np.array(stations))
        return [
            MotionSegment(entity=entity, tid=tid, t0=t0, t1=t1, p0=_pt(p0), p1=_pt(p1))
            for (t0, t1), (p0, p1) in zip(
                itertools.pairwise(stations), itertools.pairwise(pts), strict=True
            )
        ]

    def speed(
        self, entity: SpotId, tid: TransitionId, window: tuple[float, float] | None = None
    ) -> SpeedStats:
        """Returns movement demand over a local window; counts and seconds cover moving time."""
        compiled = self.compile(tid)
        lo, hi = window or (0.0, compiled.counts)
        distance = moving_counts = seconds = fastest = 0.0
        for seg in self._clipped(entity, tid, lo, hi):
            length, span = math.dist(seg.p0, seg.p1), seg.t1 - seg.t0
            if length <= MOVING_SPEED * span or span <= 0:
                continue
            g0, g1 = compiled.t_global0 + seg.t0, compiled.t_global0 + seg.t1
            distance += length
            moving_counts += span
            seconds += count_to_seconds(self.show.timeline, g1) - count_to_seconds(
                self.show.timeline, g0
            )
            fastest = max(fastest, length / span)
        return SpeedStats(
            distance=distance,
            counts=moving_counts,
            m_per_count=distance / moving_counts if moving_counts else 0.0,
            m_per_s=distance / seconds if seconds else 0.0,
            max_instant=fastest,
        )

    def _clipped(
        self, entity: SpotId, tid: TransitionId, lo: float, hi: float
    ) -> list[MotionSegment]:
        plan = self._plan(entity, tid)
        out = []
        for seg in self.entity_segments(entity, tid):
            t0, t1 = max(seg.t0, lo), min(seg.t1, hi)
            if t1 <= t0:
                continue
            p0, p1 = plan_positions(plan, np.array([t0, t1]))
            out.append(seg.replace(t0=t0, t1=t1, p0=_pt(p0), p1=_pt(p1)))
        return out

    def path_geometry(self, entity: SpotId, tid: TransitionId) -> Points:
        """Returns the drawn path of a spot in a transition, start to end."""
        plan = self._plan(entity, tid)
        if plan.curve is None:
            return np.asarray([plan.start.point], dtype=float)
        return plan.curve.to_polyline(self.curve_tol)

    def invalidate(
        self, sets: Iterable[SetId] = (), transitions: Iterable[TransitionId] = ()
    ) -> None:
        """Clears cached compiles touching the given sets or transitions."""
        set_ids = set(sets)
        for tid in list(self._cache):
            if tid in set(transitions) or set_ids & set(tid):
                del self._cache[tid]


def _pt(p: Points) -> Point:
    return float(p[0]), float(p[1])


def _probe_seed(entity: SpotId, tid: TransitionId) -> int:
    key = f"{tid[0]}->{tid[1]}:{entity}".encode()
    return int.from_bytes(hashlib.blake2b(key, digest_size=SEED_BYTES).digest(), "big")


class _SegmentFit:
    """Splits one plan's motion into time-linear segments that stay within `tol` of it.

    Pieces on curves with a known curvature bound are accepted by the closed-form chord bound
    (linear-interpolation error h^2/8 times the largest second derivative); other curves are
    probed at seeded jittered shares.

    Attributes:
        plan: Plan being segmented.
        tid: Transition of the plan, for errors and the probe seed.
        tol: Largest allowed distance between a segment and the motion, meters.
    """

    def __init__(self, plan: MotionPlan, tid: TransitionId, tol: float) -> None:
        self.plan, self.tid, self.tol = plan, tid, tol
        self._kappa = None if plan.curve is None else curvature_bound(plan.curve)
        self._rng: np.random.Generator | None = None
        self._corners = plan.curve if isinstance(plan.curve, Polyline) else None

    def stations(self, counts: float) -> list[float]:
        """Returns segment end counts from 0 to `counts`, every motion kink among them."""
        if not self.plan.moves:
            return [0.0, counts]
        edges = np.unique(
            np.clip(np.concatenate([[0.0, counts], plan_breaks(self.plan)]), 0, counts)
        )
        stations = [float(edges[0])]
        for lo, hi in itertools.pairwise(edges):
            stations.extend(self._split(float(lo), float(hi), 0))
        return stations

    def _split(self, lo: float, hi: float, depth: int) -> list[float]:
        error, limit = self._error(lo, hi)
        if error <= limit:
            return [hi]
        if depth >= MAX_SEGMENT_DEPTH:
            accepted_tol = error / limit * self.tol
            raise PathError(
                f"segment {lo:g}-{hi:g} of {self.plan.entity} stays {error:.3g} m off its motion",
                hint=f"pass a tol above {accepted_tol:.3g} m or give the motion fewer turns",
                context={
                    "entity": self.plan.entity,
                    "tid": self.tid,
                    "tol": self.tol,
                    "error": error,
                },
                code=ErrorCode.SEGMENT_BOUND,
            )
        mid = (lo + hi) / 2
        return self._split(lo, mid, depth + 1) + self._split(mid, hi, depth + 1)

    def _error(self, lo: float, hi: float) -> tuple[float, float]:
        if self._kappa is not None:
            return self._bounded_error(lo, hi, self._kappa), self.tol
        return self._probed_error(lo, hi), self.tol * PROBE_TOL_SHARE

    def _bounded_error(self, lo: float, hi: float, kappa: float) -> float:
        start, end = self.plan.window
        shares = np.clip((np.array([lo, hi]) - start) / (end - start), 0.0, 1.0)
        eased = ease(self.plan.easing, shares)
        if eased[1] <= eased[0]:
            return 0.0
        s0, s1 = plan_progress(self.plan, np.array([lo, hi]))
        travel = abs(float(s1 - s0))
        gap = easing_chord_error(self.plan.easing, float(shares[0]), float(shares[1]))
        bend = kappa * travel * travel / 8 + self._corner_error(float(s0), float(s1))
        return travel * gap / float(eased[1] - eased[0]) + bend

    def _corner_error(self, s0: float, s1: float) -> float:
        """Returns the exact gap between a constant-speed walk along a polyline and its chord.

        The gap is piecewise linear in time, so it peaks on a corner strictly inside [s0, s1].
        """
        curve = self._corners
        if curve is None or s1 <= s0:
            return 0.0
        arc = curve._breaks()
        inside = slice(np.searchsorted(arc, s0, "right"), np.searchsorted(arc, s1, "left"))
        if inside.start >= inside.stop:
            return 0.0
        pts = curve.point_at(np.concatenate([[s0, s1], arc[inside]]))
        shares = (arc[inside] - s0) / (s1 - s0)
        chord = pts[0] + np.outer(shares, pts[1] - pts[0])
        return float(np.max(np.linalg.norm(pts[2:] - chord, axis=1)))

    def _probe_rng(self) -> np.random.Generator:
        if self._rng is None:
            self._rng = seeded_rng(_probe_seed(self.plan.entity, self.tid))
        return self._rng

    def _probed_error(self, lo: float, hi: float) -> float:
        jitter = self._probe_rng().random(PROBES_PER_SEGMENT)
        shares = (np.arange(PROBES_PER_SEGMENT) + jitter) / PROBES_PER_SEGMENT
        pts = plan_positions(self.plan, np.concatenate([[lo, hi], lo + shares * (hi - lo)]))
        linear = pts[0] + np.outer(shares, pts[1] - pts[0])
        return float(np.max(np.linalg.norm(pts[2:] - linear, axis=1)))
