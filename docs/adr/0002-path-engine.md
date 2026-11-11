# ADR 0002: PathEngine compiles every move into an arc-length path plus a time map

- Status: Accepted
- Date: 2026-11-11
- Deciders: abj360

## Context

CLAUDE.md rule 5 says `PathEngine` is the only code that turns path instructions into
positions. The canvas, checker, exports and analysis all read it, so they never disagree. The
reference lists the engine's methods but leaves several things open: how styles, timed
waypoints, easing and facing combine; what happens before and after a row's window; and what
the checker receives.

## Decision

1. **Path and clock are separate.** Each spot's move in a transition compiles to a
   `MotionPlan`, which holds two things:
   - a curve: segment, polyline, spline, arc, trimmed route, or sampled custom polyline.
   - a piecewise-linear time map from local count τ to arc length s. The knots are the
     window start, every timed waypoint, and the window end.

   Position is `curve.point_at(map(ease(u)))`, where `u = (τ − start)/(end − start)`,
   clamped. Easing is a monotone map from [0, 1] onto itself with `e(0) = 0` and
   `e(1) = 1`, so every style stays continuous and still ends on its target.
2. **Outside the window, the spot stands still.** Before `start` it is at the previous set's
   placement; after `end` it is at the next set's placement. `on_field` comes from the previous
   set before the window and from the next set after it. During the window it is the OR of the
   two, so an entrance is visible while it walks on.
3. **Facing runs in three phases.** Before the window: the previous set's facing. During the
   window: the row's `FacingMode`, blended in from the previous facing over `turn_counts`.
   After the window: the next set's facing, blended in over `min(turn_counts, counts − end)`.
   A blend of 0 counts snaps. `FacingOverride`s win over paths inside their global count range,
   with the same blend in and out.
4. **Continuity is checked at compile time.** HOLD, ROTATE, FOLLOW and CUSTOM must end
   within 1e-6 m of the target placement, or `compile` raises `ContinuityError`. STRAIGHT,
   POLYLINE and CURVE end on the target by construction.
5. **The checker gets straight, time-linear segments.** `segments(tid, tol)` subdivides time
   adaptively, starting from every knot and window edge, until the distance from each probe
   at ¼, ½ and ¾ to the linear interpolation is ≤ `tol`. Between consecutive segment ends,
   each segment is linear in both space and time. Closest-approach checks on those segments
   are therefore exact to within `tol`, including crossings between counts.
6. **Velocity comes from the plan, not a second model.** It is a central finite difference of
   the plan's own positions (h = 1e-4 counts). Nothing else interpolates motion.
7. **Registries are immutable.** Custom motions (`MotionRegistry`) and the engine's caches
   are per instance. `invalidate` only clears this engine's memo, and since shows are frozen,
   a cache can only go stale by being reused across shows.

## Alternatives

- **Per-style closed-form position functions.** Rejected: timed waypoints and easing would
  multiply the cases. One curve plus one time map covers every style.
- **Fixed-rate sampling for the checker.** Rejected: 8 samples per count misses a crossing
  that happens in about 0.06 counts at 3 m/count. Adaptive segments bound the error by `tol`
  instead.
- **Analytic velocity per piece.** Rejected for now: easing kinds and custom motions would each
  need a derivative. A finite difference of the same plan cannot disagree with positions.

## Consequences

- One code path serves every consumer. A new style only needs to build a curve.
- Custom motions are sampled, at 257 points by default, then simplified within `curve_tol`.
  Exotic motion functions are therefore only as exact as that tolerance.

## Numbers

- Continuity tolerance: 1e-6 m. Default segment tolerance: 0.02 m. Default curve tolerance:
  0.01 m.

## What would change our mind

- If profiling shows `segments` dominates check time on 300-spot shows, add analytic segments
  for STRAIGHT moves with linear easing, which are already exactly time-linear.

## References

- ADR 0001; CLAUDE.md rule 5; reference sections "stage.choreo.engine" and "paths".

## Addendum 2026-11-11: probe density for segments

A Hypothesis property test checks the segment error at nine points per segment, not just at
the probes. It found a deviation of 0.0113 m against a 0.01 m tolerance when only ¼, ½ and ¾
were probed. Subdivision now probes at every eighth (7 points) and accepts a segment only
when every probe is within `tol / 2`. That leaves margin for the error between probes. 150
random rows (delays, easing, waypoints, curves) now pass at 9 points per segment.
