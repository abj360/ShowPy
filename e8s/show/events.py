"""
events.py --- canvas events: derives them from revisions and replays them on a show

Contains:
    EVENT_ID_LENGTH: sets how many characters of a revision ID start a derived event ID
    edit_ops(): lists the placement, form, path and count edits between two shows
    apply_event(): replays one canvas event as a model operation
    delete_form(): removes a form from its owning set and from the show
    decode_event(): decodes stored event JSON, dropping a v0.2 actor field
    events_since(): derives the canvas events of every revision after a revision
"""

from collections.abc import Iterable, Mapping

from e8s.choreo.models import Placement
from e8s.core.errors import (
    LockedError,
    UnknownRefError,
)
from e8s.core.ids import FormId, SetId
from e8s.core.json_types import JsonValue
from e8s.show.change_models import ItemChange
from e8s.show.changes import apply_change
from e8s.show.collab_models import (
    CanvasEvent,
    ChangeOp,
    Comment,
    CountsOp,
    EventKind,
    EventPayload,
    FormOp,
    Highlight,
    PathsOp,
    PlacementOp,
)
from e8s.show.documents import is_show_doc
from e8s.show.history_models import Revision
from e8s.show.item_table import TIMING, set_keys_by_id, transition_key
from e8s.show.items import changes
from e8s.show.locks import require_unlocked
from e8s.show.migrate import ReportBuilder
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin, pin_key, unpin
from e8s.show.set_keys import set_key
from e8s.show.storage import ROOT_REVISION
from e8s.show.structured import COMMENTS_DOC, decode_comments
from e8s.show.workspace import Workspace

EVENT_ID_LENGTH = 12


def apply_event(show: Show, event: CanvasEvent, *, force: bool = False) -> Show:
    """Replays one canvas event; pinned items raise LockedError unless `force` is set.

    Comment and highlight events leave the show unchanged. A delete_form event removes the form
    and raises UnknownRefError when the show holds no such form. A pin event that adds a new pin
    needs no force; an unpin event, or a pin event that replaces an existing record (even an
    identical one), raises LockedError without it. An unpin event of a pin that does not exist
    raises UnknownRefError, with or without force.

    Args:
        show: Show the event applies to.
        event: Canvas event to replay.
        force: Whether the caller overrides pins and locks on the touched items.

    Returns:
        edited: The show after the event.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.collab_models import CanvasEvent, CountsOp
    >>> event = CanvasEvent(id="e1", at=datetime(2026, 11, 11, tzinfo=UTC), kind="set_counts",
    ...                     payload=CountsOp(set_id="2", counts=6))
    >>> apply_event(two_set_show(), event).set("2").counts
    6.0
    """
    payload = event.payload
    if isinstance(payload, Comment | Highlight):
        return show
    if isinstance(payload, Pin):
        _require_pin_force(show, payload, event, force=force)
        if event.kind == "pin":
            return pin(show, payload.target, payload.id, payload.reason, payload.by)
        return unpin(show, payload.target, payload.id)
    if event.kind == "delete_form" and isinstance(payload, FormOp):
        return delete_form(show, payload.form.id, force=force)
    return apply_change(show, [payload], force=force)


def decode_event(
    data: Mapping[str, JsonValue], *, report: ReportBuilder | None = None
) -> CanvasEvent:
    """Decodes stored event JSON into a CanvasEvent, dropping the `actor` field v0.2 stored.

    Authorship belongs to the host app, so an `actor` key is removed and, when a `report` is
    given, recorded as a repair. Any other unknown field raises SchemaError.

    Args:
        data: Parsed event JSON from v0.3 or v0.2.
        report: Migration report builder that records the dropped `actor`.

    Returns:
        event: The decoded event.

    >>> from e8s.examples import two_set_show
    >>> set_id = two_set_show().set_ids()[1]
    >>> raw = {"id": "e", "at": "2026-11-11T00:00:00Z", "kind": "set_counts", "actor": "agent",
    ...        "payload": {"op": "counts", "set_id": set_id, "counts": 6.0}}
    >>> decode_event(raw).payload.counts
    6.0
    """
    kept = {k: v for k, v in data.items() if k != "actor"}
    if report is not None and "actor" in data:
        report.repair(f"dropped actor {data['actor']!r} from event {data.get('id')!r}")
    return CanvasEvent._validated(kept)


