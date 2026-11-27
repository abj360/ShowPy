"""
pin_flags.py --- tells which items a held pin covers and sets their flags

Contains:
    covers_placement(): tells whether any held pin covers a spot's placement in a set
    covers_form(): tells whether any held pin covers a form
    with_pin_flags(): sets the flag of every placement, form and path row a held pin covers
"""

from collections.abc import Sequence

from e8s.choreo.match_ops import forms_of_set
from e8s.choreo.models import PlacementPatch, Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.errors import SchemaError
from e8s.core.ids import TransitionId, parse_tid
from e8s.show.models import Pin, Show


def covers_placement(held: Sequence[Pin], set_id: str, entity: str) -> bool:
    """Tells whether an entity pin on the spot or a set pin on the set covers the placement.

    >>> from e8s.show.models import Pin
    >>> covers_placement((Pin(target="set", id="2"),), "2", "T1")
    True
    """
    return any(
        (p.target == "entity" and p.id == entity) or (p.target == "set" and p.id == set_id)
        for p in held
    )


def covers_form(show: Show, held: Sequence[Pin], form_id: str) -> bool:
    """Tells whether a form pin on the form or a set pin on a set listing it covers the form.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.models import Pin
    >>> covers_form(two_set_show(), (Pin(target="form", id="row"),), "row")
    True
    """
    # a set pin may name a set that no longer exists; forms_of_set would raise on it
    return any(
        (p.target == "form" and p.id == form_id)
        or (p.target == "set" and form_id in forms_of_set(show, p.id))
        for p in held
        if p.target == "form" or p.id in show.set_ids()
    )


def _flagged_set(show: Show, owner: Set) -> Set:
    updates = {
        p.entity: PlacementPatch(pinned=True)
        for p in owner.placements
        if not p.pinned and covers_placement(show.pins, owner.id, p.entity)
    }
    return owner.with_placements(updates, force=True) if updates else owner


def _pinned_pairs(show: Show) -> set[TransitionId]:
    pairs: set[TransitionId] = set()
    for record in show.pins:
        if record.target != "transition":
            continue
        try:
            pairs.add(parse_tid(record.id))
        except SchemaError:
            continue
    return pairs & set(show.transition_ids())


def _flagged_spec(spec: TransitionSpec, pinned: set[TransitionId]) -> TransitionSpec:
    if spec.id not in pinned or all(row.locked for row in spec.rows.values()):
        return spec
    return spec.replace(rows={e: row.replace(locked=True) for e, row in spec.rows.items()})


def with_pin_flags(show: Show) -> Show:
    """Sets the flag of every placement, form and path row a held pin covers; clears none.

    A forced edit can add an item under a pin, or replace one, without its flag; this restores
    the flag the pin implies, so the record and the flags agree.

    Args:
        show: Show after an edit.

    Returns:
        show: The show with every covered flag set; `show` itself when none was missing.

    >>> from e8s.examples import two_set_show
    >>> with_pin_flags(two_set_show()) == two_set_show()
    True
    """
    if not show.pins:
        return show
    sets = tuple(_flagged_set(show, owner) for owner in show.sets)
    unlocked = [
        k for k, f in show.forms.items() if not f.locked and covers_form(show, show.pins, k)
    ]
    forms = {**show.forms, **{k: show.forms[k].replace(locked=True) for k in unlocked}}
    pinned = _pinned_pairs(show)
    specs = tuple(_flagged_spec(spec, pinned) for spec in show.transitions)
    if sets == show.sets and not unlocked and specs == show.transitions:
        return show
    return show.replace(sets=sets, forms=forms, transitions=specs)
