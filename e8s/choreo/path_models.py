"""
path_models.py --- path data classes: path rows, routes, easing, facing overrides and transitions

Contains:
    DEFAULT_EASING_STRENGTH: sets the exponent or step count an easing starts with
    MIN_EASING_STRENGTH: sets the smallest exponent, divisor or step count an easing takes
    MAX_EASING_STEPS: bounds the step count of a `steps` easing
    MAX_MOTION_CYCLES: bounds the side-to-side cycles of a weave or zigzag
    MAX_SPIRAL_TURNS: bounds the extra full turns of a spiral
    EasingKind: names the easing families
    PathStyle: names the built-in path styles
    FacingMode: names how a spot faces while it moves
    TurnDirection: names which way a facing turn goes
    FixedFacing: holds the heading a fixed facing mode holds
    PointFacing: holds the point a toward_point facing mode looks at
    EntityFacing: holds the spot a toward_entity facing mode looks at
    FacingValue: names the three typed facing values, discriminated by `kind`
    FACING_VALUE_KINDS: maps each facing mode that reads a value to its value kind
    as_facing_value(): wraps a bare heading, point or spot ID in its typed facing value
    check_facing_value(): refuses a facing value whose kind does not fit the facing mode
    FacingOverride: forces a facing on spots over a global count range
    Waypoint: holds a point a path passes through, optionally at a local count
    Easing: maps progress through a move to progress in time
    WeaveParams: holds the parameters of the built-in weave motion
    ZigzagParams: holds the parameters of the built-in zigzag motion
    FigureEightParams: holds the parameters of the built-in figure_eight motion
    SpiralParams: holds the parameters of the built-in spiral motion
    BuiltInMotionParams: names the parameter models of the four built-in motions
    BUILT_IN_MOTION_PARAMS: maps each built-in motion name to its parameter model
    PathRow: holds one spot's instructions for one transition
    Route: holds a shared path for follow-the-leader
    TransitionSpec: holds every path instruction for one transition
    TransitionSpec.row(): returns a spot's row, defaulting to straight
"""

import itertools
import math
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from e8s.core.errors import PathError, SchemaError
from e8s.core.ids import RouteId, SpotId, TransitionId
from e8s.core.readonly import ReadOnly, ReadOnlyMap
from e8s.core.selection import SelectorField
from e8s.core.types import (
    Count,
    Degrees,
    Finite,
    FrozenModel,
    Heading,
    JsonObject,
    Meters,
    NonNegCount,
    Point,
)
from e8s.geo.curves import AnyCurve

DEFAULT_EASING_STRENGTH = 2.0
MIN_EASING_STRENGTH = 1.0
# Ten times the longest transition in the examples and golden shows (16 counts, one step per
# count), rounded up.
MAX_EASING_STEPS = 200
# Ten times the most cycles any example, golden show, doc or test uses (8), rounded up.
MAX_MOTION_CYCLES = 100
# Ten times the most extra spiral turns any example, golden show, doc or test uses (2).
MAX_SPIRAL_TURNS = 20
POINT_LENGTH = 2
DEFAULT_AMPLITUDE_M = 1.0
DEFAULT_CYCLES = 2.0
HALF_CYCLE_REL_TOL = 1e-9
EasingKind = Literal[
    "linear", "ease_in", "ease_out", "ease_in_out", "hold_then_burst", "burst_then_settle",
    "steps", "custom",
]  # fmt: skip


class PathStyle(StrEnum):
    """Names the built-in path styles."""

    STRAIGHT = "straight"
    HOLD = "hold"
    CURVE = "curve"
    POLYLINE = "polyline"
    FOLLOW = "follow"
    ROTATE = "rotate"
    CUSTOM = "custom"


class FacingMode(StrEnum):
    """Names how a spot faces while it moves."""

    FIXED = "fixed"
    TO_TARGET = "to_target"
    TRAVEL = "travel"
    AGAINST_TRAVEL = "against_travel"
    TOWARD_POINT = "toward_point"
    TOWARD_ENTITY = "toward_entity"
    AUDIENCE = "audience"
    INTERPOLATE = "interpolate"


