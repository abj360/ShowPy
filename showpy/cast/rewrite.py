#!/usr/bin/env python3

"""
rewrite.py --- rewrites or drops spot IDs in every place a show refers to them

Contains:
    Rename: names a function from an old spot ID to its new ID, or None to drop it
    faces_entity(): returns the spot a facing aims at, if any
    rewrite_spot_ids(): applies a rename to every spot reference in a show
"""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from showpy.cast.models import Cast
from showpy.cast.select import is_label_dependent, select
from showpy.choreo.models import FacingMode, FacingOverride, Form, Route, Set, TransitionSpec
from showpy.core.ids import SpotId
from showpy.core.selection import Selection, SelectorField
from showpy.core.types import Point
from showpy.notes.models import Note
from showpy.sheet.models import Zone

if TYPE_CHECKING:
    from showpy.show.models import Show

type Rename = Callable[[SpotId], SpotId | None]
type FacingValue = float | Point | SpotId | None


def faces_entity(mode: FacingMode, value: FacingValue) -> SpotId | None:
    """Returns the spot a facing aims at, or None when it aims at an angle or a point.

    >>> faces_entity(FacingMode.TOWARD_ENTITY, "T2"), faces_entity(FacingMode.FIXED, 90.0)
    ('T2', None)
    """
    return value if mode == FacingMode.TOWARD_ENTITY and isinstance(value, str) else None


def _keep(ids: Sequence[SpotId], rename: Rename) -> tuple[SpotId, ...]:
    return tuple(new for new in map(rename, ids) if new is not None)


def rewrite_spot_ids(show: "Show", rename: Rename) -> "Show":
    """Applies `rename` to every spot reference in a show; None drops the reference.

    Covers the cast, named selections, placements, forms, path rows (keys, entities and
    entity facings), routes, note anchors, facing overrides and show zones. String
    selectors that name labels are frozen to the rewritten ID list; others stay as written.
    Callers check that the result keeps IDs unique.

    >>> from showpy.examples import two_set_show
    >>> rewrite_spot_ids(two_set_show(), lambda i: None if i == "T1" else i).cast.ids()
    ('T2', 'T3', 'T4')
    """
    cast = show.cast
    spots = tuple(
        s.model_copy(update={"id": new}) for s in cast.spots if (new := rename(s.id)) is not None
    )
    selections = {
        name: frozenset(_keep(sorted(ids), rename)) for name, ids in cast.selections.items()
    }
    updates = {
        "sets": tuple(_rewrite_set(s, rename) for s in show.sets),
        "forms": {fid: _rewrite_form(f, rename) for fid, f in show.forms.items()},
        "transitions": tuple(_rewrite_spec(spec, rename) for spec in show.transitions),
        "notes": tuple(_rewrite_note(n, rename) for n in show.notes),
        "facing_overrides": tuple(
            _rewrite_override(show, o, rename) for o in show.facing_overrides
        ),
        "zones": tuple(_rewrite_zone(show, z, rename) for z in show.zones),
    }
    new_cast = Cast.model_validate({**dict(cast), "spots": spots, "selections": selections})
    return show.model_copy(update={"cast": new_cast, **updates})


def _rewrite_set(set_: Set, rename: Rename) -> Set:
    placements = tuple(
        p.model_copy(update={"entity": new})
        for p in set_.placements
        if (new := rename(p.entity)) is not None
    )
    return set_.model_copy(update={"placements": placements})


def _rewrite_form(form: Form, rename: Rename) -> Form:
    return form.model_copy(update={"members": _keep(form.members, rename)})


def _rewrite_route(route: Route, rename: Rename) -> Route:
    kept = [
        (new, offset)
        for member, offset in zip(route.members, route.offsets, strict=True)
        if (new := rename(member)) is not None
    ]
    return route.model_copy(
        update={"members": tuple(m for m, _ in kept), "offsets": tuple(o for _, o in kept)}
    )


def _rewrite_value(mode: FacingMode, value: FacingValue, rename: Rename) -> FacingValue:
    target = faces_entity(mode, value)
    return value if target is None else rename(target)


def _rewrite_spec(spec: TransitionSpec, rename: Rename) -> TransitionSpec:
    rows = {}
    for row in spec.rows.values():
        new = rename(row.entity)
        if new is not None:
            value = _rewrite_value(row.facing_mode, row.facing_value, rename)
            rows[new] = row.model_copy(update={"entity": new, "facing_value": value})
    routes = {rid: _rewrite_route(r, rename) for rid, r in spec.routes.items()}
    return spec.model_copy(update={"rows": rows, "routes": routes})


def _rewrite_note(note: Note, rename: Rename) -> Note:
    anchor = note.anchor
    if anchor.spots is None:
        return note
    moved = anchor.model_copy(update={"spots": _keep(anchor.spots, rename)})
    return note.model_copy(update={"anchor": moved})


def _rewrite_selector(show: "Show", selector: SelectorField, rename: Rename) -> SelectorField:
    if isinstance(selector, Selection):
        return selector.model_copy(update={"ids": _keep(selector.ids, rename)})
    if isinstance(selector, str):
        if not is_label_dependent(selector):
            return selector
        return _keep(select(show, selector).ids, rename)
    return _keep(selector, rename)


def _rewrite_override(show: "Show", override: FacingOverride, rename: Rename) -> FacingOverride:
    entities = _rewrite_selector(show, override.entities, rename)
    value = _rewrite_value(override.mode, override.value, rename)
    return override.model_copy(update={"entities": entities, "value": value})


def _rewrite_zone(show: "Show", zone: Zone, rename: Rename) -> Zone:
    return zone.model_copy(update={"applies_to": _rewrite_selector(show, zone.applies_to, rename)})
