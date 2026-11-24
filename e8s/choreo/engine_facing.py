"""
engine_facing.py --- computes a spot's facing from its plan, in three phases

Contains:
    FacingContext: gives the facing rules access to positions of any spot at a global count
    steady_facing(): returns the facing a mode asks for at every moment alike, or None
    mode_facing(): returns the facing a FacingMode asks for at one moment
    plan_facing(): returns the facing from a plan: before, during and after the window
    plan_facings(): returns a plan's facings at many counts when they only step, else None
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from e8s.choreo.engine_compile import plan_positions, plan_progress
from e8s.choreo.engine_models import MotionPlan
from e8s.choreo.motion import ease, turn
from e8s.choreo.path_models import (
    EntityFacing,
    FacingMode,
    FixedFacing,
    PointFacing,
    TurnDirection,
)
from e8s.core.arrays import FloatArray
from e8s.core.errors import PathError
from e8s.core.types import Count, Degrees, Point
from e8s.core.units import normalize_angle, vector_facing

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


def steady_facing(plan: MotionPlan) -> Degrees | None:
    """Returns the facing the plan's mode asks for at every moment alike, or None when it varies.

    >>> from e8s.choreo.engine_compile import compile_plan
    >>> from e8s.choreo.models import Placement
    >>> from e8s.choreo.motion import DEFAULT_MOTIONS
    >>> from e8s.choreo.path_models import PathRow, TransitionSpec
    >>> spec = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> a, b = Placement(entity="A", x=0, y=0), Placement(entity="A", x=0, y=4, facing=90.0)
    >>> steady_facing(compile_plan(PathRow(entity="A"), spec, a, b, DEFAULT_MOTIONS))
    90.0
    """
    mode = plan.facing_mode
    if mode == FacingMode.FIXED:
        return _value(plan, FixedFacing).degrees
    if mode == FacingMode.TO_TARGET:
        return plan.end.facing
    if mode == FacingMode.AUDIENCE:
        return AUDIENCE_FACING
    return None


def mode_facing(plan: MotionPlan, tau: float, ctx: FacingContext) -> Degrees:
    """Returns the facing the plan's FacingMode asks for at local count tau."""
    steady = steady_facing(plan)
    if steady is not None:
        return steady
    mode = plan.facing_mode
    if mode == FacingMode.TRAVEL:
        return _travel(plan, tau)
    if mode == FacingMode.AGAINST_TRAVEL:
        return normalize_angle(_travel(plan, tau) + HALF_TURN)
    here = plan_positions(plan, np.array([tau]))[0]
    point = (float(here[0]), float(here[1]))
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


def plan_facings(plan: MotionPlan, taus: FloatArray) -> FloatArray | None:
    """Returns a plan's facings at many local counts when they only step, else None.

    A steady mode with no turn counts faces the previous facing up to the window start, the
    mode's facing inside the window and the next facing from its end on. The values equal
    `plan_facing` at each count; any other plan returns None, so the caller asks one count at
    a time.

    Args:
        plan: Compiled plan of one spot.
        taus: Local counts to face at.

    Returns:
        facings: One facing per count, or None when the plan's facing turns gradually.

    >>> from e8s.choreo.engine_compile import compile_plan
    >>> from e8s.choreo.models import Placement
    >>> from e8s.choreo.motion import DEFAULT_MOTIONS
    >>> from e8s.choreo.path_models import PathRow, TransitionSpec
    >>> spec = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> a, b = Placement(entity="A", x=0, y=0), Placement(entity="A", x=0, y=4, facing=90.0)
    >>> plan = compile_plan(PathRow(entity="A", start=2.0), spec, a, b, DEFAULT_MOTIONS)
    >>> plan_facings(plan, np.array([0.0, 2.0, 4.0, 8.0])).tolist()
    [0.0, 0.0, 90.0, 90.0]
    """
    steady = steady_facing(plan)
    if steady is None or plan.turn_counts > 0:
        return None
    start, end = plan.window
    facings: FloatArray = np.select(
        [taus <= start, taus < end], [plan.start.facing, steady], plan.end.facing
    )
    return facings