class TurnDirection(StrEnum):
    """Names which way a facing turn goes."""

    SHORTEST = "shortest"
    CLOCKWISE = "clockwise"
    COUNTERCLOCKWISE = "counterclockwise"


class FixedFacing(FrozenModel):
    """Holds the heading a `fixed` facing mode holds.

    >>> FixedFacing(degrees=450.0).degrees
    90.0

    Attributes:
        kind: Tag that tells the facing values apart in stored documents.
        degrees: Heading wrapped to [0, 360); 0 faces the audience, counter-clockwise positive.
    """

    kind: Literal["fixed"] = "fixed"
    degrees: Heading


class PointFacing(FrozenModel):
    """Holds the point a `toward_point` facing mode looks at.

    >>> PointFacing(point=(1.0, 2.0)).point
    (1.0, 2.0)

    Attributes:
        kind: Tag that tells the facing values apart in stored documents.
        point: Meters on the sheet.
    """

    kind: Literal["point"] = "point"
    point: Point


class EntityFacing(FrozenModel):
    """Holds the spot a `toward_entity` facing mode looks at.

    >>> EntityFacing(entity="T2").entity
    'T2'

    Attributes:
        kind: Tag that tells the facing values apart in stored documents.
        entity: Spot ID.
    """

    kind: Literal["entity"] = "entity"
    entity: SpotId


type FacingValue = Annotated[FixedFacing | PointFacing | EntityFacing, Field(discriminator="kind")]
FACING_VALUE_KINDS: Final[Mapping[FacingMode, str]] = ReadOnlyMap(
    {
        FacingMode.FIXED: "fixed",
        FacingMode.TOWARD_POINT: "point",
        FacingMode.TOWARD_ENTITY: "entity",
    }
)


def as_facing_value(value: FacingValue | Degrees | Point | str | None) -> FacingValue | None:
    """Wraps a bare heading, point or spot ID in its typed facing value.

    A typed value or None passes through. Mismatch with a facing mode is checked by the model
    that holds the value, not here.

    Args:
        value: A heading in degrees, an (x, y) point, a spot ID or an already typed value.

    Returns:
        typed: The typed facing value, or None for None.

    >>> as_facing_value(450.0)
    FixedFacing(kind='fixed', degrees=90.0)
    >>> as_facing_value("T2")
    EntityFacing(kind='entity', entity='T2')
    """
    if value is None or isinstance(value, FixedFacing | PointFacing | EntityFacing):
        return value
    if isinstance(value, bool):
        raise _bad_facing_value(value)
    if isinstance(value, int | float):
        return FixedFacing(degrees=value)
    if isinstance(value, str):
        return EntityFacing(entity=SpotId(value))
    if isinstance(value, tuple) and len(value) == POINT_LENGTH:
        return PointFacing(point=value)
    raise _bad_facing_value(value)


def _bad_facing_value(value: object) -> SchemaError:
    return SchemaError(
        f"facing value {value!r} is not a heading, a point or a spot ID",
        hint="pass degrees, an (x, y) point, a spot ID or a FixedFacing, PointFacing or "
        "EntityFacing",
        context={"value": repr(value)},
    )


def check_facing_value(mode: FacingMode, value: FacingValue | None) -> None:
    """Refuses a facing value whose kind does not fit the facing mode.

    Modes that read no value need None; fixed, toward_point and toward_entity need a value of
    kind fixed, point and entity.

    Args:
        mode: The facing mode.
        value: The typed value stored next to it.

    >>> check_facing_value(FacingMode.FIXED, FixedFacing(degrees=90.0))
    """
    needed = FACING_VALUE_KINDS.get(mode)
    held = None if value is None else value.kind
    if held != needed:
        raise SchemaError(
            f"facing mode {mode.value} needs a value of kind {needed}, got {held}",
            hint=_facing_hint(needed),
            context={"mode": mode.value, "kind": held, "needed": needed},
        )


def _facing_hint(needed: str | None) -> str:
    if needed is None:
        return "leave the facing value empty for this mode"
    return f"pass a value of kind {needed}: FixedFacing, PointFacing or EntityFacing"


