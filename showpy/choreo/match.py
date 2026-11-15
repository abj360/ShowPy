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

import numpy as np

from showpy.choreo.forms import slots as form_slots
from showpy.choreo.match_cost import (
    MatchCosts,
    MatchProblem,
    SlotSource,
    Source,
    build_costs,
    build_problem,
    combine_masks,
    point_tuple,
)
from showpy.choreo.match_solve import (
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
from showpy.choreo.models import Form, MatchOptions, MatchResult, Set
from showpy.core.errors import MatchError
from showpy.core.ids import SpotId
from showpy.core.types import Points
from showpy.core.util import as_points, natural_sort

BOTTLENECK_OBJECTIVES = frozenset({"bottleneck", "bottleneck_then_sum"})


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
    p = build_problem(from_, to_slots, entities, groups, slot_groups)
    return build_costs(p, options or MatchOptions()).cost


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


def _require_feasible(p: MatchProblem, costs: MatchCosts, ordered: bool) -> None:
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


def _relaxed_feasible(costs: MatchCosts, name: str, ordered: bool) -> bool:
    kept = {k: v for k, v in costs.masks.items() if k != name}
    allowed = ~combine_masks(kept, costs.cost.shape)
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


def _row_groups(p: MatchProblem) -> IntArray:
    names = sorted({p.groups[e] for e in p.entities if e in p.groups})
    index = {name: k for k, name in enumerate(names)}
    return np.array([index.get(p.groups.get(e, ""), -1) for e in p.entities], dtype=np.intp)


def _improve(
    p: MatchProblem, cost: FloatArray, cols: IntArray, cap: float, options: MatchOptions
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
    p: MatchProblem, costs: MatchCosts, cols: IntArray, options: MatchOptions, swaps: int
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
        targets=tuple(point_tuple(t) for t in targets),
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
    p = build_problem(from_, to_slots, entities, groups, slot_groups)
    if not opts.preserve_order:
        index = {e: i for i, e in enumerate(p.entities)}
        p = p.permuted([index[e] for e in natural_sort(p.entities)])
    costs = build_costs(p, opts)
    _require_feasible(p, costs, opts.preserve_order)
    cols, cap = _solve(costs.cost, opts)
    cols, swaps = _improve(p, costs.cost, cols, cap, opts)
    return _result(p, costs, cols, opts, swaps)


def match_by_order(
    ordered_sel: Sequence[SpotId], form: Form | Points, from_: Set | None = None
) -> MatchResult:
    """Assigns the i-th spot to the i-th slot; distances need the starting set.

    >>> from showpy.choreo.forms import line_form
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
    p = MatchProblem(ids, starts, None, slots, {}, (None,) * len(slots))
    costs = build_costs(p, MatchOptions(keep_groups=False))
    result = _result(p, costs, np.arange(len(ids), dtype=np.intp), MatchOptions(), 0)
    if from_ is None:
        note = "no starting set given; distances are reported as 0"
        return result.model_copy(update={"explain": (*result.explain, note)})
    return result
