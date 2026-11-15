"""
match_ops.py --- applies matches to a show: writes targets, swaps spots, suggests swaps

Every edit returns a new Show. Moving a spot's target invalidates its stored path rows in
the touching transitions, so those rows are dropped and the spot moves straight by default.

Contains:
    with_set(): returns a show with one set replaced by ID
    clear_rows(): drops stored path rows of some spots in one transition, respecting locks
    apply_match(): writes a match result into a transition's destination set, forms included
    swap(): swaps two spots' places in one set, keeping forms and paths consistent
    suggest_swaps(): ranks swaps that shrink the largest straight move or remove crossings
"""

import itertools
from collections.abc import Iterable

import numpy as np

from showpy.choreo.forms import check_unlocked, slot_poses
from showpy.choreo.match_solve import BoolArray, FloatArray, crossing_mask
from showpy.choreo.models import Form, MatchResult, Placement, Set
from showpy.core.errors import LockedError, MatchError, UnknownRefError
from showpy.core.ids import FormId, SetId, SpotId, TransitionId
from showpy.core.ids import format_tid as _format_tid
from showpy.core.types import Points
from showpy.core.units import angle_diff
from showpy.core.util import natural_sort
from showpy.show.models import Show

SAME_POSITION_TOL = 1e-9
GAIN_TOL = 1e-9
FACING_TOL = 1e-9
SWAPPED_FIELDS = ("x", "y", "facing", "form", "order", "on_field")


def with_set(show: Show, new: Set) -> Show:
    """Returns a show with the set of the same ID replaced by `new`.

    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> with_set(show, show.set("2").replace(label="B")).set("2").label
    'B'
    """
    sets = tuple(new if s.id == new.id else s for s in show.sets)
    return show.replace(sets=sets)


