#!/usr/bin/env python3

"""
test_revalidate_properties.py --- checks incremental revalidation against a full validation

Random edits run through the library's edit functions on a seeded show: placement moves, form
references, field flags and pin flags, non-finite counts, far-off coordinates, set counts, pins,
added sets, path rows, stored path counts, form members and locks, a set's form list, removed forms,
inactive spots, facing overrides, the title and the sheet, and several placements and a form changed
by one change. Further edits reach what facing overrides and rows resolve: overrides selecting by
`form:`, `group:`, `tag:`, `sel:` and `T*` text, overrides and rows facing toward a known or unknown
spot, rows of unknown spots, routes, group, selection and tag edits, override removal, pin reorder,
`remove_set`, `join_sections` after `split_section`, forced form edits under pins, and added forms.
Besides four 6-spot, 4-set shows, eight 8-spot, 5-set shows start with one text override of each
selector kind, one naming a form the show lacks. While overrides name forms, form removal prefers a
form one selects, form addition uses a name one looks for, and two thirds of the other form edits
add or remove a form instead. Some edits create issues and others clear them, and the show before
the edit may already hold issues. Stray references that only a v1 migration can leave are not
generated.

Contains:
    test_revalidate_equals_validate_show_after_random_edits: gives exactly validate_show's
        report of the edited show, issue order included
"""

from functools import cache

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from e8s.cast.models import Group
from e8s.cast.spots import add_group, define_selection, edit_group, remove_group
from e8s.choreo.facing import add_facing_override
from e8s.choreo.models import Placement, PlacementPatch
from e8s.choreo.path_models import EntityFacing, FacingMode, FacingOverride, PathRow, PathStyle
from e8s.choreo.routes import follow_and_set_targets, make_route
from e8s.core.caps import MAX_COUNTS
from e8s.core.errors import E8sError
from e8s.core.ids import FormId, SpotId
from e8s.show.changes import apply_change
from e8s.show.collab_models import FormOp, PathsOp, PlacementOp
from e8s.show.manage import add_set, remove_set, set_counts
from e8s.show.models import Pin, PinTarget, Show
from e8s.show.pinning import pin, unpin
from e8s.show.revalidate import revalidate
from e8s.show.sections import join_sections, split_section
from e8s.show.validate import validate_show
from tests.fixtures.generators import seeded_show

SEEDED_SPOTS = 6
SEEDED_SETS = 4
PLAIN_SEEDS = 4
SELECTING_SEEDS = 8
SEED_COUNT = PLAIN_SEEDS + SELECTING_SEEDS
SELECTING_SPOTS = 8
SELECTING_SETS = 5
FORM_WORD = "form:"
EDIT_KINDS = 33
FAR_OFF_M = 5_000.0
LATE_COUNT = 1e5
UNKNOWN_SPOT = SpotId("Z9")
UNKNOWN_FORM = FormId("zz")
PIN_TARGETS: tuple[PinTarget, ...] = ("entity", "set", "form", "transition")
COUNTS = (0.0, 2.0, 8.0, 12.0)
SELECTORS = (
    "form:{form}",
    "group:{group}",
    "tag:{tag}",
    "sel:{selection}",
    "{prefix}*",
    "form:{form}{added}",
)
ADDED_FORM_SUFFIX = "x"
EXTRA_GROUP = Group(key="flags", label_prefix="F")
TAGS = ("solo", "front")
SELECTION = "pick"
GROUP_COLORS = ("#112233", "#445566")
ROUTE_STEP_M = 2.0
type Step = tuple[int, int, float]

seeds = st.integers(min_value=0, max_value=SEED_COUNT - 1)
steps = st.tuples(
    st.integers(min_value=0, max_value=EDIT_KINDS - 1),
    st.integers(min_value=0, max_value=60),
    st.floats(min_value=-20.0, max_value=20.0, allow_nan=False),
)
edits = st.lists(steps, min_size=1, max_size=4)
earlier_edits = st.lists(steps, max_size=3)


