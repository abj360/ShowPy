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
    GapInfo: describes the tightest moment between two spots
    DelayFix: holds the arguments of a delay fix
    BendFix: holds the arguments of a waypoint fix that bends one spot's path
    StaggerFix: holds the arguments of a stagger fix
    ReassignFix: marks a reassignment fix, which carries no path arguments
    FixParams: names the argument models of the four fix kinds
    FIX_PARAMS: maps each fix kind to its argument model
    Fix: describes one suggested change that opens a gap
"""

from collections.abc import Mapping
from typing import Final, Literal

from pydantic import model_validator

from e8s.choreo.models import Placement
from e8s.choreo.path_models import (
    Easing,
    FacingMode,
    FacingValue,
    PathStyle,
    TurnDirection,
    check_facing_value,
)
from e8s.core.errors import SchemaError
from e8s.core.ids import SpotId, TransitionId
from e8s.core.readonly import ReadOnlyMap
from e8s.core.types import (
    Count,
    Degrees,
    Finite,
    FinitePoint,
    FrozenModel,
    Point,
    PositiveFinite,
)
from e8s.geo.curves import AnyCurve

PieceKind = Literal["hold", "line", "arc", "curve", "route", "custom"]


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
    kind: PieceKind
    params: dict[str, float] = {}
    length: Finite = 0.0


class MotionPlan(FrozenModel):
    """Holds one spot's compiled motion in one transition.

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
        facing_value: Typed value the facing mode reads; None for modes that read none.
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
    facing_value: FacingValue | None = None
    turn_counts: float = 0.0
    turn_direction: TurnDirection = TurnDirection.SHORTEST

    @model_validator(mode="after")
    def _facing_fits_mode(self) -> "MotionPlan":
        check_facing_value(self.facing_mode, self.facing_value)
        return self

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
    p0: FinitePoint
    p1: FinitePoint


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
    position: FinitePoint
    facing: Degrees
    velocity: FinitePoint
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


class GapInfo(FrozenModel):
    """Describes the tightest moment between two spots in a transition.

    Attributes:
        a: First spot (sorted order).
        b: Second spot.
        tau: Local count of closest approach.
        gap: Meters between footprints; negative means they overlap.
    """

    a: SpotId
    b: SpotId
    tau: float
    gap: Finite


class DelayFix(FrozenModel):
    """Holds the arguments of a delay fix.

    >>> DelayFix(entity="T1", counts=2.0).counts
    2.0

    Attributes:
        entity: Spot to hold back.
        counts: Local counts to wait before moving.
    """

    entity: SpotId
    counts: PositiveFinite


class BendFix(FrozenModel):
    """Holds the arguments of a waypoint fix that bends one spot's path.

    >>> BendFix(entity="T1", point=(2.0, 3.0), at=4.0).point
    (2.0, 3.0)

    Attributes:
        entity: Spot whose path bends.
        point: Meters the spot passes.
        at: Local count at which it passes the point.
    """

    entity: SpotId
    point: Point
    at: Finite


class StaggerFix(FrozenModel):
    """Holds the arguments of a stagger fix.

    >>> StaggerFix(order=("T1", "T2"), step=1.0).step
    1.0

    Attributes:
        order: Spots in the order they start.
        step: Local counts between consecutive starts.
    """

    order: tuple[SpotId, ...]
    step: Finite


class ReassignFix(FrozenModel):
    """Marks a reassignment fix; the new targets go through `apply_match`, not the paths."""


type FixParams = DelayFix | BendFix | StaggerFix | ReassignFix
FixKind = Literal["delay", "waypoint", "reassign", "stagger"]
FIX_PARAMS: Final[Mapping[str, type[FixParams]]] = ReadOnlyMap(
    {"delay": DelayFix, "waypoint": BendFix, "stagger": StaggerFix, "reassign": ReassignFix}
)


class Fix(FrozenModel):
    """Describes one suggested change that opens a gap; advisory, the checker decides.

    Attributes:
        kind: delay, waypoint, reassign or stagger.
        params: Arguments for apply_fix, the model of the fix kind.
        new_gap: Gap between the pair after the change.
        max_speed_after: Fastest instant speed of the changed spot after the change, m/count.
    """

    kind: FixKind
    params: FixParams
    new_gap: Finite
    max_speed_after: float

    # A pydantic discriminator cannot key on a sibling field, so kind picks the model here.
    @model_validator(mode="before")
    @classmethod
    def _params_for_kind(cls, data: object) -> object:
        if not isinstance(data, dict) or "params" not in data:
            return data
        kind = data.get("kind")
        model = FIX_PARAMS.get(kind) if isinstance(kind, str) else None
        params = data["params"]
        if model is None or isinstance(params, model):
            return data
        if isinstance(params, FrozenModel):
            raise _mismatched_fix(kind, params)
        return {**data, "params": model._validated(params)}


def _mismatched_fix(kind: object, params: FrozenModel) -> SchemaError:
    return SchemaError(
        f"fix kind {kind!r} cannot hold {type(params).__name__} parameters",
        hint="build the parameters with the model of the fix kind",
        context={"kind": repr(kind), "params": type(params).__name__},
    )
