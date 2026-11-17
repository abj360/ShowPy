"""
form_place.py --- builds sets from forms and regenerates a form's placements in its show

Contains:
    place(): builds a set from forms, matching spots into open slots, and returns the filled forms
    filled_forms(): returns forms whose members are read back from a placed set
    rebuild(): regenerates one form's placements and resets its neighbouring paths
"""

from collections.abc import Mapping, Sequence

import numpy as np

from showpy.choreo.form_fns import DEFAULT_FORM_FNS, FormFnRegistry
from showpy.choreo.form_layout import slot_count
from showpy.choreo.form_text import require_edited_members
from showpy.choreo.forms import placements, slot_poses
from showpy.choreo.match import match
from showpy.choreo.match_ops import clear_rows, forms_of_set, is_elsewhere, with_set
from showpy.choreo.models import Form, MatchOptions, Placement, Set
from showpy.core.errors import MatchError, SchemaError, ShowPyError, UnknownRefError
from showpy.core.ids import FormId, SetId, SpotId
from showpy.core.types import Degrees, Points
from showpy.core.util import close_matches
from showpy.show.models import Show

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


def _check_text_counts(forms: Sequence[Form]) -> None:
    for form in forms:
        count = slot_count(form)
        if form.kind == "text" and count is not None:
            require_edited_members(form.id, count, len(form.members))


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
    return update.replace(note=current.note, pinned=current.pinned, on_field=True)


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


def _clear_stale_forms(target: Set, placed_ids: set[SpotId]) -> dict[SpotId, Placement]:
    return {
        p.entity: p.replace(form=None, order=None)
        for p in target.placements
        if p.entity not in placed_ids and not p.pinned and p.form is not None
    }


def place(
    show: Show,
    set_id: SetId,
    forms: Sequence[Form],
    match_options: MatchOptions | None = None,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
) -> tuple[Set, list[Form]]:
    """Builds a set's placements from forms and returns it with the forms filled.

    Members stand on their form's slots. Slots past a form's last member are open: spots of
    the set that belong to no given form are matched into them from the previous set.
    Pinned placements stay untouched. Each placement's `form` and `order` record membership.
    The set lists the placed forms plus any form a pinned placement still references; other
    placements that pointed at a form no longer placed lose their `form` and `order`. The
    show is not changed: store the returned forms, which carry the matched spots as members.
    A text form has no open slots: one whose member count differs from its `n` (a deleted
    spot left it short) raises `MEMBER_COUNT_MISMATCH`; fix it with `edit_text_count`.

    Args:
        show: Show that holds the set and its previous set.
        set_id: Set to build.
        forms: Forms to place; each is stamped with `set_id`.
        match_options: Options for matching spots into open slots.
        registry: Form generators that produce the slots.

    Returns:
        placed_and_forms: The placed set, and the forms in the order given with members filled.

    >>> from showpy.choreo.forms import line_form
    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> row = line_form("row", ["T1", "T2"], (0, 12), (12, 12), n=4)
    >>> placed, (filled,) = place(show, "2", [row])
    >>> [(p.entity, p.x, p.order) for p in placed.placements]
    [('T1', 0.0, 0), ('T2', 4.0, 1), ('T3', 8.0, 2), ('T4', 12.0, 3)]
    >>> filled.members
    ('T1', 'T2', 'T3', 'T4')
    """
    target = show.set(set_id)
    stamped = [form.replace(set=set_id) for form in forms]
    _check_disjoint(stamped)
    _check_text_counts(stamped)
    pinned = {p.entity for p in target.placements if p.pinned}
    rows = [p for form in stamped for p in placements(form, registry) if p.entity not in pinned]
    rows += _matched(show, target, stamped, match_options, registry)
    updates = {p.entity: _placement(target.get(p.entity), p) for p in rows}
    updates.update(_clear_stale_forms(target, set(updates)))
    pinned_forms = (p.form for p in target.placements if p.pinned and p.form is not None)
    form_ids = tuple(dict.fromkeys((*(f.id for f in stamped), *pinned_forms)))
    placed = target.with_placements(updates).replace(forms=form_ids)
    return placed, filled_forms(placed, stamped)


