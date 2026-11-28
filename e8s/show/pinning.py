"""
pinning.py --- designer locks: records pins and sets the flags they cover

Contains:
    pin_key(): returns the stored spelling of a pin ID
    pin(): locks a spot, form, set or transition and records why
    unpin(): removes a lock, keeping flags other pins still cover
"""

from collections.abc import Sequence

from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import PathRow
from e8s.core.errors import UnknownRefError
from e8s.core.ids import format_tid, parse_tid
from e8s.core.util import close_matches
from e8s.show.changes import store_paths
from e8s.show.collab_models import PathsOp
from e8s.show.models import Pin, PinTarget, Show
from e8s.show.pin_flags import covers_form, covers_placement


def _require_target(show: Show, target: PinTarget, target_id: str) -> None:
    if target == "entity":
        show.cast.spot(target_id)
    elif target == "set":
        show.set(target_id)
    elif target == "transition":
        show.transition(parse_tid(target_id))
    elif target_id not in show.forms:
        close = close_matches(target_id, show.forms)
        raise UnknownRefError(
            f"unknown form {target_id!r}",
            hint=f"did you mean {', '.join(close)}?" if close else "check the form id",
            context={"form": target_id},
        )


def _flag_placements(show: Show, held: Sequence[Pin], scope: Pin) -> Show:
    sets = []
    for current in show.sets:
        updates = {
            p.entity: PlacementPatch(pinned=covers_placement(held, current.id, p.entity))
            for p in current.placements
            if (scope.target == "entity" and p.entity == scope.id)
            or (scope.target == "set" and current.id == scope.id)
        }
        sets.append(current.with_placements(updates, force=True))
    return show.replace(sets=tuple(sets))


def _flag_forms(show: Show, held: Sequence[Pin], scope: Pin) -> Show:
    forms = {
        k: f.replace(locked=covers_form(show, held, k))
        if (scope.target == "form" and k == scope.id)
        or (scope.target == "set" and f.set == scope.id)
        else f
        for k, f in show.forms.items()
    }
    return show.replace(forms=forms)


def _flag_paths(show: Show, held: Sequence[Pin], scope: Pin) -> Show:
    tid = parse_tid(scope.id)
    spec = show.transition(tid)
    is_locked = any(p.target == "transition" and parse_tid(p.id) == tid for p in held)
    entities = dict.fromkeys([*show.set(tid[1]).entity_ids(), *spec.rows])
    rows = {e: spec.row(e).replace(locked=is_locked) for e in entities}
    if not is_locked:
        rows = {e: r for e, r in rows.items() if r != PathRow(entity=e)}
    return store_paths(show, PathsOp(spec=spec.replace(rows=rows)))


def _reflag(show: Show, held: tuple[Pin, ...], scope: Pin) -> Show:
    flagged = show.replace(pins=held)
    if scope.target == "transition":
        return _flag_paths(flagged, held, scope)
    if scope.target in ("entity", "set"):
        flagged = _flag_placements(flagged, held, scope)
    return _flag_forms(flagged, held, scope) if scope.target in ("form", "set") else flagged


def pin_key(target: PinTarget, pin_id: str) -> str:
    """Returns the stored spelling of a pin ID; transition IDs take their canonical form."""
    return format_tid(parse_tid(pin_id)) if target == "transition" else pin_id


def pin(show: Show, target: PinTarget, pin_id: str, reason: str = "", by: str = "designer") -> Show:
    """Locks an item and records why; pinning the same item again replaces the record.

    Entity pins pin the spot's placement in every set; set pins pin every placement and lock
    every form the set lists; form pins lock the form; transition pins lock a row for every
    spot. A stale form (one the set no longer lists) is not covered by its set's pin: pin the
    form itself.

    >>> from e8s.examples import two_set_show
    >>> pinned = pin(two_set_show(), "entity", "T1", "soloist")
    >>> [s.get("T1").pinned for s in pinned.sets], pinned.pins[0].reason
    ([True, True], 'soloist')
    """
    _require_target(show, target, pin_id)
    pin_id = pin_key(target, pin_id)
    record = Pin(target=target, id=pin_id, by=by, reason=reason)
    held = (*(p for p in show.pins if (p.target, p.id) != (target, pin_id)), record)
    return _reflag(show, held, record)


def unpin(show: Show, target: PinTarget, pin_id: str) -> Show:
    """Removes a lock; flags another remaining pin still covers stay set.

    >>> from e8s.examples import two_set_show
    >>> unpin(pin(two_set_show(), "set", "2"), "set", "2") == two_set_show()
    True
    """
    pin_id = pin_key(target, pin_id)
    found = [p for p in show.pins if (p.target, p.id) == (target, pin_id)]
    if not found:
        raise UnknownRefError(
            f"no {target} pin on {pin_id}",
            hint="list the locks with pins(show)",
            context={"target": target, "id": pin_id},
        )
    held = tuple(p for p in show.pins if p != found[0])
    return _reflag(show, held, found[0])
