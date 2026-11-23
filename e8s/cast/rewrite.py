"""
rewrite.py --- rewrites or drops spot IDs in every place a show refers to them

Contains:
    Rename: names a function from an old spot ID to its new ID, or None to drop it
    faces_entity(): returns the spot a facing aims at, if any
    rewrite_spots(): applies a rename to every spot reference and reports what it dropped
    rewrite_spot_ids(): applies a rename to every spot reference in a show
    rewrite_note_spots(): applies a rename to the spot anchors of notes and lists the orphans
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from e8s.cast.models import Cast, RewriteReport
from e8s.cast.select import is_label_dependent, select
from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import (
    EntityFacing,
    FacingMode,
    FacingOverride,
    FacingValue,
    Route,
    TransitionSpec,
)
from e8s.core.errors import SchemaError, ShowValidationError, UnknownRefError
from e8s.core.ids import RouteId, SpotId, TransitionId
from e8s.core.selection import Selection, SelectorField
from e8s.sheet.models import Sheet, Zone
from e8s.show._notes_document import Note, NotesDocument, require_notes_of

if TYPE_CHECKING:
    from e8s.show.models import Pin, Show

type Rename = Callable[[SpotId], str | None]


@dataclass
class _Dropped:
    """Collects what one rewrite drops, so the caller can report it."""

    routes: list[tuple[TransitionId, RouteId]] = field(default_factory=list)
    facings: list[FacingOverride] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    selectors: list[str] = field(default_factory=list)


def faces_entity(mode: FacingMode, value: FacingValue | None) -> SpotId | None:
    """Returns the spot a facing aims at, or None when it aims at an angle or a point.

    >>> faces_entity(FacingMode.TOWARD_ENTITY, EntityFacing(entity="T2"))
    'T2'
    >>> faces_entity(FacingMode.AUDIENCE, None) is None
    True
    """
    if mode == FacingMode.TOWARD_ENTITY and isinstance(value, EntityFacing):
        return value.entity
    return None


def _keep(ids: Sequence[SpotId], rename: Rename) -> tuple[str, ...]:
    return tuple(new for new in map(rename, ids) if new is not None)


def _renamed_spots(show: "Show", rename: Rename) -> dict[SpotId, SpotId]:
    """Maps every kept spot to its new ID, raising SchemaError when two share one."""
    sources: dict[str, list[SpotId]] = {}
    for spot_id in show.cast.ids():
        new = rename(spot_id)
        if new is not None:
            sources.setdefault(new, []).append(spot_id)
    clashes = {new: olds for new, olds in sources.items() if len(olds) > 1}
    if clashes:
        shown = "; ".join(
            f"{' and '.join(olds)} both become {new}" for new, olds in clashes.items()
        )
        raise SchemaError(
            f"renaming would repeat IDs: {shown}",
            hint="map to labels no other spot keeps",
            context={"duplicates": sorted(clashes), "sources": clashes},
        )
    return {old: SpotId(new) for new, olds in sources.items() for old in olds}


def _require_no_dropped_row_facings(show: "Show", rename: Rename) -> None:
    """Raises ShowValidationError when a kept path row would face a dropped spot."""
    references = [
        f"{row.entity} faces {target} in {spec.id}"
        for spec in show.transitions
        for row in spec.rows.values()
        if rename(row.entity) is not None
        and (target := faces_entity(row.facing_mode, row.facing_value)) is not None
        and rename(target) is None
    ]
    if references:
        raise ShowValidationError(
            f"other spots face deleted spots: {'; '.join(references)}",
            hint="re-aim those facings at another spot or a point first",
            context={"references": references},
        )


def rewrite_spots(
    show: "Show", rename: Rename, *, notes: NotesDocument | None = None
) -> "tuple[Show, RewriteReport]":
    """Applies `rename` to every spot reference in a show and reports what it dropped.

    Covers the cast, named selections, placements, forms, path rows (keys, entities and
    entity facings), routes, facing overrides, entity pins and zones of the show and its sheet,
    and the note anchors of the notes document when one is passed. A selector that names labels
    is frozen to the rewritten ID list; a selector that cannot be resolved stays as written and
    is reported. A route left without members, a facing override left without spots or aimed at
    a dropped spot, and a note anchored only to dropped spots are removed and reported.
    Raises SchemaError when two spots would share one ID or the notes document belongs to
    another show, and ShowValidationError when a path row of a kept spot faces a dropped spot.

    Args:
        show: The show to rewrite.
        rename: Old spot ID to new ID, or None to drop the spot.
        notes: The notes document stored beside the show, or None.

    Returns:
        show: The rewritten show.
        report: What was renamed, dropped or left unresolved, and the rewritten notes document.

    >>> from e8s.examples import two_set_show
    >>> rewritten, report = rewrite_spots(two_set_show(), lambda i: "S1" if i == "T1" else i)
    >>> rewritten.cast.ids(), dict(report.renamed)
    (('S1', 'T2', 'T3', 'T4'), {'T1': 'S1'})
    """
    if notes is not None:
        require_notes_of(notes, show.id)
    renamed = _renamed_spots(show, rename)
    _require_no_dropped_row_facings(show, rename)
    dropped = _Dropped()
    cast = show.cast
    spots = tuple(s.replace(id=renamed[s.id]) for s in cast.spots if s.id in renamed)
    selections = {name: _keep(ids, rename) for name, ids in cast.selections.items()}
    updates = {
        "sets": tuple(_rewrite_set(s, rename) for s in show.sets),
        "forms": {fid: _rewrite_form(f, rename) for fid, f in show.forms.items()},
        "transitions": tuple(_rewrite_spec(spec, rename, dropped) for spec in show.transitions),
        "facing_overrides": _rewrite_overrides(show, rename, dropped),
        "zones": tuple(_rewrite_zone(show, z, rename, dropped) for z in show.zones),
        "pins": _rewrite_pins(show.pins, rename),
        "sheet": _rewrite_sheet(show, rename, dropped),
    }
    new_cast = Cast._validated({**dict(cast), "spots": spots, "selections": selections})
    rewritten_notes = None
    if notes is not None:
        kept_notes, orphaned = rewrite_note_spots(notes.notes, rename)
        rewritten_notes = notes.replace(notes=kept_notes)
        dropped.notes.extend(orphaned)
    report = RewriteReport(
        renamed={old: new for old, new in renamed.items() if old != new},
        dropped_routes=tuple(dropped.routes),
        dropped_facings=tuple(dropped.facings),
        orphaned_notes=tuple(dropped.notes),
        unresolved_selectors=tuple(dict.fromkeys(dropped.selectors)),
        notes=rewritten_notes,
    )
    return show.replace(cast=new_cast, **updates), report


def rewrite_spot_ids(show: "Show", rename: Rename) -> "Show":
    """Applies `rename` to every spot reference in a show; None drops the reference.

    Behaves as rewrite_spots() without the report.

    >>> from e8s.examples import two_set_show
    >>> rewrite_spot_ids(two_set_show(), lambda i: None if i == "T1" else i).cast.ids()
    ('T2', 'T3', 'T4')
    """
    return rewrite_spots(show, rename)[0]


def _rewrite_set(set_: Set, rename: Rename) -> Set:
    placements = tuple(
        p.replace(entity=new) for p in set_.placements if (new := rename(p.entity)) is not None
    )
    return set_.replace(placements=placements)


def _rewrite_form(form: Form, rename: Rename) -> Form:
    """Renames a form's members; a dropped member leaves an open slot, except in a text form."""
    renamed = [None if member is None else rename(member) for member in form.members]
    if form.kind == "text":
        renamed = [
            new
            for member, new in zip(form.members, renamed, strict=True)
            if member is None or new is not None
        ]
    return form.replace(members=tuple(renamed))


