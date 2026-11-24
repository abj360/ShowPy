"""
history.py --- diffs sets, paths and shows; reverts; spot renames; the changelog

Contains:
    diff_sets(): lists spots moved, turned, added or removed and forms changed between sets
    diff_paths(): lists spots and routes whose effective paths differ between transitions
    diff_show(): summarizes every difference between two shows as a JSON-safe dict
    revert(): saves the documents of an earlier revision as a new revision
    changelog(): lists revisions since a revision as terse lines
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.ids import FormId, SpotId, format_tid
from e8s.core.units import angle_diff
from e8s.core.util import approx_equal, natural_sort
from e8s.show.models import PathDiff, Revision, SetDiff, Show
from e8s.show.storage import ROOT_REVISION, Workspace

SHORT_ID = 7
TIME_FORMAT = "%Y-%m-%d %H:%M"
SHOW_FIELDS = ("sheet", "timeline", "facing_overrides", "dependencies")


def _member_key(row: tuple[int | None, SpotId]) -> tuple[bool, int, SpotId]:
    return row[0] is None, row[0] or 0, row[1]


def _form_members(s: Set) -> dict[FormId, list[tuple[int | None, SpotId]]]:
    members: dict[FormId, list[tuple[int | None, SpotId]]] = {}
    for p in s.placements:
        if p.form is not None:
            members.setdefault(p.form, []).append((p.order, p.entity))
    return {form: sorted(rows, key=_member_key) for form, rows in members.items()}


def _changed_forms(a: Set, b: Set) -> list[FormId]:
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

    >>> from e8s.examples import two_set_show
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
        if not approx_equal(left[e].point, right[e].point, abs_tol=tol)
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

    >>> from e8s.choreo.path_models import PathRow
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
def _keyed[K: str](left: Mapping[K, object], right: Mapping[K, object]) -> dict[str, Any]:
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
    changed = {s: diff_sets(old[s], new[s])._dumped(exclude_defaults=True) for s in common}
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
    return {t: d._dumped(exclude_defaults=True) for t, d in diffs.items() if not d.is_empty}


# Any: diff summaries hold JSON values of mixed types.
def diff_show(a: Show, b: Show) -> dict[str, Any]:
    """Summarizes every difference between two shows; keys with no difference are left out.

    Keys: `title`, `sets` (added, removed, changed, counts, order_changed), `forms`, `paths`,
    `cast`, `pins` and one flag per changed whole field (`sheet`, `timeline`, ...).

    >>> from e8s.examples import two_set_show
    >>> diff_show(two_set_show(), two_set_show().replace(title="B"))
    {'title': ['Test show', 'B']}
    """
    found: dict[str, Any] = {
        "title": [a.title, b.title] if a.title != b.title else None,
        "sets": _sets_diff(a, b),
        "forms": _keyed(a.forms, b.forms),
        "paths": _paths_diff(a, b),
        "cast": _keyed({s.id: s for s in a.cast.spots}, {s.id: s for s in b.cast.spots}),
        "pins": _keyed(
            {f"{p.target}:{p.id}": p for p in a.pins}, {f"{p.target}:{p.id}": p for p in b.pins}
        ),
    }
    found.update({name: True for name in SHOW_FIELDS if getattr(a, name) != getattr(b, name)})
    return {key: value for key, value in found.items() if value}


def revert(ws: Workspace, revision_id: str, at: datetime) -> Revision:
    """Saves the documents of an earlier revision as a new head revision.

    Hosts build undo on it: only the host knows which revisions its user made. The restored
    documents come back as they were, pinned and locked items included, and no `force` is asked:
    the host decides who may revert.

    Args:
        ws: Workspace; it moves to the head first.
        revision_id: Revision whose documents come back.
        at: Time stamp of the new revision.

    Returns:
        revision: The new head; the head itself when nothing differs.

    >>> from datetime import UTC
    >>> from e8s.show.repositories import InMemoryRepository
    >>> from e8s.show.storage import load_show, open_workspace, save_show
    >>> from e8s.examples import two_set_show
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
    return ws._commit_stored({p: old.get(p) for p in sorted(paths)}, at=at)


def _line(revision: Revision) -> str:
    when = revision.at.strftime(TIME_FORMAT)
    return f"{revision.id[:SHORT_ID]} {when} ({', '.join(revision.changed)})"


def changelog(ws: Workspace, since: str = ROOT_REVISION) -> str:
    """Lists revisions after `since`, oldest first, as `<short id> <at> (<paths>)` lines.

    >>> from datetime import UTC
    >>> from e8s.show.repositories import InMemoryRepository
    >>> from e8s.show.storage import open_workspace, save_show
    >>> from e8s.examples import two_set_show
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