class FacingOverride(FrozenModel):
    """Forces a facing on spots over a global count range, regardless of paths.

    Attributes:
        entities: Spots affected.
        mode: Facing mode during the range.
        value: Typed value the mode reads; None for modes that read none.
        start: Global count where the override starts.
        end: Global count where it ends.
        turn_counts: Counts spent turning into and out of the override.
    """

    entities: SelectorField
    mode: FacingMode
    value: FacingValue | None = None
    start: Count
    end: Count
    turn_counts: float = 0.0

    @model_validator(mode="after")
    def _ordered(self) -> "FacingOverride":
        check_facing_value(self.mode, self.value)
        if self.end <= self.start:
            raise SchemaError("override end must follow start", hint="use start < end")
        return self


class Waypoint(FrozenModel):
    """Holds a point a path passes through.

    Attributes:
        x: Meters.
        y: Meters.
        at: Local count to pass the point; None keeps constant speed.
    """

    x: Meters
    y: Meters
    at: float | None = None


class Easing(FrozenModel):
    """Maps progress through a move (time share) to progress along it (distance share).

    Attributes:
        kind: Easing family; `custom` uses `points`.
        strength: Exponent or step count, depending on the kind.
        points: Custom curve as (time share, distance share) pairs from (0, 0) to (1, 1).
    """

    kind: EasingKind = "linear"
    strength: Annotated[float, Field(ge=MIN_EASING_STRENGTH)] = DEFAULT_EASING_STRENGTH
    points: tuple[tuple[float, float], ...] = ()

    @model_validator(mode="after")
    def _capped_steps(self) -> "Easing":
        if self.kind == "steps" and round(self.strength) > MAX_EASING_STEPS:
            raise SchemaError(
                f"a steps easing takes at most {MAX_EASING_STEPS} steps, not {self.strength}",
                hint=f"use a strength from {MIN_EASING_STRENGTH:g} to {MAX_EASING_STEPS}",
                context={"strength": self.strength, "cap": MAX_EASING_STEPS},
            )
        return self

    @model_validator(mode="after")
    def _valid_custom(self) -> "Easing":
        if self.kind != "custom":
            return self
        pts = self.points
        ordered = all(b[0] > a[0] and b[1] >= a[1] for a, b in itertools.pairwise(pts))
        if len(pts) < 2 or pts[0] != (0.0, 0.0) or pts[-1] != (1.0, 1.0) or not ordered:
            raise SchemaError(
                "custom easing needs increasing points from (0, 0) to (1, 1)",
                hint="pass points like ((0, 0), (0.5, 0.2), (1, 1))",
            )
        return self


def _half_cycles(cycles: float) -> float:
    if not math.isclose(2 * cycles, round(2 * cycles), rel_tol=HALF_CYCLE_REL_TOL):
        raise SchemaError(
            f"cycles must be a multiple of 0.5 so the move ends on target, not {cycles}",
            hint=f"use cycles like 1, 1.5 or 2, up to {MAX_MOTION_CYCLES}",
            context={"cycles": cycles},
        )
    return cycles


type WaveCycles = Annotated[
    float, Field(gt=0, le=MAX_MOTION_CYCLES, allow_inf_nan=False), AfterValidator(_half_cycles)
]


class WeaveParams(FrozenModel):
    """Holds the parameters of the built-in weave motion: a sinusoidal side-to-side offset.

    >>> WeaveParams(cycles=1.5).amplitude
    1.0

    Attributes:
        amplitude: Side offset in meters; negative starts to the other side.
        cycles: Side-to-side cycles, a positive multiple of 0.5.
    """

    amplitude: Finite = DEFAULT_AMPLITUDE_M
    cycles: WaveCycles = DEFAULT_CYCLES


class ZigzagParams(FrozenModel):
    """Holds the parameters of the built-in zigzag motion: a triangle-wave side offset.

    >>> ZigzagParams(cycles=1).cycles
    1.0

    Attributes:
        amplitude: Side offset in meters; negative starts to the other side.
        cycles: Side-to-side cycles, a positive multiple of 0.5.
    """

    amplitude: Finite = DEFAULT_AMPLITUDE_M
    cycles: WaveCycles = DEFAULT_CYCLES