def _rewrite_route(route: Route, rename: Rename) -> Route:
    kept = [
        (new, offset)
        for member, offset in zip(route.members, route.offsets, strict=True)
        if (new := rename(member)) is not None
    ]
    return route.replace(members=tuple(m for m, _ in kept), offsets=tuple(o for _, o in kept))


def _rewrite_routes(
    spec: TransitionSpec, rename: Rename, dropped: _Dropped
) -> dict[RouteId, Route]:
    routes = {}
    for rid, route in spec.routes.items():
        moved = _rewrite_route(route, rename)
        if route.members and not moved.members:
            dropped.routes.append((spec.id, rid))
        else:
            routes[rid] = moved
    return routes


def _rewrite_facing(
    mode: FacingMode, value: FacingValue | None, rename: Rename
) -> tuple[FacingMode, FacingValue | None]:
    target = faces_entity(mode, value)
    if target is None:
        return mode, value
    new = rename(target)
    return mode, value if new is None else EntityFacing(entity=SpotId(new))


def _rewrite_spec(spec: TransitionSpec, rename: Rename, dropped: _Dropped) -> TransitionSpec:
    rows = {}
    for row in spec.rows.values():
        new = rename(row.entity)
        if new is not None:
            mode, value = _rewrite_facing(row.facing_mode, row.facing_value, rename)
            rows[new] = row.replace(entity=new, facing_mode=mode, facing_value=value)
    return spec.replace(rows=rows, routes=_rewrite_routes(spec, rename, dropped))


