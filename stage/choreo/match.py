#!/usr/bin/env python3

"""
match.py --- decides who goes where: deterministic, exact assignment of spots to slots

Costs are straight-line meters plus optional penalties (MatchOptions). Forbidden pairs cost
infinity. Objectives: `sum` (total cost**power), `sum_squares`, `bottleneck` (smallest
possible largest move, found exactly by threshold search) and `bottleneck_then_sum` (that
largest move, then the shortest total among assignments meeting it). Spots are solved in
natural ID order, so the result never depends on input order.

Contains:
    cost_matrix(): returns the cost of every (spot, slot) pair, infinity where forbidden
    bottleneck_value(): returns the smallest possible largest cost of a full assignment
    match(): assigns spots to slots under the options' constraints and objective
    match_by_order(): assigns the i-th spot to the i-th slot
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np

from stage.choreo.forms import slots as form_slots
from stage.choreo.match_solve import (
    BoolArray,
    FloatArray,
    IntArray,
    SwapScore,
    count_crossings,
    improve_by_swaps,
    is_feasible,
    min_max_threshold,
    solve_ordered,
    solve_sum,
)
from stage.choreo.models import Form, MatchOptions, MatchResult, Set
from stage.core.errors import MatchError, SchemaError, UnknownRefError
from stage.core.ids import SpotId
from stage.core.types import Points
from stage.core.units import angle_diff, facing_vector
from stage.core.util import as_points, close_matches, natural_sort

BACKWARD_TOL = 1e-9
SPEED_TOL = 1e-9
BOTTLENECK_OBJECTIVES = frozenset({"bottleneck", "bottleneck_then_sum"})

type SlotSource = Points | Sequence[Form] | object
type Source = Set | Points | object


@dataclass(frozen=True)
class _Problem:
    entities: tuple[SpotId, ...]
    starts: Points
    facings: tuple[float, ...] | None
    slots: Points
    groups: Mapping[SpotId, str]
    slot_groups: tuple[str | None, ...]

    def permuted(self, order: list[int]) -> "_Problem":
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


def _problem(
    from_: Source,
    to_slots: SlotSource,
    entities: Sequence[SpotId] | None,
    groups: Mapping[SpotId, str] | None,
    slot_groups: Sequence[str | None] | None,
) -> _Problem:
    ids, starts, facings = _source(from_, entities)
    if len(set(ids)) != len(ids):
        raise SchemaError("an entity is listed twice", hint="list each spot once")
    slots = _slot_points(to_slots)
    labels = _labels(len(slots), slot_groups)
    return _Problem(ids, starts, facings, slots, dict(groups or {}), labels)


def _group_mask(p: _Problem, labels: Sequence[str | None]) -> BoolArray:
    reserved = {label for label in labels if label is not None}
    row_groups = [p.groups.get(e) for e in p.entities]
    label_array = np.array([label if label is not None else "" for label in labels], dtype=object)
    mask = np.zeros((len(p.entities), len(labels)), dtype=bool)
    for i, group in enumerate(row_groups):
        differs = label_array != (group or "")
        has_label = np.array([label is not None for label in labels])
        mask[i] = differs & (has_label | (group in reserved))
    return mask


def _reserved_labels(p: _Problem, group_slots: Mapping[str, Sequence[int]]) -> list[str | None]:
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


def _index_of(p: _Problem, entity: SpotId) -> int:
    if entity in p.entities:
        return p.entities.index(entity)
    close = close_matches(entity, p.entities)
    raise UnknownRefError(
        f"spot {entity!r} is not being matched",
        hint=f"did you mean {', '.join(close)}?" if close else "pin or forbid matched spots only",
        context={"entity": entity},
    )


def _check_slot(p: _Problem, slot: int, what: str) -> int:
    if not 0 <= slot < len(p.slots):
        raise MatchError(
            f"{what} names slot {slot}, outside 0..{len(p.slots) - 1}",
            hint="use a slot index that exists",
            context={"slot": slot, "slots": len(p.slots)},
        )
    return slot


def _pin_mask(p: _Problem, pinned: Mapping[SpotId, int]) -> BoolArray:
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


def _forbid_mask(p: _Problem, forbid: Sequence[tuple[SpotId, int]]) -> BoolArray:
    mask = np.zeros((len(p.entities), len(p.slots)), dtype=bool)
    for entity, slot in forbid:
        mask[_index_of(p, entity), _check_slot(p, slot, f"forbid of {entity}")] = True
    return mask


def _need_facings(p: _Problem) -> tuple[float, ...]:
    if p.facings is None:
        raise MatchError(
            "backward and turn rules need each spot's facing",
            hint="match from a Set, whose placements carry facings",
        )
    return p.facings


def _backward(p: _Problem, moves: FloatArray) -> FloatArray:
    fronts = np.array([facing_vector(f) for f in _need_facings(p)]).reshape(-1, 2)
    along: FloatArray = np.einsum("imk,ik->im", moves, fronts)
    return np.maximum(-along, 0.0)


def _turn(p: _Problem, moves: FloatArray, distance: FloatArray) -> FloatArray:
    facings = np.asarray(_need_facings(p), dtype=float)
    travel = np.degrees(np.arctan2(moves[..., 0], -moves[..., 1]))
    turn = np.abs(np.vectorize(angle_diff)(facings[:, None], travel))
    result: FloatArray = np.where(distance > 0, turn, 0.0)
    return result


def _cost_fn_matrix(p: _Problem, options: MatchOptions) -> FloatArray:
    fn = options.cost_fn
    n, m = len(p.entities), len(p.slots)
    if fn is None or n == 0:
        return np.zeros((n, m))
    extra = np.array(
        [
            [
                fn(e, j, (float(p.starts[i, 0]), float(p.starts[i, 1])), _pt(p.slots[j]))
                for j in range(m)
            ]
            for i, e in enumerate(p.entities)
        ],
        dtype=float,
    ).reshape(n, m)
    if np.any(extra < 0) or np.any(np.isnan(extra)):
        raise SchemaError("cost_fn returned a negative or NaN cost", hint="return costs >= 0")
    return extra


def _pt(row: Points) -> tuple[float, float]:
    return float(row[0]), float(row[1])


@dataclass(frozen=True)
class _Costs:
    distance: FloatArray
    cost: FloatArray
    masks: dict[str, BoolArray]


def _masks(
    p: _Problem, options: MatchOptions, moves: FloatArray, d: FloatArray
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


def _penalties(p: _Problem, options: MatchOptions, moves: FloatArray, d: FloatArray) -> FloatArray:
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


def _costs(p: _Problem, options: MatchOptions) -> _Costs:
    moves = p.slots[None, :, :] - p.starts[:, None, :]
    d = np.linalg.norm(moves, axis=2).reshape(len(p.entities), len(p.slots))
    masks = _masks(p, options, moves, d)
    extra = _penalties(p, options, moves, d)
    if options.cost_fn is not None and np.any(np.isinf(extra)):
        masks["cost_fn"] = np.isinf(extra)
    cost = d + np.where(np.isinf(extra), 0.0, extra)
    forbidden = _combined(masks, cost.shape)
    return _Costs(distance=d, cost=np.where(forbidden, np.inf, cost), masks=masks)


def _combined(masks: Mapping[str, BoolArray], shape: tuple[int, ...]) -> BoolArray:
    forbidden = np.zeros(shape, dtype=bool)
    for mask in masks.values():
        forbidden |= mask
    return forbidden


def cost_matrix(
    from_: Source,
    to_slots: SlotSource,
    options: MatchOptions | None = None,
    entities: Sequence[SpotId] | None = None,
    groups: Mapping[SpotId, str] | None = None,
    slot_groups: Sequence[str | None] | None = None,
) -> FloatArray:
    """Returns the (spots x slots) cost: meters plus penalties, infinity where forbidden.

    Rows follow `entities` (or the source's order); the objective's power is not applied.

    >>> cost_matrix([(0, 0)], [(3, 4), (0, 1)]).tolist()
    [[5.0, 1.0]]
    """
    p = _problem(from_, to_slots, entities, groups, slot_groups)
    return _costs(p, options or MatchOptions()).cost


def bottleneck_value(cost: object) -> float:
    """Returns the smallest possible largest cost over full assignments of rows to columns.

    >>> bottleneck_value([[1.0, 9.0], [2.0, 3.0]])
    3.0
    """
    matrix = np.asarray(cost, dtype=float)
    if matrix.ndim != 2 or not is_feasible(np.isfinite(matrix)):
        raise MatchError(
            "no full assignment exists for this cost matrix",
            hint="give a 2D matrix with rows <= columns and enough finite entries",
            context={"shape": matrix.shape},
        )
    if matrix.shape[0] == 0:
        return 0.0
    return min_max_threshold(matrix)


def _feasible(allowed: BoolArray, ordered: bool) -> bool:
    if not ordered or allowed.shape[0] == 0:
        return is_feasible(allowed)
    return len(solve_ordered(np.where(allowed, 0.0, np.inf))) > 0


def _require_feasible(p: _Problem, costs: _Costs, ordered: bool) -> None:
    n, m = costs.cost.shape
    if n > m:
        raise MatchError(
            f"{n} spots but only {m} slots",
            hint=f"add {n - m} slots or match fewer spots",
            context={"spots": n, "slots": m},
        )
    if _feasible(np.isfinite(costs.cost), ordered):
        return
    names = list(costs.masks) + (["preserve_order"] if ordered else [])
    blocking = [name for name in names if _relaxed_feasible(costs, name, ordered)]
    stuck = [p.entities[i] for i in range(n) if not np.isfinite(costs.cost[i]).any()]
    culprits = blocking or names
    suffix = f"; {', '.join(stuck)} have no allowed slot" if stuck else ""
    raise MatchError(
        f"no assignment satisfies {', '.join(culprits)}",
        hint=f"relax {' or '.join(culprits)}{suffix}",
        context={"blocking": culprits, "spots_without_slot": stuck},
    )


def _relaxed_feasible(costs: _Costs, name: str, ordered: bool) -> bool:
    kept = {k: v for k, v in costs.masks.items() if k != name}
    allowed = ~_combined(kept, costs.cost.shape)
    return _feasible(allowed, ordered and name != "preserve_order")


def _objective_cost(cost: FloatArray, options: MatchOptions) -> FloatArray:
    if options.objective == "sum":
        powered: FloatArray = cost**options.power
        return powered
    if options.objective == "sum_squares":
        squared: FloatArray = cost**2
        return squared
    return cost


def _solve(cost: FloatArray, options: MatchOptions) -> tuple[IntArray, float]:
    ordered = options.preserve_order
    if options.objective not in BOTTLENECK_OBJECTIVES:
        weighted = _objective_cost(cost, options)
        return (solve_ordered(weighted) if ordered else solve_sum(weighted)), np.inf
    cap = min_max_threshold(cost, ordered)
    capped = np.where(cost <= cap, cost, np.inf)
    if options.objective == "bottleneck_then_sum":
        return (solve_ordered(capped) if ordered else solve_sum(capped)), cap
    if ordered:
        return solve_ordered(cost, "max"), cap
    return solve_sum(np.where(cost <= cap, 0.0, 1.0)), cap


def _row_groups(p: _Problem) -> IntArray:
    names = sorted({p.groups[e] for e in p.entities if e in p.groups})
    index = {name: k for k, name in enumerate(names)}
    return np.array([index.get(p.groups.get(e, ""), -1) for e in p.entities], dtype=np.intp)


def _improve(
    p: _Problem, cost: FloatArray, cols: IntArray, cap: float, options: MatchOptions
) -> tuple[IntArray, int]:
    crossing = options.weights.get("crossing", 0.0)
    spread = options.weights.get("group_spread", 0.0)
    if options.preserve_order or not (crossing or spread) or len(cols) < 2:
        return cols, 0
    frozen = np.array([e in options.pinned for e in p.entities], dtype=bool)
    score = SwapScore(
        _objective_cost(cost, options), cost, cap, p.starts, p.slots, crossing, spread,
        _row_groups(p), frozen,
    )  # fmt: skip
    return improve_by_swaps(cols, score)


def _explain(
    options: MatchOptions, result_max: float, total: float, swaps: int, unassigned: int
) -> tuple[str, ...]:
    lines = [f"objective {options.objective}: largest move {result_max:.2f} m, total {total:.2f} m"]
    lines += [f"pinned {e} to slot {j}" for e, j in sorted(options.pinned.items())]
    if swaps:
        lines.append(f"{swaps} swaps lowered crossing or spread penalties")
    if unassigned:
        lines.append(f"{unassigned} slots left open")
    return tuple(lines)


def _result(
    p: _Problem, costs: _Costs, cols: IntArray, options: MatchOptions, swaps: int
) -> MatchResult:
    rows = np.arange(len(cols))
    moves = costs.distance[rows, cols] if len(cols) else np.zeros(0)
    targets = p.slots[cols] if len(cols) else np.zeros((0, 2))
    largest = float(moves.max()) if len(moves) else 0.0
    total = float(moves.sum())
    unassigned = tuple(sorted(set(range(len(p.slots))) - {int(c) for c in cols}))
    return MatchResult(
        entities=p.entities,
        assignment={e: int(c) for e, c in zip(p.entities, cols, strict=True)},
        targets=tuple(_pt(t) for t in targets),
        total_distance=total,
        max_distance=largest,
        max_speed=None if options.counts is None else largest / options.counts,
        crossings=count_crossings(p.starts, targets),
        unassigned_slots=unassigned,
        explain=_explain(options, largest, total, swaps, len(unassigned)),
    )


def match(
    from_: Source,
    to_slots: SlotSource,
    entities: Sequence[SpotId] | None = None,
    options: MatchOptions | None = None,
    groups: Mapping[SpotId, str] | None = None,
    slot_groups: Sequence[str | None] | None = None,
) -> MatchResult:
    """Assigns spots to slots, exactly and deterministically, under the options.

    Args:
        from_: A Set (its on-field spots, with facings) or start points (N, 2).
        to_slots: Slot points (M, 2), M >= N, or forms whose slots are concatenated.
        entities: Spots to match; defaults to the set's on-field spots, or "0".."N-1".
            With `preserve_order`, this order is the order kept along the slots.
        options: Constraints, objective and penalties.
        groups: Spot to group key, for `keep_groups`, `group_slots` and `group_spread`.
        slot_groups: Group label per slot (None = open to all), for `keep_groups`.

    Returns:
        result: Assignment, targets, move statistics and an explanation.

    >>> r = match([(0, 0), (10, 0)], [(10, 1), (0, 1)])
    >>> r.assignment, r.max_distance
    ({'0': 1, '1': 0}, 1.0)
    """
    opts = options or MatchOptions()
    p = _problem(from_, to_slots, entities, groups, slot_groups)
    if not opts.preserve_order:
        index = {e: i for i, e in enumerate(p.entities)}
        p = p.permuted([index[e] for e in natural_sort(p.entities)])
    costs = _costs(p, opts)
    _require_feasible(p, costs, opts.preserve_order)
    cols, cap = _solve(costs.cost, opts)
    cols, swaps = _improve(p, costs.cost, cols, cap, opts)
    return _result(p, costs, cols, opts, swaps)


def match_by_order(
    ordered_sel: Sequence[SpotId], form: Form | Points, from_: Set | None = None
) -> MatchResult:
    """Assigns the i-th spot to the i-th slot; distances need the starting set.

    >>> from stage.choreo.forms import line_form
    >>> match_by_order(["B", "A"], line_form("f", [], (0, 0), (2, 0), n=2)).assignment
    {'B': 0, 'A': 1}
    """
    slots = form_slots(form) if isinstance(form, Form) else as_points(form)
    ids = tuple(ordered_sel)
    if len(ids) > len(slots):
        raise MatchError(
            f"{len(ids)} spots but only {len(slots)} slots",
            hint="select fewer spots or add slots",
            context={"spots": len(ids), "slots": len(slots)},
        )
    starts = slots[: len(ids)] if from_ is None else from_.positions(list(ids))
    p = _Problem(ids, starts, None, slots, {}, (None,) * len(slots))
    costs = _costs(p, MatchOptions(keep_groups=False))
    result = _result(p, costs, np.arange(len(ids), dtype=np.intp), MatchOptions(), 0)
    if from_ is None:
        note = "no starting set given; distances are reported as 0"
        return result.model_copy(update={"explain": (*result.explain, note)})
    return result
