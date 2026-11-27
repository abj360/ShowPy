"""
events.py --- canvas events: derives them from revisions and replays them on a show

Contains:
    EVENT_ID_LENGTH: sets how many characters of a revision ID start a derived event ID
    edit_ops(): lists the placement, form, path and count edits between two shows, form
        deletions included
    apply_event(): replays one canvas event as a model operation
    delete_form(): removes a form from its owning set and from the show
    decode_event(): decodes stored event JSON, dropping a v0.2 actor field
    events_since(): derives the canvas events of every revision after a revision
"""

from collections.abc import Mapping

from e8s.core.errors import (
    LockedError,
    SchemaError,
    UnknownRefError,
)
from e8s.core.ids import FormId
from e8s.core.json_types import JsonValue
from e8s.show.changes import apply_change
from e8s.show.collab_models import (
    CanvasEvent,
    ChangeOp,
    Comment,
    CountsOp,
    FormOp,
    Highlight,
    PathsOp,
    PlacementOp,
)
from e8s.show.document_reading import reraised_for_show
from e8s.show.documents import is_show_doc
from e8s.show.event_derivation import Edit, WholeShow, pin_edits, show_edits
from e8s.show.history_models import Revision
from e8s.show.integrity import changed_paths
from e8s.show.items import changes
from e8s.show.locks import require_unlocked
from e8s.show.migrate import ReportBuilder
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin, pin_key, unpin
from e8s.show.storage import ROOT_REVISION
from e8s.show.structured import COMMENTS_DOC, decode_comments
from e8s.show.tree_items import reads_alone, tree_changes
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


def edit_ops(before: Show, after: Show) -> list[ChangeOp]:
    """Lists the path, count, placement and form edits between two shows, for overlap checks.

    A form the later show deleted is listed as a FormOp of the form as it was, so a check of
    what changed sees the deletion; replaying that op would add the form back.
    """
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
    ops += [FormOp(form=f) for k, f in sorted(before.forms.items()) if k not in after.forms]
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


def _whole_edits(ws: Workspace, r: Revision) -> list[Edit]:
    """Derives a revision's edits from its parent's whole show and its own."""
    before, after = ws.show_at(r.parent), ws.show_at(r.id)
    if before is None or after is None:
        return []
    delta = changes(before, after).changes
    return [*pin_edits(before, after), *show_edits(delta, WholeShow(before), WholeShow(after))]


def _revision_edits(ws: Workspace, r: Revision, paths: list[str]) -> list[Edit]:
    """Derives a revision's edits, from its changed documents alone when they hold all of them.

    A revision whose parent holds no show only has its show checked. One that changed no
    show.json, cast document or set ID is read part by part: each changed document is decoded
    and checked, and nothing else; pins live in show.json, so it has no pin edits.
    """
    before, after = ws._parts_at(r.parent), ws._parts_at(r.id)
    if not after.has_show():
        return []
    if not before.has_show():
        ws.show_at(r.id)
        return []
    try:
        if reads_alone(before, after, paths):
            return show_edits(tree_changes(before, after, paths).changes, before, after)
    except SchemaError as exc:
        raise reraised_for_show(exc) from exc
    return _whole_edits(ws, r)


def _comments(ws: Workspace, revision: str) -> dict[str, Comment]:
    raw = ws.tree_at(revision).get(COMMENTS_DOC)
    return {} if raw is None else {c.id: c for c in decode_comments(raw)}


def _comment_events(ws: Workspace, r: Revision, paths: list[str]) -> list[Edit]:
    if COMMENTS_DOC not in paths:
        return []
    old, new = _comments(ws, r.parent), _comments(ws, r.id)
    changed = [c for k, c in new.items() if old.get(k) != c]
    return [("comment", c) for c in changed]


def _stamped(r: Revision, found: list[Edit]) -> list[CanvasEvent]:
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

    Events are derived from the items each revision changed. What a revision changed is read
    from the blob digests of its tree and its parent's, never from its stored record, and only
    the documents it changed are decoded: a revision that changed no show document decodes
    nothing. A revision that changed show.json, the cast or a set's ID, or that is stored in an
    older format or layout, is compared as two whole shows instead. Replaying the events
    with apply_event() on the show at `revision` reproduces each later placement, form, path and
    count edit. Structural edits (sets added or removed, cast or sheet changes) show up in
    changelog() only. Replaying a log that holds unpin events or pin replacements needs
    `force=True` in apply_event().

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
        ws.show_at(revision)
    events: list[CanvasEvent] = []
    for r in log:
        changed = changed_paths(ws.tree_at(r.parent), ws.tree_at(r.id))
        found = _revision_edits(ws, r, changed) if any(map(is_show_doc, changed)) else []
        events += _stamped(r, [*found, *_comment_events(ws, r, changed)])
    return events
