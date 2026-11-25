#!/usr/bin/env python3

"""
test_merge_properties.py --- checks the three-way merge over random edits of a seeded show

Edits cover placements (drill, facing, labels), the title, sets and their order, the spot order,
pins, spot deletion, forms, path rows, routes, the sheet, the timeline, groups, selections,
facing overrides, and single moves within the spot order and a set's placement order.

Contains:
    test_merge_with_an_unchanged_side_returns_the_other: returns the edited side itself
    test_merge_is_symmetric_in_ours_and_theirs: gives one show, or the same conflicts with the
        sides swapped, whichever side is which
    test_clean_merge_is_lossless: keeps every item neither side changed, every change one side
        made, every change both made alike, and exactly the elements of each merged order, in
        the order of every pair both sides keep alike
"""

from functools import cache

from hypothesis import example, given
from hypothesis import strategies as st

from e8s.cast.models import Group
from e8s.cast.spots import add_group, define_selection, delete_spots
from e8s.choreo.facing import add_facing_override
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import FacingMode, FacingOverride, TransitionSpec
from e8s.core.ids import SpotId
from e8s.core.readonly import freeze_json
from e8s.core.types import JsonAny
from e8s.show.change_models import ItemChange
from e8s.show.items import changes, items
from e8s.show.manage import add_set, remove_set, replace_sets
from e8s.show.merge import merge
from e8s.show.models import Show
from e8s.show.pinning import pin
from tests.fixtures.generators import seeded_show

SEEDED_SPOTS = 6
SEEDED_SETS = 3
PLACEMENT_KINDS = 4
STRUCTURE_KINDS = 10
CONTENT_KINDS = 19
EDIT_KINDS = 21
ADDED_SET_IDS = ("9", "9A", "9B")
ADDED_GROUPS = (("extra_x", "X"), ("extra_y", "Y"), ("extra_z", "Z"))
VARIANTS = 3
ROUTE_SHARE_STEP = 0.1
OVERRIDE_END = 1.0
ORDER_KINDS = frozenset({"set_order", "spot_order", "pin_order"})
SWAP_THIRD_AND_FOURTH_SPOTS = (CONTENT_KINDS, 3, 2.0)
FIRST_SPOT_AFTER_THIRD = (CONTENT_KINDS, 0, 2.0)
type _Element = str | tuple[str, ...]
seeds = st.integers(min_value=0, max_value=3)
edits = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=EDIT_KINDS - 1),
        st.integers(min_value=0, max_value=60),
        st.floats(min_value=-20.0, max_value=20.0, allow_nan=False),
    ),
    max_size=3,
)


@cache
def _base(seed: int) -> Show:
    return seeded_show(seed, SEEDED_SPOTS, SEEDED_SETS)


def _patched(show: Show, position: int, spot: str, patch: PlacementPatch) -> Show:
    edited = show.sets[position].with_placements({spot: patch}, force=True)
    return show.replace(sets=tuple(edited if i == position else s for i, s in enumerate(show.sets)))


def _structural(show: Show, kind: int, pick: int, spot: str) -> Show:
    sets = show.sets
    match kind:
        case 4:
            set_id = ADDED_SET_IDS[pick % len(ADDED_SET_IDS)]
            return show if set_id in show.set_ids() else add_set(show, set_id, 4.0)
        case 5:
            return remove_set(show, sets[-1].id, force=True) if len(sets) > 1 else show
        case 6:
            spots = tuple(reversed(show.cast.spots))
            return show.replace(cast=show.cast.replace(spots=spots))
        case 7:
            return pin(show, "entity", spot)
        case 8:
            return replace_sets(show, (*sets[1:2], sets[0], *sets[2:]), force=True)
        case _:
            return delete_spots(show, spot, force=True) if len(show.cast.spots) > 1 else show


def _with_spec(show: Show, spec: TransitionSpec) -> Show:
    specs = tuple(spec if s.id == spec.id else s for s in show.transitions)
    return show.replace(transitions=specs)


