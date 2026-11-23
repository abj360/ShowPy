"""
match_models.py --- matching data classes: options, objectives and results

Contains:
    MATCH_WEIGHT_KEYS: lists the penalty names a match accepts
    MatchWeights: holds the four penalty weights of a match, each a finite number at or above 0
    MatchObjective: names the objectives a match can minimize
    CostFn: types the extra per-pair cost callback
    MatchOptions: holds the constraints, objective and penalties for matching spots to slots
    MatchOptions.to_json(): returns the options as JSON, refusing a set cost_fn
    MatchResult: holds who goes where, with move statistics and an explanation
    MatchResult.target_points(): returns targets as an (N, 2) array in entity order
"""

from collections.abc import Callable, Mapping
from typing import Literal

import numpy as np
from pydantic import Field, StrictInt, model_validator

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError
from e8s.core.ids import FormId, SpotId
from e8s.core.json_types import JsonValue
from e8s.core.types import Finite, FrozenModel, NonNegFinite, Point, Points

type MatchObjective = Literal["sum", "sum_squares", "bottleneck", "bottleneck_then_sum"]
type CostFn = Callable[[str, int, Point, Point], float]


class MatchWeights(FrozenModel):
    """Holds the penalty weights of a match; each is a finite number at or above 0.

    Attributes:
        backward: Meters of penalty per meter of travel against the spot's facing.
        crossing: Meters of penalty per crossing of two straight-line moves.
        group_spread: Meters of penalty per meter of mean distance from a group's target centroid.
        turn: Meters of penalty per degree between the facing and the travel direction.

    >>> MatchWeights(turn=0.5).turn
    0.5
    """

    backward: NonNegFinite = 0.0
    crossing: NonNegFinite = 0.0
    group_spread: NonNegFinite = 0.0
    turn: NonNegFinite = 0.0

    @model_validator(mode="before")
    @classmethod
    def _known_names(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        unknown = sorted(str(name) for name in data if name not in cls._field_names())
        if unknown:
            raise SchemaError(
                f"unknown match weight {unknown[0]!r}",
                hint=f"use weights from {', '.join(cls._field_names())}",
                context={"weights": sorted(str(name) for name in data)},
            )
        return data


MATCH_WEIGHT_KEYS = tuple(MatchWeights._field_names())


class MatchOptions(FrozenModel):
    """Holds the constraints, objective and penalties for matching spots to slots.

    Penalties are in meters and add to a move's length before the objective is applied.

    Attributes:
        keep_groups: Spots only take slots labeled with their own group.
        group_slots: Group key to the slot indexes reserved for it.
        pinned: Forced assignments, spot to slot index.
        forbid: Disallowed (spot, slot index) pairs.
        no_backward: True forbids travel against facing; a number adds that many meters.
        max_speed: Fastest allowed move in meters per count; needs `counts`.
        objective: `sum`, `sum_squares`, `bottleneck` or `bottleneck_then_sum`.
        power: Exponent applied to move costs by the `sum` objective.
        preserve_order: Spots, in the order given, take slots in increasing index order.
        weights: Penalties added to a move's length; see `MatchWeights`.
        cost_fn: Extra cost per (spot, slot index, start, target); infinity forbids the pair.
            Not serialized.
        counts: Counts the move takes, for speed limits and reporting.
    """

    keep_groups: bool = True
    group_slots: dict[str, tuple[StrictInt, ...]] | None = None
    pinned: dict[SpotId, StrictInt] = {}
    forbid: tuple[tuple[SpotId, StrictInt], ...] = ()
    no_backward: bool | NonNegFinite = False
    max_speed: float | None = None
    objective: MatchObjective = "bottleneck_then_sum"
    power: float = 1.0
    preserve_order: bool = False
    weights: MatchWeights = MatchWeights()
    cost_fn: CostFn | None = Field(default=None, exclude=True)
    counts: float | None = None

    @model_validator(mode="after")
    def _valid(self) -> "MatchOptions":
        positive = {"power": self.power, "max_speed": self.max_speed, "counts": self.counts}
        for name, value in positive.items():
            if value is not None and not value > 0:
                raise SchemaError(f"{name} must be above 0", hint=f"give a positive {name}")
        return self

    def to_json(self) -> dict[str, JsonValue]:
        """Returns the options as a JSON-safe dict; options holding a cost function refuse.

        A function has no JSON form, so writing options with `cost_fn` set raises SchemaError
        code NOT_SERIALIZABLE instead of dropping the function without a word.

        >>> MatchOptions(power=2.0).to_json()["power"]
        2.0

        Returns:
            document: Field names mapped to their JSON values.
        """
        if self.cost_fn is not None:
            raise SchemaError(
                "MatchOptions with a cost_fn have no JSON form",
                hint="store the options without cost_fn and pass the function again to match()",
                context={"model": "MatchOptions"},
                code=ErrorCode.NOT_SERIALIZABLE,
            )
        return super().to_json()


class MatchResult(FrozenModel):
    """Holds who goes where, with move statistics and an explanation.

    Attributes:
        entities: Matched spots, in the order the solver used.
        assignment: Spot to slot index.
        targets: Each spot's slot position, aligned with `entities`.
        total_distance: Sum of straight-line move lengths.
        max_distance: Longest straight-line move.
        max_speed: Longest move divided by counts; None when counts are unknown.
        crossings: Pairs of straight-line moves that cross.
        unassigned_slots: Slot indexes nobody takes.
        explain: Plain-language lines describing the result.
        slots: Spot to (form id, slot index in that form); empty when matched onto points.
    """

    entities: tuple[SpotId, ...] = ()
    assignment: dict[SpotId, int] = {}
    targets: tuple[Point, ...] = ()
    total_distance: Finite = 0.0
    max_distance: Finite = 0.0
    max_speed: float | None = None
    crossings: int = 0
    unassigned_slots: tuple[int, ...] = ()
    explain: tuple[str, ...] = ()
    slots: dict[SpotId, tuple[FormId, int]] = {}

    @model_validator(mode="after")
    def _slot_indexes_exist(self) -> "MatchResult":
        indexes = [*self.assignment.values(), *self.unassigned_slots]
        indexes += [slot for _, slot in self.slots.values()]
        negative = sorted({i for i in indexes if i < 0})
        if negative:
            raise SchemaError(
                f"match result names slot {negative[0]}, below 0",
                hint="slot indexes count from 0",
                context={"slots": negative},
            )
        return self

    @model_validator(mode="after")
    def _aligned(self) -> "MatchResult":
        is_misaligned = len(self.entities) != len(self.targets) or set(self.entities) != set(
            self.assignment
        )
        if is_misaligned or (self.slots and set(self.slots) != set(self.assignment)):
            raise SchemaError(
                "match result entities, targets and assignment disagree",
                hint="give one target and one slot per entity",
            )
        return self

    def target_points(self) -> Points:
        """Returns targets as an (N, 2) array in entity order."""
        return np.asarray(self.targets, dtype=float).reshape(-1, 2)
