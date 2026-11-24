"""
events.py --- canvas events: derives them from revisions and replays them on a show

Contains:
    OP_KIND: maps each change op type to its canvas event kind
    EVENT_ID_LENGTH: sets how many characters of a revision ID start a derived event ID
    edit_ops(): lists the placement, form, path and count edits between two shows
    apply_event(): replays one canvas event as a model operation
    decode_event(): decodes stored event JSON, dropping a v0.2 actor field
    events_since(): derives the canvas events of every revision after a revision
"""

from collections.abc import Mapping

from e8s.core.errors import (
    LockedError,
)
from e8s.core.json_types import JsonValue
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
from e8s.show.history_models import Revision
from e8s.show.migrate import ReportBuilder
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin, pin_key, unpin
from e8s.show.storage import ROOT_REVISION
from e8s.show.structured import COMMENTS_DOC, decode_comments
from e8s.show.workspace import Workspace

OP_KIND: dict[type, EventKind] = {
    PlacementOp: "move",
    FormOp: "edit_form",
    PathsOp: "edit_paths",
    CountsOp: "set_counts",
}
EVENT_ID_LENGTH = 12


def apply_event(show: Show, event: CanvasEvent, *, force: bool = False) -> Show:
    """Replays one canvas event; pinned items raise LockedError unless `force` is set.

    Comment and highlight events leave the show unchanged. A pin event that adds a new pin needs
    no force; an unpin event, or a pin event that replaces an existing record (even an identical
    one), raises LockedError without it. An unpin event of a pin that does not exist raises
    UnknownRefError, with or without force.

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


def _show_events(before: Show, after: Show) -> list[tuple[EventKind, EventPayload]]:
    edits: list[tuple[EventKind, EventPayload]] = [
        (OP_KIND[type(op)], op) for op in edit_ops(before, after)
    ]
    return [*_pin_events(before, after), *edits]


def _comments(ws: Workspace, revision: str) -> dict[str, Comment]:
    raw = ws.tree_at(revision).get(COMMENTS_DOC)
    return {} if raw is None else {c.id: c for c in decode_comments(raw)}


def _comment_events(ws: Workspace, r: Revision) -> list[tuple[EventKind, EventPayload]]:
    if COMMENTS_DOC not in r.changed:
        return []
    old, new = _comments(ws, r.parent), _comments(ws, r.id)
    changed = [c for k, c in new.items() if old.get(k) != c]
    return [("comment", c) for c in changed]


def _revision_events(ws: Workspace, r: Revision) -> list[CanvasEvent]:
    before, after = ws.show_at(r.parent), ws.show_at(r.id)
    found = _show_events(before, after) if before and after else []
    found += _comment_events(ws, r)
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

    Events are derived from stored states, so replaying them with apply_event() on the show
    at `revision` reproduces each later placement, form, path and count edit. Structural
    edits (sets added or removed, cast or sheet changes) show up in changelog() only. Replaying a
    log that holds unpin events or pin replacements needs `force=True` in apply_event().
    """
    log = ws.repo.revisions(ws.show_id)
    if revision != ROOT_REVISION:
        ws.repo.get_revision(ws.show_id, revision)
        log = log[[r.id for r in log].index(revision) + 1 :]
    return [event for r in log for event in _revision_events(ws, r)]