@cache
def _base(seed: int) -> Show:
    if seed < PLAIN_SEEDS:
        return seeded_show(seed, SEEDED_SPOTS, SEEDED_SETS)
    show = seeded_show(seed, SELECTING_SPOTS, SELECTING_SETS)
    for pick in range(len(SELECTORS)):
        override = FacingOverride(
            entities=_selector(show, pick), mode=FacingMode.AUDIENCE, start=0.0, end=1.0
        )
        show = add_facing_override(show, override)
    return show


@pytest.fixture(scope="module", autouse=True)
def _built_bases() -> None:
    """Builds every seeded base show before the property runs, so no timed example builds one."""
    for seed in range(SEED_COUNT):
        _base(seed)


def _form_words(show: Show) -> set[str]:
    words = (
        word
        for override in show.facing_overrides
        if isinstance(override.entities, str)
        for word in override.entities.split()
    )
    return {word.removeprefix(FORM_WORD) for word in words if word.startswith(FORM_WORD)}


def _selected_forms(show: Show) -> list[FormId]:
    return sorted(FormId(name) for name in _form_words(show) if name in show.forms)


def _added_form(show: Show, pick: int) -> Show:
    if not show.forms:
        return show
    source = show.forms[_form_id(show, pick)]
    wanted = sorted(name for name in _form_words(show) if name not in show.forms)
    added = FormId(wanted[pick % len(wanted)] if wanted else f"{source.id}{ADDED_FORM_SUFFIX}")
    return show._copied(update={"forms": {**show.forms, added: source.replace(id=added)}})


def _replaced_set(show: Show, position: int, spot: str, patch: PlacementPatch) -> Show:
    edited = show.sets[position].with_placements({spot: patch}, force=True)
    sets = tuple(edited if i == position else s for i, s in enumerate(show.sets))
    return show._copied(update={"sets": sets})


def _form_id(show: Show, pick: int) -> FormId:
    forms = sorted(show.forms)
    return forms[pick % len(forms)] if forms else UNKNOWN_FORM


def _placement(show: Show, kind: int, pick: int, value: float) -> Show:
    position = pick % len(show.sets)
    current = show.sets[position].placements[pick % len(show.sets[position].placements)]
    patches = {
        0: PlacementPatch(x=value),
        1: PlacementPatch(form=(_form_id(show, pick), UNKNOWN_FORM, None)[pick % 3]),
        2: PlacementPatch(on_field=not current.on_field),
        3: PlacementPatch(pinned=not current.pinned),
        4: PlacementPatch(x=FAR_OFF_M),
    }
    if kind == 2 and pick % 5 == 0:
        kept = tuple(p for p in show.sets[position].placements if p is not current)
        owner = show.sets[position]._copied(update={"placements": kept})
        return show._copied(
            update={"sets": tuple(owner if i == position else s for i, s in enumerate(show.sets))}
        )
    if kind == 4 and value > 0:
        owner = show.sets[position]._copied(update={"counts": float("inf")})
        sets = tuple(owner if i == position else s for i, s in enumerate(show.sets))
        return show._copied(update={"sets": sets})
    return _replaced_set(show, position, current.entity, patches[kind])


def _pin_names(show: Show, target: PinTarget) -> list[str]:
    if target == "entity":
        return list(show.cast.ids())
    if target == "set":
        return list(show.set_ids())
    if target == "form":
        return sorted(show.forms) or [UNKNOWN_FORM]
    return [f"{a}→{b}" for a, b in show.transition_ids()]


def _pins(show: Show, kind: int, pick: int) -> Show:
    target = PIN_TARGETS[pick % len(PIN_TARGETS)]
    names = _pin_names(show, target)
    if kind == 6:
        return pin(show, target, names[pick % len(names)])
    if show.pins:
        record = show.pins[pick % len(show.pins)]
        return unpin(show, record.target, record.id)
    stray = (
        Pin(target="form", id=UNKNOWN_FORM),
        Pin(target="transition", id="12"),
        Pin(target="set", id=show.set_ids()[pick % len(show.sets)]),
    )[pick % 3]
    return show._copied(update={"pins": (stray,)})