def rewrite_note_spots(
    notes: Sequence[Note], rename: Rename
) -> tuple[tuple[Note, ...], tuple[Note, ...]]:
    """Applies `rename` to the spot anchors of notes, dropping a note left without spots.

    Args:
        notes: Notes in document order.
        rename: Old spot ID to new ID, or None to drop the spot.

    Returns:
        kept: The notes that still have an anchor, spot anchors rewritten, in order.
        orphaned: Each note removed because every spot it was anchored to was dropped.

    >>> from datetime import UTC, datetime
    >>> from e8s.show.anchors import Anchor
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> note = Note(id="n1", text="solo", anchor=Anchor.on_spots(["T2"]), author="d", created_at=at)
    >>> kept, orphaned = rewrite_note_spots([note], lambda i: None if i == "T2" else i)
    >>> kept, [n.id for n in orphaned]
    ((), ['n1'])
    """
    kept: list[Note] = []
    orphaned: list[Note] = []
    for note in notes:
        spots = note.anchor.spots
        if spots is None:
            kept.append(note)
            continue
        moved = _keep(spots, rename)
        if spots and not moved:
            orphaned.append(note)
        else:
            kept.append(note.replace(anchor=note.anchor.replace(spots=moved)))
    return tuple(kept), tuple(orphaned)


def _rewrite_selector(
    show: "Show", selector: SelectorField, rename: Rename, dropped: _Dropped
) -> str | tuple[str, ...] | Selection:
    if isinstance(selector, Selection):
        return selector.replace(ids=_keep(selector.ids, rename))
    if isinstance(selector, str):
        try:
            if not is_label_dependent(selector):
                return selector
            return _keep(select(show, selector).ids, rename)
        except (SchemaError, UnknownRefError):
            dropped.selectors.append(selector)
            return selector
    return _keep(selector, rename)


def _has_no_spots(selector: str | tuple[str, ...] | Selection) -> bool:
    return not isinstance(selector, str) and len(selector) == 0


def _resolved_ids(show: "Show", selector: SelectorField) -> tuple[str, ...]:
    if isinstance(selector, Selection):
        return tuple(selector.ids)
    if isinstance(selector, str):
        try:
            return tuple(select(show, selector).ids)
        except (SchemaError, UnknownRefError):
            return ()
    return tuple(selector)


def _rewrite_overrides(
    show: "Show", rename: Rename, dropped: _Dropped
) -> tuple[FacingOverride, ...]:
    kept = []
    for override in show.facing_overrides:
        entities = _rewrite_selector(show, override.entities, rename, dropped)
        target = faces_entity(override.mode, override.value)
        lost_entities = _has_no_spots(entities) and bool(_resolved_ids(show, override.entities))
        if lost_entities or (target is not None and rename(target) is None):
            dropped.facings.append(override)
        else:
            _, value = _rewrite_facing(override.mode, override.value, rename)
            kept.append(override.replace(entities=entities, value=value))
    return tuple(kept)


def _rewrite_zone(show: "Show", zone: Zone, rename: Rename, dropped: _Dropped) -> Zone:
    return zone.replace(applies_to=_rewrite_selector(show, zone.applies_to, rename, dropped))


def _rewrite_pins(pins: "tuple[Pin, ...]", rename: Rename) -> "tuple[Pin, ...]":
    kept = []
    for pin in pins:
        if pin.target != "entity":
            kept.append(pin)
        elif (new := rename(SpotId(pin.id))) is not None:
            kept.append(pin.replace(id=new))
    return tuple(kept)


def _rewrite_sheet(show: "Show", rename: Rename, dropped: _Dropped) -> Sheet:
    zones = tuple(_rewrite_zone(show, z, rename, dropped) for z in show.sheet.zones)
    return show.sheet.replace(zones=zones)
