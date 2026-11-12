#!/usr/bin/env python3

"""
form_place.py --- builds sets from forms and regenerates a form's placements in its show

Contains:
    place(): builds a set's placements from forms, matching spots into open slots
    filled_forms(): returns forms whose members are read back from a placed set
    rebuild(): regenerates one form's placements and resets its neighbouring paths
"""

from collections.abc import Mapping, Sequence

import numpy as np

from stage.choreo.form_fns import DEFAULT_FORM_FNS, FormFnRegistry
from stage.choreo.forms import placements, slot_poses
from stage.choreo.match import match
from stage.choreo.match_ops import clear_rows, with_set
from stage.choreo.models import Form, MatchOptions, Placement, Set
from stage.core.errors import MatchError, SchemaError, UnknownRefError
from stage.core.ids import FormId, SetId, SpotId
from stage.core.types import Degrees, Points
from stage.core.util import close_matches
from stage.show.models import Show

type SlotRef = tuple[Form, int]


def _check_disjoint(forms: Sequence[Form]) -> None:
    seen: dict[SpotId, FormId] = {}
    for form in forms:
        for member in form.members:
            if member in seen:
                raise SchemaError(
                    f"{member} is in forms {seen[member]} and {form.id}",
                    hint="put each spot in one form per set",
                    context={"spot": member},
                )
            seen[member] = form.id


def _open_slots(
    forms: Sequence[Form], registry: FormFnRegistry
) -> tuple[list[SlotRef], Points, list[Degrees]]:
    refs: list[SlotRef] = []
    points, facings = [], []
    for form in forms:
        pts, poses = slot_poses(form, registry)
        for index in range(len(form.members), len(pts)):
            refs.append((form, index))
            points.append(pts[index])
            facings.append(poses[index])
    return refs, np.asarray(points, dtype=float).reshape(-1, 2), facings


def _previous(show: Show, set_id: SetId) -> Set:
    index = show.set_index(set_id)
    if index == 0:
        raise MatchError(
            f"set {set_id} is the first set; open slots have nobody to match from",
            hint="give every form its members in the first set",
            context={"set": set_id},
        )
    return show.sets[index - 1]


def _placement(current: Placement, update: Placement) -> Placement:
    return update.model_copy(update={"note": current.note, "on_field": True})


def _matched(
    show: Show,
    target: Set,
    forms: Sequence[Form],
    options: MatchOptions | None,
    registry: FormFnRegistry,
) -> list[Placement]:
    refs, points, facings = _open_slots(forms, registry)
    taken = {m for form in forms for m in form.members}
    pool = [p.entity for p in target.placements if p.entity not in taken and not p.pinned]
    if not refs or not pool:
        return []
    prev = _previous(show, target.id)
    candidates = [e for e in pool if e in prev.entity_ids() and prev.get(e).on_field]
    if len(candidates) > len(refs):
        raise MatchError(
            f"{len(candidates)} spots without a form but only {len(refs)} open slots",
            hint="add members to forms, add slots with n=, or place those spots elsewhere",
            context={"spots": candidates, "open_slots": len(refs)},
        )
    groups = {spot.id: spot.group for spot in show.cast.spots}
    result = match(prev, points, entities=candidates, options=options, groups=groups)
    return [
        Placement(
            entity=e,
            x=float(points[j, 0]),
            y=float(points[j, 1]),
            facing=facings[j],
            form=refs[j][0].id,
            order=refs[j][1],
        )
        for e, j in result.assignment.items()
    ]


def place(
    show: Show,
    set_id: SetId,
    forms: Sequence[Form],
    match_options: MatchOptions | None = None,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
) -> Set:
    """Builds a set's placements from forms; spots without a form fill open slots.

    Members stand on their form's slots. Slots past a form's last member are open: spots of
    the set that belong to no given form are matched into them from the previous set.
    Pinned placements stay untouched. Each placement's `form` and `order` record membership
    (read it back with filled_forms()).

    >>> from stage.choreo.forms import line_form
    >>> from tests.fixtures.shows import two_set_show
    >>> show = two_set_show()
    >>> row = line_form("row", ["T1", "T2"], (0, 12), (12, 12), n=4)
    >>> placed = place(show, "2", [row])
    >>> [(p.entity, p.x, p.order) for p in placed.placements]
    [('T1', 0.0, 0), ('T2', 4.0, 1), ('T3', 8.0, 2), ('T4', 12.0, 3)]
    """
    target = show.set(set_id)
    stamped = [form.model_copy(update={"set": set_id}) for form in forms]
    _check_disjoint(stamped)
    pinned = {p.entity for p in target.placements if p.pinned}
    rows = [p for form in stamped for p in placements(form, registry) if p.entity not in pinned]
    rows += _matched(show, target, stamped, match_options, registry)
    updates = {p.entity: _placement(target.get(p.entity), p) for p in rows}
    form_ids = tuple(dict.fromkeys((*target.forms, *(f.id for f in stamped))))
    placed = target.with_placements(updates)
    return placed.model_copy(update={"forms": form_ids})


def filled_forms(placed: Set, forms: Sequence[Form]) -> list[Form]:
    """Returns forms whose members are read back from a placed set, in slot order.

    >>> from stage.choreo.forms import line_form
    >>> from tests.fixtures.shows import two_set_show
    >>> show = two_set_show()
    >>> row = line_form("row", ["T1"], (0, 12), (12, 12), n=4)
    >>> filled_forms(place(show, "2", [row]), [row])[0].members
    ('T1', 'T2', 'T3', 'T4')
    """
    filled = []
    for form in forms:
        rows = sorted(
            (p for p in placed.placements if p.form == form.id and p.order is not None),
            key=lambda p: p.order or 0,
        )
        if [p.order for p in rows] != list(range(len(rows))):
            raise SchemaError(
                f"form {form.id} has open slots between its members",
                hint="fill every open slot, or give the form n equal to its spots",
                context={"form": form.id, "orders": [p.order for p in rows]},
            )
        members = tuple(p.entity for p in rows)
        filled.append(form.model_copy(update={"set": placed.id, "members": members}))
    return filled


def _form_of(forms: Mapping[FormId, Form], form_id: FormId) -> Form:
    if form_id in forms:
        return forms[form_id]
    close = close_matches(form_id, forms)
    raise UnknownRefError(
        f"unknown form {form_id!r}",
        hint=f"did you mean {', '.join(close)}?" if close else "check the form id",
        context={"form": form_id},
    )


def rebuild(
    show: Show, form_id: FormId, force: bool = False, registry: FormFnRegistry = DEFAULT_FORM_FNS
) -> Show:
    """Regenerates a form's placements in its set and resets its members' neighbouring paths.

    Pinned members stay where they are. Stored path rows of the moved members in the
    transitions into and out of the set are dropped, so they move straight; locked rows
    need force=True.

    >>> from stage.choreo.forms import line_form
    >>> from tests.fixtures.shows import two_set_show
    >>> show = two_set_show()
    >>> row = line_form("row", ["T1", "T2", "T3", "T4"], (0, 12), (6, 12), set_id="2")
    >>> rebuild(show.model_copy(update={"forms": {"row": row}}), "row").set("2").get("T4").y
    12.0
    """
    form = _form_of(show.forms, form_id)
    target = show.set(form.set)
    fresh = [p for p in placements(form, registry) if not target.get(p.entity).pinned]
    updates = {p.entity: _placement(target.get(p.entity), p) for p in fresh}
    edited = with_set(show, target.with_placements(updates))
    for tid in edited.transition_ids():
        if form.set in tid:
            edited = clear_rows(edited, tid, list(updates), force)
    return edited
