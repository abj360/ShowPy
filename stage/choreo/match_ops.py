#!/usr/bin/env python3

"""
match_ops.py --- applies matches to a show: writes targets, swaps spots, suggests swaps

Every edit returns a new Show. Moving a spot's target invalidates its stored path rows in
the touching transitions, so those rows are dropped and the spot moves straight by default.

Contains:
    clear_rows(): drops stored path rows of some spots in one transition, respecting locks
    apply_match(): writes a match result's targets into a transition's destination set
    swap(): swaps two spots' places in one set, keeping forms and paths consistent
    suggest_swaps(): ranks swaps that shrink the largest straight move or remove crossings
"""

import itertools
from collections.abc import Iterable

import numpy as np

from stage.choreo.forms import check_unlocked
from stage.choreo.match_solve import BoolArray, FloatArray, crossing_mask
from stage.choreo.models import MatchResult, Placement, Set
from stage.core.errors import LockedError
from stage.core.ids import SetId, SpotId, TransitionId
from stage.core.ids import format_tid as _format_tid
from stage.core.types import Points
from stage.core.util import natural_sort
from stage.show.models import Show

SAME_POSITION_TOL = 1e-9
GAIN_TOL = 1e-9
SWAPPED_FIELDS = ("x", "y", "facing", "form", "order", "on_field")


def _replace_set(show: Show, new: Set) -> Show:
    sets = tuple(new if s.id == new.id else s for s in show.sets)
    return show.model_copy(update={"sets": sets})


def clear_rows(
    show: Show, tid: TransitionId, entities: Iterable[SpotId], force: bool = False
) -> Show:
    """Drops stored path rows of `entities` in transition `tid`; locked rows need force.

    >>> from tests.fixtures.shows import two_set_show
    >>> clear_rows(two_set_show(), ("1", "2"), ["T1"]).transitions
    ()
    """
    spec = show.transition(tid)
    doomed = [e for e in entities if e in spec.rows]
    if not doomed:
        return show
    locked = [e for e in doomed if spec.rows[e].locked]
    if locked and not force:
        raise LockedError(
            f"path rows of {', '.join(locked)} in {_format_tid(tid)} are locked",
            hint="unlock the rows first, or pass force=True as the designer",
            context={"tid": tid, "entities": locked},
        )
    rows = {e: row for e, row in spec.rows.items() if e not in doomed}
    new_spec = spec.model_copy(update={"rows": rows})
    others = tuple(t for t in show.transitions if t.id != tid)
    return show.model_copy(update={"transitions": (*others, new_spec)})


def _neighbour_tids(show: Show, set_id: SetId) -> list[TransitionId]:
    return [tid for tid in show.transition_ids() if set_id in tid]


def apply_match(show: Show, tid: TransitionId, result: MatchResult, force: bool = False) -> Show:
    """Writes the result's targets into the destination set and resets those spots' paths.

    Spots already on their target are left alone, so pinned spots that stay put are fine;
    moving a pinned spot or dropping a locked path row needs force=True.

    >>> from stage.choreo.match import match
    >>> from tests.fixtures.shows import two_set_show
    >>> show = two_set_show()
    >>> r = match(show.set("1"), [(0, 3), (2, 3), (4, 3), (6, 3)])
    >>> apply_match(show, ("1", "2"), r).set("2").get("T4").point
    (6.0, 3.0)
    """
    show.transition(tid)
    dest = show.set(tid[1])
    updates = {}
    for entity, (x, y) in zip(result.entities, result.targets, strict=True):
        current = dest.get(entity)
        moved = abs(current.x - x) > SAME_POSITION_TOL or abs(current.y - y) > SAME_POSITION_TOL
        if moved or not current.on_field:
            updates[entity] = {"x": x, "y": y, "on_field": True}
    edited = _replace_set(show, dest.with_placements(updates, force=force))
    return clear_rows(edited, tid, list(updates), force)


def _swap_members(members: tuple[SpotId, ...], a: SpotId, b: SpotId) -> tuple[SpotId, ...]:
    return tuple(b if m == a else a if m == b else m for m in members)


def _swap_forms(show: Show, set_id: SetId, a: SpotId, b: SpotId, force: bool) -> Show:
    forms = dict(show.forms)
    for form_id, form in show.forms.items():
        if form.set != set_id or not {a, b} & set(form.members):
            continue
        check_unlocked(form, force)
        members = _swap_members(form.members, a, b)
        forms[form_id] = form.model_copy(update={"members": members})
    return show.model_copy(update={"forms": forms})


def _spot_fields(placement: Placement) -> dict[str, object]:
    return {name: getattr(placement, name) for name in SWAPPED_FIELDS}


