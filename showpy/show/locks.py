#!/usr/bin/env python3

"""
locks.py --- finds edits that touch pinned placements, locked forms, locked path rows or pins

Contains:
    locked_changes(): lists every pinned or locked item that differs between two shows
    require_unlocked(): raises LockedError when an edit touches a pinned or locked item
"""

from showpy.choreo.models import PathRow, Set
from showpy.core.errors import LockedError
from showpy.core.ids import format_tid
from showpy.show.models import Show

PINS_LABEL = "pins"


def _set_pinned(show: Show, set_id: str) -> bool:
    return any(pin.target == "set" and pin.id == set_id for pin in show.pins)


def _placement_changes(before: Set, after: Set | None) -> list[str]:
    after_rows = {} if after is None else {p.entity: p for p in after.placements}
    return [
        f"placement {p.entity} in set {before.id}"
        for p in before.placements
        if p.pinned and after_rows.get(p.entity) != p
    ]


def _set_changes(before: Show, after: Show) -> list[str]:
    after_sets = {s.id: s for s in after.sets}
    found: list[str] = []
    for old in before.sets:
        new = after_sets.get(old.id)
        found.extend(_placement_changes(old, new))
        if _set_pinned(before, old.id) and (new is None or new.counts != old.counts):
            found.append(f"counts of set {old.id}")
    return found


def _form_changes(before: Show, after: Show) -> list[str]:
    return [
        f"form {form_id}"
        for form_id, form in sorted(before.forms.items())
        if form.locked and after.forms.get(form_id) != form
    ]


def _locked_rows(show: Show) -> dict[tuple[str, str], PathRow]:
    rows: dict[tuple[str, str], PathRow] = {}
    for spec in show.transitions:
        for entity, row in spec.rows.items():
            if row.locked:
                rows[(format_tid(spec.id), entity)] = row
    return rows


def _path_changes(before: Show, after: Show) -> list[str]:
    after_specs = {format_tid(spec.id): spec for spec in after.transitions}
    found: list[str] = []
    for (tid, entity), row in _locked_rows(before).items():
        spec = after_specs.get(tid)
        if spec is None or spec.rows.get(entity) != row:
            found.append(f"path {entity} in {tid}")
    return found


def locked_changes(before: Show, after: Show) -> list[str]:
    """Lists every pinned or locked item of `before` that `after` changes or drops.

    Args:
        before: Show as stored, whose pins and locks apply.
        after: Show as an edit would leave it.

    Returns:
        touched: Readable names such as `placement T1 in set 2`, in show order.

    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> pinned = show.sets[1].with_placements({"T1": {"pinned": True}})
    >>> before = show.model_copy(update={"sets": (show.sets[0], pinned)})
    >>> moved = pinned.with_placements({"T1": {"x": 9.0}}, force=True)
    >>> locked_changes(before, before.model_copy(update={"sets": (show.sets[0], moved)}))
    ['placement T1 in set 2']
    """
    pins = [PINS_LABEL] if before.pins != after.pins else []
    return [
        *pins,
        *_set_changes(before, after),
        *_form_changes(before, after),
        *_path_changes(before, after),
    ]


def require_unlocked(before: Show, after: Show, force: bool = False) -> None:
    """Raises LockedError when `after` touches what `before` pins or locks, unless forced.

    >>> from showpy.examples import two_set_show
    >>> require_unlocked(two_set_show(), two_set_show()) is None
    True
    """
    if force:
        return
    touched = locked_changes(before, after)
    if touched:
        raise LockedError(
            f"edit touches pinned or locked items: {', '.join(touched)}",
            hint="leave pinned items as they are, ask the designer to unpin, or pass force=True "
            "as the designer",
            context={"touched": touched},
        )
