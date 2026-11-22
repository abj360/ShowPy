"""
match_models.py --- matching data classes: options, objectives and results

Contains:
    MATCH_WEIGHT_KEYS: lists the penalty names a match accepts
    MatchObjective: names the objectives a match can minimize
    CostFn: types the extra per-pair cost callback
    MatchOptions: holds the constraints, objective and penalties for matching spots to slots
    MatchResult: holds who goes where, with move statistics and an explanation
    MatchResult.target_points(): returns targets as an (N, 2) array in entity order
"""

from collections.abc import Callable
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from showpy.core.errors import SchemaError
from showpy.core.ids import FormId, SpotId
from showpy.core.types import Finite, FrozenModel, Point, Points

MATCH_WEIGHT_KEYS = ("backward", "crossing", "group_spread", "turn")

type MatchObjective = Literal["sum", "sum_squares", "bottleneck", "bottleneck_then_sum"]
type CostFn = Callable[[str, int, Point, Point], float]


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
        weights: Penalties: `backward` (per meter against facing), `turn` (per degree of turn
            toward the travel direction), `crossing` (per crossing), `group_spread` (per meter
            of mean distance from the group's target centroid).
        cost_fn: Extra cost per (spot, slot index, start, target); infinity forbids the pair.
            Not serialized.
        counts: Counts the move takes, for speed limits and reporting.
    """

    keep_groups: bool = True
    group_slots: dict[str, tuple[int, ...]] | None = None
    pinned: dict[SpotId, int] = {}
    forbid: tuple[tuple[SpotId, int], ...] = ()
    no_backward: bool | float = False
    max_speed: float | None = None
    objective: MatchObjective = "bottleneck_then_sum"
    power: float = 1.0
    preserve_order: bool = False
    weights: dict[str, float] = {}
    cost_fn: CostFn | None = Field(default=None, exclude=True)
    counts: float | None = None

    @model_validator(mode="after")
    def _valid(self) -> "MatchOptions":
        unknown = sorted(set(self.weights) - set(MATCH_WEIGHT_KEYS))
        if unknown:
            raise SchemaError(
                f"unknown match weight {unknown[0]!r}",
                hint=f"use weights from {', '.join(MATCH_WEIGHT_KEYS)}",
                context={"weights": sorted(self.weights)},
            )
        positive = {"power": self.power, "max_speed": self.max_speed, "counts": self.counts}
        for name, value in positive.items():
            if value is not None and not value > 0:
                raise SchemaError(f"{name} must be above 0", hint=f"give a positive {name}")
        if any(w < 0 for w in self.weights.values()):
            raise SchemaError("match weights must be >= 0", hint="use non-negative weights")
        return self


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