def swap(show: Show, set_id: SetId, a: SpotId, b: SpotId, force: bool = False) -> Show:
    """Swaps where two spots stand in one set, with facing and form slot.

    Form members are swapped too, so regenerating a form keeps the swap, and both spots'
    path rows into and out of the set are dropped. Pins and locks need force=True.

    >>> from tests.fixtures.shows import two_set_show
    >>> swap(two_set_show(), "2", "T1", "T4").set("2").get("T1").x
    6.0
    """
    target = show.set(set_id)
    pa, pb = target.get(a), target.get(b)
    updates = {a: _spot_fields(pb), b: _spot_fields(pa)}
    edited = _replace_set(show, target.with_placements(updates, force))
    edited = _swap_forms(edited, set_id, a, b, force)
    for tid in _neighbour_tids(edited, set_id):
        edited = clear_rows(edited, tid, [a, b], force)
    return edited


def _swappable(show: Show, tid: TransitionId) -> list[SpotId]:
    src, dst = show.set(tid[0]), show.set(tid[1])
    present = set(src.entity_ids())
    ids = [
        p.entity
        for p in dst.placements
        if p.on_field and not p.pinned and p.entity in present and src.get(p.entity).on_field
    ]
    return natural_sort(ids)


def _crossing_table(starts: Points, ends: Points) -> BoolArray:
    n = len(starts)
    table = np.zeros((n, n), dtype=bool)
    for i in range(n):
        table[i] = crossing_mask(starts[i], ends[i], starts, ends)
    return table


def _others_max(order: list[int], dist: FloatArray, skip: set[int]) -> float:
    for index in order[:3]:
        if index not in skip:
            return float(dist[index])
    return 0.0


def _pair_crossings(starts: Points, ends: Points, i: int, k: int) -> int:
    others = np.ones(len(starts), dtype=bool)
    others[[i, k]] = False
    total = int(crossing_mask(starts[i], ends[k], starts[others], ends[others]).sum())
    total += int(crossing_mask(starts[k], ends[i], starts[others], ends[others]).sum())
    pair = crossing_mask(starts[i], ends[k], starts[k : k + 1], ends[i : i + 1])
    return total + int(pair.sum())


class _Moves:
    """Holds straight moves of swappable spots for scoring candidate swaps."""

    def __init__(self, starts: Points, ends: Points) -> None:
        self.starts, self.ends = starts, ends
        self.dist: FloatArray = np.linalg.norm(ends - starts, axis=1)
        self.order = [int(i) for i in np.argsort(-self.dist, kind="stable")]
        self.largest = float(self.dist.max()) if len(self.dist) else 0.0
        self.crossings = _crossing_table(starts, ends)

    def score(self, i: int, j: int) -> tuple[float, int]:
        """Returns the largest-move reduction and crossings removed by swapping i and j."""
        new_i = float(np.linalg.norm(self.ends[j] - self.starts[i]))
        new_j = float(np.linalg.norm(self.ends[i] - self.starts[j]))
        new_max = max(_others_max(self.order, self.dist, {i, j}), new_i, new_j)
        table = self.crossings
        old = int(table[i].sum() + table[j].sum() - table[i, j])
        return self.largest - new_max, old - _pair_crossings(self.starts, self.ends, i, j)


def suggest_swaps(
    show: Show, tid: TransitionId, k: int = 5, same_group: bool = True
) -> list[tuple[SpotId, SpotId, float]]:
    """Ranks destination swaps that shrink the largest straight move or remove crossings.

    Only straight moves are considered (stored path rows are ignored). Candidates are
    on-field, unpinned spots, within one group by default. Ranked by largest-move
    reduction, then crossings removed, then spot IDs.

    Returns:
        swaps: Up to k (a, b, gain) tuples, gain being the largest-move reduction in meters.

    >>> from tests.fixtures.shows import two_set_show
    >>> [(a, b, round(g, 2)) for a, b, g in
    ...     suggest_swaps(swap(two_set_show(), "2", "T1", "T4"), ("1", "2"), k=1)]
    [('T1', 'T4', 3.21)]
    """
    show.transition(tid)
    ids = _swappable(show, tid)
    moves = _Moves(show.set(tid[0]).positions(ids), show.set(tid[1]).positions(ids))
    group = {spot.id: spot.group for spot in show.cast.spots}
    ranked = []
    for i, j in itertools.combinations(range(len(ids)), 2):
        if same_group and group.get(ids[i]) != group.get(ids[j]):
            continue
        gain, removed = moves.score(i, j)
        if gain > GAIN_TOL or (gain >= -GAIN_TOL and removed > 0):
            ranked.append((-max(gain, 0.0), -removed, ids[i], ids[j]))
    ranked.sort()
    return [(a, b, -neg_gain) for neg_gain, _, a, b in ranked[:k]]