def clear_rows(
    show: Show, tid: TransitionId, entities: Iterable[SpotId], force: bool = False
) -> Show:
    """Drops stored path rows of `entities` in transition `tid`; locked rows need force.

    >>> from showpy.examples import two_set_show
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
    new_spec = spec.replace(rows=rows)
    others = tuple(t for t in show.transitions if t.id != tid)
    return show.replace(transitions=(*others, new_spec))


def _neighbour_tids(show: Show, set_id: SetId) -> list[TransitionId]:
    return [tid for tid in show.transition_ids() if set_id in tid]


def _slot_targets(show: Show, set_id: SetId, result: MatchResult) -> dict[SpotId, Placement]:
    poses: dict[FormId, tuple[Points, list[float]]] = {}
    targets = {}
    for entity, (form_id, slot) in result.slots.items():
        if form_id not in poses:
            form = show.forms.get(form_id)
            if form is None:
                raise UnknownRefError(
                    f"match result names unknown form {form_id!r}",
                    hint="match onto forms of this show, or match onto points",
                    context={"form": form_id, "forms": sorted(show.forms)},
                )
            if form.set != set_id:
                raise MatchError(
                    f"form {form_id} belongs to set {form.set}, not {set_id}",
                    hint=f"match onto forms of set {set_id}",
                    context={"form": form_id, "form_set": form.set, "set": set_id},
                )
            poses[form_id] = slot_poses(form)
        points, facings = poses[form_id]
        x, y = (float(v) for v in points[slot])
        targets[entity] = Placement(
            entity=entity, x=x, y=y, facing=facings[slot], form=form_id, order=slot
        )
    return targets


def _form_slot_error(form: Form, reason: str, slot: int) -> MatchError:
    return MatchError(
        f"match leaves form {form.id} with {reason} at slot {slot}",
        hint="match every member of the form, or use place()",
        context={"form": form.id, "slot": slot},
    )


def _matched_members(form: Form, result: MatchResult) -> tuple[SpotId, ...]:
    held: list[SpotId | None] = [m if m not in result.slots else None for m in form.members]
    for entity, (form_id, slot) in result.slots.items():
        if form_id != form.id:
            continue
        held.extend([None] * (slot + 1 - len(held)))
        if held[slot] is not None:
            raise _form_slot_error(form, "two spots", slot)
        held[slot] = entity
    while held and held[-1] is None:
        held.pop()
    for slot, member in enumerate(held):
        if member is None:
            raise _form_slot_error(form, "an empty slot", slot)
    return tuple(m for m in held if m is not None)


def _matched_forms(show: Show, set_id: SetId, result: MatchResult, force: bool) -> Show:
    touched = {form_id for form_id, _ in result.slots.values()}
    forms = dict(show.forms)
    for form_id, form in show.forms.items():
        involved = form_id in touched or bool(set(form.members) & set(result.slots))
        if form.set != set_id or not involved:
            continue
        members = _matched_members(form, result)
        if members != form.members:
            check_unlocked(form, force)
            forms[form_id] = form.replace(members=members)
    return show.replace(forms=forms)


def _is_elsewhere(current: Placement, x: float, y: float) -> bool:
    return (
        abs(current.x - x) > SAME_POSITION_TOL
        or abs(current.y - y) > SAME_POSITION_TOL
        or not current.on_field
    )


def _slot_updates(dest: Set, targets: dict[SpotId, Placement]) -> dict[SpotId, Placement]:
    updates = {}
    for entity, wanted in targets.items():
        current = dest.get(entity)
        is_same_slot = (current.form, current.order) == (wanted.form, wanted.order)
        is_same_pose = not _is_elsewhere(current, wanted.x, wanted.y) and (
            abs(angle_diff(current.facing, wanted.facing)) <= FACING_TOL
        )
        if not (is_same_slot and is_same_pose):
            updates[entity] = current.replace(
                x=wanted.x, y=wanted.y, facing=wanted.facing, form=wanted.form,
                order=wanted.order, on_field=True,
            )  # fmt: skip
    return updates


def _point_updates(dest: Set, result: MatchResult) -> dict[SpotId, dict[str, object]]:
    updates: dict[SpotId, dict[str, object]] = {}
    for entity, (x, y) in zip(result.entities, result.targets, strict=True):
        if _is_elsewhere(dest.get(entity), x, y):
            updates[entity] = {"x": x, "y": y, "on_field": True}
    return updates


def apply_match(show: Show, tid: TransitionId, result: MatchResult, force: bool = False) -> Show:
    """Writes the result's targets into the destination set and resets moved spots' paths.

    Spots already on their target are left alone, so pinned spots that stay put are fine;
    moving a pinned spot or dropping a locked path row needs force=True. A result matched
    onto forms also writes each spot's facing, form and order from its slot and updates
    `Form.members` like `swap`, so rebuilding a form keeps the match; changing a locked
    form needs force=True.

    >>> from showpy.choreo.match import match
    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> r = match(show.set("1"), [(0, 3), (2, 3), (4, 3), (6, 3)])
    >>> apply_match(show, ("1", "2"), r).set("2").get("T4").point
    (6.0, 3.0)
    """
    show.transition(tid)
    dest = show.set(tid[1])
    if not result.slots:
        updates = _point_updates(dest, result)
        edited = with_set(show, dest.with_placements(updates, force=force))
        return clear_rows(edited, tid, list(updates), force)
    slot_updates = _slot_updates(dest, _slot_targets(show, tid[1], result))
    forms_edited = _matched_forms(show, tid[1], result, force)
    edited = with_set(forms_edited, dest.with_placements(slot_updates, force=force))
    relocated = [e for e, p in slot_updates.items() if _is_elsewhere(dest.get(e), p.x, p.y)]
    return clear_rows(edited, tid, relocated, force)


def _swap_members(members: tuple[SpotId, ...], a: SpotId, b: SpotId) -> tuple[SpotId, ...]:
    return tuple(b if m == a else a if m == b else m for m in members)


def _swap_forms(show: Show, set_id: SetId, a: SpotId, b: SpotId, force: bool) -> Show:
    forms = dict(show.forms)
    for form_id, form in show.forms.items():
        if form.set != set_id or not {a, b} & set(form.members):
            continue
        check_unlocked(form, force)
        members = _swap_members(form.members, a, b)
        forms[form_id] = form.replace(members=members)
    return show.replace(forms=forms)


def _spot_fields(placement: Placement) -> dict[str, object]:
    return {name: getattr(placement, name) for name in SWAPPED_FIELDS}


def swap(show: Show, set_id: SetId, a: SpotId, b: SpotId, force: bool = False) -> Show:
    """Swaps where two spots stand in one set, with facing and form slot.

    Form members are swapped too, so regenerating a form keeps the swap, and both spots'
    path rows into and out of the set are dropped. Pins and locks need force=True.

    >>> from showpy.examples import two_set_show
    >>> swap(two_set_show(), "2", "T1", "T4").set("2").get("T1").x
    6.0
    """
    target = show.set(set_id)
    pa, pb = target.get(a), target.get(b)
    updates = {a: _spot_fields(pb), b: _spot_fields(pa)}
    edited = with_set(show, target.with_placements(updates, force))
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

    >>> from showpy.examples import two_set_show
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
