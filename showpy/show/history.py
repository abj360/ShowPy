"""
history.py --- diffs sets, paths and shows; reverts; spot renames; the changelog

Contains:
    diff_sets(): lists spots moved, turned, added or removed and forms changed between sets
    diff_paths(): lists spots and routes whose effective paths differ between transitions
    diff_show(): summarizes every difference between two shows as a JSON-safe dict
    revert(): saves the documents of an earlier revision as a new revision
    rename_entities(): rewrites spot IDs everywhere in a show
    changelog(): lists revisions since a revision as terse lines
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from showpy.choreo.models import (
    FacingMode,
    FacingOverride,
    PathRow,
    Placement,
    Set,
    TransitionSpec,
)
from showpy.core.errors import SchemaError
from showpy.core.ids import SpotId, format_tid
from showpy.core.selection import Selection, SelectorField
from showpy.core.units import angle_diff
from showpy.core.util import approx_equal, natural_sort
from showpy.notes.models import Note
from showpy.show.models import PathDiff, Revision, SetDiff, Show
from showpy.show.storage import ROOT_REVISION, Workspace

SHORT_ID = 7
TIME_FORMAT = "%Y-%m-%d %H:%M"
SHOW_FIELDS = ("sheet", "timeline", "coordinates", "zones", "facing_overrides", "dependencies")


def _member_key(row: tuple[int | None, SpotId]) -> tuple[bool, int, SpotId]:
    return row[0] is None, row[0] or 0, row[1]


def _form_members(s: Set) -> dict[str, list[tuple[int | None, SpotId]]]:
    members: dict[str, list[tuple[int | None, SpotId]]] = {}
    for p in s.placements:
        if p.form is not None:
            members.setdefault(p.form, []).append((p.order, p.entity))
    return {form: sorted(rows, key=_member_key) for form, rows in members.items()}


def _changed_forms(a: Set, b: Set) -> list[str]:
    left, right = _form_members(a), _form_members(b)
    declared = set(a.forms) ^ set(b.forms)
    membership = {f for f in set(left) | set(right) if left.get(f) != right.get(f)}
    return natural_sort(declared | membership)


def diff_sets(a: Set, b: Set, tol: float = 1e-6) -> SetDiff:
    """Lists what differs between two sets.

    Args:
        a: Earlier set.
        b: Later set.
        tol: Meters and degrees below which values count as equal.

    Returns:
        diff: Moved (position or on-field change), turned, added and removed spots, and forms
            declared or re-membered differently.

    >>> from showpy.examples import two_set_show
    >>> first, second = two_set_show().sets
    >>> diff_sets(first, second).moved
    ('T1', 'T2', 'T3', 'T4')
    """
    left = {p.entity: p for p in a.placements}
    right = {p.entity: p for p in b.placements}
    common = [e for e in left if e in right]
    moved = [
        e
        for e in common
        if not approx_equal(left[e].point, right[e].point, tol)
        or left[e].on_field != right[e].on_field
    ]
    turned = [e for e in common if abs(angle_diff(left[e].facing, right[e].facing)) > tol]
    return SetDiff(
        moved=tuple(natural_sort(moved)),
        facing_changed=tuple(natural_sort(turned)),
        added=tuple(natural_sort(set(right) - set(left))),
        removed=tuple(natural_sort(set(left) - set(right))),
        forms_changed=tuple(_changed_forms(a, b)),
    )


def diff_paths(a: TransitionSpec, b: TransitionSpec) -> PathDiff:
    """Lists spots and routes whose effective paths differ; a missing row equals a default row.

    >>> from showpy.choreo.models import PathRow
    >>> plain = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> late = plain.replace(rows={"T1": PathRow(entity="T1", start=2.0)})
    >>> diff_paths(plain, late).changed_rows
    ('T1',)
    """
    rows = [e for e in set(a.rows) | set(b.rows) if a.row(e) != b.row(e)]
    routes = [r for r in set(a.routes) | set(b.routes) if a.routes.get(r) != b.routes.get(r)]
    return PathDiff(
        changed_rows=tuple(natural_sort(rows)), routes_changed=tuple(natural_sort(routes))
    )


# Any: diff summaries hold JSON values of mixed types.
def _keyed(left: Mapping[str, object], right: Mapping[str, object]) -> dict[str, Any]:
    found = {
        "added": natural_sort(set(right) - set(left)),
        "removed": natural_sort(set(left) - set(right)),
        "changed": natural_sort(k for k in set(left) & set(right) if left[k] != right[k]),
    }
    return {key: value for key, value in found.items() if value}


# Any: diff summaries hold JSON values of mixed types.
def _sets_diff(a: Show, b: Show) -> dict[str, Any]:
    old, new = {s.id: s for s in a.sets}, {s.id: s for s in b.sets}
    common = [s for s in b.set_ids() if s in old]
    changed = {s: diff_sets(old[s], new[s]).model_dump(exclude_defaults=True) for s in common}
    counts = {s: [old[s].counts, new[s].counts] for s in common if old[s].counts != new[s].counts}
    found = {
        **_keyed(old, new),
        "changed": {s: d for s, d in changed.items() if d},
        "counts": counts,
        "order_changed": [s for s in a.set_ids() if s in new] != common,
    }
    return {key: value for key, value in found.items() if value}


# Any: diff summaries hold JSON values of mixed types.
def _paths_diff(a: Show, b: Show) -> dict[str, Any]:
    shared = [t for t in b.transition_ids() if t in a.transition_ids()]
    diffs = {format_tid(t): diff_paths(a.transition(t), b.transition(t)) for t in shared}
    return {t: d.model_dump(exclude_defaults=True) for t, d in diffs.items() if not d.is_empty}


# Any: diff summaries hold JSON values of mixed types.
def diff_show(a: Show, b: Show) -> dict[str, Any]:
    """Summarizes every difference between two shows; keys with no difference are left out.

    Keys: `title`, `sets` (added, removed, changed, counts, order_changed), `forms`, `paths`,
    `cast`, `notes`, `pins` and one flag per changed whole field (`sheet`, `timeline`, ...).

    >>> from showpy.examples import two_set_show
    >>> diff_show(two_set_show(), two_set_show().replace(title="B"))
    {'title': ['Test show', 'B']}
    """
    found: dict[str, Any] = {
        "title": [a.title, b.title] if a.title != b.title else None,
        "sets": _sets_diff(a, b),
        "forms": _keyed(a.forms, b.forms),
        "paths": _paths_diff(a, b),
        "cast": _keyed({s.id: s for s in a.cast.spots}, {s.id: s for s in b.cast.spots}),
        "notes": _keyed({n.id: n for n in a.notes}, {n.id: n for n in b.notes}),
        "pins": _keyed(
            {f"{p.target}:{p.id}": p for p in a.pins}, {f"{p.target}:{p.id}": p for p in b.pins}
        ),
    }
    found.update({name: True for name in SHOW_FIELDS if getattr(a, name) != getattr(b, name)})
    return {key: value for key, value in found.items() if value}


def revert(ws: Workspace, revision_id: str, at: datetime) -> Revision:
    """Saves the documents of an earlier revision as a new head revision.

    Hosts build undo on it: only the host knows which revisions its user made (ADR 0003).

    Args:
        ws: Workspace; it moves to the head first.
        revision_id: Revision whose documents come back.
        at: Time stamp of the new revision.

    Returns:
        revision: The new head; the head itself when nothing differs.

    >>> from datetime import UTC
    >>> from showpy.show.repositories import InMemoryRepository
    >>> from showpy.show.storage import load_show, open_workspace, save_show
    >>> from showpy.examples import two_set_show
    >>> ws = open_workspace(InMemoryRepository(), "s")
    >>> first = save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC))
    >>> _ = save_show(ws, load_show(ws).replace(title="B"), datetime(2026, 11, 12, tzinfo=UTC))
    >>> _ = revert(ws, first.id, datetime(2026, 11, 13, tzinfo=UTC))
    >>> load_show(ws).title
    'Test show'
    """
    target = ws.repo.get_revision(ws.show_id, revision_id)
    ws.refresh()
    old, head = ws.tree_at(target.id), ws.tree_at(ws.revision)
    paths = {p for p in set(old) | set(head) if old.get(p) != head.get(p)}
    return ws.commit({p: old.get(p) for p in sorted(paths)}, at=at)


def _require_renamable(show: Show, mapping: Mapping[SpotId, SpotId]) -> None:
    known = set(show.cast.ids())
    for old in mapping:
        show.cast.spot(old)
    targets = list(mapping.values())
    kept = known - set(mapping)
    clashes = sorted({t for t in targets if t in kept or targets.count(t) > 1 or not t})
    if clashes:
        raise SchemaError(
            f"renaming would collide on {', '.join(clashes) or 'an empty id'}",
            hint="map every spot to a unique, non-empty id that no other spot keeps",
            context={"collisions": clashes},
        )


def _selector(value: SelectorField, rename: Callable[[str], str]) -> SelectorField:
    if isinstance(value, str):
        return rename(value)
    if isinstance(value, Selection):
        return value.replace(ids=tuple(map(rename, value.ids)))
    return tuple(map(rename, value))


def _row(row: PathRow, rename: Callable[[str], str]) -> PathRow:
    value = row.facing_value
    if row.facing_mode is FacingMode.TOWARD_ENTITY and isinstance(value, str):
        value = rename(value)
    return row.replace(entity=rename(row.entity), facing_value=value)


def _spec(spec: TransitionSpec, rename: Callable[[str], str]) -> TransitionSpec:
    rows = {rename(e): _row(r, rename) for e, r in spec.rows.items()}
    routes = {k: r.replace(members=tuple(map(rename, r.members))) for k, r in spec.routes.items()}
    return spec.replace(rows=rows, routes=routes)


def _override(item: FacingOverride, rename: Callable[[str], str]) -> FacingOverride:
    value = item.value
    if item.mode is FacingMode.TOWARD_ENTITY and isinstance(value, str):
        value = rename(value)
    return item.replace(entities=_selector(item.entities, rename), value=value)


def _note(note: Note, rename: Callable[[str], str]) -> Note:
    spots = note.anchor.spots
    if spots is None:
        return note
    anchor = note.anchor.replace(spots=tuple(map(rename, spots)))
    return note.replace(anchor=anchor)


def _placements(s: Set, rename: Callable[[str], str]) -> Set:
    rows: tuple[Placement, ...] = tuple(p.replace(entity=rename(p.entity)) for p in s.placements)
    return s.replace(placements=rows)


def rename_entities(show: Show, mapping: Mapping[SpotId, SpotId]) -> Show:
    """Rewrites spot IDs everywhere in a show.

    Covers cast, selections, placements, forms, paths, routes, notes, facing overrides, zones
    and pins. Renaming relabels spots without moving them, so pins do not block it. Swaps are
    allowed; a new ID that another spot keeps raises SchemaError.

    >>> from showpy.examples import two_set_show
    >>> renamed = rename_entities(two_set_show(), {"T1": "S1"})
    >>> renamed.cast.ids(), renamed.sets[1].entity_ids()[0]
    (('S1', 'T2', 'T3', 'T4'), 'S1')
    """
    _require_renamable(show, mapping)

    def rename(spot: str) -> str:
        return mapping.get(spot, spot)

    cast = show.cast.replace(
        spots=tuple(s.replace(id=rename(s.id)) for s in show.cast.spots),
        selections={k: frozenset(map(rename, v)) for k, v in show.cast.selections.items()},
    )
    return show.replace(
        cast=cast,
        sets=tuple(_placements(s, rename) for s in show.sets),
        forms={k: f.replace(members=tuple(map(rename, f.members))) for k, f in show.forms.items()},
        transitions=tuple(_spec(spec, rename) for spec in show.transitions),
        notes=tuple(_note(n, rename) for n in show.notes),
        facing_overrides=tuple(_override(o, rename) for o in show.facing_overrides),
        zones=tuple(z.replace(applies_to=_selector(z.applies_to, rename)) for z in show.zones),
        pins=tuple(p.replace(id=rename(p.id)) if p.target == "entity" else p for p in show.pins),
    )


def _line(revision: Revision) -> str:
    when = revision.at.strftime(TIME_FORMAT)
    return f"{revision.id[:SHORT_ID]} {when} ({', '.join(revision.changed)})"


def changelog(ws: Workspace, since: str = ROOT_REVISION) -> str:
    """Lists revisions after `since`, oldest first, as `<short id> <at> (<paths>)` lines.

    >>> from datetime import UTC
    >>> from showpy.show.repositories import InMemoryRepository
    >>> from showpy.show.storage import open_workspace, save_show
    >>> from showpy.examples import two_set_show
    >>> ws = open_workspace(InMemoryRepository(), "s")
    >>> first = save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC))
    >>> _ = ws.write("/sets/1/set.py", "pass", at=datetime(2026, 11, 11, 0, 5, tzinfo=UTC))
    >>> changelog(ws, first.id).split(" ", 1)[1]
    '2026-11-11 00:05 (/sets/1/set.py)'
    """
    log = ws.repo.revisions(ws.show_id)
    if since != ROOT_REVISION:
        ws.repo.get_revision(ws.show_id, since)
        log = log[[r.id for r in log].index(since) + 1 :]
    return "\n".join(map(_line, log))