def _require_pin_force(show: Show, held: Pin, event: CanvasEvent, *, force: bool) -> None:
    key = (held.target, pin_key(held.target, held.id))
    exists = any((p.target, p.id) == key for p in show.pins)
    if force or not exists:
        return
    raise LockedError(
        f"{event.kind} event on {held.target} {held.id} drops or replaces an existing pin",
        hint="pass force=True to replay it, or keep the pin as it is",
        context={"target": held.target, "id": held.id, "event": event.id},
    )


def _pin_events(before: Show, after: Show) -> list[tuple[EventKind, EventPayload]]:
    removed = [p for p in before.pins if p not in after.pins]
    added = [p for p in after.pins if p not in before.pins]
    return [
        *(("unpin", p) for p in removed),
        *(("pin", p) for p in added),
    ]


def edit_ops(before: Show, after: Show) -> list[ChangeOp]:
    """Lists the path, count, placement and form edits that turn one show into another."""
    ops: list[ChangeOp] = []
    shared = [t for t in after.transition_ids() if t in before.transition_ids()]
    ops += [
        PathsOp(spec=after.transition(t))
        for t in shared
        if after.transition(t) != before.transition(t)
    ]
    old = {s.id: s for s in before.sets}
    common = [s for s in after.sets if s.id in old]
    ops += [CountsOp(set_id=s.id, counts=s.counts) for s in common if s.counts != old[s.id].counts]
    for current in common:
        previous = {p.entity: p for p in old[current.id].placements}
        ops += [
            PlacementOp(set_id=current.id, placement=p)
            for p in current.placements
            if p.entity in previous and previous[p.entity] != p
        ]
    ops += [FormOp(form=f) for k, f in sorted(after.forms.items()) if before.forms.get(k) != f]
    return ops


def delete_form(show: Show, form_id: FormId, *, force: bool = False) -> Show:
    """Removes a form from its owning set and from the show; a locked form needs `force`.

    Args:
        show: Show that holds the form.
        form_id: ID of the form to remove.
        force: Whether the caller overrides a lock on the form.

    Returns:
        edited: The show without the form.

    >>> from e8s.choreo.form_models import Form
    >>> from e8s.choreo.recipe_models import LineRecipe
    >>> from e8s.core.ids import FormId
    >>> from e8s.examples import two_set_show
    >>> from e8s.geo.shapes import line
    >>> form = Form(id="f1", set="2", recipe=LineRecipe(curve=line((0, 0), (6, 0))),
    ...             members=("T1", "T2"))
    >>> show = two_set_show().replace(forms={"f1": form})
    >>> show = show.replace(sets=(show.sets[0], show.sets[1].replace(forms=("f1",))))
    >>> edited = delete_form(show, FormId("f1"))
    >>> dict(edited.forms), edited.set("2").forms
    ({}, ())
    >>> delete_form(edited, FormId("f1"))
    Traceback (most recent call last):
    ...
    e8s.core.errors.UnknownRefError: UNKNOWN_REF: no form f1 (hint: forms: none)
    """
    stored = show.forms.get(form_id)
    if stored is None:
        raise UnknownRefError(
            f"no form {form_id}",
            hint=f"forms: {', '.join(sorted(show.forms)) or 'none'}",
            context={"form": form_id},
        )
    position = show.set_index(stored.set)
    owner = show.sets[position]
    kept = tuple(f for f in owner.forms if f != form_id)
    sets = (*show.sets[:position], owner.replace(forms=kept), *show.sets[position + 1 :])
    forms = {k: f for k, f in show.forms.items() if k != form_id}
    edited = show.replace(forms=forms, sets=sets)
    require_unlocked(show, edited, force=force)
    return edited


type _Edit = tuple[EventKind, EventPayload]


def _move_edit(set_id: SetId, placement: Placement) -> _Edit:
    return "move", PlacementOp(set_id=set_id, placement=placement)


def _touched_sets(delta: Iterable[ItemChange], before: Show, after: Show) -> list[_Edit]:
    """Lists the count and placement edits of sets present in both shows, in set order."""
    old = {set_key(s) for s in before.sets}
    timing: set[str] = set()
    moved: dict[str, set[str]] = {}
    for change in delta:
        key, path = change.key, change.key.path
        if key.kind not in ("set", "placement"):
            continue
        if change.before is None or change.after is None or path[0] not in old:
            continue
        if key.kind == "set" and key.role == TIMING:
            timing.add(path[0])
        elif key.kind == "placement":
            moved.setdefault(path[0], set()).add(path[1])
    touched = timing | moved.keys()
    counts: list[_Edit] = []
    placements: list[_Edit] = []
    for current in after.sets:
        name = set_key(current)
        if name not in touched:
            continue
        if name in timing:
            counts.append(("set_counts", CountsOp(set_id=current.id, counts=current.counts)))
        entities = moved.get(name, set())
        placements += [
            _move_edit(current.id, p) for p in current.placements if p.entity in entities
        ]
    return [*counts, *placements]