class FigureEightParams(FrozenModel):
    """Holds the parameters of the built-in figure_eight motion.

    >>> FigureEightParams().amplitude
    1.0

    Attributes:
        amplitude: Side offset in meters; negative mirrors the loops.
    """

    amplitude: Finite = DEFAULT_AMPLITUDE_M


class SpiralParams(FrozenModel):
    """Holds the parameters of the built-in spiral motion: half a turn plus whole extra turns.

    >>> SpiralParams(turns=2).clockwise
    False

    Attributes:
        turns: Extra full turns around the midpoint of the move.
        clockwise: Turns clockwise instead of counter-clockwise.
    """

    turns: Annotated[int, Field(ge=0, le=MAX_SPIRAL_TURNS)] = 0
    clockwise: bool = False


type BuiltInMotionParams = WeaveParams | ZigzagParams | FigureEightParams | SpiralParams
BUILT_IN_MOTION_PARAMS: Final[Mapping[str, type[BuiltInMotionParams]]] = ReadOnlyMap(
    {
        "weave": WeaveParams,
        "zigzag": ZigzagParams,
        "figure_eight": FigureEightParams,
        "spiral": SpiralParams,
    }
)


def _mismatched_params(motion_fn: object, params: BaseModel) -> SchemaError:
    return SchemaError(
        f"motion {motion_fn!r} cannot take {type(params).__name__}",
        hint="pass the parameters as a JSON object, or set motion_fn to the motion they belong to",
        context={"motion_fn": repr(motion_fn), "params": type(params).__name__},
    )


def _typed_motion_params(motion_fn: object, params: object) -> object:
    """Returns the parameters `motion_fn` takes: its built-in model, or the JSON object as is."""
    model = BUILT_IN_MOTION_PARAMS.get(motion_fn) if isinstance(motion_fn, str) else None
    if model is None:
        if isinstance(params, BaseModel):
            raise _mismatched_params(motion_fn, params)
        return params
    if isinstance(params, model):
        return params
    if isinstance(params, BaseModel):
        raise _mismatched_params(motion_fn, params)
    return model.model_validate(dict(params) if isinstance(params, Mapping) else params)