def _paths(show: Show, kind: int, pick: int) -> Show:
    if not show.transitions:
        return show
    spec = show.transitions[pick % len(show.transitions)]
    rows = sorted(spec.rows)
    match kind:
        case 10 if rows:
            row = spec.rows[rows[pick % len(rows)]].replace(note=f"row {pick % VARIANTS}")
            return _with_spec(show, spec.replace(rows={**spec.rows, row.entity: row}))
        case 11 if rows:
            kept = {e: row for e, row in spec.rows.items() if e != rows[pick % len(rows)]}
            return _with_spec(show, spec.replace(rows=kept))
        case 12 if spec.routes:
            route = spec.routes[min(spec.routes)]
            share = 1.0 - ROUTE_SHARE_STEP * (pick % VARIANTS + 1)
            routes = {**spec.routes, route.id: route.replace(travel=route.travel * share)}
            return _with_spec(show, spec.replace(routes=routes))
        case _:
            return show


def _content(show: Show, kind: int, pick: int, spot: str) -> Show:
    variant = pick % VARIANTS
    match kind:
        case 13 if show.forms:
            form = show.forms[sorted(show.forms)[pick % len(show.forms)]]
            return show.replace(forms={**show.forms, form.id: form.replace(locked=not form.locked)})
        case 14:
            return show.replace(sheet=show.sheet.replace(name=f"sheet {variant}"))
        case 15:
            timeline = show.timeline.replace(total_counts=64.0 + variant)
            return show.replace(timeline=timeline)
        case 16:
            key, prefix = ADDED_GROUPS[variant]
            if key in show.cast.groups:
                return show
            return add_group(show, Group(key=key, label_prefix=prefix))
        case 17:
            return define_selection(show, f"sel_{variant}", spot, replace=True)
        case 18:
            end = OVERRIDE_END + variant
            if end > sum(current.counts for current in show.sets[1:]):
                return show
            override = FacingOverride(
                entities=(SpotId(spot),), mode=FacingMode.AUDIENCE, start=0.0, end=end
            )
            return add_facing_override(show, override)
        case _:
            return _paths(show, kind, pick)


def _moved[T](members: tuple[T, ...], pick: int, value: float) -> tuple[T, ...]:
    rest = list(members)
    member = rest.pop(pick % len(rest))
    rest.insert(int(abs(value)) % (len(rest) + 1), member)
    return tuple(rest)


