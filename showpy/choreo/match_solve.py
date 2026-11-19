"""
match_solve.py --- exact assignment solvers: min-sum, bottleneck, order-preserving, swap repair

Rows are entities and columns are slots, with rows <= columns. An infinite cost forbids a
pair. Every solver is deterministic for a given matrix.

Contains:
    is_feasible(): reports whether every row can take a distinct allowed column
    solve_sum(): returns the min-sum assignment (SciPy's linear_sum_assignment)
    min_max_threshold(): returns the smallest achievable largest cost (threshold search)
    solve_ordered(): returns the best assignment whose columns increase with the row
    crossing_mask(): flags which straight moves properly cross one given move
    count_crossings(): counts properly crossing pairs among straight moves
    SwapScore: holds what the swap repair pass scores an assignment by
    improve_by_swaps(): lowers penalties by pairwise swaps and moves to free slots
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import linear_sum_assignment

from showpy.core.types import Points

type FloatArray = npt.NDArray[np.float64]
type IntArray = npt.NDArray[np.intp]
type BoolArray = npt.NDArray[np.bool_]

IMPROVE_TOL = 1e-9
MAX_SWAP_PASSES = 25


def is_feasible(allowed: BoolArray) -> bool:
    """Reports whether every row can take a distinct allowed column.

    >>> is_feasible(np.array([[True, False], [True, False]]))
    False
    """
    rows, cols = allowed.shape
    if rows == 0:
        return True
    if rows > cols:
        return False
    blocked = (~allowed).astype(float)
    r, c = linear_sum_assignment(blocked)
    return bool(blocked[r, c].sum() == 0)


def solve_sum(cost: FloatArray) -> IntArray:
    """Returns the column for each row minimizing the total cost; needs a feasible matrix.

    >>> solve_sum(np.array([[1.0, 5.0], [1.0, 2.0]])).tolist()
    [0, 1]
    """
    if cost.shape[0] == 0:
        return np.zeros(0, dtype=np.intp)
    _, cols = linear_sum_assignment(cost)
    return np.asarray(cols, dtype=np.intp)


def min_max_threshold(cost: FloatArray, *, ordered: bool = False) -> float:
    """Returns the smallest t such that a full assignment uses only costs <= t.

    Binary search over the sorted unique finite costs, testing each threshold for a full
    assignment (bipartite feasibility, or the order-preserving DP when `ordered`).

    >>> min_max_threshold(np.array([[1.0, 9.0], [2.0, 3.0]]))
    3.0
    """
    values = np.unique(cost[np.isfinite(cost)])
    if cost.shape[0] == 0 or len(values) == 0:
        return 0.0
    lo, hi = 0, len(values) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _feasible_under(cost, float(values[mid]), ordered=ordered):
            hi = mid
        else:
            lo = mid + 1
    return float(values[lo])


def _feasible_under(cost: FloatArray, threshold: float, *, ordered: bool) -> bool:
    allowed = cost <= threshold
    if not ordered:
        return is_feasible(allowed)
    indicator = np.where(allowed, 0.0, np.inf)
    return bool(np.isfinite(_ordered_table(indicator, "sum")[0][-1]).any())


def _prefix_best(row: FloatArray) -> tuple[FloatArray, IntArray]:
    running = np.minimum.accumulate(row)
    is_new = np.concatenate([[True], row[1:] < running[:-1]])
    arg = np.maximum.accumulate(np.where(is_new, np.arange(len(row)), 0))
    best = np.concatenate([[np.inf], running[:-1]])
    return best, np.concatenate([[0], arg[:-1]]).astype(np.intp)


def _ordered_table(cost: FloatArray, combine: Literal["sum", "max"]) -> tuple[FloatArray, IntArray]:
    rows, cols = cost.shape
    table = np.full((rows, cols), np.inf)
    back = np.zeros((rows, cols), dtype=np.intp)
    table[0] = cost[0]
    for i in range(1, rows):
        best, arg = _prefix_best(table[i - 1])
        table[i] = best + cost[i] if combine == "sum" else np.maximum(best, cost[i])
        back[i] = arg
    return table, back


def solve_ordered(cost: FloatArray, combine: Literal["sum", "max"] = "sum") -> IntArray:
    """Returns increasing columns minimizing the summed (or largest) cost, by DP in O(n·m).

    Ties go to the smallest column. Returns an empty array when no ordered assignment exists.

    >>> solve_ordered(np.array([[0.0, 1.0, 5.0], [9.0, 0.0, 1.0]])).tolist()
    [0, 1]
    """
    rows = cost.shape[0]
    if rows == 0:
        return np.zeros(0, dtype=np.intp)
    table, back = _ordered_table(cost, combine)
    last = int(np.argmin(table[-1]))
    if not np.isfinite(table[-1, last]):
        return np.zeros(0, dtype=np.intp)
    cols = [last]
    for i in range(rows - 1, 0, -1):
        cols.append(int(back[i, cols[-1]]))
    return np.asarray(cols[::-1], dtype=np.intp)


def _orient(a: Points, b: Points, c: Points) -> FloatArray:
    d1, d2 = b - a, c - a
    return np.sign(d1[..., 0] * d2[..., 1] - d1[..., 1] * d2[..., 0])


def crossing_mask(p: Points, q: Points, starts: Points, ends: Points) -> BoolArray:
    """Flags moves starts[i]→ends[i] that properly cross the move p→q.

    A proper crossing meets strictly inside both moves; touching ends, shared endpoints,
    collinear overlaps and zero-length moves do not count.

    >>> crossing_mask(np.array([0.0, 0.0]), np.array([2.0, 2.0]),
    ...     np.array([[0.0, 2.0]]), np.array([[2.0, 0.0]])).tolist()
    [True]
    """
    d1 = _orient(p, q, starts)
    d2 = _orient(p, q, ends)
    d3 = _orient(starts, ends, np.broadcast_to(p, starts.shape))
    d4 = _orient(starts, ends, np.broadcast_to(q, starts.shape))
    mask: BoolArray = (d1 * d2 < 0) & (d3 * d4 < 0)
    return mask


def count_crossings(starts: Points, ends: Points) -> int:
    """Counts pairs of straight moves that properly cross.

    >>> count_crossings(np.array([[0.0, 0.0], [0.0, 2.0]]), np.array([[2.0, 2.0], [2.0, 0.0]]))
    1
    """
    total = 0
    for i in range(len(starts) - 1):
        total += int(crossing_mask(starts[i], ends[i], starts[i + 1 :], ends[i + 1 :]).sum())
    return total


@dataclass(frozen=True)
class SwapScore:
    """Holds what the swap repair pass scores an assignment by.

    Attributes:
        move_cost: Objective cost per (row, column); infinity forbids.
        raw_cost: Untransformed cost compared against `cap`.
        cap: Largest raw cost allowed, to keep a bottleneck; infinity when unconstrained.
        starts: Row start points.
        slots: Column points.
        crossing_weight: Penalty per crossing.
        spread_weight: Penalty per meter of mean distance from a group's target centroid.
        row_groups: Group index per row; -1 means no group.
        frozen: Rows the pass may not move (pins).
    """

    move_cost: FloatArray
    raw_cost: FloatArray
    cap: float
    starts: Points
    slots: Points
    crossing_weight: float
    spread_weight: float
    row_groups: IntArray
    frozen: BoolArray


def _spread(score: SwapScore, cols: IntArray, group: int) -> float:
    if group < 0:
        return 0.0
    pts = score.slots[cols[score.row_groups == group]]
    return float(np.linalg.norm(pts - pts.mean(axis=0), axis=1).mean()) if len(pts) else 0.0


def _allowed(score: SwapScore, row: int, col: int) -> bool:
    return bool(np.isfinite(score.move_cost[row, col]) and score.raw_cost[row, col] <= score.cap)


def _crossings_of(score: SwapScore, cols: IntArray, rows: list[int], others: BoolArray) -> int:
    ends = score.slots[cols]
    total = 0
    for r in rows:
        total += int(
            crossing_mask(score.starts[r], ends[r], score.starts[others], ends[others]).sum()
        )
    if len(rows) == 2:
        a, b = rows
        pair = crossing_mask(score.starts[a], ends[a], score.starts[b : b + 1], ends[b : b + 1])
        total += int(pair.sum())
    return total


def _penalty(score: SwapScore, cols: IntArray, rows: list[int]) -> float:
    others = np.ones(len(cols), dtype=bool)
    others[rows] = False
    value = sum(float(score.move_cost[r, cols[r]]) for r in rows)
    if score.crossing_weight:
        value += score.crossing_weight * _crossings_of(score, cols, rows, others)
    if score.spread_weight:
        groups = {int(score.row_groups[r]) for r in rows}
        value += score.spread_weight * sum(_spread(score, cols, g) for g in groups)
    return value


def _try_move(score: SwapScore, cols: IntArray, rows: list[int], new_cols: list[int]) -> bool:
    if any(score.frozen[r] for r in rows):
        return False
    if not all(_allowed(score, r, c) for r, c in zip(rows, new_cols, strict=True)):
        return False
    before = _penalty(score, cols, rows)
    old_cols = [int(cols[r]) for r in rows]
    cols[rows] = new_cols
    if _penalty(score, cols, rows) < before - IMPROVE_TOL:
        return True
    cols[rows] = old_cols
    return False


def _one_pass(score: SwapScore, cols: IntArray) -> int:
    changes = 0
    n = len(cols)
    for i in range(n):
        for k in range(i + 1, n):
            changes += _try_move(score, cols, [i, k], [int(cols[k]), int(cols[i])])
        free = sorted(set(range(score.slots.shape[0])) - set(cols.tolist()))
        for j in free:
            changes += _try_move(score, cols, [i], [j])
    return changes


def improve_by_swaps(cols: IntArray, score: SwapScore) -> tuple[IntArray, int]:
    """Lowers cost plus crossing and spread penalties by first-improvement swaps.

    Each pass tries every pair swap, then every move to a free slot, in index order, and
    keeps a change only when the penalized score drops. Stops when a pass changes nothing,
    or after MAX_SWAP_PASSES passes.

    Returns:
        improved: New column per row.
        changes: Number of accepted swaps and moves.

    >>> starts = np.array([[0.0, 0.0], [0.0, 2.0]])
    >>> slots = np.array([[2.0, 0.0], [2.0, 2.0]])
    >>> flat = np.full((2, 2), 2.0)
    >>> score = SwapScore(flat, flat, np.inf, starts, slots, 1.0, 0.0,
    ...     np.full(2, -1, dtype=np.intp), np.zeros(2, dtype=bool))
    >>> improve_by_swaps(np.array([1, 0], dtype=np.intp), score)[0].tolist()
    [0, 1]
    """
    current = cols.copy()
    total = 0
    for _ in range(MAX_SWAP_PASSES):
        changes = _one_pass(score, current)
        total += changes
        if changes == 0:
            break
    return current, total