class PathRow(FrozenModel):
    """Holds one spot's instructions for one transition; times are local counts.

    Attributes:
        entity: Spot ID.
        style: Path style.
        start: Local count the move starts.
        end: Local count the move ends; None means the end of the transition.
        waypoints: Points passed on the way, for CURVE and POLYLINE.
        route: Route followed, for FOLLOW.
        pivot: Rotation center, for ROTATE.
        sweep_deg: Signed rotation, for ROTATE; positive is counter-clockwise.
        facing_mode: How the spot faces during the move.
        facing_value: Typed value the facing mode reads; None for modes that read none.
        turn_counts: Counts spent turning into and out of the move's facing.
        turn_direction: Which way turns go.
        pace: Step pace multiplier for step-size reporting; positions ignore it.
        locked: Designer lock.
        note: Free text.
        motion_fn: Registered motion function, for CUSTOM.
        motion_params: The built-in motion's typed parameters, or a read-only JSON object for a
            registered custom motion.
        easing: How progress maps to time across the window.
    """

    entity: SpotId
    style: PathStyle = PathStyle.STRAIGHT
    start: float = 0.0
    end: float | None = None
    waypoints: tuple[Waypoint, ...] = ()
    route: RouteId | None = None
    pivot: Point | None = None
    sweep_deg: Degrees | None = None
    facing_mode: FacingMode = FacingMode.TO_TARGET
    facing_value: FacingValue | None = None
    turn_counts: float = 0.0
    turn_direction: TurnDirection = TurnDirection.SHORTEST
    pace: float = 1.0
    locked: bool = False
    note: str | None = None
    motion_fn: str | None = None
    motion_params: Annotated[
        JsonObject | BuiltInMotionParams, Field(union_mode="left_to_right")
    ] = Field(default_factory=dict, validate_default=True)
    easing: Easing = Easing()

    # A pydantic discriminator cannot key on a sibling field, so motion_fn picks the model here.
    @model_validator(mode="before")
    @classmethod
    def _motion_params_for_motion(cls, data: object) -> object:
        if not isinstance(data, dict) or ("motion_params" not in data and "motion_fn" not in data):
            return data
        params = _typed_motion_params(data.get("motion_fn"), data.get("motion_params", {}))
        return {**data, "motion_params": params}

    @field_validator("start", mode="before")
    @classmethod
    def _finite_start(cls, value: object) -> object:
        if isinstance(value, int | float) and not math.isfinite(value):
            raise PathError(
                f"path start must be a finite count, got {value}",
                hint="use 0 <= start < end",
                context={"start": value},
            )
        return value

    @model_validator(mode="after")
    def _facing_fits_mode(self) -> "PathRow":
        check_facing_value(self.facing_mode, self.facing_value)
        return self

    @model_validator(mode="after")
    def _window(self) -> "PathRow":
        if self.start < 0 or (self.end is not None and self.end <= self.start):
            raise PathError(
                f"path window for {self.entity} is invalid",
                hint="use 0 <= start < end",
                context={"start": self.start, "end": self.end},
            )
        if self.turn_counts < 0 or self.pace <= 0:
            raise PathError("turn_counts must be >= 0 and pace > 0", hint="fix the row")
        return self

    def window(self, counts: float) -> tuple[float, float]:
        """Returns (start, end) in local counts, filling a missing end with `counts`."""
        return self.start, counts if self.end is None else self.end


class Route(FrozenModel):
    """Holds a shared path for follow-the-leader.

    Members start on the route at arc lengths `offsets` and all advance `travel` meters.

    Attributes:
        id: Route ID.
        curve: Path the route follows, any curve kind.
        members: Spots on the route, leader first.
        offsets: Each member's starting arc length on the route.
        travel: Meters every member advances.
        start: Local count the move starts.
        end: Local count the move ends; None means the end of the transition.
    """

    id: RouteId
    curve: AnyCurve
    members: tuple[SpotId, ...]
    offsets: tuple[Meters, ...]
    travel: Meters
    start: float = 0.0
    end: float | None = None

    @model_validator(mode="after")
    def _aligned(self) -> "Route":
        if len(self.members) != len(self.offsets):
            raise PathError("route needs one offset per member", hint="align members and offsets")
        return self


class TransitionSpec(FrozenModel):
    """Holds every path instruction for one transition.

    Rows are sparse: a spot without a row moves straight over the full counts.

    Attributes:
        id: (from set, to set).
        counts: Length of the transition; equals the destination set's counts.
        rows: Spot ID to path row, read-only; every key equals its row's spot.
        routes: Route ID to route, read-only; every key equals its route's ID.
        notes: Free text.
        source_hash: Hash of the script that wrote the paths.
    """

    id: TransitionId
    counts: NonNegCount
    rows: Annotated[Mapping[SpotId, PathRow], ReadOnly()] = Field(default={}, validate_default=True)
    routes: Annotated[Mapping[RouteId, Route], ReadOnly()] = Field(
        default={}, validate_default=True
    )
    notes: str = ""
    source_hash: str = ""

    @model_validator(mode="after")
    def _keys_match(self) -> "TransitionSpec":
        for key, row in self.rows.items():
            if key != row.entity:
                raise SchemaError(f"row key {key} holds {row.entity}", hint="key rows by entity")
        for route_key, route in self.routes.items():
            if route_key != route.id:
                raise SchemaError(
                    f"route key {route_key} holds {route.id}",
                    hint="key routes by their ID",
                    context={"key": route_key, "id": route.id},
                )
        return self

    def row(self, entity: str) -> PathRow:
        """Returns the spot's row, or a default straight row when it has none."""
        if entity in self.rows:
            return self.rows[SpotId(entity)]
        return PathRow.model_validate({"entity": entity})