def _reordered(show: Show, kind: int, pick: int, value: float) -> Show:
    if kind == CONTENT_KINDS:
        spots = _moved(show.cast.spots, pick, value)
        return show.replace(cast=show.cast.replace(spots=spots))
    position = pick % len(show.sets)
    current = show.sets[position]
    moved = current.replace(placements=_moved(current.placements, pick // 3, value))
    return show.replace(sets=tuple(moved if i == position else s for i, s in enumerate(show.sets)))


def _edited(show: Show, kind: int, pick: int, value: float) -> Show:
    position = pick % len(show.sets)
    placements = show.sets[position].placements
    spot = placements[pick % len(placements)].entity
    match kind:
        case 0:
            return _patched(show, position, spot, PlacementPatch(x=value))
        case 1:
            return _patched(show, position, spot, PlacementPatch(facing=abs(value)))
        case 2:
            return _patched(show, position, spot, PlacementPatch(note=f"note {pick % 3}"))
        case 3:
            return show.replace(title=f"title {pick % 3}")
        case _ if kind < STRUCTURE_KINDS:
            return _structural(show, kind, pick, spot)
        case _ if kind < CONTENT_KINDS:
            return _content(show, kind, pick, spot)
        case _:
            return _reordered(show, kind, pick, value)


def _applied(show: Show, steps: list[tuple[int, int, float]]) -> Show:
    for kind, pick, value in steps:
        show = _edited(show, kind, pick, value)
    return show


@given(seeds, edits)
def test_merge_with_an_unchanged_side_returns_the_other(
    seed: int, steps: list[tuple[int, int, float]]
) -> None:
    base = _base(seed)
    edited = _applied(base, steps)
    copy = Show.from_json(base.to_json())
    assert merge(base, edited, copy).show is edited
    returned = merge(base, copy, edited).show
    assert returned is (edited if changes(base, edited).changes else copy)


@given(seeds, edits, edits)
def test_merge_is_symmetric_in_ours_and_theirs(
    seed: int, our_steps: list[tuple[int, int, float]], their_steps: list[tuple[int, int, float]]
) -> None:
    base = _base(seed)
    ours, theirs = _applied(base, our_steps), _applied(base, their_steps)
    forward, backward = merge(base, ours, theirs), merge(base, theirs, ours)
    swapped = tuple(c.replace(ours=c.theirs, theirs=c.ours) for c in forward.conflicts)
    assert backward.conflicts == swapped
    keys = [c.key.sort_key() for c in forward.conflicts]
    assert keys == sorted(set(keys))
    assert forward.show == backward.show


def _element(part: JsonAny) -> _Element:
    if isinstance(part, str):
        return part
    assert isinstance(part, tuple | list)
    return tuple(str(name) for name in part)


def _elements(value: JsonAny) -> list[_Element]:
    return [_element(part) for part in value] if isinstance(value, tuple | list) else []


def _merged_elements(mine: ItemChange, other: ItemChange) -> set[_Element]:
    """Returns the elements a merged order holds: every element either side kept or added,
    less every base element either side removed."""
    base = set(_elements(mine.before))
    ours, theirs = set(_elements(mine.after)), set(_elements(other.after))
    return (ours | theirs) - (base - ours) - (base - theirs)


def _keeps_shared_order(
    ours: list[_Element], theirs: list[_Element], merged: list[_Element]
) -> bool:
    """Reports whether every pair both sides hold in one order keeps that order when merged."""
    other = {element: i for i, element in enumerate(theirs)}
    at = {element: i for i, element in enumerate(merged)}
    shared = [element for element in ours if element in other]
    return all(
        at[first] < at[second]
        for i, first in enumerate(shared)
        for second in shared[i + 1 :]
        if other[first] < other[second]
    )


def _settled(
    mine: ItemChange | None, other: ItemChange | None, before: JsonAny
) -> tuple[bool, JsonAny]:
    """Returns whether a clean merge settles an item as one known value, and that value."""
    if other is None:
        return True, before if mine is None else mine.after
    if mine is None:
        return True, other.after
    return mine.after == other.after, mine.after


@given(seeds, edits, edits)
@example(0, [SWAP_THIRD_AND_FOURTH_SPOTS], [FIRST_SPOT_AFTER_THIRD])
def test_clean_merge_is_lossless(
    seed: int, our_steps: list[tuple[int, int, float]], their_steps: list[tuple[int, int, float]]
) -> None:
    base = _base(seed)
    ours, theirs = _applied(base, our_steps), _applied(base, their_steps)
    merged = merge(base, ours, theirs).show
    if merged is None:
        return
    mine = {c.key.sort_key(): c for c in changes(base, ours).changes}
    other = {c.key.sort_key(): c for c in changes(base, theirs).changes}
    before, after = items(base), items(merged)
    keys = {key.sort_key(): key for key in (*before, *after)}
    assert {*mine, *other} <= keys.keys()
    for sort_key, key in keys.items():
        found = freeze_json(after.get(key))
        mine_change, other_change = mine.get(sort_key), other.get(sort_key)
        is_settled, expected = _settled(mine_change, other_change, freeze_json(before.get(key)))
        if is_settled:
            assert found == expected, key
            continue
        assert mine_change is not None
        assert other_change is not None
        assert key.kind in ORDER_KINDS or key.role == "placements", key
        elements = _elements(found)
        assert len(set(elements)) == len(elements)
        assert set(elements) == _merged_elements(mine_change, other_change)
        assert _keeps_shared_order(
            _elements(mine_change.after), _elements(other_change.after), elements
        ), key
