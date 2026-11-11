#!/usr/bin/env python3

"""
engine_models.py --- data classes the PathEngine produces: plans, segments, states, frames

Contains:
    MotionPiece: describes one piece of motion in local counts
    MotionPlan: holds one spot's compiled motion in one transition
    CompiledTransition: holds every plan of one transition
    MotionSegment: holds a straight, time-linear piece for exact collision checks
    EntityState: holds everything about one spot at one moment
    Frame: holds every spot's state at one moment
    MotionStats: summarizes how varied the movement of a transition is
"""

from typing import Literal

from stage.choreo.models import Easing, FacingMode, PathStyle, Placement, TurnDirection
from stage.core.ids import SpotId, TransitionId
from stage.core.types import Count, Degrees, FrozenModel, Meters, Point
from stage.geo.curves import AnyCurve


class MotionPiece(FrozenModel):
    """Describes one piece of motion in local counts.

    Attributes:
        t0: Local count the piece starts.
        t1: Local count the piece ends.
        kind: hold, line, arc, curve, route or custom.
        params: Arc lengths `s0` and `s1` the piece covers on the plan's curve.
        length: Meters travelled in the piece.
    """

    t0: float
    t1: float
    kind: Literal["hold", "line", "arc", "curve", "route", "custom"]
    params: dict[str, float] = {}
    length: Meters = 0.0


class MotionPlan(FrozenModel):
    """Holds one spot's compiled motion in one transition (see ADR 0002).

    Attributes:
        entity: Spot ID.
        pieces: Motion pieces covering the whole transition.
        start: Placement in the previous set.
        end: Placement in the next set.
        style: Path style the plan was compiled from.
        curve: Path in space; None when the spot does not move.
        window: Local (start, end) counts of the move.
        knots_t: Local counts of the time-map knots.
        knots_s: Arc lengths at those knots.
        easing: Easing applied across the window.
        facing_mode: Facing during the window.
        facing_value: Degrees, point or spot ID for the facing mode.
        turn_counts: Counts spent blending facings.
        turn_direction: Which way facing turns go.
    """

    entity: SpotId
    pieces: tuple[MotionPiece, ...]
    start: Placement
    end: Placement
    style: PathStyle
    curve: AnyCurve | None
    window: tuple[float, float]
    knots_t: tuple[float, ...]
    knots_s: tuple[float, ...]
    easing: Easing = Easing()
    facing_mode: FacingMode = FacingMode.TO_TARGET
    facing_value: Degrees | Point | SpotId | None = None
    turn_counts: float = 0.0
    turn_direction: TurnDirection = TurnDirection.SHORTEST

    @property
    def moves(self) -> bool:
        """Reports whether the plan changes position."""
        return self.curve is not None


class CompiledTransition(FrozenModel):
    """Holds every plan of one transition.

    Attributes:
        id: Transition ID.
        counts: Transition length in counts.
        t_global0: Global count the transition starts.
        plans: Spot ID to plan.
        warnings: Non-fatal notes from compilation.
    """

    id: TransitionId
    counts: float
    t_global0: Count
    plans: dict[SpotId, MotionPlan]
    warnings: tuple[str, ...] = ()


class MotionSegment(FrozenModel):
    """Holds a straight piece that is linear in both space and time.

    Attributes:
        entity: Spot ID.
        tid: Transition ID.
        t0: Local count at p0.
        t1: Local count at p1.
        p0: Position at t0.
        p1: Position at t1.
    """

    entity: SpotId
    tid: TransitionId
    t0: float
    t1: float
    p0: Point
    p1: Point


class EntityState(FrozenModel):
    """Holds everything about one spot at one moment.

    Attributes:
        entity: Spot ID.
        position: Meters.
        facing: Degrees.
        velocity: Meters per count.
        moving: Whether speed is above zero.
        on_field: Whether the spot is drawn and collision-checked.
    """

    entity: SpotId
    position: Point
    facing: Degrees
    velocity: Point
    moving: bool
    on_field: bool


class Frame(FrozenModel):
    """Holds every spot's state at one moment.

    Attributes:
        t: Global count.
        tid: Transition in progress; None outside any transition.
        tau: Local count within that transition.
        states: One state per spot.
    """

    t: Count
    tid: TransitionId | None
    tau: float
    states: tuple[EntityState, ...]


class MotionStats(FrozenModel):
    """Summarizes how varied the movement of a transition is; feedback, not a rule.

    Attributes:
        moving_share: Share of on-field spots that move.
        speed_spread: Coefficient of variation of moving spots' mean speeds.
        direction_variety: Circular variance of net travel directions, 0 (all alike) to 1.
    """

    moving_share: float
    speed_spread: float
    direction_variety: float