def _paths(show: Show, kind: int, pick: int, value: float) -> Show:
    tid = show.transition_ids()[pick % len(show.transition_ids())]
    spec = show.transition(tid)
    spot = show.cast.ids()[pick % len(show.cast.ids())]
    if kind == 9:
        held = PathRow(entity=SpotId(spot), style=PathStyle.HOLD)
        rows = {**spec.rows, spot: held}
        return apply_change(show, [PathsOp(spec=spec.replace(rows=rows))], force=pick % 2 == 1)
    if kind == 10:
        kept = {entity: row for entity, row in spec.rows.items() if entity != spot}
        return apply_change(show, [PathsOp(spec=spec.replace(rows=kept))])
    others = tuple(t for t in show.transitions if t.id != spec.id)
    return show._copied(update={"transitions": (*others, spec.replace(counts=abs(value) + 1))})


def _forms(show: Show, kind: int, pick: int) -> Show:
    if not show.forms:
        return show
    if kind == 15:
        return _removed_form(show, pick)
    if _form_words(show) and pick % 3:
        return _added_form(show, pick) if pick % 2 else _removed_form(show, pick)
    form = show.forms[_form_id(show, pick)]
    if kind == 12:
        members = form.members[1:] if pick % 2 else (*form.members, UNKNOWN_SPOT)
        return show._copied(
            update={"forms": {**show.forms, form.id: form.replace(members=members)}}
        )
    if kind == 13:
        return show.replace(forms={**show.forms, form.id: form.replace(locked=not form.locked)})
    if kind == 14:
        owner = show.set(form.set)
        sets = tuple(owner.replace(forms=()) if s is owner else s for s in show.sets)
        return show.replace(sets=sets)
    return show


def _removed_form(show: Show, pick: int) -> Show:
    selected = _selected_forms(show)
    removed = selected[pick % len(selected)] if selected else _form_id(show, pick)
    return show.replace(forms={k: f for k, f in show.forms.items() if k != removed})


def _cast(show: Show, kind: int, pick: int, value: float) -> Show:
    spot = show.cast.spots[pick % len(show.cast.spots)]
    if kind == 16:
        spots = tuple(s.replace(active=not s.active) if s is spot else s for s in show.cast.spots)
        return show.replace(cast=show.cast.replace(spots=spots))
    if kind == 17 and pick % 4 == 0:
        late = FacingOverride(
            entities="all", mode=FacingMode.AUDIENCE, start=LATE_COUNT, end=LATE_COUNT + 1
        )
        return show._copied(update={"facing_overrides": (*show.facing_overrides, late)})
    if kind == 17:
        entities = (spot.id,) if pick % 2 else (UNKNOWN_SPOT,)
        override = FacingOverride(
            entities=entities, mode=FacingMode.AUDIENCE, start=0.0, end=abs(value) % 4 + 1
        )
        return add_facing_override(show, override)
    if kind == 18:
        return show.replace(title=f"title {pick % 3}") if pick % 2 else add_set(show, "9", 4.0)
    return show.replace(sheet=show.sheet.replace(name=f"sheet {pick % 3}"))


def _several(show: Show, pick: int, value: float) -> Show:
    first, last = show.sets[0], show.sets[-1]
    spot = first.placements[pick % len(first.placements)].entity
    ops: list[PlacementOp | FormOp] = [
        PlacementOp(set_id=first.id, placement=first.get(spot).replace(x=value)),
        PlacementOp(set_id=last.id, placement=Placement(entity=SpotId(spot), x=value, y=-value)),
    ]
    if show.forms:
        form = show.forms[_form_id(show, pick)]
        ops.append(FormOp(form=form.replace(members=tuple(reversed(form.members)))))
    return apply_change(show, ops, force=True)


