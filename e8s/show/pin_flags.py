"""
pin_flags.py --- tells which items a held pin covers and sets their flags

Contains:
    covers_placement(): tells whether any held pin covers a spot's placement in a set
    covers_form(): tells whether any held pin covers a form
    flag_written(): sets the flag a held pin implies on each placement, form and row a change wrote
"""

from collections.abc import Sequence
from typing import NamedTuple

from e8s.choreo.match_ops import forms_of_set
from e8s.choreo.models import PlacementPatch, Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.errors import SchemaError
from e8s.core.ids import FormId, TransitionId, parse_tid
from e8s.show.collab_models import ChangeOp, FormOp, PathsOp, PlacementOp
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


class _HeldPins(NamedTuple):
    """Holds the IDs each kind of held pin names, built once per change."""

    entities: frozenset[str]
    sets: frozenset[str]
    forms: frozenset[str]
    pairs: frozenset[TransitionId]


def _held_pins(held: Sequence[Pin]) -> _HeldPins:
    pairs: set[TransitionId] = set()
    for record in held:
        if record.target != "transition":
            continue
        try:
            pairs.add(parse_tid(record.id))
        except SchemaError:
            continue
    return _HeldPins(
        entities=frozenset(p.id for p in held if p.target == "entity"),
        sets=frozenset(p.id for p in held if p.target == "set"),
        forms=frozenset(p.id for p in held if p.target == "form"),
        pairs=frozenset(pairs),
    )


class _Written(NamedTuple):
    """Holds what a change wrote: spots per set, form IDs and transition IDs."""

    placements: dict[str, set[str]]
    forms: set[FormId]
    pairs: set[TransitionId]


def _written(change: Sequence[ChangeOp]) -> _Written:
    written = _Written(placements={}, forms=set(), pairs=set())
    for op in change:
        if isinstance(op, PlacementOp):
            written.placements.setdefault(op.set_id, set()).add(op.placement.entity)
        elif isinstance(op, FormOp):
            written.forms.add(op.form.id)
        elif isinstance(op, PathsOp):
            written.pairs.add(op.spec.id)
    return written


def _flagged_set(owner: Set, spots: set[str], held: _HeldPins) -> Set:
    is_set_pinned = owner.id in held.sets
    updates = {
        p.entity: PlacementPatch(pinned=True)
        for p in owner.placements
        if p.entity in spots and not p.pinned and (is_set_pinned or p.entity in held.entities)
    }
    return owner.with_placements(updates, force=True) if updates else owner


def _is_form_covered(show: Show, form_id: FormId, held: _HeldPins) -> bool:
    if form_id in held.forms:
        return True
    owner = show.forms[form_id].set
    return owner in held.sets and owner in show.set_ids() and form_id in forms_of_set(show, owner)


def _flagged_spec(spec: TransitionSpec) -> TransitionSpec:
    if all(row.locked for row in spec.rows.values()):
        return spec
    return spec.replace(rows={e: row.replace(locked=True) for e, row in spec.rows.items()})


def _flagged_specs(show: Show, pairs: set[TransitionId]) -> tuple[TransitionSpec, ...]:
    live = pairs & set(show.transition_ids())
    return tuple(_flagged_spec(s) if s.id in live else s for s in show.transitions)


def flag_written(show: Show, change: Sequence[ChangeOp]) -> Show:
    """Sets the flag a held pin implies on each placement, form and path row a change wrote.

    A forced op can add or replace an item under a pin without its flag; this restores the flag
    the pin implies. Items the change did not write are left as they are, and no flag is
    cleared. The held pins are indexed once, so the cost follows what the change wrote.

    Args:
        show: Show after the change.
        change: The ops that were applied.

    Returns:
        show: The show with every written, covered flag set; `show` itself when none was missing.

    >>> from e8s.examples import two_set_show
    >>> flag_written(two_set_show(), []) == two_set_show()
    True
    """
    if not show.pins:
        return show
    held, written = _held_pins(show.pins), _written(change)
    sets = tuple(
        _flagged_set(owner, written.placements[owner.id], held)
        if owner.id in written.placements
        else owner
        for owner in show.sets
    )
    unlocked = [
        key
        for key in sorted(written.forms)
        if key in show.forms and not show.forms[key].locked and _is_form_covered(show, key, held)
    ]
    forms = {**show.forms, **{key: show.forms[key].replace(locked=True) for key in unlocked}}
    specs = _flagged_specs(show, written.pairs & held.pairs)
    if sets == show.sets and not unlocked and specs == show.transitions:
        return show
    return show.replace(sets=sets, forms=forms, transitions=specs)
