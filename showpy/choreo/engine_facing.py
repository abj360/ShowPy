"""
engine_facing.py --- computes a spot's facing from its plan, in three phases

Contains:
    FacingContext: gives the facing rules access to positions of any spot at a global count
    mode_facing(): returns the facing a FacingMode asks for at one moment
    plan_facing(): returns the facing from a plan: before, during and after the window
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from showpy.choreo.engine_compile import plan_positions, plan_progress
from showpy.choreo.engine_models import MotionPlan
from showpy.choreo.motion import ease, turn
from showpy.choreo.path_models import (
    EntityFacing,
    FacingMode,
    FixedFacing,
    PointFacing,
    TurnDirection,
)
from showpy.core.errors import PathError
from showpy.core.types import Count, Degrees, Point
from showpy.core.units import normalize_angle, vector_facing

ZERO_DIRECTION_TOL = 1e-9
HALF_TURN = 180.0
AUDIENCE_FACING = 0.0


@dataclass(frozen=True)
class FacingContext:
    """Gives facing rules what they need beyond the plan itself.

    Attributes:
        position_of: Returns any spot's position at a global count.
        t_global0: Global count where the plan's transition starts.
        counts: Transition length in counts.
    """

    position_of: Callable[[str, Count], Point]
    t_global0: Count
    counts: float


def _toward(origin: Point, target: Point, fallback: Degrees) -> Degrees:
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    if math.hypot(dx, dy) < ZERO_DIRECTION_TOL:
        return fallback
    return vector_facing((dx, dy))


def _travel(plan: MotionPlan, tau: float) -> Degrees:
    if plan.curve is None:
        return plan.start.facing
    s = plan_progress(plan, np.array([tau]))[0]
    tangent = plan.curve.tangent_at(s)
    return vector_facing((float(tangent[0]), float(tangent[1])))


def _value[T: FixedFacing | PointFacing | EntityFacing](plan: MotionPlan, kind: type[T]) -> T:
    value = plan.facing_value
    if isinstance(value, kind):
        return value
    raise PathError(
        f"facing mode {plan.facing_mode.value} of {plan.entity} needs a {kind.__name__} value",
        hint="give the mode the value kind it reads",
        context={"entity": plan.entity, "value": repr(value)},
    )


def mode_facing(plan: MotionPlan, tau: float, ctx: FacingContext) -> Degrees:
    """Returns the facing the plan's FacingMode asks for at local count tau."""
    mode = plan.facing_mode
    here = plan_positions(plan, np.array([tau]))[0]
    point = (float(here[0]), float(here[1]))
    if mode == FacingMode.FIXED:
        return _value(plan, FixedFacing).degrees
    if mode == FacingMode.TO_TARGET:
        return plan.end.facing
    if mode == FacingMode.AUDIENCE:
        return AUDIENCE_FACING
    if mode == FacingMode.TRAVEL:
        return _travel(plan, tau)
    if mode == FacingMode.AGAINST_TRAVEL:
        return normalize_angle(_travel(plan, tau) + HALF_TURN)
    if mode == FacingMode.TOWARD_POINT:
        return _toward(point, _value(plan, PointFacing).point, plan.start.facing)
    if mode == FacingMode.TOWARD_ENTITY:
        other = ctx.position_of(_value(plan, EntityFacing).entity, ctx.t_global0 + tau)
        return _toward(point, other, plan.start.facing)
    start, end = plan.window
    share = float(ease(plan.easing, np.array([(tau - start) / (end - start)]))[0])
    return turn(plan.start.facing, plan.end.facing, share, plan.turn_direction)


def _blend(a: Degrees, b: Degrees, elapsed: float, span: float, way: TurnDirection) -> Degrees:
    if span <= 0 or elapsed >= span:
        return b
    return turn(a, b, elapsed / span, way)


def plan_facing(plan: MotionPlan, tau: float, ctx: FacingContext) -> Degrees:
    """Returns the facing from a plan: previous facing, mode facing, then next facing."""
    start, end = plan.window
    if tau <= start:
        return plan.start.facing
    if tau < end:
        target = mode_facing(plan, tau, ctx)
        return _blend(plan.start.facing, target, tau - start, plan.turn_counts, plan.turn_direction)
    leaving = mode_facing(plan, end - ZERO_DIRECTION_TOL, ctx)
    span = min(plan.turn_counts, ctx.counts - end)
    return _blend(leaving, plan.end.facing, tau - end, span, plan.turn_direction)