def _counts(show: Show, pick: int, value: float) -> Show:
    position = 1 + pick % (len(show.sets) - 1)
    if pick % 3:
        return set_counts(show, show.set_ids()[position], COUNTS[pick % len(COUNTS)], force=True)
    counts = (-abs(value), float(MAX_COUNTS), 0.0)[pick // 3 % 3]
    owner = show.sets[position]._copied(update={"counts": counts})
    return show._copied(
        update={"sets": tuple(owner if s is show.sets[position] else s for s in show.sets)}
    )


def _selector(show: Show, pick: int) -> str:
    template = SELECTORS[pick % len(SELECTORS)]
    groups = sorted(show.cast.groups)
    return template.format(
        form=_form_id(show, pick // len(SELECTORS)),
        group=groups[pick // len(SELECTORS) % len(groups)] if groups else EXTRA_GROUP.key,
        tag=TAGS[pick // len(SELECTORS) % len(TAGS)],
        selection=SELECTION,
        prefix=show.cast.ids()[pick % len(show.cast.ids())][0],
        added=ADDED_FORM_SUFFIX,
    )


def _toward(show: Show, pick: int) -> EntityFacing:
    target = UNKNOWN_SPOT if pick % 3 == 0 else show.cast.ids()[pick % len(show.cast.ids())]
    return EntityFacing(entity=target)


def _overrides(show: Show, kind: int, pick: int, value: float) -> Show:
    end = abs(value) % 4 + 1
    if kind == 20:
        override = FacingOverride(
            entities=_selector(show, pick), mode=FacingMode.AUDIENCE, start=0.0, end=end
        )
        return add_facing_override(show, override)
    if kind == 21:
        spot = show.cast.ids()[pick % len(show.cast.ids())]
        override = FacingOverride(
            entities=(spot,),
            mode=FacingMode.TOWARD_ENTITY,
            value=_toward(show, pick // 2),
            start=0.0,
            end=end,
        )
        return add_facing_override(show, override)
    if not show.facing_overrides:
        return show
    dropped = pick % len(show.facing_overrides)
    kept = tuple(o for i, o in enumerate(show.facing_overrides) if i != dropped)
    return show.replace(facing_overrides=kept)


def _facing_rows(show: Show, pick: int) -> Show:
    tid = show.transition_ids()[pick % len(show.transition_ids())]
    spec = show.transition(tid)
    entity = UNKNOWN_SPOT if pick % 4 == 0 else show.cast.ids()[pick % len(show.cast.ids())]
    row = PathRow(
        entity=SpotId(entity),
        style=PathStyle.HOLD,
        facing_mode=FacingMode.TOWARD_ENTITY,
        facing_value=_toward(show, pick // 4),
    )
    others = tuple(t for t in show.transitions if t.id != spec.id)
    edited = spec.replace(rows={**spec.rows, row.entity: row})
    return show._copied(update={"transitions": (*others, edited)})


def _routes(show: Show, pick: int) -> Show:
    tid = show.transition_ids()[pick % len(show.transition_ids())]
    spec = show.transition(tid)
    if spec.routes and pick % 2:
        others = tuple(t for t in show.transitions if t.id != spec.id)
        return show._copied(update={"transitions": (*others, spec.replace(routes={}))})
    leader = show.set(tid[0]).placements[pick % len(show.set(tid[0]).placements)]
    waypoint = (leader.x + ROUTE_STEP_M, leader.y)
    route = make_route(show, tid, leader.entity, [waypoint], members=[leader.entity])
    return follow_and_set_targets(show, tid, route)


def _groups(show: Show, pick: int) -> Show:
    if pick % 3 == 1:
        key = sorted(show.cast.groups)[pick % len(show.cast.groups)]
        return edit_group(show, key, color=GROUP_COLORS[pick % len(GROUP_COLORS)])
    if EXTRA_GROUP.key in show.cast.groups:
        return remove_group(show, EXTRA_GROUP.key)[0]
    return add_group(show, EXTRA_GROUP)


def _selections(show: Show, pick: int) -> Show:
    if pick % 3 == 0:
        return show.replace(cast=show.cast.replace(selections={}))
    ids = show.cast.ids()
    picked = [ids[pick % len(ids)], ids[(pick + 1) % len(ids)]]
    return define_selection(show, SELECTION, picked, replace=True)


def _tags(show: Show, pick: int) -> Show:
    spot = show.cast.spots[pick % len(show.cast.spots)]
    tags = () if spot.tags else (TAGS[pick % len(TAGS)],)
    spots = tuple(s.replace(tags=tags) if s is spot else s for s in show.cast.spots)
    return show.replace(cast=show.cast.replace(spots=spots))


def _reorder_pins(show: Show, pick: int) -> Show:
    if len(show.pins) < 2:
        target = PIN_TARGETS[pick % len(PIN_TARGETS)]
        names = _pin_names(show, target)
        return pin(show, target, names[pick % len(names)])
    turn = 1 + pick % (len(show.pins) - 1)
    return show._copied(update={"pins": show.pins[turn:] + show.pins[:turn]})


def _sections(show: Show, kind: int, pick: int) -> Show:
    if len(show.sets) < 3:
        return show
    inner = show.set_ids()[1:-1]
    set_id = inner[pick % len(inner)]
    if kind == 29:
        return remove_set(show, set_id, merge_counts=pick % 2 == 0, force=pick % 3 == 0)
    first = inner[pick // 2 % len(inner)]
    skipped = show.set_ids()[show.set_ids().index(first) + 2 :]
    resumed = skipped[0] if pick % 2 and skipped else first
    return join_sections(split_section(show, first)[0], split_section(show, resumed)[1])


def _forced_form(show: Show, pick: int) -> Show:
    if not show.forms:
        return show
    pinned = sorted(p.id for p in show.pins if p.target == "form" and p.id in show.forms)
    form_id = FormId(pinned[pick % len(pinned)]) if pinned else _form_id(show, pick)
    form = show.forms[form_id]
    outside = [spot for spot in show.cast.ids() if spot not in form.members]
    if len(form.members) > 1 and (pick % 2 or not outside):
        members = form.members[:-1]
    else:
        members = (*form.members, outside[pick % len(outside)])
    edited = form.replace(members=members, locked=not form.locked)
    return apply_change(show, [FormOp(form=edited)], force=True)


def _resolved(show: Show, kind: int, pick: int, value: float) -> Show:
    if kind <= 22:
        return _overrides(show, kind, pick, value)
    edits = {
        23: lambda: _facing_rows(show, pick),
        24: lambda: _routes(show, pick),
        25: lambda: _groups(show, pick),
        26: lambda: _selections(show, pick),
        27: lambda: _tags(show, pick),
        28: lambda: _reorder_pins(show, pick),
        31: lambda: _forced_form(show, pick),
        32: lambda: _added_form(show, pick),
    }
    return edits[kind]() if kind in edits else _sections(show, kind, pick)


def _edited(show: Show, step: Step) -> Show:
    kind, pick, value = step
    if kind >= 20:
        return _resolved(show, kind, pick, value)
    if kind <= 4:
        return _placement(show, kind, pick, value)
    if kind == 5:
        return _counts(show, pick, value)
    if kind in (6, 7):
        return _pins(show, kind, pick)
    if kind == 8:
        return _several(show, pick, value)
    if kind <= 11:
        return _paths(show, kind, pick, value)
    if kind <= 15:
        return _forms(show, kind, pick)
    return _cast(show, kind, pick, value)


def _applied(show: Show, steps_taken: list[Step]) -> Show:
    for step in steps_taken:
        try:
            show = _edited(show, step)
        except E8sError:
            continue
    return show


# Each example below fails if revalidate skips one cross-item recheck: in order, one spot
# moved; sets naming a changed form; the owners of a removed form; entity, set, form and
# transition pins; facing overrides after a count edit; a transition after its destination's
# counts changed; a form's members after a placement edit; inactive spots after a spot edit;
# a transition skipped for negative counts; a non-finite count added to a finite show; a
# previous report holding zero-count and missing-spot issues. The rest reach one new edit kind
# each: an override selecting a removed form; `tag:`, `sel:`, `group:` and `T*` overrides
# under tag, selection, group and pin edits; overrides facing a known and an unknown spot;
# override removal; rows of an unknown spot or facing one; routes; pin reorder; `remove_set`;
# `join_sections` skipping a set; a forced edit of a pinned form; a form added under the name
# an override's `form:` word looks for.
@example(seed=0, earlier=[], later=[(0, 7, 3.0)])
@example(seed=3, earlier=[(11, 33, 7.5), (1, 21, 3.0)], later=[(17, 11, 3.0), (15, 21, 0.0)])
@example(seed=2, earlier=[(15, 39, 7.5)], later=[(15, 58, 1.0)])
@example(seed=0, earlier=[(6, 32, 0.0)], later=[(3, 20, 1.0)])
@example(seed=0, earlier=[(7, 56, 0.0)], later=[(6, 44, 7.5)])
@example(seed=0, earlier=[(6, 2, 0.0)], later=[(13, 2, 0.0)])
@example(seed=0, earlier=[(6, 3, 0.0)], later=[(9, 3, 0.0)])
@example(seed=3, earlier=[(6, 58, 1.0), (17, 0, -4.0)], later=[(5, 35, 3.0)])
@example(seed=0, earlier=[], later=[(11, 17, -4.0), (5, 33, 7.5)])
@example(seed=3, earlier=[], later=[(1, 35, 0.0), (9, 26, 0.0)])
@example(seed=3, earlier=[], later=[(5, 4, 0.0), (16, 57, 3.0)])
@example(seed=0, earlier=[(14, 9, 3.0)], later=[(5, 27, -4.0)])
@example(seed=1, earlier=[], later=[(4, 5, 3.0)])
@example(seed=0, earlier=[(5, 6, 0.0), (2, 0, 0.0)], later=[(0, 7, 3.0)])
@example(seed=0, earlier=[(20, 0, 0.0)], later=[(15, 0, 0.0)])
@example(seed=0, earlier=[(20, 2, 0.0)], later=[(27, 0, 0.0)])
@example(seed=1, earlier=[(20, 3, 0.0)], later=[(26, 1, 0.0)])
@example(seed=2, earlier=[(25, 0, 0.0), (20, 7, 0.0)], later=[(25, 2, 0.0)])
@example(seed=3, earlier=[(20, 4, 0.0)], later=[(28, 0, 0.0)])
@example(seed=0, earlier=[], later=[(21, 1, 3.0), (21, 0, 2.0)])
@example(seed=1, earlier=[(21, 0, 3.0)], later=[(22, 0, 0.0)])
@example(seed=2, earlier=[], later=[(23, 0, 0.0), (23, 1, 0.0)])
@example(seed=3, earlier=[], later=[(24, 1, 0.0)])
@example(seed=0, earlier=[(6, 0, 0.0), (6, 1, 0.0)], later=[(28, 0, 0.0)])
@example(seed=1, earlier=[], later=[(29, 0, 0.0)])
@example(seed=2, earlier=[], later=[(30, 1, 0.0)])
@example(seed=0, earlier=[(6, 2, 0.0)], later=[(31, 1, 0.0)])
@example(seed=4, earlier=[], later=[(32, 0, 0.0)])
@given(seed=seeds, earlier=earlier_edits, later=edits)
def test_revalidate_equals_validate_show_after_random_edits(
    seed: int, earlier: list[Step], later: list[Step]
) -> None:
    """Gives exactly validate_show's report of the edited show, issue order included."""
    before = _applied(_base(seed), earlier)
    after = _applied(before, later)
    assert revalidate(validate_show(before), before, after) == validate_show(after)
