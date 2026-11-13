#!/usr/bin/env python3

"""
match_cost.py --- builds matching problems and their cost matrices, one mask per constraint

Contains:
    MatchProblem: holds spots, starts, facings and slots for one matching problem
    MatchProblem.permuted(): returns the problem with spots reordered
    build_problem(): builds a problem from a Set or points and slot points or forms
    MatchCosts: holds distances, penalized costs and each constraint's forbidden pairs
    build_costs(): computes the costs and constraint masks of a problem
    combine_masks(): ORs constraint masks into one forbidden-pair mask
    point_tuple(): converts an array row into an (x, y) tuple
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np

from stage.choreo.forms import slots as form_slots
from stage.choreo.match_solve import BoolArray, FloatArray
from stage.choreo.models import Form, MatchOptions, Set
from stage.core.errors import MatchError, SchemaError, UnknownRefError
from stage.core.ids import SpotId
from stage.core.types import Points
from stage.core.units import angle_diff, facing_vector
from stage.core.util import as_points, close_matches

BACKWARD_TOL = 1e-9
SPEED_TOL = 1e-9

type SlotSource = Points | Sequence[Form] | object
type Source = Set | Points | object


@dataclass(frozen=True)
class MatchProblem:
    """Holds one matching problem: spots, their starts and facings, and the slots.

    Attributes:
        entities: Spot IDs, one per start row.
        starts: Start points (N, 2).
        facings: Start facings, or None when the source carried none.
        slots: Slot points (M, 2).
        groups: Spot to group key.
        slot_groups: Group label per slot; None is open to all.
    """

    entities: tuple[SpotId, ...]
    starts: Points
    facings: tuple[float, ...] | None
    slots: Points
    groups: Mapping[SpotId, str]
    slot_groups: tuple[str | None, ...]

    def permuted(self, order: list[int]) -> "MatchProblem":
        """Returns the problem with spots reordered by `order`."""
        facings = None if self.facings is None else tuple(self.facings[i] for i in order)
        entities = tuple(self.entities[i] for i in order)
        return replace(self, entities=entities, starts=self.starts[order], facings=facings)


def _slot_points(to_slots: SlotSource) -> Points:
    if isinstance(to_slots, Sequence) and to_slots and all(isinstance(f, Form) for f in to_slots):
        return np.vstack([form_slots(f) for f in to_slots if isinstance(f, Form)])
    return as_points(to_slots)


def _source(
    from_: Source, entities: Sequence[SpotId] | None
) -> tuple[tuple[SpotId, ...], Points, tuple[float, ...] | None]:
    if isinstance(from_, Set):
        ids = (
            tuple(entities)
            if entities is not None
            else tuple(p.entity for p in from_.placements if p.on_field)
        )
        rows = [from_.get(e) for e in ids]
        return ids, from_.positions(list(ids)), tuple(p.facing for p in rows)
    starts = as_points(from_)
    ids = tuple(entities) if entities is not None else tuple(str(i) for i in range(len(starts)))
    if len(ids) != len(starts):
        raise SchemaError(
            f"{len(ids)} entity ids for {len(starts)} start points",
            hint="give one entity id per start point",
        )
    return ids, starts, None


def _labels(m: int, slot_groups: Sequence[str | None] | None) -> tuple[str | None, ...]:
    if slot_groups is None:
        return (None,) * m
    if len(slot_groups) != m:
        raise SchemaError(
            f"{len(slot_groups)} slot groups for {m} slots", hint="give one label per slot"
        )
    return tuple(slot_groups)


def build_problem(
    from_: Source,
    to_slots: SlotSource,
    entities: Sequence[SpotId] | None,
    groups: Mapping[SpotId, str] | None,
    slot_groups: Sequence[str | None] | None,
) -> MatchProblem:
    """Builds a problem from a Set or start points and slot points or forms.

    >>> build_problem([(0, 0)], [(1, 0), (2, 0)], None, None, None).entities
    ('0',)
    """
    ids, starts, facings = _source(from_, entities)
    if len(set(ids)) != len(ids):
        raise SchemaError("an entity is listed twice", hint="list each spot once")
    slots = _slot_points(to_slots)
    labels = _labels(len(slots), slot_groups)
    return MatchProblem(ids, starts, facings, slots, dict(groups or {}), labels)


def _group_mask(p: MatchProblem, labels: Sequence[str | None]) -> BoolArray:
    reserved = {label for label in labels if label is not None}
    row_groups = [p.groups.get(e) for e in p.entities]
    label_array = np.array([label if label is not None else "" for label in labels], dtype=object)
    mask = np.zeros((len(p.entities), len(labels)), dtype=bool)
    for i, group in enumerate(row_groups):
        differs = label_array != (group or "")
        has_label = np.array([label is not None for label in labels])
        mask[i] = differs & (has_label | (group in reserved))
    return mask


def _reserved_labels(p: MatchProblem, group_slots: Mapping[str, Sequence[int]]) -> list[str | None]:
    labels: list[str | None] = [None] * len(p.slots)
    for group, indexes in sorted(group_slots.items()):
        for j in indexes:
            if not 0 <= j < len(labels) or labels[j] not in (None, group):
                raise SchemaError(
                    f"group_slots gives slot {j} to {group!r} but it is out of range or taken",
                    hint=f"use distinct slot indexes in 0..{len(labels) - 1}",
                    context={"group": group, "slot": j},
                )
            labels[j] = group
    return labels


def _index_of(p: MatchProblem, entity: SpotId) -> int:
    if entity in p.entities:
        return p.entities.index(entity)
    close = close_matches(entity, p.entities)
    raise UnknownRefError(
        f"spot {entity!r} is not being matched",
        hint=f"did you mean {', '.join(close)}?" if close else "pin or forbid matched spots only",
        context={"entity": entity},
    )


def _check_slot(p: MatchProblem, slot: int, what: str) -> int:
    if not 0 <= slot < len(p.slots):
        raise MatchError(
            f"{what} names slot {slot}, outside 0..{len(p.slots) - 1}",
            hint="use a slot index that exists",
            context={"slot": slot, "slots": len(p.slots)},
        )
    return slot


def _pin_mask(p: MatchProblem, pinned: Mapping[SpotId, int]) -> BoolArray:
    mask = np.zeros((len(p.entities), len(p.slots)), dtype=bool)
    taken: dict[int, SpotId] = {}
    for entity, slot in sorted(pinned.items()):
        i, j = _index_of(p, entity), _check_slot(p, slot, f"pin of {entity}")
        if j in taken:
            raise MatchError(
                f"{taken[j]} and {entity} are both pinned to slot {j}",
                hint="pin each slot to one spot",
                context={"slot": j},
            )
        taken[j] = entity
        mask[i] = True
        mask[:, j] = True
        mask[i, j] = False
    for j, entity in taken.items():
        mask[_index_of(p, entity), j] = False
    return mask


def _forbid_mask(p: MatchProblem, forbid: Sequence[tuple[SpotId, int]]) -> BoolArray:
    mask = np.zeros((len(p.entities), len(p.slots)), dtype=bool)
    for entity, slot in forbid:
        mask[_index_of(p, entity), _check_slot(p, slot, f"forbid of {entity}")] = True
    return mask


def _need_facings(p: MatchProblem) -> tuple[float, ...]:
    if p.facings is None:
        raise MatchError(
            "backward and turn rules need each spot's facing",
            hint="match from a Set, whose placements carry facings",
        )
    return p.facings


def _backward(p: MatchProblem, moves: FloatArray) -> FloatArray:
    fronts = np.array([facing_vector(f) for f in _need_facings(p)]).reshape(-1, 2)
    along: FloatArray = np.einsum("imk,ik->im", moves, fronts)
    return np.maximum(-along, 0.0)


def _turn(p: MatchProblem, moves: FloatArray, distance: FloatArray) -> FloatArray:
    facings = np.asarray(_need_facings(p), dtype=float)
    travel = np.degrees(np.arctan2(moves[..., 0], -moves[..., 1]))
    turn = np.abs(np.vectorize(angle_diff)(facings[:, None], travel))
    result: FloatArray = np.where(distance > 0, turn, 0.0)
    return result


def _cost_fn_matrix(p: MatchProblem, options: MatchOptions) -> FloatArray:
    fn = options.cost_fn
    n, m = len(p.entities), len(p.slots)
    if fn is None or n == 0:
        return np.zeros((n, m))
    extra = np.array(
        [
            [
                fn(e, j, (float(p.starts[i, 0]), float(p.starts[i, 1])), point_tuple(p.slots[j]))
                for j in range(m)
            ]
            for i, e in enumerate(p.entities)
        ],
        dtype=float,
    ).reshape(n, m)
    if np.any(extra < 0) or np.any(np.isnan(extra)):
        raise SchemaError("cost_fn returned a negative or NaN cost", hint="return costs >= 0")
    return extra


def point_tuple(row: Points) -> tuple[float, float]:
    """Converts an array row into an (x, y) tuple of floats.

    >>> point_tuple(np.array([1, 2]))
    (1.0, 2.0)
    """
    return float(row[0]), float(row[1])


@dataclass(frozen=True)
class MatchCosts:
    """Holds a problem's distances, final costs and the masks of each constraint.

    Attributes:
        distance: Straight-line meters per (spot, slot).
        cost: Meters plus penalties, infinity where any constraint forbids the pair.
        masks: Constraint name to the pairs it forbids.
    """

    distance: FloatArray
    cost: FloatArray
    masks: dict[str, BoolArray]


def _masks(
    p: MatchProblem, options: MatchOptions, moves: FloatArray, d: FloatArray
) -> dict[str, BoolArray]:
    masks: dict[str, BoolArray] = {}
    if options.keep_groups and any(label is not None for label in p.slot_groups):
        masks["keep_groups"] = _group_mask(p, p.slot_groups)
    if options.group_slots:
        masks["group_slots"] = _group_mask(p, _reserved_labels(p, options.group_slots))
    if options.pinned:
        masks["pinned"] = _pin_mask(p, options.pinned)
    if options.forbid:
        masks["forbid"] = _forbid_mask(p, options.forbid)
    if options.no_backward is True:
        masks["no_backward"] = _backward(p, moves) > BACKWARD_TOL
    if options.max_speed is not None:
        masks["max_speed"] = d / _counts(options) > options.max_speed + SPEED_TOL
    return masks


def _counts(options: MatchOptions) -> float:
    if options.counts is None:
        raise MatchError("max_speed needs counts", hint="set MatchOptions(counts=...)")
    return options.counts


def _penalties(
    p: MatchProblem, options: MatchOptions, moves: FloatArray, d: FloatArray
) -> FloatArray:
    penalty = _cost_fn_matrix(p, options)
    weights = options.weights
    flat = options.no_backward
    if not isinstance(flat, bool) and flat > 0:
        penalty = penalty + flat * (_backward(p, moves) > BACKWARD_TOL)
    if weights.get("backward", 0.0) > 0:
        penalty = penalty + weights["backward"] * _backward(p, moves)
    if weights.get("turn", 0.0) > 0:
        penalty = penalty + weights["turn"] * _turn(p, moves, d)
    return penalty


def build_costs(p: MatchProblem, options: MatchOptions) -> MatchCosts:
    """Computes distances, penalized costs and each constraint's forbidden pairs.

    >>> problem = build_problem([(0, 0)], [(3, 4)], None, None, None)
    >>> build_costs(problem, MatchOptions()).cost.tolist()
    [[5.0]]
    """
    moves = p.slots[None, :, :] - p.starts[:, None, :]
    d = np.linalg.norm(moves, axis=2).reshape(len(p.entities), len(p.slots))
    masks = _masks(p, options, moves, d)
    extra = _penalties(p, options, moves, d)
    if options.cost_fn is not None and np.any(np.isinf(extra)):
        masks["cost_fn"] = np.isinf(extra)
    cost = d + np.where(np.isinf(extra), 0.0, extra)
    forbidden = combine_masks(masks, cost.shape)
    return MatchCosts(distance=d, cost=np.where(forbidden, np.inf, cost), masks=masks)


def combine_masks(masks: Mapping[str, BoolArray], shape: tuple[int, ...]) -> BoolArray:
    """ORs constraint masks into one mask of forbidden pairs.

    >>> combine_masks({"a": np.array([[True, False]])}, (1, 2)).tolist()
    [[True, False]]
    """
    forbidden = np.zeros(shape, dtype=bool)
    for mask in masks.values():
        forbidden |= mask
    return forbidden