def _edited_transitions(delta: Iterable[ItemChange]) -> set[str]:
    """Names the transitions whose rows, routes, notes or source hash changed, not their counts."""
    found: set[str] = set()
    for change in delta:
        if change.key.kind not in ("transition", "path_row", "route"):
            continue
        if change.key.kind == "transition" and _without_counts(change.before) == _without_counts(
            change.after
        ):
            continue
        found.add(change.key.path[0])
    return found


def _without_counts(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return {name: field for name, field in value.items() if name != "counts"}


def _path_edits(delta: Iterable[ItemChange], before: Show, after: Show) -> list[_Edit]:
    names = _edited_transitions(delta)
    if not names:
        return []
    keys = set_keys_by_id(after)
    shared = set(before.transition_ids())
    return [
        ("edit_paths", PathsOp(spec=after.transition(tid)))
        for tid in after.transition_ids()
        if tid in shared and transition_key(keys, tid) in names
    ]


def _form_edits(delta: Iterable[ItemChange], before: Show, after: Show) -> list[_Edit]:
    edits: list[_Edit] = []
    for change in sorted(delta, key=lambda c: c.key.path):
        if change.key.kind != "form":
            continue
        form_id = FormId(change.key.path[0])
        if change.after is None:
            edits.append(("delete_form", FormOp(form=before.forms[form_id])))
        else:
            edits.append(("edit_form", FormOp(form=after.forms[form_id])))
    return edits


def _show_events(before: Show, after: Show) -> list[_Edit]:
    delta = changes(before, after).changes
    return [
        *_pin_events(before, after),
        *_path_edits(delta, before, after),
        *_touched_sets(delta, before, after),
        *_form_edits(delta, before, after),
    ]


def _comments(ws: Workspace, revision: str) -> dict[str, Comment]:
    raw = ws.tree_at(revision).get(COMMENTS_DOC)
    return {} if raw is None else {c.id: c for c in decode_comments(raw)}


def _comment_events(ws: Workspace, r: Revision) -> list[_Edit]:
    if COMMENTS_DOC not in r.changed:
        return []
    old, new = _comments(ws, r.parent), _comments(ws, r.id)
    changed = [c for k, c in new.items() if old.get(k) != c]
    return [("comment", c) for c in changed]


def _stamped(r: Revision, found: list[_Edit]) -> list[CanvasEvent]:
    return [
        CanvasEvent(
            id=f"{r.id[:EVENT_ID_LENGTH]}-{n}",
            at=r.at,
            kind=kind,
            payload=payload,
            revision=r.id,
        )
        for n, (kind, payload) in enumerate(found)
    ]


def events_since(ws: Workspace, revision: str) -> list[CanvasEvent]:
    """Derives the canvas events of every revision after `revision`, oldest first.

    Events are derived from the items that changed between stored states, so a revision that
    changed no show document decodes no show, and replaying the events with apply_event() on the
    show at `revision` reproduces each later placement, form, path and count edit. Structural
    edits (sets added or removed, cast or sheet changes) show up in changelog() only. Replaying a
    log that holds unpin events or pin replacements needs `force=True` in apply_event().

    Args:
        ws: Workspace on the show.
        revision: Revision after which events start; the root revision lists every revision.

    Returns:
        events: Pin, path, count, placement, form and comment events of each later revision.
    """
    log = ws.repo.revisions(ws.show_id)
    if revision != ROOT_REVISION:
        ws.repo.get_revision(ws.show_id, revision)
        log = log[[r.id for r in log].index(revision) + 1 :]
    current = None if revision == ROOT_REVISION else ws.show_at(revision)
    events: list[CanvasEvent] = []
    for r in log:
        found: list[_Edit] = []
        if any(is_show_doc(path) for path in r.changed):
            after = ws.show_at(r.id)
            if current is not None and after is not None:
                found = _show_events(current, after)
            current = after
        events += _stamped(r, [*found, *_comment_events(ws, r)])
    return events