def filled_forms(placed: Set, forms: Sequence[Form]) -> list[Form]:
    """Returns forms whose members include every spot placed on them, in slot order.

    A form's own members keep their place, even a pinned member whose slot stayed open. The
    other spots placed on the form follow in increasing `order`. Gaps in the orders are
    compacted, so rebuilding a compacted form may shift spots that took non-lowest open slots,
    until open slots are typed (Phase 2).

    Args:
        placed: Set whose placements carry `form` and `order`.
        forms: Forms to read back.

    Returns:
        filled: The forms stamped with the set's id and their members filled.

    Raises:
        SchemaError: Two placements hold the same order on one form.

    >>> from showpy.choreo.forms import line_form
    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> row = line_form("row", ["T1"], (0, 12), (12, 12), n=4)
    >>> placed, _ = place(show, "2", [row])
    >>> filled_forms(placed, [row])[0].members
    ('T1', 'T2', 'T3', 'T4')
    """
    return [_filled(placed, form) for form in forms]


def _filled(placed: Set, form: Form) -> Form:
    rows = sorted(
        (p for p in placed.placements if p.form == form.id and p.order is not None),
        key=lambda p: p.order or 0,
    )
    orders = [p.order for p in rows]
    if len(set(orders)) != len(orders):
        raise SchemaError(
            f"form {form.id} has two spots on one slot",
            hint="give each spot of a form its own order",
            context={"form": form.id, "orders": orders},
        )
    kept = set(form.members)
    members = (*form.members, *(p.entity for p in rows if p.entity not in kept))
    return form.replace(set=placed.id, members=members)


def _form_of(forms: Mapping[FormId, Form], form_id: FormId) -> Form:
    if form_id in forms:
        return forms[form_id]
    close = close_matches(form_id, forms)
    raise UnknownRefError(
        f"unknown form {form_id!r}",
        hint=f"did you mean {', '.join(close)}?" if close else "check the form id",
        context={"form": form_id},
    )


def _check_placed(show: Show, form: Form) -> None:
    if form.id not in forms_of_set(show, form.set):
        listed = list(show.set(form.set).forms)
        raise ShowPyError(
            f"set {form.set} no longer places form {form.id}; it places {', '.join(listed)}",
            hint="re-place the set with this form, or delete the form",
            context={"form": form.id, "set": form.set, "set_forms": listed},
            code="STALE_FORM",
        )


def rebuild(
    show: Show, form_id: FormId, force: bool = False, registry: FormFnRegistry = DEFAULT_FORM_FNS
) -> Show:
    """Regenerates a form's placements in its set and resets its members' neighbouring paths.

    Every non-pinned member takes its regenerated pose, form and order, keeping its note. A
    member moved when its x or y changes by more than `SAME_POSITION_TOL` or it was off the
    field. Stored path rows of the moved members in the transitions into and out of the set
    are dropped, so they move straight; rows of unmoved members are kept, since a facing
    change does not alter a row's geometry. A locked row of a moved member needs force=True.
    Pinned members stay where they are unless force=True moves them; they keep their pin.
    A form its set no longer lists (the set was re-placed without it) raises STALE_FORM.

    >>> from showpy.choreo.forms import line_form
    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> row = line_form("row", ["T1", "T2", "T3", "T4"], (0, 12), (6, 12), set_id="2")
    >>> rebuild(show.replace(forms={"row": row}), "row").set("2").get("T4").y
    12.0
    """
    form = _form_of(show.forms, form_id)
    _check_placed(show, form)
    target = show.set(form.set)
    fresh = [p for p in placements(form, registry) if force or not target.get(p.entity).pinned]
    updates = {p.entity: _placement(target.get(p.entity), p) for p in fresh}
    moved = [e for e, p in updates.items() if is_elsewhere(target.get(e), p.x, p.y)]
    edited = with_set(show, target.with_placements(updates, force=force))
    for tid in edited.transition_ids():
        if form.set in tid:
            edited = clear_rows(edited, tid, moved, force)
    return edited
